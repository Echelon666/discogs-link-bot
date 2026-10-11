#!/usr/bin/env python3
r"""Discogs Link Bot — Stage 2 v5 normalization verification (read-only).

This script NEVER submits edits to MusicBrainz. It evaluates candidates from
Stage 1 against the MusicBrainz Web Service on the user's local mirror and the
Discogs API, then writes an auditable TSV report.

Requirements:
- Python 3.10+
- Local MusicBrainz mirror serving /ws/2 (default: http://localhost:5000)
- A Discogs personal API token in environment variable DISCOGS_TOKEN

Run a small pilot first:
  $env:DISCOGS_TOKEN = "..."  # don't paste the token into ChatGPT or source code
  py .\discogs_bot_verify.py .\discogs_bot_preflight_output\stage1_unique_release_candidates.tsv --limit 20 --out-dir .\discogs_bot_verify_output --cache-dir .\discogs_bot_verify_output\cache

Then, after reviewing report and confirming API access:
  py .\discogs_bot_verify.py .\discogs_bot_preflight_output\stage1_unique_release_candidates.tsv --out-dir .\discogs_bot_verify_output --cache-dir .\discogs_bot_verify_output\cache

No fuzzy matching is used. v5 applies explicit conservative normalizations: typography variants for text, punctuation-only equivalence for titles while preserving every letter, digit, accent, apostrophe and word order, whitespace/dash-glyph normalization for catalog numbers, and a final “Records” label suffix variant only when the same catalog number also matches on the same label entry. All metadata checks are evaluated before selecting the final status. Barcode comparison is exact except for standard UPC-A/EAN-13 equivalence. Network/API failures are fail-closed.
"""

from __future__ import annotations

import argparse
import csv
import html.parser
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

BLACKLIST_URL = "https://musicbrainz.org/doc/Bots/Blacklist/Discogs_Links"
CACHE_MAX_AGE_SECONDS = 6 * 60 * 60
DEFAULT_MB_BASE_URL = "http://localhost:5000"
DEFAULT_DISCOGS_USER_AGENT = "DiscogsLinkBot/0.2 (+https://github.com/Echelon666/discogs-link-bot; read-only release verification)"
MBID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class HTTPStatusError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = "") -> None:
        super().__init__(f"HTTP {status} for {url}: {body[:240]}")
        self.status = status
        self.url = url
        self.body = body


class AnchorCollector(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            for key, value in attrs:
                if key.lower() == "href" and value:
                    self.hrefs.append(value)


def http_get(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> tuple[bytes, dict[str, str]]:
    req_headers = {"User-Agent": DEFAULT_DISCOGS_USER_AGENT, **(headers or {})}
    req = urllib.request.Request(url, headers=req_headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read(), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise HTTPStatusError(exc.code, url, body) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Network error for {url}: {exc}") from exc


def http_get_json(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> tuple[dict[str, Any], dict[str, str]]:
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            data, response_headers = http_get(url, {"Accept": "application/json", **(headers or {})}, timeout)
            decoded = json.loads(data.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise RuntimeError(f"JSON root is not an object: {url}")
            return decoded, response_headers
        except HTTPStatusError as exc:
            last_error = exc
            if exc.status in (401, 403):
                raise RuntimeError(f"Access denied (HTTP {exc.status}) for {url}. Check token/permissions.") from exc
            if exc.status == 404:
                raise
            if exc.status == 429 or exc.status in (500, 502, 503, 504):
                delay = min(60, 2 ** attempt)
                if exc.status == 429:
                    delay = max(delay, 30)
                print(f"  API returned HTTP {exc.status}; retry in {delay}s", flush=True)
                time.sleep(delay)
                continue
            raise
        except (json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt == 4:
                break
            time.sleep(min(10, 2 ** attempt))
    raise RuntimeError(f"Request failed after retries: {url}: {last_error}")


TYPOGRAPHIC_TRANSLATION = str.maketrans({
    "\u2010": "-",  # hyphen
    "\u2011": "-",  # non-breaking hyphen
    "\u2012": "-",  # figure dash
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2212": "-",  # minus sign
    "\u2018": "'",  # left single quotation mark
    "\u2019": "'",  # right single quotation mark
    "\u201b": "'",  # single high-reversed-9 quotation mark
    "\u02bc": "'",  # modifier letter apostrophe
    "\uff07": "'",  # fullwidth apostrophe
})


def norm_text(value: Any) -> str:
    """Normalize Unicode typography, case, and whitespace without removing words."""
    if value is None:
        return ""
    value = unicodedata.normalize("NFC", str(value)).translate(TYPOGRAPHIC_TRANSLATION)
    return " ".join(value.split()).casefold()


def norm_title_punctuation(value: Any) -> str:
    """Ignore title punctuation only; retain letters, digits, accents, apostrophes and order."""
    if value is None:
        return ""
    value = unicodedata.normalize("NFC", str(value)).translate(TYPOGRAPHIC_TRANSLATION).casefold()
    chunks: list[str] = []
    for char in value:
        category = unicodedata.category(char)
        if category[0] in {"L", "N", "M"} or char == "'":
            chunks.append(char)
        else:
            chunks.append(" ")
    return " ".join("".join(chunks).split())


def strip_records_suffix(value: Any) -> str:
    """Return a label comparison key with only a terminal 'Records' suffix removed."""
    value = norm_text(value)
    return re.sub(r"\s+records\s*$", "", value).strip()


def compare_titles(left: Any, right: Any) -> str:
    """Return exact, safe-normalized, punctuation-equivalent, or mismatch."""
    if not str(left or "").strip() or not str(right or "").strip():
        return "MISSING"
    if norm_text(left) == norm_text(right):
        if unicodedata.normalize("NFC", str(left)).strip() == unicodedata.normalize("NFC", str(right)).strip():
            return "EXACT"
        return "SAFE_NORMALIZATION"
    a = norm_title_punctuation(left)
    b = norm_title_punctuation(right)
    if a and a == b:
        return "PUNCTUATION_EQUIVALENT"
    return "MISMATCH"


def compare_catalog_numbers(left: Any, right: Any) -> str:
    """Normalize typography and whitespace while preserving catalog punctuation."""
    if not str(left or "").strip() or not str(right or "").strip():
        return "MISSING"
    a_text = norm_text(left)
    b_text = norm_text(right)
    if a_text == b_text:
        return "EXACT" if str(left).strip() == str(right).strip() else "SAFE_NORMALIZATION"
    a_compact = re.sub(r"\s+", "", a_text)
    b_compact = re.sub(r"\s+", "", b_text)
    if a_compact and a_compact == b_compact:
        return "WHITESPACE_NORMALIZATION"
    return "MISMATCH"


def norm_barcode(value: Any) -> str:
    """Remove common separators while preserving all digits and leading zeros."""
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    compact = re.sub(r"[\s-]+", "", raw)
    return compact if compact.isdigit() else ""


def barcodes_equivalent(left: Any, right: Any) -> bool:
    """Compare barcodes exactly, allowing the standard UPC-A/EAN-13 form pair.

    A 12-digit UPC-A may be represented as a 13-digit EAN-13 by prepending one
    zero. This equivalence is limited to those exact 12/13 digit lengths; we do
    not strip arbitrary leading zeros or otherwise rewrite identifiers.
    """
    a = norm_barcode(left)
    b = norm_barcode(right)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) == 13 and a.startswith("0") and len(b) == 12 and a[1:] == b:
        return True
    if len(b) == 13 and b.startswith("0") and len(a) == 12 and b[1:] == a:
        return True
    return False


def mb_artist_credit(release: dict[str, Any]) -> str:
    chunks: list[str] = []
    credits = release.get("artist-credit", [])
    if not isinstance(credits, list):
        return ""
    for item in credits:
        if isinstance(item, str):
            chunks.append(item)
            continue
        if not isinstance(item, dict):
            continue
        artist = item.get("artist") or {}
        name = item.get("name") or artist.get("name") or ""
        chunks.append(str(name) + str(item.get("joinphrase") or ""))
    return "".join(chunks)


def discogs_artist_credit(release: dict[str, Any], *, prefer_anv: bool = True) -> str:
    """Build the release-level Discogs artist credit.

    Discogs `name` is the primary artist name (PAN), while `anv`, when present,
    records the name variation used as the release credit. For strict comparison,
    compare the credited ANV rather than silently substituting the PAN.
    """
    artists = release.get("artists", [])
    if not isinstance(artists, list):
        return ""
    chunks: list[str] = []
    for item in artists:
        if not isinstance(item, dict):
            continue
        primary_name = str(item.get("name") or "")
        anv = str(item.get("anv") or "").strip()
        credited_name = anv if prefer_anv and anv else primary_name
        chunks.append(credited_name + str(item.get("join") or ""))
    return "".join(chunks)


def discogs_artist_anv_report(release: dict[str, Any]) -> str:
    """Return an auditable PAN -> ANV list for the Discogs API report."""
    artists = release.get("artists", [])
    if not isinstance(artists, list):
        return ""
    variations: list[str] = []
    for item in artists:
        if not isinstance(item, dict):
            continue
        primary_name = str(item.get("name") or "").strip()
        anv = str(item.get("anv") or "").strip()
        if anv:
            variations.append(f"{primary_name} => {anv}")
    return ";".join(variations)


def mb_label_info(release: dict[str, Any]) -> list[tuple[str, str]]:
    """Return MusicBrainz (label name, catalog number) pairs from label-info."""
    result: list[tuple[str, str]] = []
    items = release.get("label-info", [])
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        label = item.get("label") or {}
        if not isinstance(label, dict):
            label = {}
        name = str(label.get("name") or "").strip()
        catalog_number = str(item.get("catalog-number") or "").strip()
        # MusicBrainz uses [none] to explicitly mean that no catalog number exists.
        if catalog_number.casefold() == "[none]":
            catalog_number = ""
        pair = (name, catalog_number)
        if pair != ("", "") and pair not in result:
            result.append(pair)
    return result


def discogs_label_info(release: dict[str, Any]) -> list[tuple[str, str]]:
    """Return Discogs (label name, catalog number) pairs."""
    result: list[tuple[str, str]] = []
    items = release.get("labels", [])
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        catalog_number = str(item.get("catno") or "").strip()
        pair = (name, catalog_number)
        if pair != ("", "") and pair not in result:
            result.append(pair)
    return result


def norm_catalog_number(value: Any) -> str:
    """Normalize case, Unicode dash glyphs, and whitespace; preserve punctuation."""
    return re.sub(r"\s+", "", norm_text(value))


def discogs_barcodes(release: dict[str, Any]) -> list[str]:
    result: list[str] = []
    identifiers = release.get("identifiers", [])
    if not isinstance(identifiers, list):
        return result
    for item in identifiers:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").casefold()
        if "barcode" in kind:
            val = norm_barcode(item.get("value"))
            if val and val not in result:
                result.append(val)
    return result


def discogs_release_id_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if (parsed.hostname or "").casefold() not in {"discogs.com", "www.discogs.com"}:
        return ""
    match = re.match(r"^/release/(\d+)(?:[-/]|$)", parsed.path, re.IGNORECASE)
    return match.group(1) if match else ""


def mb_discogs_links(release: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    relations = release.get("relations", [])
    if not isinstance(relations, list):
        return found
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        url_obj = relation.get("url") or {}
        resource = str(url_obj.get("resource") or "")
        parsed = urllib.parse.urlparse(resource)
        host = (parsed.hostname or "").casefold()
        if host in {"discogs.com", "www.discogs.com"}:
            path = parsed.path or ""
            if path.startswith("/release/") or path.startswith("/master/"):
                found.append((resource, relation.get("type") or ""))
    return found


def fetch_blacklist_mbids() -> set[str]:
    """Fetch official blacklist page every run; fail closed if its release IDs cannot be read."""
    html_bytes, _ = http_get(BLACKLIST_URL, {"Accept": "text/html"}, timeout=45)
    parser = AnchorCollector()
    parser.feed(html_bytes.decode("utf-8", "replace"))
    ids: set[str] = set()
    for href in parser.hrefs:
        parsed = urllib.parse.urlparse(urllib.parse.urljoin("https://musicbrainz.org", href))
        match = re.match(r"^/release/([^/?#]+)", parsed.path, re.IGNORECASE)
        if match and MBID_RE.fullmatch(match.group(1)):
            try:
                ids.add(str(uuid.UUID(match.group(1))))
            except ValueError:
                pass
    if not ids:
        raise RuntimeError(
            "Could not extract MusicBrainz release MBIDs from the official Discogs-link blacklist. "
            "Failing closed: no candidates will be approved. Check the blacklist page or parsing rules."
        )
    return ids


class JsonCache:
    def __init__(self, folder: Path, max_age: int = CACHE_MAX_AGE_SECONDS) -> None:
        self.folder = folder
        self.max_age = max_age
        folder.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.folder / f"{key}.json"
        if not path.exists():
            return None
        try:
            wrapped = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = float(wrapped["fetched_at"])
            if time.time() - fetched_at > self.max_age:
                path.unlink(missing_ok=True)
                return None
            data = wrapped.get("data")
            return data if isinstance(data, dict) else None
        except (OSError, ValueError, KeyError, TypeError):
            path.unlink(missing_ok=True)
            return None

    def put(self, key: str, data: dict[str, Any]) -> None:
        path = self.folder / f"{key}.json"
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps({"fetched_at": time.time(), "data": data}, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(path)


def load_input(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)
    required = {"mbid", "barcode", "discogs_url", "discogs_release_id"}
    if not rows or not required.issubset(set(rows[0].keys())):
        raise RuntimeError(f"Input must be a Stage 1 TSV with columns: {', '.join(sorted(required))}")
    return rows


def fetch_mb_release(mbid: str, base_url: str, cache: JsonCache) -> dict[str, Any]:
    cached = cache.get(mbid)
    if cached is not None:
        return cached
    query = urllib.parse.urlencode({"inc": "artist-credits+url-rels+labels", "fmt": "json"})
    url = f"{base_url.rstrip('/')}/ws/2/release/{urllib.parse.quote(mbid)}?{query}"
    data, _ = http_get_json(url, {"Accept": "application/json"}, timeout=45)
    release = data.get("release")
    if not isinstance(release, dict):
        # A few server versions return the release fields at the root.
        release = data if data.get("id") else None
    if not isinstance(release, dict):
        raise RuntimeError(f"Unexpected MusicBrainz response structure for MBID {mbid}")
    cache.put(mbid, release)
    return release


def fetch_discogs_release(release_id: str, token: str, user_agent: str, delay: float,
                          cache: JsonCache, last_request: list[float]) -> dict[str, Any]:
    cached = cache.get(release_id)
    if cached is not None:
        return cached
    elapsed = time.monotonic() - last_request[0]
    if elapsed < delay:
        time.sleep(delay - elapsed)
    url = f"https://api.discogs.com/releases/{urllib.parse.quote(release_id)}"
    headers = {"Authorization": f"Discogs token={token}", "User-Agent": user_agent, "Accept": "application/json"}
    last_request[0] = time.monotonic()
    data, _ = http_get_json(url, headers, timeout=45)
    returned_id = str(data.get("id") or "")
    if not returned_id:
        raise RuntimeError(f"Discogs API response did not contain an ID for requested release {release_id}")
    if returned_id != release_id:
        raise RuntimeError(f"Discogs API ID mismatch: requested {release_id}, returned {returned_id}")
    cache.put(release_id, data)
    return data


def evaluate(row: dict[str, str], mb: dict[str, Any], dg: dict[str, Any], blacklist: set[str]) -> dict[str, str]:
    """Evaluate every available field, then assign a single conservative status."""
    mbid = str(row.get("mbid") or "").strip().lower()
    wanted_id = str(row.get("discogs_release_id") or "").strip()
    input_barcode = norm_barcode(row.get("barcode"))
    mb_title = str(mb.get("title") or "")
    mb_artist = mb_artist_credit(mb)
    mb_barcode = norm_barcode(mb.get("barcode"))
    dg_title = str(dg.get("title") or "")
    dg_artist = discogs_artist_credit(dg, prefer_anv=True)
    dg_artist_pan = discogs_artist_credit(dg, prefer_anv=False)
    dg_artist_anv = discogs_artist_anv_report(dg)
    dg_barcodes = discogs_barcodes(dg)
    mb_pairs = mb_label_info(mb)
    dg_pairs = discogs_label_info(dg)
    existing = mb_discogs_links(mb)

    mb_labels = list(dict.fromkeys(name for name, _catno in mb_pairs if name))
    mb_catalog_numbers = list(dict.fromkeys(catno for _name, catno in mb_pairs if catno))
    dg_labels = list(dict.fromkeys(name for name, _catno in dg_pairs if name))
    dg_catalog_numbers = list(dict.fromkeys(catno for _name, catno in dg_pairs if catno))

    result = {key: str(row.get(key) or "") for key in row.keys()}
    result.update({
        "status": "",
        "reason": "",
        "all_findings": "",
        "normalization_actions": "",
        "title_match_type": "NOT_CHECKED",
        "artist_match_type": "NOT_CHECKED",
        "mb_title": mb_title,
        "mb_artist": mb_artist,
        "mb_barcode": mb_barcode,
        "discogs_api_title": dg_title,
        "discogs_api_artist": dg_artist,
        "discogs_api_artist_primary_names": dg_artist_pan,
        "discogs_api_artist_anv": dg_artist_anv,
        "discogs_api_barcodes": ";".join(dg_barcodes),
        "mb_labels": ";".join(mb_labels),
        "mb_catalog_numbers": ";".join(mb_catalog_numbers),
        "discogs_api_labels": ";".join(dg_labels),
        "discogs_api_catalog_numbers": ";".join(dg_catalog_numbers),
        "input_mb_barcode_check": "NOT_CHECKED",
        "discogs_barcode_check": "NOT_CHECKED",
        "label_check": "NOT_PRESENT_IN_MUSICBRAINZ" if not mb_labels else "NOT_CHECKED",
        "catalog_number_check": "NOT_PRESENT_IN_MUSICBRAINZ" if not mb_catalog_numbers else "NOT_CHECKED",
        "label_catalog_pair_check": "NOT_PRESENT_AS_PAIR_IN_MUSICBRAINZ" if not any(name and catno for name, catno in mb_pairs) else "NOT_CHECKED",
        "existing_discogs_links": ";".join(url for url, _kind in existing),
        "unmatched_mb_catalog_numbers": "",
        "unmatched_mb_labels": "",
        "unmatched_mb_label_catalog_pairs": "",
    })

    findings: list[tuple[str, str]] = []
    actions: list[str] = []
    reject_findings: list[tuple[str, str]] = []
    error_findings: list[tuple[str, str]] = []

    # Identity and input checks.
    if str(mb.get("id") or "").lower() != mbid:
        error_findings.append(("ERROR_MBID_MISMATCH", "LOCAL_MUSICBRAINZ_RESPONSE_MBID_DIFFERS_FROM_INPUT"))
    if not input_barcode:
        reject_findings.append(("REJECT_INVALID_INPUT_BARCODE", "INPUT_BARCODE_IS_NOT_A_VALID_DIGIT_SEQUENCE"))
        result["input_mb_barcode_check"] = "INVALID_INPUT_BARCODE"
    elif not mb_barcode:
        findings.append(("MANUAL_MB_BARCODE_MISSING", "MUSICBRAINZ_RELEASE_HAS_NO_BARCODE"))
        result["input_mb_barcode_check"] = "MUSICBRAINZ_BARCODE_MISSING"
    elif barcodes_equivalent(input_barcode, mb_barcode):
        result["input_mb_barcode_check"] = "MATCH"
        if input_barcode != mb_barcode:
            actions.append("UPC_A_EAN_13_EQUIVALENCE_MB_BARCODE")
    else:
        reject_findings.append(("REJECT_MB_BARCODE_MISMATCH", "INPUT_BARCODE_DIFFERS_FROM_CURRENT_MUSICBRAINZ_BARCODE"))
        result["input_mb_barcode_check"] = "MISMATCH"

    # Title: exact/typographic normalization first, punctuation-only fallback second.
    title_mode = compare_titles(mb_title, dg_title)
    result["title_match_type"] = title_mode
    if title_mode == "MISSING":
        findings.append(("MANUAL_TITLE_MISSING", "TITLE_MISSING_IN_ONE_OR_BOTH_DATABASES"))
    elif title_mode == "MISMATCH":
        findings.append(("MANUAL_TITLE_MISMATCH", "TITLE_WORDS_OR_ORDER_DIFFER_AFTER_SAFE_NORMALIZATION"))
    elif title_mode == "PUNCTUATION_EQUIVALENT":
        actions.append("TITLE_PUNCTUATION_ONLY_NORMALIZATION")
    elif title_mode == "SAFE_NORMALIZATION":
        actions.append("TITLE_TYPOGRAPHY_CASE_OR_WHITESPACE_NORMALIZATION")

    # Artist credit: only typography/case/whitespace normalization is allowed.
    if not mb_artist.strip() or not dg_artist.strip():
        findings.append(("MANUAL_ARTIST_CREDIT_MISSING", "ARTIST_CREDIT_MISSING_IN_ONE_OR_BOTH_DATABASES"))
    elif norm_text(mb_artist) != norm_text(dg_artist):
        findings.append(("MANUAL_ARTIST_MISMATCH", "ARTIST_CREDITS_DIFFER_AFTER_TYPOGRAPHY_NORMALIZATION"))
        result["artist_match_type"] = "MISMATCH"
    else:
        if mb_artist == dg_artist:
            result["artist_match_type"] = "EXACT"
        else:
            result["artist_match_type"] = "SAFE_NORMALIZATION"
            actions.append("ARTIST_TYPOGRAPHY_CASE_OR_WHITESPACE_NORMALIZATION")

    # Discogs barcode: compare every available barcode, but do not stop other checks.
    if not dg_barcodes:
        findings.append(("MANUAL_DISCOGS_BARCODE_MISSING", "DISCOGS_API_HAS_NO_BARCODE_IDENTIFIER"))
        result["discogs_barcode_check"] = "MISSING"
    else:
        matching_barcodes = [value for value in dg_barcodes if input_barcode and barcodes_equivalent(input_barcode, value)]
        if not matching_barcodes:
            reject_findings.append(("REJECT_DISCOGS_BARCODE_MISMATCH", "INPUT_BARCODE_NOT_FOUND_IN_DISCOGS_IDENTIFIERS"))
            result["discogs_barcode_check"] = "MISMATCH"
        else:
            exact = any(norm_barcode(input_barcode) == norm_barcode(value) for value in matching_barcodes)
            result["discogs_barcode_check"] = "MATCH_EXACT" if exact else "MATCH_UPC_A_EAN_13_EQUIVALENT"
            if not exact:
                actions.append("UPC_A_EAN_13_EQUIVALENCE_DISCOGS_BARCODE")

    # Helpers for normalized Label/Catalog Number comparisons.
    def labels_equal(mb_label: str, dg_label: str) -> bool:
        return bool(norm_text(mb_label)) and norm_text(mb_label) == norm_text(dg_label)

    def catalog_match_type(mb_cat: str, dg_cat: str) -> str:
        return compare_catalog_numbers(mb_cat, dg_cat)

    def label_suffix_alias(mb_label: str, dg_label: str) -> bool:
        mb_base = strip_records_suffix(mb_label)
        dg_base = strip_records_suffix(dg_label)
        return bool(mb_base) and mb_base == dg_base and norm_text(mb_label) != norm_text(dg_label)

    # Label names: exact typography match is preferred. A terminal "Records" variant
    # is accepted only if the catalog number matches on the same Label entry.
    unmatched_labels: list[str] = []
    suffix_labels: list[str] = []
    if mb_labels:
        if not dg_labels:
            result["label_check"] = "DISCOGS_LABELS_MISSING"
            findings.append(("MANUAL_DISCOGS_LABEL_MISSING", "MUSICBRAINZ_HAS_LABEL_BUT_DISCOGS_HAS_NO_LABEL_NAMES"))
        else:
            for mb_label in mb_labels:
                if any(labels_equal(mb_label, dg_label) for dg_label in dg_labels):
                    continue
                suffix_found = False
                for mb_label_pair, mb_cat in mb_pairs:
                    if mb_label_pair != mb_label or not mb_cat:
                        continue
                    for dg_label, dg_cat in dg_pairs:
                        if label_suffix_alias(mb_label, dg_label) and catalog_match_type(mb_cat, dg_cat) != "MISMATCH" and catalog_match_type(mb_cat, dg_cat) != "MISSING":
                            suffix_found = True
                            suffix_labels.append(mb_label)
                            if catalog_match_type(mb_cat, dg_cat) == "WHITESPACE_NORMALIZATION":
                                actions.append("CATALOG_NUMBER_WHITESPACE_NORMALIZATION")
                            break
                    if suffix_found:
                        break
                if not suffix_found:
                    unmatched_labels.append(mb_label)
            if unmatched_labels:
                result["label_check"] = "MISMATCH"
                result["unmatched_mb_labels"] = ";".join(unmatched_labels)
                findings.append(("MANUAL_LABEL_MISMATCH", "ONE_OR_MORE_MUSICBRAINZ_LABEL_NAMES_NOT_CONFIRMED_IN_DISCOGS"))
            elif suffix_labels:
                result["label_check"] = "MATCH_RECORDS_SUFFIX_CONFIRMED_BY_CATALOG_NUMBER"
                actions.append("LABEL_RECORDS_SUFFIX_CONFIRMED_BY_MATCHING_CATALOG_NUMBER")
            else:
                result["label_check"] = "MATCH"
    else:
        result["label_check"] = "NOT_PRESENT_IN_MUSICBRAINZ"

    # Catalog numbers: normalize case, whitespace, and dash glyphs only.
    unmatched_catnos: list[str] = []
    cat_normalized = False
    if mb_catalog_numbers:
        if not dg_catalog_numbers:
            result["catalog_number_check"] = "DISCOGS_CATALOG_NUMBERS_MISSING"
            findings.append(("MANUAL_DISCOGS_CATALOG_NUMBER_MISSING", "MUSICBRAINZ_HAS_CATALOG_NUMBER_BUT_DISCOGS_HAS_NONE"))
        else:
            for mb_cat in mb_catalog_numbers:
                matches = [dg_cat for dg_cat in dg_catalog_numbers if catalog_match_type(mb_cat, dg_cat) not in {"MISMATCH", "MISSING"}]
                if not matches:
                    unmatched_catnos.append(mb_cat)
                elif any(catalog_match_type(mb_cat, dg_cat) != "EXACT" for dg_cat in matches):
                    cat_normalized = True
            if unmatched_catnos:
                result["catalog_number_check"] = "MISMATCH"
                result["unmatched_mb_catalog_numbers"] = ";".join(unmatched_catnos)
                findings.append(("MANUAL_CATALOG_NUMBER_MISMATCH", "ONE_OR_MORE_MUSICBRAINZ_CATALOG_NUMBERS_NOT_FOUND_IN_DISCOGS"))
            elif cat_normalized:
                result["catalog_number_check"] = "MATCH_AFTER_SAFE_NORMALIZATION"
                actions.append("CATALOG_NUMBER_WHITESPACE_OR_DASH_GLYPH_NORMALIZATION")
            else:
                result["catalog_number_check"] = "MATCH"
    else:
        result["catalog_number_check"] = "NOT_PRESENT_IN_MUSICBRAINZ"

    # Pair-level check: a MusicBrainz Label and catalog number must occur together
    # on a single Discogs labels entry; the Records suffix exception also requires
    # a matching catalog number on that same entry.
    mb_complete_pairs = [(label, catno) for label, catno in mb_pairs if label and catno]
    pair_misses: list[str] = []
    pair_normalized = False
    pair_suffix_used = False
    if mb_complete_pairs:
        for mb_label, mb_cat in mb_complete_pairs:
            found = False
            for dg_label, dg_cat in dg_pairs:
                cat_mode = catalog_match_type(mb_cat, dg_cat)
                if cat_mode in {"MISMATCH", "MISSING"}:
                    continue
                if labels_equal(mb_label, dg_label):
                    found = True
                    if cat_mode != "EXACT":
                        pair_normalized = True
                    break
                if label_suffix_alias(mb_label, dg_label):
                    found = True
                    pair_suffix_used = True
                    if cat_mode != "EXACT":
                        pair_normalized = True
                    break
            if not found:
                pair_misses.append(f"{mb_label} | {mb_cat}")
        if pair_misses:
            result["label_catalog_pair_check"] = "MISMATCH"
            result["unmatched_mb_label_catalog_pairs"] = ";".join(pair_misses)
            findings.append(("MANUAL_LABEL_CATALOG_PAIR_MISMATCH", "MUSICBRAINZ_LABEL_AND_CATALOG_NUMBER_PAIR_NOT_FOUND_AS_A_PAIR_IN_DISCOGS"))
        else:
            if pair_suffix_used:
                result["label_catalog_pair_check"] = "MATCH_RECORDS_SUFFIX_CONFIRMED_BY_CATALOG_NUMBER"
            elif pair_normalized:
                result["label_catalog_pair_check"] = "MATCH_AFTER_SAFE_NORMALIZATION"
            else:
                result["label_catalog_pair_check"] = "MATCH"
    else:
        result["label_catalog_pair_check"] = "NOT_PRESENT_AS_PAIR_IN_MUSICBRAINZ"

    # Record existing links after gathering all other available comparisons.
    existing_same = any(discogs_release_id_from_url(url) == wanted_id for url, _kind in existing)
    has_other_discogs_link = bool(existing) and not existing_same

    result["all_findings"] = ";".join(f"{status}:{reason}" for status, reason in [*error_findings, *reject_findings, *findings])
    result["normalization_actions"] = ";".join(dict.fromkeys(actions))

    # Final status is selected only after every available metadata field was checked.
    if error_findings:
        status, reason = error_findings[0]
    elif mbid in blacklist:
        status, reason = "BLOCKED_BLACKLIST", "MBID_APPEARS_IN_OFFICIAL_DISCOGS_LINK_BLACKLIST"
    elif existing_same:
        status, reason = "ALREADY_LINKED", "THIS_DISCOGS_RELEASE_ID_IS_ALREADY_LINKED"
    elif has_other_discogs_link:
        status, reason = "MANUAL_EXISTING_DISCOGS_LINK", "A_DIFFERENT_DISCOGS_RELEASE_OR_MASTER_LINK_ALREADY_EXISTS"
    elif reject_findings:
        status, reason = reject_findings[0]
    elif findings:
        # The first finding is the primary category; all findings remain in all_findings.
        status, reason = findings[0]
    else:
        status = "AUTO_CANDIDATE"
        passed = ["ARTIST_MATCH", "TITLE_MATCH", "BARCODE_MATCH", "NO_EXISTING_DISCOGS_LINK", "NOT_BLACKLISTED"]
        if result["label_check"].startswith("MATCH"):
            passed.append("LABEL_CHECK_" + result["label_check"])
        if result["catalog_number_check"].startswith("MATCH"):
            passed.append("CATALOG_NUMBER_CHECK_" + result["catalog_number_check"])
        if result["label_catalog_pair_check"].startswith("MATCH"):
            passed.append("LABEL_CATALOG_PAIR_CHECK_" + result["label_catalog_pair_check"])
        reason = ";".join(passed)
    result["status"] = status
    result["reason"] = reason
    return result


def write_report(path: Path, rows: list[dict[str, str]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only v5 verification with conservative normalization.")
    parser.add_argument("input", nargs="?", default="discogs_bot_preflight_output/stage1_unique_release_candidates.tsv")
    parser.add_argument("--mb-base-url", default=os.environ.get("MUSICBRAINZ_BASE_URL", DEFAULT_MB_BASE_URL))
    parser.add_argument("--discogs-delay", type=float, default=1.1, help="Minimum seconds between Discogs API requests (default: 1.1)")
    parser.add_argument("--limit", type=int, default=0, help="Verify only first N rows (0 = all); use for a small pilot")
    parser.add_argument("--out-dir", default="discogs_bot_verify_output")
    parser.add_argument("--cache-dir", default="discogs_bot_verify_output/cache", help="Cache root; may reuse fresh v4 Discogs/MB cache data")
    parser.add_argument("--user-agent", default=os.environ.get("DISCOGS_USER_AGENT", DEFAULT_DISCOGS_USER_AGENT))
    args = parser.parse_args()

    token = os.environ.get("DISCOGS_TOKEN", "").strip()
    if not token:
        print("ERROR: DISCOGS_TOKEN environment variable is not set. No requests or edits were made.", file=sys.stderr)
        print("PowerShell example: $env:DISCOGS_TOKEN = \"YOUR_TOKEN\"", file=sys.stderr)
        return 2
    source = Path(args.input).expanduser().resolve()
    if not source.is_file():
        print(f"ERROR: candidate input not found: {source}", file=sys.stderr)
        return 2
    if args.discogs_delay < 1.0:
        print("ERROR: --discogs-delay must be at least 1.0 second to avoid aggressive Discogs API access.", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir).expanduser().resolve()
    cache_root = Path(args.cache_dir).expanduser().resolve()
    cache_mb = JsonCache(cache_root / "musicbrainz_labels_catno_v4")
    cache_dg = JsonCache(cache_root / "discogs")
    try:
        input_rows = load_input(source)
        if args.limit > 0:
            input_rows = input_rows[:args.limit]
        print(f"Candidates to verify: {len(input_rows)}")
        print(f"Local MusicBrainz mirror: {args.mb_base_url}")
        print("Fetching official MusicBrainz Discogs-link blacklist...", flush=True)
        blacklist = fetch_blacklist_mbids()
        print(f"Blacklist release MBIDs loaded: {len(blacklist)}", flush=True)
    except Exception as exc:
        print(f"ERROR: startup check failed: {exc}", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, str]] = []
    counts: Counter[str] = Counter()
    last_dg_request = [0.0]
    mb_host = (urllib.parse.urlparse(args.mb_base_url).hostname or "").casefold()
    mb_delay = 1.1 if mb_host not in {"localhost", "127.0.0.1", "::1"} else 0.05

    fatal_error = False
    for index, row in enumerate(input_rows, start=1):
        mbid = str(row.get("mbid") or "").strip()
        release_id = str(row.get("discogs_release_id") or "").strip()
        result: dict[str, str]
        try:
            if not mbid or not release_id:
                raise RuntimeError("Input row is missing MBID or Discogs release ID")
            try:
                mb = fetch_mb_release(mbid, args.mb_base_url, cache_mb)
            except HTTPStatusError as exc:
                if exc.status == 404:
                    result = {**row, "status": "REJECT_MB_RELEASE_NOT_FOUND", "reason": "LOCAL_MUSICBRAINZ_API_RETURNED_404"}
                else:
                    raise
            else:
                try:
                    dg = fetch_discogs_release(release_id, token, args.user_agent, args.discogs_delay, cache_dg, last_dg_request)
                except HTTPStatusError as exc:
                    if exc.status == 404:
                        result = {**row, "status": "REJECT_DISCOGS_NOT_FOUND", "reason": "DISCOGS_API_RETURNED_404", "mb_title": str(mb.get("title") or ""), "mb_artist": mb_artist_credit(mb), "mb_barcode": norm_barcode(mb.get("barcode")), "discogs_api_title": "", "discogs_api_artist": "", "discogs_api_barcodes": "", "existing_discogs_links": ";".join(u for u, _ in mb_discogs_links(mb))}
                    else:
                        raise
                else:
                    result = evaluate(row, mb, dg, blacklist)
        except Exception as exc:
            message = str(exc).replace("\t", " ").replace("\n", " ")[:500]
            # A bad token should stop the run immediately, not repeatedly hit the API.
            if message.startswith("Access denied (HTTP 401)") or message.startswith("Access denied (HTTP 403)"):
                print(f"FATAL: {message}", file=sys.stderr)
                fatal_error = True
                break
            # Fail closed: the row is never deemed safe after an exception.
            result = {**row, "status": "ERROR_FETCH_OR_PARSE", "reason": message}
        results.append(result)
        counts[result.get("status", "UNKNOWN")] += 1
        if index % 100 == 0 or index == len(input_rows):
            print(f"[{index}/{len(input_rows)}] AUTO_CANDIDATE={counts['AUTO_CANDIDATE']} "
                  f"MANUAL={sum(v for k,v in counts.items() if k.startswith('MANUAL_'))} "
                  f"REJECT={sum(v for k,v in counts.items() if k.startswith('REJECT_'))} "
                  f"BLOCKED={counts['BLOCKED_BLACKLIST']} ALREADY_LINKED={counts['ALREADY_LINKED']} "
                  f"ERROR={sum(v for k,v in counts.items() if k.startswith('ERROR_'))}", flush=True)
        if fatal_error:
            break

    report_path = out_dir / "stage2_verification_report.tsv"
    write_report(report_path, results)
    with (out_dir / "stage2_summary.txt").open("w", encoding="utf-8-sig") as f:
        f.write("DISCOGS LINK BOT — STAGE 2 V5 VERIFICATION (READ-ONLY)\n")
        f.write("=" * 53 + "\n")
        f.write(f"Input: {source}\nRows processed: {len(results)}\nBlacklist MBIDs: {len(blacklist)}\n")
        for status, count in sorted(counts.items()):
            f.write(f"{status}: {count}\n")
        f.write("\nAUTO_CANDIDATE means eligible for a separate review/approval stage only. v5 uses conservative normalization and evaluates all available fields before status assignment.\n")
        f.write("No MusicBrainz edits were submitted by this program.\n")
        if fatal_error:
            f.write("FATAL: run stopped because Discogs authorization was denied.\n")
    print(f"\nReport saved: {report_path}")
    print(f"Summary saved: {out_dir / 'stage2_summary.txt'}")
    print("No edits were submitted to MusicBrainz.")
    return 2 if fatal_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
