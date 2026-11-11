#!/usr/bin/env python3
r"""Discogs Link Bot — Stage 2 v6 release identity verification (read-only).

This script NEVER submits edits to MusicBrainz. It evaluates candidates from
Stage 1 against the MusicBrainz Web Service on the user's local mirror and the
Discogs API, then writes an auditable TSV report.

Requirements:
- Python 3.10+
- Local MusicBrainz mirror serving /ws/2 (default: http://localhost:5000)
- A Discogs personal API token in environment variable DISCOGS_TOKEN

Run a small pilot first:
  $env:DISCOGS_TOKEN = "..."  # don't paste the token into ChatGPT or source code
  py .\discogs_bot_verify_v6.py .\discogs_bot_preflight_output\stage1_unique_release_candidates.tsv --limit 20 --out-dir .\discogs_bot_verify_output_v6 --cache-dir .\discogs_bot_verify_output_v6\cache

Then, after reviewing report and confirming API access:
  py .\discogs_bot_verify_v6.py .\discogs_bot_preflight_output\stage1_unique_release_candidates.tsv --out-dir .\discogs_bot_verify_output_v6 --cache-dir .\discogs_bot_verify_output_v6\cache

No fuzzy matching is used. v6 retains all v5 conservative normalizations and additionally requires a match for medium format, release year, and release country: typography variants for text, punctuation-only equivalence for titles while preserving every letter, digit, accent, apostrophe and word order, whitespace/dash-glyph normalization for catalog numbers, and a final “Records” label suffix variant only when the same catalog number also matches on the same label entry. All metadata checks are evaluated before selecting the final status. Barcode comparison is exact except for standard UPC-A/EAN-13 equivalence. Network/API failures are fail-closed.
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
DEFAULT_DISCOGS_USER_AGENT = "DiscogsLinkBot/0.3 (+https://github.com/Echelon666/discogs-link-bot; read-only release verification)"
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



# Country aliases are embedded (no third-party dependency required).
# Unknown or compound Discogs regions fail closed.
_COUNTRY_NAME_TO_ISO = json.loads(r'''{"afghanistan":"AF","albania":"AL","algeria":"DZ","american samoa":"AS","andorra":"AD","angola":"AO","anguilla":"AI","antarctica":"AQ","antigua and barbuda":"AG","arab republic of egypt":"EG","argentina":"AR","argentine republic":"AR","armenia":"AM","aruba":"AW","australia":"AU","austria":"AT","azerbaijan":"AZ","bahamas":"BS","bahrain":"BH","bangladesh":"BD","barbados":"BB","belarus":"BY","belgium":"BE","belize":"BZ","benin":"BJ","bermuda":"BM","bhutan":"BT","bolivarian republic of venezuela":"VE","bolivia":"BO","bolivia, plurinational state of":"BO","bonaire, sint eustatius and saba":"BQ","bosnia and herzegovina":"BA","botswana":"BW","bouvet island":"BV","brazil":"BR","britain":"GB","british indian ocean territory":"IO","british virgin islands":"VG","brunei":"BN","brunei darussalam":"BN","bulgaria":"BG","burkina faso":"BF","burundi":"BI","cabo verde":"CV","cambodia":"KH","cameroon":"CM","canada":"CA","cape verde":"CV","cayman islands":"KY","central african republic":"CF","chad":"TD","chile":"CL","china":"CN","christmas island":"CX","cocos (keeling) islands":"CC","colombia":"CO","commonwealth of dominica":"DM","commonwealth of the bahamas":"BS","commonwealth of the northern mariana islands":"MP","comoros":"KM","congo":"CG","congo, the democratic republic of the":"CD","cook islands":"CK","costa rica":"CR","croatia":"HR","cuba":"CU","curaçao":"CW","cyprus":"CY","czech republic":"CZ","czechia":"CZ","côte d'ivoire":"CI","democratic people's republic of korea":"KP","democratic republic of sao tome and principe":"ST","democratic republic of timor-leste":"TL","democratic socialist republic of sri lanka":"LK","denmark":"DK","djibouti":"DJ","dominica":"DM","dominican republic":"DO","eastern republic of uruguay":"UY","ecuador":"EC","egypt":"EG","el salvador":"SV","equatorial guinea":"GQ","eritrea":"ER","estonia":"EE","eswatini":"SZ","ethiopia":"ET","europe":"XE","falkland islands (malvinas)":"FK","faroe islands":"FO","federal democratic republic of ethiopia":"ET","federal democratic republic of nepal":"NP","federal republic of germany":"DE","federal republic of nigeria":"NG","federal republic of somalia":"SO","federated states of micronesia":"FM","federative republic of brazil":"BR","fiji":"FJ","finland":"FI","france":"FR","french guiana":"GF","french polynesia":"PF","french republic":"FR","french southern territories":"TF","gabon":"GA","gabonese republic":"GA","gambia":"GM","georgia":"GE","germany":"DE","ghana":"GH","gibraltar":"GI","grand duchy of luxembourg":"LU","great britain":"GB","greece":"GR","greenland":"GL","grenada":"GD","guadeloupe":"GP","guam":"GU","guatemala":"GT","guernsey":"GG","guinea":"GN","guinea-bissau":"GW","guyana":"GY","haiti":"HT","hashemite kingdom of jordan":"JO","heard island and mcdonald islands":"HM","hellenic republic":"GR","holy see (vatican city state)":"VA","honduras":"HN","hong kong":"HK","hong kong special administrative region of china":"HK","hungary":"HU","iceland":"IS","independent state of papua new guinea":"PG","independent state of samoa":"WS","india":"IN","indonesia":"ID","iran":"IR","iran, islamic republic of":"IR","iraq":"IQ","ireland":"IE","islamic republic of afghanistan":"AF","islamic republic of iran":"IR","islamic republic of mauritania":"MR","islamic republic of pakistan":"PK","isle of man":"IM","israel":"IL","italian republic":"IT","italy":"IT","ivory coast":"CI","jamaica":"JM","japan":"JP","jersey":"JE","jordan":"JO","kazakhstan":"KZ","kenya":"KE","kingdom of bahrain":"BH","kingdom of belgium":"BE","kingdom of bhutan":"BT","kingdom of cambodia":"KH","kingdom of denmark":"DK","kingdom of eswatini":"SZ","kingdom of lesotho":"LS","kingdom of morocco":"MA","kingdom of norway":"NO","kingdom of saudi arabia":"SA","kingdom of spain":"ES","kingdom of sweden":"SE","kingdom of thailand":"TH","kingdom of the netherlands":"NL","kingdom of tonga":"TO","kiribati":"KI","korea, democratic people's republic of":"KP","korea, republic of":"KR","kuwait":"KW","kyrgyz republic":"KG","kyrgyzstan":"KG","lao people's democratic republic":"LA","laos":"LA","latvia":"LV","lebanese republic":"LB","lebanon":"LB","lesotho":"LS","liberia":"LR","libya":"LY","liechtenstein":"LI","lithuania":"LT","luxembourg":"LU","macao":"MO","macao special administrative region of china":"MO","madagascar":"MG","malawi":"MW","malaysia":"MY","maldives":"MV","mali":"ML","malta":"MT","marshall islands":"MH","martinique":"MQ","mauritania":"MR","mauritius":"MU","mayotte":"YT","mexico":"MX","micronesia, federated states of":"FM","moldova":"MD","moldova, republic of":"MD","monaco":"MC","mongolia":"MN","montenegro":"ME","montserrat":"MS","morocco":"MA","mozambique":"MZ","myanmar":"MM","namibia":"NA","nauru":"NR","nepal":"NP","netherlands":"NL","new caledonia":"NC","new zealand":"NZ","nicaragua":"NI","niger":"NE","nigeria":"NG","niue":"NU","norfolk island":"NF","north korea":"KP","north macedonia":"MK","northern mariana islands":"MP","norway":"NO","oman":"OM","pakistan":"PK","palau":"PW","palestine, state of":"PS","panama":"PA","papua new guinea":"PG","paraguay":"PY","people's democratic republic of algeria":"DZ","people's republic of bangladesh":"BD","people's republic of china":"CN","peru":"PE","philippines":"PH","pitcairn":"PN","plurinational state of bolivia":"BO","poland":"PL","portugal":"PT","portuguese republic":"PT","principality of andorra":"AD","principality of liechtenstein":"LI","principality of monaco":"MC","puerto rico":"PR","qatar":"QA","republic of albania":"AL","republic of angola":"AO","republic of armenia":"AM","republic of austria":"AT","republic of azerbaijan":"AZ","republic of belarus":"BY","republic of benin":"BJ","republic of bosnia and herzegovina":"BA","republic of botswana":"BW","republic of bulgaria":"BG","republic of burundi":"BI","republic of cabo verde":"CV","republic of cameroon":"CM","republic of chad":"TD","republic of chile":"CL","republic of colombia":"CO","republic of costa rica":"CR","republic of croatia":"HR","republic of cuba":"CU","republic of cyprus":"CY","republic of côte d'ivoire":"CI","republic of djibouti":"DJ","republic of ecuador":"EC","republic of el salvador":"SV","republic of equatorial guinea":"GQ","republic of estonia":"EE","republic of fiji":"FJ","republic of finland":"FI","republic of ghana":"GH","republic of guatemala":"GT","republic of guinea":"GN","republic of guinea-bissau":"GW","republic of guyana":"GY","republic of haiti":"HT","republic of honduras":"HN","republic of iceland":"IS","republic of india":"IN","republic of indonesia":"ID","republic of iraq":"IQ","republic of kazakhstan":"KZ","republic of kenya":"KE","republic of kiribati":"KI","republic of latvia":"LV","republic of liberia":"LR","republic of lithuania":"LT","republic of madagascar":"MG","republic of malawi":"MW","republic of maldives":"MV","republic of mali":"ML","republic of malta":"MT","republic of mauritius":"MU","republic of moldova":"MD","republic of mozambique":"MZ","republic of myanmar":"MM","republic of namibia":"NA","republic of nauru":"NR","republic of nicaragua":"NI","republic of north macedonia":"MK","republic of palau":"PW","republic of panama":"PA","republic of paraguay":"PY","republic of peru":"PE","republic of poland":"PL","republic of san marino":"SM","republic of senegal":"SN","republic of serbia":"RS","republic of seychelles":"SC","republic of sierra leone":"SL","republic of singapore":"SG","republic of slovenia":"SI","republic of south africa":"ZA","republic of south sudan":"SS","republic of suriname":"SR","republic of tajikistan":"TJ","republic of the congo":"CG","republic of the gambia":"GM","republic of the marshall islands":"MH","republic of the niger":"NE","republic of the philippines":"PH","republic of the sudan":"SD","republic of trinidad and tobago":"TT","republic of tunisia":"TN","republic of türkiye":"TR","republic of uganda":"UG","republic of uzbekistan":"UZ","republic of vanuatu":"VU","republic of yemen":"YE","republic of zambia":"ZM","republic of zimbabwe":"ZW","romania":"RO","russia":"RU","russian federation":"RU","rwanda":"RW","rwandese republic":"RW","réunion":"RE","saint barthélemy":"BL","saint helena, ascension and tristan da cunha":"SH","saint kitts and nevis":"KN","saint lucia":"LC","saint martin (french part)":"MF","saint pierre and miquelon":"PM","saint vincent and the grenadines":"VC","samoa":"WS","san marino":"SM","sao tome and principe":"ST","saudi arabia":"SA","senegal":"SN","serbia":"RS","seychelles":"SC","sierra leone":"SL","singapore":"SG","sint maarten (dutch part)":"SX","slovak republic":"SK","slovakia":"SK","slovenia":"SI","socialist republic of viet nam":"VN","solomon islands":"SB","somalia":"SO","south africa":"ZA","south georgia and the south sandwich islands":"GS","south korea":"KR","south sudan":"SS","spain":"ES","sri lanka":"LK","state of israel":"IL","state of kuwait":"KW","state of qatar":"QA","sudan":"SD","sultanate of oman":"OM","suriname":"SR","svalbard and jan mayen":"SJ","swaziland":"SZ","sweden":"SE","swiss confederation":"CH","switzerland":"CH","syria":"SY","syrian arab republic":"SY","taiwan":"TW","taiwan, province of china":"TW","tajikistan":"TJ","tanzania":"TZ","tanzania, united republic of":"TZ","thailand":"TH","the state of eritrea":"ER","the state of palestine":"PS","timor-leste":"TL","togo":"TG","togolese republic":"TG","tokelau":"TK","tonga":"TO","trinidad and tobago":"TT","tunisia":"TN","turkey":"TR","turkmenistan":"TM","turks and caicos islands":"TC","tuvalu":"TV","türkiye":"TR","u.k.":"GB","u.s.":"US","u.s.a.":"US","uganda":"UG","uk":"GB","ukraine":"UA","union of the comoros":"KM","united arab emirates":"AE","united kingdom":"GB","united kingdom of great britain and northern ireland":"GB","united mexican states":"MX","united republic of tanzania":"TZ","united states":"US","united states minor outlying islands":"UM","united states of america":"US","uruguay":"UY","usa":"US","uzbekistan":"UZ","vanuatu":"VU","vatican city":"VA","venezuela":"VE","venezuela, bolivarian republic of":"VE","viet nam":"VN","vietnam":"VN","virgin islands of the united states":"VI","virgin islands, british":"VG","virgin islands, u.s.":"VI","wallis and futuna":"WF","western sahara":"EH","world":"XW","world wide":"XW","worldwide":"XW","yemen":"YE","zambia":"ZM","zimbabwe":"ZW","åland islands":"AX"}''')
_COUNTRY_NAME_TO_ISO.update({
    "uk": "GB", "u.k.": "GB", "great britain": "GB",
    "usa": "US", "u.s.a.": "US", "u.s.": "US",
    "europe": "XE", "worldwide": "XW", "world wide": "XW", "world": "XW",
})

_MEDIA_FORMAT_ALIASES = {
    "cd": "CD", "compact disc": "CD", "cd r": "CD", "cd g": "CD", "enhanced cd": "CD",
    "vinyl": "VINYL", "vinyl record": "VINYL", "record": "VINYL",
    "cassette": "CASSETTE", "cassette tape": "CASSETTE", "compact cassette": "CASSETTE",
    "digital media": "DIGITAL", "file": "DIGITAL", "digital": "DIGITAL", "download": "DIGITAL",
    "dvd": "DVD", "digital versatile disc": "DVD", "dvd r": "DVD",
    "dvd audio": "DVD-AUDIO", "dvd a": "DVD-AUDIO",
    "blu ray": "BLU-RAY", "blu ray disc": "BLU-RAY",
    "sacd": "SACD", "super audio cd": "SACD",
    "minidisc": "MINIDISC", "mini disc": "MINIDISC",
    "laserdisc": "LASERDISC", "laser disc": "LASERDISC",
    "vhs": "VHS", "vhs tape": "VHS",
    "8 track": "8-TRACK", "8 track cartridge": "8-TRACK",
    "shellac": "SHELLAC", "reel to reel": "REEL-TO-REEL",
    "open reel": "REEL-TO-REEL", "dat": "DAT", "digital audio tape": "DAT",
    "usb flash drive": "USB", "usb": "USB", "memory stick": "USB",
    "floppy disk": "FLOPPY", "cartridge": "CARTRIDGE",
}


def _compact_name(value: Any) -> str:
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).split())


def normalize_media_format(value: Any) -> str | None:
    """Map known MB/Discogs carrier names to a conservative shared vocabulary."""
    key = _compact_name(value)
    if not key or key == "all media":
        return None
    return _MEDIA_FORMAT_ALIASES.get(key)


def _format_counts_from_musicbrainz(release: dict[str, Any]) -> tuple[Counter[str], list[str], list[str]]:
    media = release.get("media")
    counts: Counter[str] = Counter()
    raw: list[str] = []
    unknown: list[str] = []
    if not isinstance(media, list) or not media:
        return counts, raw, unknown
    for item in media:
        if not isinstance(item, dict):
            unknown.append("[invalid medium entry]")
            continue
        name = str(item.get("format") or "").strip()
        if not name:
            unknown.append("[missing MusicBrainz medium format]")
            continue
        raw.append(name)
        canonical = normalize_media_format(name)
        if canonical is None:
            unknown.append(name)
        else:
            counts[canonical] += 1
    return counts, raw, unknown


def _format_counts_from_discogs(release: dict[str, Any]) -> tuple[Counter[str], list[str], list[str]]:
    formats = release.get("formats")
    counts: Counter[str] = Counter()
    raw: list[str] = []
    unknown: list[str] = []
    if not isinstance(formats, list) or not formats:
        return counts, raw, unknown
    for item in formats:
        if not isinstance(item, dict):
            unknown.append("[invalid Discogs format entry]")
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            unknown.append("[missing Discogs format name]")
            continue
        if _compact_name(name) == "all media":
            raw.append(name)
            continue
        canonical = normalize_media_format(name)
        if canonical is None:
            raw.append(name)
            unknown.append(name)
            continue
        qty_value = item.get("qty")
        qty_raw = "" if qty_value is None else str(qty_value).strip()
        if not qty_raw or not qty_raw.isdigit() or int(qty_raw) < 1:
            raw.append(name)
            unknown.append(f"{name} (invalid qty: {qty_raw or 'missing'})")
            continue
        counts[canonical] += int(qty_raw)
        raw.append(f"{name} x{qty_raw}")
    return counts, raw, unknown


def compare_media_formats(mb_release: dict[str, Any], dg_release: dict[str, Any]) -> tuple[str, str, str, str]:
    """Compare medium types and quantities; unknown values never auto-match."""
    mb_counts, mb_raw, mb_unknown = _format_counts_from_musicbrainz(mb_release)
    dg_counts, dg_raw, dg_unknown = _format_counts_from_discogs(dg_release)
    mb_display = "; ".join(mb_raw)
    dg_display = "; ".join(dg_raw)
    if not mb_raw or not dg_raw:
        return "MISSING", mb_display, dg_display, ""
    if mb_unknown or dg_unknown:
        detail = "MB unknown: " + ", ".join(mb_unknown) + "; Discogs unknown: " + ", ".join(dg_unknown)
        return "UNMAPPABLE", mb_display, dg_display, detail.strip(" ;")
    if not mb_counts or not dg_counts:
        return "MISSING", mb_display, dg_display, "No concrete medium type was available."
    if mb_counts != dg_counts:
        return "MISMATCH", mb_display, dg_display, f"MusicBrainz={dict(sorted(mb_counts.items()))}; Discogs={dict(sorted(dg_counts.items()))}"
    return "MATCH", mb_display, dg_display, ""


def _year_from_value(value: Any) -> int | None:
    if value is None:
        return None
    match = re.match(r"^\s*(\d{4})(?:$|[-\s])", str(value))
    if not match:
        return None
    year = int(match.group(1))
    return year if 1000 <= year <= 2999 else None


def extract_musicbrainz_year(release: dict[str, Any]) -> tuple[int | None, str]:
    direct = _year_from_value(release.get("date"))
    if direct is not None:
        return direct, ""
    event_years = set()
    events = release.get("release-events")
    if isinstance(events, list):
        for event in events:
            if isinstance(event, dict):
                year = _year_from_value(event.get("date"))
                if year is not None:
                    event_years.add(year)
    if len(event_years) == 1:
        return next(iter(event_years)), ""
    if len(event_years) > 1:
        return None, "Multiple MusicBrainz release-event years: " + ",".join(map(str, sorted(event_years)))
    return None, ""


def extract_discogs_year(release: dict[str, Any]) -> tuple[int | None, str]:
    api_year = _year_from_value(release.get("year"))
    released_year = _year_from_value(release.get("released"))
    if api_year is not None and released_year is not None and api_year != released_year:
        return None, f"Discogs year={api_year} conflicts with released={released_year}"
    return api_year if api_year is not None else released_year, ""


def compare_release_years(mb_release: dict[str, Any], dg_release: dict[str, Any]) -> tuple[str, str, str, str]:
    mb_year, mb_issue = extract_musicbrainz_year(mb_release)
    dg_year, dg_issue = extract_discogs_year(dg_release)
    if mb_issue or dg_issue:
        return "AMBIGUOUS", str(mb_year or ""), str(dg_year or ""), "; ".join(x for x in (mb_issue, dg_issue) if x)
    if mb_year is None or dg_year is None:
        return "MISSING", str(mb_year or ""), str(dg_year or ""), ""
    if mb_year != dg_year:
        return "MISMATCH", str(mb_year), str(dg_year), f"MusicBrainz={mb_year}; Discogs={dg_year}"
    return "MATCH", str(mb_year), str(dg_year), ""


def _mb_country_codes(release: dict[str, Any]) -> tuple[set[str], str]:
    direct = str(release.get("country") or "").strip().upper()
    if re.fullmatch(r"[A-Z]{2}", direct):
        return {direct}, ""
    events = release.get("release-events")
    codes: set[str] = set()
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            area = event.get("area") or {}
            if not isinstance(area, dict):
                continue
            values = area.get("iso-3166-1-codes") or []
            if isinstance(values, list):
                codes.update(str(x).strip().upper() for x in values if re.fullmatch(r"[A-Z]{2}", str(x).strip().upper()))
    if len(codes) == 1:
        return codes, ""
    if len(codes) > 1:
        return codes, "Multiple MusicBrainz release-event countries: " + ",".join(sorted(codes))
    return set(), ""


def normalize_discogs_country(value: Any) -> str | None:
    key = _compact_name(value)
    if not key:
        return None
    direct = _COUNTRY_NAME_TO_ISO.get(str(value).strip().casefold())
    if direct:
        return direct
    for name, code in _COUNTRY_NAME_TO_ISO.items():
        if _compact_name(name) == key:
            return code
    if re.fullmatch(r"[a-z]{2}", key):
        return key.upper()
    return None


def compare_release_countries(mb_release: dict[str, Any], dg_release: dict[str, Any]) -> tuple[str, str, str, str]:
    mb_codes, mb_issue = _mb_country_codes(mb_release)
    dg_raw = str(dg_release.get("country") or "").strip()
    dg_code = normalize_discogs_country(dg_raw)
    mb_display = ";".join(sorted(mb_codes))
    if not mb_codes or not dg_raw:
        return "MISSING", mb_display, dg_raw, ""
    if mb_issue:
        return "AMBIGUOUS", mb_display, dg_raw, mb_issue
    if dg_code is None:
        return "UNMAPPABLE", mb_display, dg_raw, "Discogs country/area is not an unambiguous ISO country or known MusicBrainz area."
    if len(mb_codes) != 1:
        return "AMBIGUOUS", mb_display, dg_raw, "MusicBrainz country is not unique."
    mb_code = next(iter(mb_codes))
    if mb_code != dg_code:
        return "MISMATCH", mb_code, dg_raw, f"MusicBrainz={mb_code}; Discogs={dg_raw} ({dg_code})"
    return "MATCH", mb_code, dg_raw, ""

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
    """Fetch the official blacklist with bounded retries; fail closed on any final failure.

    Retry only transient transport failures and temporary HTTP responses. A successful
    HTTP response whose contents cannot be parsed is not retried as a network failure.
    """
    max_attempts = 5
    retryable_http_statuses = {408, 425, 429, 500, 502, 503, 504}
    last_error: Exception | None = None
    html_bytes: bytes | None = None

    for attempt in range(max_attempts):
        try:
            html_bytes, _ = http_get(
                BLACKLIST_URL,
                {"Accept": "text/html"},
                timeout=45,
            )
            break
        except HTTPStatusError as exc:
            last_error = exc
            if exc.status not in retryable_http_statuses:
                raise
            retryable = True
            error_label = f"HTTP {exc.status}"
        except RuntimeError as exc:
            last_error = exc
            # http_get wraps TLS EOF, connection reset, timeout and other transport
            # failures as "Network error for ...". Do not retry unrelated errors.
            if not str(exc).startswith("Network error for "):
                raise
            retryable = True
            error_label = str(exc)

        if attempt == max_attempts - 1:
            break

        delay = 2 ** (attempt + 1)  # 2, 4, 8, 16 seconds
        print(
            f"  Blacklist fetch failed ({error_label}); retry "
            f"{attempt + 2}/{max_attempts} in {delay}s",
            flush=True,
        )
        time.sleep(delay)

    if html_bytes is None:
        raise RuntimeError(
            f"Could not download the official Discogs-link blacklist after "
            f"{max_attempts} attempts. Failing closed; no candidates were processed. "
            f"Last error: {last_error}"
        ) from last_error

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
        fieldnames = set(reader.fieldnames or [])
    required = {"mbid", "barcode", "discogs_url", "discogs_release_id"}
    if not rows or not required.issubset(fieldnames):
        raise RuntimeError(f"Input must be a Stage 1 TSV or a filtered Stage 2 TSV with columns: {', '.join(sorted(required))}")
    # When a Stage 2 report is used as the input (for example, the previously
    # reviewed first-100 pilot), refuse to silently re-check MANUAL/REJECT rows.
    if "status" in fieldnames:
        invalid = [str(row.get("mbid") or "") for row in rows if str(row.get("status") or "").strip() != "AUTO_CANDIDATE"]
        if invalid:
            raise RuntimeError(
                f"Stage 2 report input contains {len(invalid)} row(s) that are not AUTO_CANDIDATE. "
                "Use a filtered AUTO_CANDIDATE-only TSV or the Stage 1 candidate TSV."
            )
    return rows


def fetch_mb_release(mbid: str, base_url: str, cache: JsonCache) -> dict[str, Any]:
    cached = cache.get(mbid)
    if cached is not None:
        return cached
    query = urllib.parse.urlencode({"inc": "artist-credits+url-rels+labels+media", "fmt": "json"})
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
        "mb_media_formats": "",
        "discogs_api_formats": "",
        "media_format_check": "NOT_CHECKED",
        "mb_release_date": str(mb.get("date") or ""),
        "mb_release_year": "",
        "discogs_api_released": str(dg.get("released") or ""),
        "discogs_api_year": "",
        "discogs_api_year_raw": str(dg.get("year") or ""),
        "year_check": "NOT_CHECKED",
        "mb_release_country": "",
        "mb_release_country_raw": str(mb.get("country") or ""),
        "discogs_api_country": "",
        "discogs_api_country_raw": str(dg.get("country") or ""),
        "country_check": "NOT_CHECKED",
        "media_format_details": "",
        "year_details": "",
        "country_details": "",
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

    # Release identity checks added in v6: actual carrier/medium, release year,
    # and release country. Missing or unmappable values are never auto-approved.
    media_mode, mb_media_display, dg_media_display, media_details = compare_media_formats(mb, dg)
    result["mb_media_formats"] = mb_media_display
    result["discogs_api_formats"] = dg_media_display
    result["media_format_check"] = media_mode
    result["media_format_details"] = media_details
    if media_mode == "MISSING":
        findings.append(("MANUAL_MEDIA_FORMAT_MISSING", "MEDIUM_FORMAT_MISSING_IN_MUSICBRAINZ_OR_DISCOGS"))
    elif media_mode == "UNMAPPABLE":
        findings.append(("MANUAL_MEDIA_FORMAT_UNMAPPABLE", "ONE_OR_MORE_MEDIUM_FORMATS_CANNOT_BE_SAFELY_NORMALIZED"))
    elif media_mode == "MISMATCH":
        findings.append(("MANUAL_MEDIA_FORMAT_MISMATCH", "MUSICBRAINZ_AND_DISCOGS_MEDIUM_FORMATS_OR_COUNTS_DIFFER"))

    year_mode, mb_year, dg_year, year_details = compare_release_years(mb, dg)
    result["mb_release_year"] = mb_year
    result["discogs_api_year"] = dg_year
    result["year_check"] = year_mode
    result["year_details"] = year_details
    if year_mode == "MISSING":
        findings.append(("MANUAL_YEAR_MISSING", "RELEASE_YEAR_MISSING_IN_MUSICBRAINZ_OR_DISCOGS"))
    elif year_mode == "AMBIGUOUS":
        findings.append(("MANUAL_YEAR_AMBIGUOUS", "RELEASE_YEAR_SOURCE_CONFLICT_OR_MULTIPLE_YEARS"))
    elif year_mode == "MISMATCH":
        findings.append(("MANUAL_YEAR_MISMATCH", "MUSICBRAINZ_AND_DISCOGS_RELEASE_YEARS_DIFFER"))

    country_mode, mb_country, dg_country, country_details = compare_release_countries(mb, dg)
    result["mb_release_country"] = mb_country
    result["discogs_api_country"] = dg_country
    result["country_check"] = country_mode
    result["country_details"] = country_details
    if country_mode == "MISSING":
        findings.append(("MANUAL_COUNTRY_MISSING", "RELEASE_COUNTRY_MISSING_IN_MUSICBRAINZ_OR_DISCOGS"))
    elif country_mode == "AMBIGUOUS":
        findings.append(("MANUAL_COUNTRY_AMBIGUOUS", "MUSICBRAINZ_RELEASE_COUNTRY_IS_AMBIGUOUS"))
    elif country_mode == "UNMAPPABLE":
        findings.append(("MANUAL_COUNTRY_UNMAPPABLE", "DISCOGS_RELEASE_COUNTRY_CANNOT_BE_SAFELY_MAPPED"))
    elif country_mode == "MISMATCH":
        findings.append(("MANUAL_COUNTRY_MISMATCH", "MUSICBRAINZ_AND_DISCOGS_RELEASE_COUNTRIES_DIFFER"))

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
    pair_misses: list[str] = []    pair_normalized = False
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
        passed = ["ARTIST_MATCH", "TITLE_MATCH", "BARCODE_MATCH", "MEDIA_FORMAT_MATCH", "YEAR_MATCH", "COUNTRY_MATCH", "NO_EXISTING_DISCOGS_LINK", "NOT_BLACKLISTED"]
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
    parser = argparse.ArgumentParser(description="Read-only v6 verification with conservative normalization and release identity checks.")
    parser.add_argument("input", nargs="?", default="discogs_bot_preflight_output/stage1_unique_release_candidates.tsv")
    parser.add_argument("--mb-base-url", default=os.environ.get("MUSICBRAINZ_BASE_URL", DEFAULT_MB_BASE_URL))
    parser.add_argument("--discogs-delay", type=float, default=1.1, help="Minimum seconds between Discogs API requests (default: 1.1)")
    parser.add_argument("--limit", type=int, default=0, help="Verify only first N rows (0 = all); use for a small pilot")
    parser.add_argument("--out-dir", default="discogs_bot_verify_output_v6")
    parser.add_argument("--cache-dir", default="discogs_bot_verify_output_v6/cache", help="Dedicated v6 cache root; v5 caches are not reused because MusicBrainz media data is newly requested")
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
    cache_mb = JsonCache(cache_root / "musicbrainz_release_v6")
    cache_dg = JsonCache(cache_root / "discogs_release_v6")
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
        f.write("DISCOGS LINK BOT — STAGE 2 V6 VERIFICATION (READ-ONLY)\n")
        f.write("=" * 53 + "\n")
        f.write(f"Input: {source}\nRows processed: {len(results)}\nBlacklist MBIDs: {len(blacklist)}\n")
        for status, count in sorted(counts.items()):
            f.write(f"{status}: {count}\n")
        f.write("\nAUTO_CANDIDATE means eligible for a separate review/approval stage only. v6 evaluates artist, title, barcode, medium formats (including quantities), release year, release country, labels, catalog numbers, existing links, and blacklist before status assignment. Missing, ambiguous, or unmappable medium/year/country data is routed to MANUAL; only explicit matches can pass. v5 conservative normalization rules are retained.\n")
        f.write("No MusicBrainz edits were submitted by this program.\n")
        if fatal_error:
            f.write("FATAL: run stopped because Discogs authorization was denied.\n")
    print(f"\nReport saved: {report_path}")
    print(f"Summary saved: {out_dir / 'stage2_summary.txt'}")
    print("No edits were submitted to MusicBrainz.")
    return 2 if fatal_error else 0


if __name__ == "__main__":
    raise SystemExit(main())