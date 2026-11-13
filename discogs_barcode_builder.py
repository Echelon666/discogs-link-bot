#!/usr/bin/env python3
"""Build a Stage 2 v6 candidate TSV from barcode searches and master versions.

Workflow:
  1. Read unique MBIDs from -> ALBUM rows inside POSSIBILITIES sections.
  2. Fetch the release from the user's local MusicBrainz Web Service.
  3. Skip already-linked releases and releases without a usable barcode.
  4. Search Discogs by barcode (type=release), fetching every search page.
  5. One search result: retain that release as the sole candidate.
  6. Multiple search results: retain all search results and expand every distinct
     Discogs master through /masters/{master_id}/versions (all pages), which is
     the API equivalent of opening "View all versions".
  7. Write one TSV row per unique Discogs Release ID for Stage 2 v6.

The builder is read-only. It does not submit or modify anything on MusicBrainz.
It uses the local MusicBrainz Web Service, not PostgreSQL or psql.

Requirements: Python 3.10+ (standard library only), a Discogs API token.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Callable

DEFAULT_MB_BASE_URL = "http://localhost:5000"
DEFAULT_USER_AGENT = "DiscogsLinkBot/0.3 (read-only barcode candidate builder)"
MIN_DISCOGS_DELAY = 1.1
CACHE_MAX_AGE_SECONDS = 6 * 60 * 60
PER_PAGE = 100

MBID_RE = re.compile(
    r"\bMBID\s*:\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b",
    re.IGNORECASE,
)
TRACK_HEADER_RE = re.compile(r"^\s*\[\d+\]\s+TRACK\s*:", re.IGNORECASE)
POSSIBILITIES_RE = re.compile(r"^\s*POSSIBILITIES\s*:\s*$", re.IGNORECASE)
ARROW_ALBUM_RE = re.compile(r"^\s*->\s*ALBUM\s*:", re.IGNORECASE)

CANDIDATE_FIELDS = [
    "mbid",
    "barcode",
    "discogs_url",
    "discogs_release_id",
    "discogs_display_name",
    "search_result_index",
    "search_total_results",
    "search_pages",
    "candidate_source",
    "master_id",
    "version_year",
    "version_country",
    "version_format",
]


class HTTPStatusError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = "", headers: Any = None) -> None:
        super().__init__(f"HTTP {status} for {url}: {body[:240]}")
        self.status = status
        self.url = url
        self.body = body
        self.headers = headers or {}


def http_get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 45,
    retries: int = 5,
) -> dict[str, Any]:
    request_headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json",
        **(headers or {}),
    }
    last_error: Exception | None = None

    for attempt in range(retries):
        request = urllib.request.Request(url, headers=request_headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
            data = json.loads(payload.decode("utf-8-sig"))
            if not isinstance(data, dict):
                raise RuntimeError(f"JSON response root is not an object: {url}")
            return data
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            error = HTTPStatusError(exc.code, url, body, exc.headers)
            if exc.code in (401, 403, 404):
                raise error
            if exc.code == 429 or exc.code in (500, 502, 503, 504):
                retry_after = 0.0
                try:
                    retry_after = float(exc.headers.get("Retry-After", "0"))
                except (TypeError, ValueError):
                    pass
                delay = max(retry_after, 30.0 if exc.code == 429 else float(2 ** attempt))
                print(f"  Discogs HTTP {exc.code}; retry in {delay:.0f}s", flush=True)
                time.sleep(delay)
                last_error = error
                continue
            raise error
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt + 1 >= retries:
                break
            delay = min(15, 2 ** attempt)
            print(f"  Network/response error; retry in {delay}s: {exc}", flush=True)
            time.sleep(delay)

    raise RuntimeError(f"Request failed after {retries} attempts: {url}: {last_error}")


class JsonCache:
    def __init__(self, folder: Path, max_age: int = CACHE_MAX_AGE_SECONDS) -> None:
        self.folder = folder
        self.max_age = max_age
        self.folder.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe_key = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.folder / f"{safe_key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            wrapped = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = float(wrapped["fetched_at"])
            if time.time() - fetched_at > self.max_age:
                path.unlink(missing_ok=True)
                return None
            value = wrapped.get("data")
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            return None

    def put(self, key: str, data: dict[str, Any]) -> None:
        path = self._path(key)
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps({"fetched_at": time.time(), "data": data}, ensure_ascii=False),
            encoding="utf-8",
        )
        temp_path.replace(path)


def resolve_path(value: str | Path, script_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = script_dir / path
    return path.resolve()


def extract_unique_mbids(report_path: Path) -> list[str]:
    """Extract unique MBIDs only from -> ALBUM rows inside POSSIBILITIES sections."""
    found: list[str] = []
    seen: set[str] = set()
    in_possibilities = False

    with report_path.open("r", encoding="utf-8-sig", errors="replace") as stream:
        for line in stream:
            if POSSIBILITIES_RE.match(line):
                in_possibilities = True
                continue
            if not in_possibilities:
                continue
            if TRACK_HEADER_RE.match(line) or line.lstrip().startswith("---"):
                in_possibilities = False
                continue
            if not ARROW_ALBUM_RE.match(line):
                continue
            match = MBID_RE.search(line)
            if not match:
                continue
            mbid = match.group(1).lower()
            if mbid not in seen:
                seen.add(mbid)
                found.append(mbid)
    return found


def normalize_barcode(value: Any) -> str:
    if value is None:
        return ""
    compact = re.sub(r"[\s-]+", "", str(value).strip())
    return compact if compact.isdigit() else ""


def mb_artist_credit(release: dict[str, Any]) -> str:
    parts: list[str] = []
    credits = release.get("artist-credit", [])
    if not isinstance(credits, list):
        return ""
    for item in credits:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            artist = item.get("artist") or {}
            name = item.get("name") or (artist.get("name") if isinstance(artist, dict) else "") or ""
            parts.append(str(name) + str(item.get("joinphrase") or ""))
    return "".join(parts).strip()


def has_discogs_relation(release: dict[str, Any]) -> bool:
    relations = release.get("relations", [])
    if not isinstance(relations, list):
        return False
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        url_obj = relation.get("url") or {}
        if not isinstance(url_obj, dict):
            continue
        resource = str(url_obj.get("resource") or "")
        parsed = urllib.parse.urlparse(resource)
        host = (parsed.hostname or "").casefold()
        path = (parsed.path or "").casefold()
        if host in {"discogs.com", "www.discogs.com"} and (
            path.startswith("/release/") or path.startswith("/master/")
        ):
            return True
    return False


def fetch_mb_release(
    mbid: str,
    base_url: str,
    cache: JsonCache,
    last_request: list[float],
    delay: float = 0.05,
) -> dict[str, Any]:
    cached = cache.get(mbid)
    if cached is not None:
        return cached

    elapsed = time.monotonic() - last_request[0]
    if last_request[0] and elapsed < delay:
        time.sleep(delay - elapsed)
    # Request the same identity fields used by Stage 2 V6. Release events are
    # returned with the release; media supplies the carrier format list.
    query = urllib.parse.urlencode({"inc": "artist-credits+url-rels+labels+media", "fmt": "json"})
    url = f"{base_url.rstrip('/')}/ws/2/release/{urllib.parse.quote(mbid)}?{query}"
    last_request[0] = time.monotonic()
    data = http_get_json(url, timeout=45)
    release = data.get("release")
    if not isinstance(release, dict):
        release = data if data.get("id") else None
    if not isinstance(release, dict):
        raise RuntimeError(f"Unexpected MusicBrainz response for MBID {mbid}")
    cache.put(mbid, release)
    return release


def fetch_discogs_json(
    url: str,
    cache_key: str,
    token: str,
    user_agent: str,
    cache: JsonCache,
    last_request: list[float],
    delay: float,
) -> dict[str, Any]:
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    elapsed = time.monotonic() - last_request[0]
    if last_request[0] and elapsed < delay:
        time.sleep(delay - elapsed)
    headers = {
        "Authorization": f"Discogs token={token}",
        "User-Agent": user_agent,
        "Accept": "application/json",
    }
    # Enforce the minimum interval between request starts.
    last_request[0] = time.monotonic()
    data = http_get_json(url, headers=headers, timeout=45)
    cache.put(cache_key, data)
    return data


def _pagination_counts(data: dict[str, Any], context: str) -> tuple[int, int]:
    pagination = data.get("pagination")
    if not isinstance(pagination, dict):
        raise RuntimeError(f"{context}: response is missing pagination metadata")
    try:
        total_items = int(pagination["items"])
        total_pages = int(pagination["pages"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"{context}: pagination.items/pages is missing or invalid") from exc
    if total_items < 0 or total_pages < 0:
        raise RuntimeError(f"{context}: pagination contains a negative count")
    if total_items > 0 and total_pages < 1:
        raise RuntimeError(f"{context}: non-zero item count but zero pages")
    return total_items, total_pages


def fetch_discogs_search_page(
    barcode: str,
    page: int,
    token: str,
    user_agent: str,
    cache: JsonCache,
    last_request: list[float],
    delay: float,
) -> dict[str, Any]:
    params = urllib.parse.urlencode({"barcode": barcode, "type": "release", "per_page": PER_PAGE, "page": page})
    url = f"https://api.discogs.com/database/search?{params}"
    cache_key = f"search|barcode={barcode}|type=release|per_page={PER_PAGE}|page={page}"
    return fetch_discogs_json(url, cache_key, token, user_agent, cache, last_request, delay)


def fetch_all_discogs_results(
    barcode: str,
    token: str,
    user_agent: str,
    cache: JsonCache,
    last_request: list[float],
    delay: float,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Fetch every unique type=release search result across all pagination pages."""
    first = fetch_discogs_search_page(barcode, 1, token, user_agent, cache, last_request, delay)
    total_items, total_pages = _pagination_counts(first, "Discogs barcode search")
    all_results: list[dict[str, Any]] = []
    seen_release_ids: set[str] = set()

    def add_page(data: dict[str, Any]) -> None:
        rows = data.get("results")
        if not isinstance(rows, list):
            raise RuntimeError("Discogs search response has no results list")
        for item in rows:
            if not isinstance(item, dict) or str(item.get("type") or "").casefold() != "release":
                continue
            release_id = str(item.get("id") or "").strip()
            if release_id.isdigit() and release_id not in seen_release_ids:
                seen_release_ids.add(release_id)
                all_results.append(item)

    if total_pages:
        add_page(first)
    for page in range(2, total_pages + 1):
        page_data = fetch_discogs_search_page(barcode, page, token, user_agent, cache, last_request, delay)
        add_page(page_data)

    if total_items and not all_results:
        raise RuntimeError("Discogs reports search results but no valid release rows were returned")
    return total_items, total_pages, all_results


def fetch_master_versions_page(
    master_id: str,
    page: int,
    token: str,
    user_agent: str,
    cache: JsonCache,
    last_request: list[float],
    delay: float,
) -> dict[str, Any]:
    params = urllib.parse.urlencode({"per_page": PER_PAGE, "page": page})
    url = f"https://api.discogs.com/masters/{urllib.parse.quote(master_id)}/versions?{params}"
    cache_key = f"master_versions|master_id={master_id}|per_page={PER_PAGE}|page={page}"
    return fetch_discogs_json(url, cache_key, token, user_agent, cache, last_request, delay)


def fetch_all_master_versions(
    master_id: str,
    token: str,
    user_agent: str,
    cache: JsonCache,
    last_request: list[float],
    delay: float,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Fetch every version of one Discogs master, following all API pages."""
    first = fetch_master_versions_page(master_id, 1, token, user_agent, cache, last_request, delay)
    total_items, total_pages = _pagination_counts(first, f"Discogs master {master_id} versions")
    all_versions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def add_page(data: dict[str, Any]) -> None:
        versions = data.get("versions")
        if not isinstance(versions, list):
            raise RuntimeError(f"Discogs master {master_id} response has no versions list")
        for item in versions:
            if not isinstance(item, dict):
                continue
            release_id = str(item.get("id") or "").strip()
            if release_id.isdigit() and release_id not in seen_ids:
                seen_ids.add(release_id)
                all_versions.append(item)

    if total_pages:
        add_page(first)
    for page in range(2, total_pages + 1):
        data = fetch_master_versions_page(master_id, page, token, user_agent, cache, last_request, delay)
        add_page(data)

    if total_items and not all_versions:
        raise RuntimeError(f"Discogs master {master_id} reports versions but returned no valid release IDs")
    return total_items, total_pages, all_versions


def expand_candidates_for_master_results(
    search_total: int,
    search_results: list[dict[str, Any]],
    load_versions: Callable[[str], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str], int]:
    """Keep direct results; for multiple hits, append every unique master version.

    `load_versions` is deliberately injected so this selection rule can be tested
    offline without calling Discogs.
    """
    candidates: list[dict[str, Any]] = []
    seen_release_ids: set[str] = set()

    def add_candidate(item: dict[str, Any], source: str, master_id: str = "") -> None:
        release_id = str(item.get("id") or "").strip()
        if not release_id.isdigit() or release_id in seen_release_ids:
            return
        seen_release_ids.add(release_id)
        candidates.append({
            "id": release_id,
            "title": str(item.get("title") or "").strip(),
            "candidate_source": source,
            "master_id": master_id or str(item.get("master_id") or "").strip(),
            "version_year": str(item.get("released") or item.get("year") or "").strip(),
            "version_country": str(item.get("country") or "").strip(),
            "version_format": "; ".join(str(value) for value in item.get("format", []) if value)
                if isinstance(item.get("format"), list) else str(item.get("format") or "").strip(),
        })

    # Always keep every direct barcode search result.
    for item in search_results:
        if isinstance(item, dict):
            add_candidate(item, "BARCODE_SEARCH")

    expanded_master_ids: list[str] = []
    expanded_version_rows = 0
    # The single-result case is intentionally kept as-is. "View all versions"
    # is expanded only when the barcode search itself returns multiple releases.
    if search_total > 1:
        master_ids: list[str] = []
        seen_master_ids: set[str] = set()
        for item in search_results:
            if not isinstance(item, dict):
                continue
            master_id = str(item.get("master_id") or "").strip()
            if master_id.isdigit() and master_id != "0" and master_id not in seen_master_ids:
                seen_master_ids.add(master_id)
                master_ids.append(master_id)

        for master_id in master_ids:
            versions = load_versions(master_id)
            expanded_master_ids.append(master_id)
            expanded_version_rows += len(versions)
            for item in versions:
                if isinstance(item, dict):
                    add_candidate(item, "MASTER_VERSION", master_id)

    return candidates, expanded_master_ids, expanded_version_rows


_V6_IDENTITY_HELPERS: Any = None

def _load_v6_identity_helpers() -> Any:
    """Load the shared Stage 2 V6 identity normalizers from this package.

    Reusing V6's country and medium normalization prevents Stage 1 from
    inventing a looser or stricter interpretation of the same metadata.
    """
    global _V6_IDENTITY_HELPERS
    if _V6_IDENTITY_HELPERS is not None:
        return _V6_IDENTITY_HELPERS

    import importlib.util

    helper_path = Path(__file__).resolve().with_name("discogs_bot_verify_v6.py")
    if not helper_path.is_file():
        raise RuntimeError(f"Stage 2 V6 helper file is missing: {helper_path}")
    spec = importlib.util.spec_from_file_location("_discogs_bot_verify_v6_identity_helpers", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load Stage 2 V6 identity helpers: {helper_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _V6_IDENTITY_HELPERS = module
    return module


def _candidate_format_carriers(candidate: dict[str, Any], v6: Any) -> set[str]:
    """Extract only confidently recognized carriers from a search/version summary."""
    raw = candidate.get("version_format") or candidate.get("format") or []
    if isinstance(raw, list):
        values = [str(value).strip() for value in raw if str(value).strip()]
    else:
        text = str(raw).strip()
        # Builder serialization uses semicolons. The comma fallback also
        # handles older cached search summaries.
        values = [part.strip() for part in re.split(r"[;,]", text) if part.strip()]

    carriers: set[str] = set()
    for value in values:
        canonical = v6.normalize_media_format(value)
        if canonical:
            carriers.add(canonical)
    return carriers


def prefilter_candidates_by_mb_identity(
    candidates: list[dict[str, Any]],
    mb_release: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Drop only candidates that definitively contradict usable MusicBrainz data.

    Discogs search and Master Versions summaries are less complete than a full
    /releases/{id} response. Therefore missing/ambiguous summary values are kept
    for Stage 2 V6. This is a conservative prefilter, not final approval.
    """
    v6 = _load_v6_identity_helpers()

    # Enable a filter only when MusicBrainz supplies a usable, unambiguous value.
    mb_medium_counts, _mb_medium_raw, mb_medium_unknown = v6._format_counts_from_musicbrainz(mb_release)
    medium_types = set(mb_medium_counts)
    medium_enabled = bool(medium_types) and not mb_medium_unknown

    mb_year, mb_year_issue = v6.extract_musicbrainz_year(mb_release)
    year_enabled = mb_year is not None and not mb_year_issue

    mb_country_codes, mb_country_issue = v6._mb_country_codes(mb_release)
    country_enabled = len(mb_country_codes) == 1 and not mb_country_issue

    fields_checked = [
        name for name, enabled in (("medium", medium_enabled), ("year", year_enabled), ("country", country_enabled))
        if enabled
    ]
    counts = {"medium": 0, "year": 0, "country": 0}
    kept: list[dict[str, Any]] = []
    rejected_examples: list[dict[str, str]] = []

    for candidate in candidates:
        reasons: list[str] = []

        if medium_enabled:
            dg_types = _candidate_format_carriers(candidate, v6)
            # The summaries do not contain reliable quantities. Only reject a
            # complete carrier-type contradiction; Stage 2 checks quantities.
            # A generic DVD summary cannot distinguish DVD-Audio from DVD-Video.
            generic_dvd_is_ambiguous = (
                ("DVD" in dg_types and bool(medium_types & {"DVD-AUDIO", "DVD-VIDEO"}))
                or ("DVD" in medium_types and bool(dg_types & {"DVD-AUDIO", "DVD-VIDEO"}))
            )
            if dg_types and not (dg_types & medium_types) and not generic_dvd_is_ambiguous:
                counts["medium"] += 1
                reasons.append("MEDIUM_MISMATCH")

        if year_enabled:
            dg_year = v6._year_from_value(candidate.get("version_year") or candidate.get("released") or candidate.get("year"))
            # Blank/undated version summaries are retained for full API verification.
            if dg_year is not None and dg_year != mb_year:
                counts["year"] += 1
                reasons.append("YEAR_MISMATCH")

        if country_enabled:
            dg_country = str(candidate.get("version_country") or candidate.get("country") or "").strip()
            # Reuse V6's region-overlap logic. Only a definitive MISMATCH is
            # removed; broad/compound/unknown regions remain for Stage 2.
            if dg_country:
                country_status, _mb_display, _dg_display, _detail = v6.compare_release_countries(
                    mb_release, {"country": dg_country}
                )
                if country_status == "MISMATCH":
                    counts["country"] += 1
                    reasons.append("COUNTRY_MISMATCH")

        if reasons:
            if len(rejected_examples) < 20:
                rejected_examples.append({
                    "release_id": str(candidate.get("id") or ""),
                    "reasons": ";".join(reasons),
                })
        else:
            kept.append(candidate)

    summary = {
        "fields_checked": fields_checked,
        "candidates_before": len(candidates),
        "candidates_after": len(kept),
        "candidates_removed": len(candidates) - len(kept),
        "removed_by": counts,
        "rejected_examples": rejected_examples,
        "policy": "Only definite contradictions are removed; missing or ambiguous Discogs summary fields are retained for Stage 2 V6.",
    }
    return kept, summary


def load_progress(path: Path) -> dict[str, dict[str, Any]]:
    """Load the latest JSONL progress record for each MBID."""
    records: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return records
    lines = path.read_text(encoding="utf-8").splitlines()
    valid_lines: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            valid_lines.append(line)
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            # An interrupted final append can leave one incomplete line. Remove
            # that fragment now, otherwise the next append would turn it into a
            # malformed middle line and prevent future resumes.
            if line_number == len(lines):
                path.write_text(
                    "\n".join(valid_lines) + ("\n" if valid_lines else ""),
                    encoding="utf-8",
                    newline="\n",
                )
                break
            raise RuntimeError(f"Progress file has invalid JSON at line {line_number}: {path}")
        valid_lines.append(line)
        mbid = str(row.get("mbid") or "").strip().lower()
        if mbid:
            records[mbid] = row
    return records


def append_progress(path: Path, group: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(group, ensure_ascii=False, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def write_outputs(out_dir: Path, groups_by_mbid: dict[str, dict[str, Any]], report_path: Path,
                  mbids_total: int, limit: int) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    groups = sorted(groups_by_mbid.values(), key=lambda row: int(row.get("report_position") or 0))
    candidate_rows: list[dict[str, str]] = []
    for group in groups:
        if str(group.get("status") or "").startswith("ERROR"):
            continue
        for index, candidate in enumerate(group.get("candidates") or [], start=1):
            release_id = str(candidate.get("id") or "").strip()
            if not release_id.isdigit():
                continue
            title = str(candidate.get("title") or "").strip()
            if not title:
                title = f"{group.get('mb_artist') or ''} - {group.get('mb_title') or ''}".strip(" -")
            candidate_rows.append({
                "mbid": str(group.get("mbid") or ""),
                "barcode": str(group.get("barcode") or ""),
                "discogs_url": f"https://www.discogs.com/release/{release_id}",
                "discogs_release_id": release_id,
                "discogs_display_name": title,
                "search_result_index": str(index),
                "search_total_results": str(group.get("search_total_results") or 0),
                "search_pages": str(group.get("search_pages") or 0),
                "candidate_source": str(candidate.get("candidate_source") or ""),
                "master_id": str(candidate.get("master_id") or ""),
                "version_year": str(candidate.get("version_year") or ""),
                "version_country": str(candidate.get("version_country") or ""),
                "version_format": str(candidate.get("version_format") or ""),
            })

    tsv_path = out_dir / "stage1_v6_candidates.tsv"
    with tsv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CANDIDATE_FIELDS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(candidate_rows)

    identity_filter_totals = {"candidates_before": 0, "candidates_after": 0, "candidates_removed": 0,
                              "removed_by": {"medium": 0, "year": 0, "country": 0}}
    identity_filter_fields: Counter[str] = Counter()
    for group in groups:
        info = group.get("identity_filter") or {}
        for key in ("candidates_before", "candidates_after", "candidates_removed"):
            identity_filter_totals[key] += int(info.get(key) or 0)
        for field, count in (info.get("removed_by") or {}).items():
            if field in identity_filter_totals["removed_by"]:
                identity_filter_totals["removed_by"][field] += int(count or 0)
        for field in info.get("fields_checked") or []:
            identity_filter_fields[str(field)] += 1

    json_path = out_dir / "stage1_v6_candidates.json"
    temp_json = json_path.with_suffix(".json.tmp")
    temp_json.write_text(json.dumps({
        "summary": {
            "source_report": str(report_path),
            "unique_mbids_in_report": mbids_total,
            "mbids_in_this_run_limit": limit,
            "mbids_with_progress": len(groups),
            "candidate_rows_for_stage2_v6": len(candidate_rows),
            "identity_filter": {
                **identity_filter_totals,
                "groups_with_fields_checked": dict(identity_filter_fields),
                "policy": "Only definite contradictions are removed; missing or ambiguous Discogs summary fields are retained for Stage 2 V6.",
            },
            "status_counts": dict(Counter(str(g.get("status") or "UNKNOWN") for g in groups)),
            "search_result_buckets": {
                "0": sum(1 for g in groups if "search_total_results" in g and int(g.get("search_total_results") or 0) == 0),
                "1": sum(1 for g in groups if "search_total_results" in g and int(g.get("search_total_results") or 0) == 1),
                "2": sum(1 for g in groups if "search_total_results" in g and int(g.get("search_total_results") or 0) == 2),
                "3+": sum(1 for g in groups if "search_total_results" in g and int(g.get("search_total_results") or 0) >= 3),
            },
            "master_ids_expanded": sum(len(g.get("expanded_master_ids") or []) for g in groups),
            "master_version_rows_seen": sum(int(g.get("expanded_version_rows") or 0) for g in groups),
            "notes": [
                "All Discogs barcode search pages are collected.",
                "For multiple direct search results, all distinct associated masters are expanded through /masters/{id}/versions, including every versions page.",
                "A single direct search result is retained as-is and is not expanded.",
                "Duplicate Discogs Release IDs are removed per MBID.",
                "Candidate summaries are prefiltered against usable MusicBrainz medium, year and country; only definite contradictions are removed.",
                "Missing or ambiguous Discogs summary fields are retained for full Stage 2 V6 API verification.",
                "Candidate TSV is ready as direct input for discogs_bot_verify_v6.py.",
                "MusicBrainz is read through its local Web Service; PostgreSQL/psql is not used.",
                "No MusicBrainz edits or relationships are created.",
            ],
        },
        "groups": groups,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_json.replace(json_path)

    summary_path = out_dir / "stage1_v6_summary.txt"
    status_counts = Counter(str(g.get("status") or "UNKNOWN") for g in groups)
    buckets = {
        "0": sum(1 for g in groups if "search_total_results" in g and int(g.get("search_total_results") or 0) == 0),
        "1": sum(1 for g in groups if "search_total_results" in g and int(g.get("search_total_results") or 0) == 1),
        "2": sum(1 for g in groups if "search_total_results" in g and int(g.get("search_total_results") or 0) == 2),
        "3+": sum(1 for g in groups if "search_total_results" in g and int(g.get("search_total_results") or 0) >= 3),
    }
    with summary_path.open("w", encoding="utf-8-sig") as stream:
        stream.write("DISCOGS BARCODE BUILDER — MULTI-RESULT + MASTER VERSIONS (READ-ONLY)\n")
        stream.write("=" * 72 + "\n")
        stream.write(f"Source report: {report_path}\n")
        stream.write(f"Unique MBIDs in report: {mbids_total:,}\n")
        stream.write(f"MBIDs with progress records: {len(groups):,}\n")
        stream.write(f"Discogs candidate rows for Stage 2 v6: {len(candidate_rows):,}\n")
        stream.write(f"Distinct master IDs expanded: {sum(len(g.get('expanded_master_ids') or []) for g in groups):,}\n")
        stream.write(f"Master version rows seen (before per-MBID deduplication): {sum(int(g.get('expanded_version_rows') or 0) for g in groups):,}\n")
        identity_before = sum(int((g.get("identity_filter") or {}).get("candidates_before") or 0) for g in groups)
        identity_after = sum(int((g.get("identity_filter") or {}).get("candidates_after") or 0) for g in groups)
        identity_removed = sum(int((g.get("identity_filter") or {}).get("candidates_removed") or 0) for g in groups)
        stream.write(f"Candidates before MB identity prefilter: {identity_before:,}\n")
        stream.write(f"Candidates after MB identity prefilter: {identity_after:,}\n")
        stream.write(f"Candidates removed by MB identity prefilter: {identity_removed:,}\n")
        for field in ("medium", "year", "country"):
            removed = sum(int(((g.get("identity_filter") or {}).get("removed_by") or {}).get(field) or 0) for g in groups)
            stream.write(f"  Removed by {field}: {removed:,}\n")
        stream.write("\nSTATUS COUNTS\n")
        for status, count in sorted(status_counts.items()):
            stream.write(f"{status}: {count:,}\n")
        stream.write("\nINITIAL BARCODE SEARCH RESULT BUCKETS\n")
        for label, count in buckets.items():
            stream.write(f"{label}: {count:,}\n")
        stream.write("\nOne result = keep that release. Multiple results = keep all search results and expand all linked master versions.\n")
        stream.write("Candidate TSV is the direct input for Stage 2 v6.\n")
        stream.write("MusicBrainz is read through the local Web Service; no PostgreSQL/psql is used.\n")
        stream.write("No MusicBrainz edits were submitted.\n")
    return tsv_path, json_path, summary_path


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Search Discogs by MusicBrainz barcode; retain all results and expand master versions when multiple results exist."
    )
    parser.add_argument("report", nargs="?", default="report.txt", help="Source report (default: report.txt beside this script)")
    parser.add_argument("--mb-base-url", default=os.environ.get("MUSICBRAINZ_BASE_URL", DEFAULT_MB_BASE_URL))
    parser.add_argument("--out-dir", default="discogs_barcode_builder_v6_output")
    parser.add_argument("--cache-dir", default="discogs_barcode_builder_v6_cache")
    parser.add_argument("--progress", default="discogs_barcode_builder_v6_progress.jsonl")
    parser.add_argument("--limit", type=int, default=0, help="First N MBIDs (0 = all)")
    parser.add_argument("--discogs-delay", type=float, default=MIN_DISCOGS_DELAY)
    parser.add_argument("--user-agent", default=os.environ.get("DISCOGS_USER_AGENT", DEFAULT_USER_AGENT))
    parser.add_argument("--include-already-linked", action="store_true")
    parser.add_argument("--reset", action="store_true", help="Reset progress and output files; API cache is preserved")
    args = parser.parse_args()

    report_path = resolve_path(args.report, script_dir)
    out_dir = resolve_path(args.out_dir, script_dir)
    cache_root = resolve_path(args.cache_dir, script_dir)
    progress_path = resolve_path(args.progress, script_dir)
    state_path = progress_path.with_suffix(progress_path.suffix + ".state.json")

    if not report_path.is_file():
        print(f"ERROR: report file not found: {report_path}", file=sys.stderr)
        return 2
    if args.limit < 0:
        parser.error("--limit cannot be negative")
    if args.discogs_delay < 1.0:
        parser.error("--discogs-delay must be at least 1.0 second")

    try:
        mbids = extract_unique_mbids(report_path)
    except OSError as exc:
        print(f"ERROR reading report: {exc}", file=sys.stderr)
        return 2
    if not mbids:
        print("ERROR: no MBIDs found in -> ALBUM rows inside POSSIBILITIES sections.", file=sys.stderr)
        return 2
    selected_mbids = mbids[:args.limit] if args.limit else mbids

    report_signature = {
        "path": str(report_path),
        "size": report_path.stat().st_size,
        "mtime_ns": report_path.stat().st_mtime_ns,
    }
    if args.reset:
        progress_path.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
    if progress_path.exists() and not state_path.exists():
        print("ERROR: progress JSONL exists but its state file is missing. Use --reset to start over.", file=sys.stderr)
        return 2
    if state_path.exists():
        try:
            old_signature = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"ERROR: cannot read progress state: {state_path}. Use --reset.", file=sys.stderr)
            return 2
        if old_signature != report_signature:
            print("ERROR: progress belongs to a different report file. Use --reset or a new --progress path.", file=sys.stderr)
            return 2
    else:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(report_signature, ensure_ascii=False, indent=2), encoding="utf-8")

    progress = load_progress(progress_path)
    done = {mbid for mbid, group in progress.items() if str(group.get("status") or "") != "ERROR"}
    remaining = [mbid for mbid in selected_mbids if mbid not in done]

    token = os.environ.get("DISCOGS_TOKEN", "").strip()
    if not token and remaining:
        token = getpass.getpass("Discogs API token (hidden input): ").strip()
    if not token and remaining:
        print("ERROR: no Discogs API token supplied.", file=sys.stderr)
        return 2

    cache_root.mkdir(parents=True, exist_ok=True)
    # New cache namespace is required because earlier builder runs fetched only
    # artist/URL relations and did not request media. Reusing that cache would
    # silently disable the new Stage 1 identity prefilter.
    cache_mb = JsonCache(cache_root / "musicbrainz_release_identity_v1")
    cache_dg = JsonCache(cache_root / "discogs_api")
    last_mb_request = [0.0]
    last_dg_request = [0.0]

    print("=" * 72)
    print("DISCOGS BARCODE BUILDER — MULTI-RESULT + VIEW ALL VERSIONS")
    print("=" * 72)
    print(f"Report: {report_path}")
    print(f"Unique MBIDs in report: {len(mbids):,}")
    print(f"MBIDs selected: {len(selected_mbids):,}")
    print(f"Already completed: {len(done):,}")
    print(f"Remaining this run: {len(remaining):,}")
    print(f"Local MusicBrainz Web Service: {args.mb_base_url}")
    print("Discogs: all barcode search pages; multiple hits expand all associated master versions.")
    print("Identity prefilter: definite MB medium/year/country contradictions removed; missing or ambiguous summaries retained.")
    print("Read-only. Uses no PostgreSQL/psql and submits no MusicBrainz edits.\n")

    started = time.monotonic()
    fatal = False
    processed_this_run = 0
    try:
        for report_position, mbid in enumerate(selected_mbids, start=1):
            if mbid in done:
                continue
            group: dict[str, Any] = {
                "mbid": mbid,
                "report_position": report_position,
                "status": "ERROR",
                "barcode": "",
                "candidates": [],
                "reason": "UNCLASSIFIED_ERROR",
            }
            try:
                try:
                    mb = fetch_mb_release(mbid, args.mb_base_url, cache_mb, last_mb_request)
                except HTTPStatusError as exc:
                    if exc.status == 404:
                        group.update(status="SKIP_MBID_NOT_FOUND", reason="LOCAL_MUSICBRAINZ_404")
                        mb = None
                    else:
                        raise

                if mb is not None:
                    returned_mbid = str(mb.get("id") or "").strip().lower()
                    if returned_mbid and returned_mbid != mbid:
                        raise RuntimeError(f"MusicBrainz returned MBID {returned_mbid} for requested {mbid}")
                    if has_discogs_relation(mb) and not args.include_already_linked:
                        group.update(status="SKIP_ALREADY_LINKED", reason="EXISTING_DISCOGS_RELATION")
                    else:
                        barcode = normalize_barcode(mb.get("barcode"))
                        group["barcode"] = barcode
                        group["mb_artist"] = mb_artist_credit(mb)
                        group["mb_title"] = str(mb.get("title") or "")
                        if not barcode:
                            group.update(status="SKIP_NO_BARCODE", reason="MUSICBRAINZ_BARCODE_MISSING_OR_INVALID")
                        else:
                            total_items, total_pages, search_results = fetch_all_discogs_results(
                                barcode, token, args.user_agent, cache_dg, last_dg_request, args.discogs_delay
                            )
                            group.update(
                                search_total_results=total_items,
                                search_pages=total_pages,
                                search_release_rows=len(search_results),
                            )
                            if total_items == 0:
                                group.update(status="SKIP_NO_DISCOGS_RESULT", reason="ZERO_DISCOGS_RESULTS")
                            else:
                                def load_versions(master_id: str) -> list[dict[str, Any]]:
                                    _count, _pages, versions = fetch_all_master_versions(
                                        master_id, token, args.user_agent, cache_dg,
                                        last_dg_request, args.discogs_delay,
                                    )
                                    return versions

                                candidates, expanded_master_ids, expanded_version_rows = expand_candidates_for_master_results(
                                    total_items, search_results, load_versions
                                )
                                candidates_before_filter = len(candidates)
                                candidates, identity_filter = prefilter_candidates_by_mb_identity(candidates, mb)
                                identity_filter["candidates_before"] = candidates_before_filter
                                identity_filter["candidates_after"] = len(candidates)
                                identity_filter["candidates_removed"] = candidates_before_filter - len(candidates)
                                group.update(
                                    status=(
                                        ("SEARCHED_SINGLE" if total_items == 1 else "SEARCHED_MULTIPLE")
                                        if candidates else "SKIP_NO_IDENTITY_MATCH"
                                    ),
                                    candidates=candidates,
                                    expanded_master_ids=expanded_master_ids,
                                    expanded_version_rows=expanded_version_rows,
                                    candidate_count=len(candidates),
                                    candidate_count_before_identity_filter=candidates_before_filter,
                                    identity_filter=identity_filter,
                                    mb_identity_fields_checked=identity_filter["fields_checked"],
                                    reason=(
                                        ("SINGLE_DISCOGS_RELEASE_RESULT" if total_items == 1 else "ALL_SEARCH_RESULTS_AND_ALL_ASSOCIATED_MASTER_VERSIONS_RETAINED")
                                        if candidates else "ALL_CANDIDATES_DEFINITIVELY_CONTRADICT_AVAILABLE_MB_IDENTITY_FIELDS"
                                    ),
                                )
                                if total_items == 1 and candidates and len(candidates) != 1:
                                    raise RuntimeError("Discogs reports one result but candidate construction did not return exactly one release")
                                if total_items > 1 and candidates_before_filter == 0:
                                    raise RuntimeError("Discogs reports multiple results but no valid release candidates were built")

                append_progress(progress_path, group)
                progress[mbid] = group
                processed_this_run += 1
            except HTTPStatusError as exc:
                group.update(status="ERROR", reason=f"HTTP_{exc.status}: {exc.body[:200]}")
                append_progress(progress_path, group)
                progress[mbid] = group
                processed_this_run += 1
                if exc.status in (401, 403):
                    print(f"FATAL: Discogs API access denied (HTTP {exc.status}); stopping.", file=sys.stderr)
                    fatal = True
            except Exception as exc:
                message = str(exc).replace("\t", " ").replace("\r", " ").replace("\n", " ")[:500]
                group.update(status="ERROR", reason=message)
                append_progress(progress_path, group)
                progress[mbid] = group
                processed_this_run += 1

            if processed_this_run % 25 == 0 or processed_this_run == len(remaining):
                write_outputs(out_dir, progress, report_path, len(mbids), args.limit)
                elapsed = max(time.monotonic() - started, 0.001)
                speed = processed_this_run / elapsed
                left = max(0, len(remaining) - processed_this_run)
                eta = left / speed if speed else 0.0
                counts = Counter(str(item.get("status") or "UNKNOWN") for item in progress.values())
                identity_removed = sum(
                    int((item.get("identity_filter") or {}).get("candidates_removed") or 0)
                    for item in progress.values()
                )
                identity_after = sum(
                    int((item.get("identity_filter") or {}).get("candidates_after") or 0)
                    for item in progress.values()
                )
                print(
                    f"[{processed_this_run:,}/{len(remaining):,}] searched={counts['SEARCHED_SINGLE'] + counts['SEARCHED_MULTIPLE']:,} "
                    f"single={counts['SEARCHED_SINGLE']:,} multiple={counts['SEARCHED_MULTIPLE']:,} "
                    f"kept_candidates={identity_after:,} filtered_out={identity_removed:,} "
                    f"already_linked={counts['SKIP_ALREADY_LINKED']:,} no_barcode={counts['SKIP_NO_BARCODE']:,} "
                    f"no_identity_match={counts['SKIP_NO_IDENTITY_MATCH']:,} "
                    f"errors={counts['ERROR']:,} | {speed:.2f} MBID/s | ETA {eta / 60:.1f} min",
                    flush=True,
                )
            if fatal:
                break
    except KeyboardInterrupt:
        print("\nInterrupted. Saved progress is preserved; rerun to resume.", flush=True)

    tsv_path, json_path, summary_path = write_outputs(out_dir, progress, report_path, len(mbids), args.limit)
    counts = Counter(str(item.get("status") or "UNKNOWN") for item in progress.values())
    print("\n" + "=" * 72)
    print("STAGE 1 V6 CANDIDATE SEARCH FINISHED")
    print("=" * 72)
    print(f"MBIDs in report: {len(mbids):,}")
    print(f"Completed MBIDs: {sum(1 for item in progress.values() if item.get('status') != 'ERROR'):,}")
    print(f"Single search results: {counts['SEARCHED_SINGLE']:,}")
    print(f"Multiple search results (expanded): {counts['SEARCHED_MULTIPLE']:,}")
    print(f"Candidate TSV for Stage 2 v6: {tsv_path}")
    print(f"Detailed JSON: {json_path}")
    print(f"Summary: {summary_path}")
    print("No MusicBrainz edits were submitted.")
    return 2 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())