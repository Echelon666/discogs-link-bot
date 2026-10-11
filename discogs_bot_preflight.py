#!/usr/bin/env python3
"""Discogs Link Bot — Stage 1 preflight (read-only, no network calls).

Reads the four-column TSV exported for Discogs linking and creates:
  - stage1_unique_release_candidates.tsv: structurally valid rows whose Discogs
    release ID is not assigned to more than one MusicBrainz MBID in the input.
  - stage1_manual_discogs_id_conflicts.tsv: every row involved in such a conflict.
  - stage1_invalid_rows.tsv: malformed or invalid input rows.
  - stage1_barcode_collisions.tsv: barcode values associated with multiple
    Discogs release IDs (potential conflicts; not automatically resolved).
  - stage1_report.txt: summary and safety notes.

Important: a row in the candidates file is NOT yet verified for artist/title,
barcode, edition, blacklist, existing relationships, or suitability for editing.
This script never contacts MusicBrainz/Discogs and never writes to any database.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import uuid
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

FIELDS = ["line_no", "mbid", "barcode", "discogs_url", "discogs_release_id", "discogs_display_name"]
RELEASE_PATH_RE = re.compile(r"^/release/(\d+)(?:[-/]|$)", re.IGNORECASE)


def parse_row(line: str, line_no: int) -> tuple[dict[str, str] | None, str | None]:
    parts = line.rstrip("\r\n").split("\t")
    if len(parts) != 4:
        return None, f"EXPECTED_4_TAB_COLUMNS_GOT_{len(parts)}"

    mbid, barcode, url, display_name = (part.strip() for part in parts)
    problems: list[str] = []

    try:
        parsed_uuid = uuid.UUID(mbid)
        # UUID accepts several textual forms; MBIDs must use canonical hyphenated form.
        if str(parsed_uuid) != mbid.lower():
            problems.append("INVALID_MBID_FORMAT")
    except (ValueError, AttributeError):
        problems.append("INVALID_MBID")

    if not barcode:
        problems.append("MISSING_BARCODE")
    elif not barcode.isdigit():
        problems.append("BARCODE_NOT_DIGITS")

    parsed_url = urlparse(url)
    if parsed_url.scheme.lower() != "https" or (parsed_url.hostname or "").lower() not in {
        "discogs.com", "www.discogs.com"
    }:
        problems.append("URL_NOT_CANONICAL_DISCOGS_HTTPS")
        release_id = ""
    else:
        match = RELEASE_PATH_RE.match(parsed_url.path)
        if not match:
            problems.append("URL_NOT_A_RELEASE_PAGE")
            release_id = ""
        else:
            release_id = match.group(1)

    if not display_name:
        problems.append("MISSING_DISPLAY_NAME")

    row = {
        "line_no": str(line_no),
        "mbid": mbid,
        "barcode": barcode,
        "discogs_url": url,
        "discogs_release_id": release_id,
        "discogs_display_name": display_name,
    }
    return row, ";".join(problems) if problems else None


def write_tsv(path: Path, rows: list[dict[str, str]], extra_fields: list[str] | None = None) -> None:
    fields = FIELDS + (extra_fields or [])
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only preflight for Discogs-link input TSV.")
    parser.add_argument("input", nargs="?", default="input.tsv", help="Input file (default: input.tsv)")
    parser.add_argument("--out-dir", default="discogs_bot_preflight_output", help="Output directory (default: discogs_bot_preflight_output)")
    args = parser.parse_args()

    source = Path(args.input).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if not source.is_file():
        print(f"ERROR: input file not found: {source}", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    valid_rows: list[dict[str, str]] = []
    invalid_rows: list[dict[str, str]] = []
    raw_lines = source.read_text(encoding="utf-8-sig").splitlines()

    for line_no, line in enumerate(raw_lines, start=1):
        if not line.strip():
            invalid_rows.append({"line_no": str(line_no), "mbid": "", "barcode": "", "discogs_url": "", "discogs_release_id": "", "discogs_display_name": "", "reason": "BLANK_LINE", "raw_line": line})
            continue
        row, error = parse_row(line, line_no)
        if error:
            invalid_rows.append({**(row or {field: "" for field in FIELDS}), "reason": error, "raw_line": line})
        elif row is not None:
            valid_rows.append(row)

    mbid_to_lines: dict[str, list[dict[str, str]]] = defaultdict(list)
    release_to_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    barcode_to_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in valid_rows:
        mbid_to_lines[row["mbid"].lower()].append(row)
        release_to_rows[row["discogs_release_id"]].append(row)
        barcode_to_rows[row["barcode"]].append(row)

    duplicate_mbids = {mbid: group for mbid, group in mbid_to_lines.items() if len(group) > 1}
    release_conflicts = {
        release_id: group
        for release_id, group in release_to_rows.items()
        if len({r["mbid"].lower() for r in group}) > 1
    }
    barcode_collisions = {
        barcode: group
        for barcode, group in barcode_to_rows.items()
        if len({r["discogs_release_id"] for r in group}) > 1
    }

    conflict_line_nos = {
        row["line_no"]
        for group in release_conflicts.values()
        for row in group
    }
    duplicate_mbid_line_nos = {
        row["line_no"]
        for group in duplicate_mbids.values()
        for row in group
    }
    blocked_line_nos = conflict_line_nos | duplicate_mbid_line_nos
    structurally_valid_candidates = [row for row in valid_rows if row["line_no"] not in blocked_line_nos]

    conflict_rows: list[dict[str, str]] = []
    for release_id, group in sorted(release_conflicts.items(), key=lambda kv: int(kv[0])):
        for row in group:
            conflict_rows.append({**row, "reason": "SAME_DISCOGS_RELEASE_ID_FOR_MULTIPLE_MBIDS", "conflict_mbids": ";".join(sorted({r["mbid"] for r in group}))})

    mbid_conflict_rows: list[dict[str, str]] = []
    for mbid, group in sorted(duplicate_mbids.items()):
        for row in group:
            mbid_conflict_rows.append({**row, "reason": "SAME_MBID_MULTIPLE_INPUT_ROWS", "conflict_discogs_release_ids": ";".join(sorted({r["discogs_release_id"] for r in group}, key=int))})

    barcode_rows: list[dict[str, str]] = []
    for barcode, group in sorted(barcode_collisions.items()):
        release_ids = sorted({r["discogs_release_id"] for r in group}, key=int)
        for row in group:
            barcode_rows.append({**row, "reason": "SAME_BARCODE_FOR_MULTIPLE_DISCOGS_RELEASE_IDS", "conflict_release_ids": ";".join(release_ids)})

    invalid_fields = FIELDS + ["reason", "raw_line"]
    with (out_dir / "stage1_invalid_rows.tsv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=invalid_fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(invalid_rows)

    write_tsv(out_dir / "stage1_unique_release_candidates.tsv", structurally_valid_candidates)
    with (out_dir / "stage1_manual_discogs_id_conflicts.tsv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS + ["reason", "conflict_mbids"], delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(conflict_rows)
    with (out_dir / "stage1_manual_mbid_conflicts.tsv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS + ["reason", "conflict_discogs_release_ids"], delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(mbid_conflict_rows)
    with (out_dir / "stage1_barcode_collisions.tsv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS + ["reason", "conflict_release_ids"], delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(barcode_rows)

    report = [
        "DISCOGS LINK BOT — STAGE 1 PREFLIGHT",
        "=" * 42,
        f"Input file: {source}",
        f"Input lines: {len(raw_lines)}",
        f"Structurally valid rows: {len(valid_rows)}",
        f"Malformed/invalid rows: {len(invalid_rows)}",
        f"Unique MBIDs among structurally valid rows: {len(mbid_to_lines)}",
        f"Duplicate MBID keys: {len(duplicate_mbids)}",
        f"Unique Discogs release IDs: {len(release_to_rows)}",
        f"Discogs release IDs assigned to multiple MBIDs: {len(release_conflicts)}",
        f"Rows involved in Discogs-ID conflicts: {len(conflict_line_nos)}",
        f"Rows passed to next verification stage: {len(structurally_valid_candidates)}",
        f"Barcodes shared by multiple Discogs release IDs: {len(barcode_collisions)}",
        "",
        "IMPORTANT:",
        "Rows in stage1_unique_release_candidates.tsv are only free of input-structure and duplicate-release-ID conflicts.",
        "They have NOT been verified against MusicBrainz/Discogs metadata and are NOT approved for writing.",
        "No network requests or database changes were performed.",
        "",
        "Generated files:",
        "  stage1_unique_release_candidates.tsv",
        "  stage1_manual_discogs_id_conflicts.tsv",
        "  stage1_manual_mbid_conflicts.tsv",
        "  stage1_invalid_rows.tsv",
        "  stage1_barcode_collisions.tsv",
    ]
    (out_dir / "stage1_report.txt").write_text("\n".join(report) + "\n", encoding="utf-8-sig")

    print("=" * 68)
    print("DISCOGS LINK BOT — STAGE 1 PREFLIGHT")
    print("=" * 68)
    for line in report[2:14]:
        print(line)
    print(f"\nReports saved to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
