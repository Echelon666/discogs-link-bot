#!/usr/bin/env python3
"""Create a private Stage 3 JSON queue from a DiscogsBot v5 TSV report.

This tool selects AUTO_CANDIDATE rows only. It makes no network requests and
does not submit MusicBrainz edits. Keep the generated JSON/TSV out of Git.
"""
from __future__ import annotations
import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

MBID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DISCogs = re.compile(r"^/release/(\d+)(?:-|/|$)", re.I)
MAX_BATCH = 150

def validate(row: dict[str, str], line: int) -> dict[str, str]:
    mbid = (row.get("mbid") or "").strip()
    url = (row.get("discogs_url") or "").strip()
    parsed = urlparse(url)
    match = DISCogs.match(parsed.path)
    if not MBID.fullmatch(mbid):
        raise ValueError(f"Line {line}: invalid MBID")
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {"discogs.com", "www.discogs.com"} or not match:
        raise ValueError(f"Line {line}: invalid Discogs release URL")
    release_id = match.group(1)
    reported_id = (row.get("discogs_release_id") or "").strip()
    if reported_id and reported_id != release_id:
        raise ValueError(f"Line {line}: URL Release ID differs from report")
    return {
        "source_line_no": (row.get("line_no") or str(line)).strip(),
        "mbid": mbid.lower(),
        "discogs_url": url,
        "discogs_release_id": release_id,
        "mb_title": (row.get("mb_title") or "").strip(),
        "mb_artist": (row.get("mb_artist") or "").strip(),
        "mb_barcode": (row.get("mb_barcode") or row.get("barcode") or "").strip(),
        "discogs_title": (row.get("discogs_api_title") or "").strip(),
        "discogs_artist": (row.get("discogs_api_artist") or "").strip(),
        "reason": (row.get("reason") or "").strip(),
        "normalization_actions": (row.get("normalization_actions") or "").strip(),
        "status": "AUTO_CANDIDATE",
    }

def spread(rows: list[dict[str, str]], count: int) -> list[dict[str, str]]:
    if count >= len(rows):
        return rows[:]
    if count == 1:
        return [rows[len(rows)//2]]
    return [rows[round(i * (len(rows)-1) / (count-1))] for i in range(count)]

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, type=Path, help="Stage 2 v5 stage2_verification_report.tsv")
    p.add_argument("--limit", type=int, default=100, help="Batch size, 1..150 (default 100)")
    p.add_argument("--out-dir", type=Path, default=Path("stage3_output"))
    a = p.parse_args()
    if not 1 <= a.limit <= MAX_BATCH:
        p.error(f"--limit must be 1..{MAX_BATCH}")
    eligible, mbids, discogs_ids = [], set(), set()
    with a.input.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        required = {"mbid", "discogs_url", "discogs_release_id", "status"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            p.error("Input is not a Stage 2 v5 verification report.")
        for line, row in enumerate(reader, 2):
            if (row.get("status") or "").strip() != "AUTO_CANDIDATE":
                continue
            item = validate(row, line)
            if item["mbid"] in mbids or item["discogs_release_id"] in discogs_ids:
                raise ValueError(f"Duplicate AUTO_CANDIDATE on line {line}")
            mbids.add(item["mbid"]); discogs_ids.add(item["discogs_release_id"])
            eligible.append(item)
    if not eligible:
        raise SystemExit("No AUTO_CANDIDATE rows found.")
    selected = spread(eligible, min(a.limit, len(eligible)))
    if len({x["mbid"] for x in selected}) != len(selected):
        raise ValueError("Selection contains duplicate MBIDs.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "schema_version": 1,
        "batch_id": "discogsbot-v5-" + stamp,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_report": a.input.name,
        "source_engine": "DiscogsBot Stage 2 v5",
        "selection_method": "Evenly spaced through AUTO_CANDIDATE rows in report order",
        "eligible_auto_candidates": len(eligible),
        "selected_count": len(selected),
        "submission_policy": {"relationship": "Release -> URL -> Discogs", "link_type_id": 76, "edit_type": 90, "make_votable": True},
        "items": selected,
    }
    a.out_dir.mkdir(parents=True, exist_ok=True)
    (a.out_dir / "submission_batch.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = ["batch_index","source_line_no","mbid","mb_artist","mb_title","discogs_release_id","discogs_artist","discogs_title","mb_barcode","discogs_url","reason","normalization_actions","status"]
    with (a.out_dir / "submission_batch_preview.tsv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for i, item in enumerate(selected, 1):
            w.writerow({"batch_index": i, **item})
    print(f"AUTO_CANDIDATE={len(eligible)} | selected={len(selected)}")
    print(f"Output: {a.out_dir.resolve()}")
    print("No MusicBrainz requests made; no edits submitted.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
