#!/usr/bin/env python3
"""Discogs Link Bot V8.1 — Phase 1: MusicBrainz-only snapshot builder.

Reads unique MBIDs from Album Hunter report.txt and talks ONLY to the local
MusicBrainz Web Service. It does not import, prompt for, or contact Discogs.

Eligible physical releases are written to:
  stage1_musicbrainz_v8_1.tsv       compact, human-readable index
  stage1_musicbrainz_v8_1.jsonl     full MusicBrainz snapshots for Phase 2

The append-only progress file permits safe resume. No PostgreSQL/psql and no
MusicBrainz edits are used.

Requirements: Python 3.10+ (standard library only).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_MB_BASE_URL = "http://localhost:5000"
DEFAULT_USER_AGENT = "DiscogsLinkBot/0.3 (read-only MusicBrainz snapshot builder)"
DEFAULT_MB_DELAY = 0.0
DEFAULT_WORKERS = 16
CACHE_MAX_AGE_SECONDS = 6 * 60 * 60

MBID_RE = re.compile(
    r"\bMBID\s*:\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b",
    re.IGNORECASE,
)
TRACK_HEADER_RE = re.compile(r"^\s*\[\d+\]\s+TRACK\s*:", re.IGNORECASE)
POSSIBILITIES_RE = re.compile(r"^\s*POSSIBILITIES\s*:\s*$", re.IGNORECASE)
ARROW_ALBUM_RE = re.compile(r"^\s*->\s*ALBUM\s*:", re.IGNORECASE)

class HTTPStatusError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = "", headers: Any = None) -> None:
        super().__init__(f"HTTP {status} for {url}: {body[:240]}")
        self.status = status
        self.url = url
        self.body = body
        self.headers = headers or {}

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
            if time.time() - float(wrapped["fetched_at"]) > self.max_age:
                path.unlink(missing_ok=True)
                return None
            data = wrapped.get("data")
            return data if isinstance(data, dict) else None
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            return None

    def put(self, key: str, data: dict[str, Any]) -> None:
        path = self._path(key)
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps({"fetched_at": time.time(), "data": data}, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)

class StartRateLimiter:
    """Enforce a minimum start-to-start interval independently per worker thread.

    The local MusicBrainz mirror is queried concurrently. A single global
    limiter would cap the whole worker pool at 1 / interval. Thread-local
    timestamps preserve the configured pacing independently per worker.
    """
    def __init__(self, interval: float) -> None:
        self.interval = max(0.0, interval)
        self._local = threading.local()

    def acquire(self) -> None:
        now = time.monotonic()
        last_start = getattr(self._local, "last_start", 0.0)
        wait = self.interval - (now - last_start)
        if wait > 0:
            time.sleep(wait)
        self._local.last_start = time.monotonic()

def http_get_json(url: str, timeout: int = 45, retries: int = 5) -> dict[str, Any]:
    headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
    last_error: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
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
                print(f"  MusicBrainz HTTP {exc.code}; retry in {delay:.0f}s", flush=True)
                time.sleep(delay)
                last_error = error
                continue
            raise error
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt + 1 >= retries:
                break
            delay = min(15, 2 ** attempt)
            print(f"  MusicBrainz network/response error; retry in {delay}s: {exc}", flush=True)
            time.sleep(delay)
    raise RuntimeError(f"MusicBrainz request failed after {retries} attempts: {url}: {last_error}")

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
            if match:
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
        if host in {"discogs.com", "www.discogs.com"} and (path.startswith("/release/") or path.startswith("/master/")):
            return True
    return False

def has_digital_media_format(release: dict[str, Any]) -> bool:
    media = release.get("media")
    if not isinstance(media, list):
        return False
    for medium in media:
        if isinstance(medium, dict):
            media_format = " ".join(str(medium.get("format") or "").split()).casefold()
            if media_format == "digital media":
                return True
    return False

def fetch_mb_release(mbid: str, base_url: str, cache: JsonCache, limiter: StartRateLimiter) -> dict[str, Any]:
    cached = cache.get(mbid)
    if cached is not None:
        return cached
    query = urllib.parse.urlencode({"inc": "artist-credits+url-rels+labels+media", "fmt": "json"})
    url = f"{base_url.rstrip('/')}/ws/2/release/{urllib.parse.quote(mbid)}?{query}"
    limiter.acquire()
    data = http_get_json(url, timeout=45)
    release = data.get("release")
    if not isinstance(release, dict):
        release = data if data.get("id") else None
    if not isinstance(release, dict):
        raise RuntimeError(f"Unexpected MusicBrainz response for MBID {mbid}")
    returned = str(release.get("id") or "").strip().lower()
    if returned and returned != mbid:
        raise RuntimeError(f"MusicBrainz returned MBID {returned} for requested {mbid}")
    cache.put(mbid, release)
    return release

def classify_release(mbid: str, report_position: int, release: dict[str, Any] | None, include_already_linked: bool) -> dict[str, Any]:
    group: dict[str, Any] = {"mbid": mbid, "report_position": report_position, "barcode": "", "mb_artist": "", "mb_title": "", "status": "ERROR", "reason": "UNCLASSIFIED_ERROR"}
    if release is None:
        group.update(status="SKIP_MBID_NOT_FOUND", reason="LOCAL_MUSICBRAINZ_404")
        return group
    group["mb_artist"] = mb_artist_credit(release)
    group["mb_title"] = str(release.get("title") or "")
    if has_digital_media_format(release):
        group.update(status="SKIP_DIGITAL_MEDIA", reason="MUSICBRAINZ_RELEASE_HAS_DIGITAL_MEDIA_MEDIUM")
    elif has_discogs_relation(release) and not include_already_linked:
        group.update(status="SKIP_ALREADY_LINKED", reason="EXISTING_DISCOGS_RELATION")
    else:
        barcode = normalize_barcode(release.get("barcode"))
        group["barcode"] = barcode
        if not barcode:
            group.update(status="SKIP_NO_BARCODE", reason="MUSICBRAINZ_BARCODE_MISSING_OR_INVALID")
        else:
            group.update(status="READY_FOR_DISCOGS", reason="PHYSICAL_RELEASE_WITH_BARCODE_AND_NO_EXISTING_DISCOGS_LINK", mb_release=release)
    return group

def load_progress(path: Path) -> dict[str, dict[str, Any]]:
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
            if line_number == len(lines):
                path.write_text("\n".join(valid_lines) + ("\n" if valid_lines else ""), encoding="utf-8", newline="\n")
                break
            raise RuntimeError(f"Progress file has invalid JSON at line {line_number}: {path}")
        valid_lines.append(line)
        mbid = str(row.get("mbid") or "").strip().lower()
        if mbid:
            records[mbid] = row
    return records

def append_progress(stream, group: dict[str, Any], count: int) -> None:
    stream.write(json.dumps(group, ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()
    if count % 100 == 0:
        os.fsync(stream.fileno())

def write_outputs(out_dir: Path, progress: dict[str, dict[str, Any]], report_path: Path, total: int, limit: int) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    groups = sorted(progress.values(), key=lambda r: int(r.get("report_position") or 0))
    ready = [g for g in groups if g.get("status") == "READY_FOR_DISCOGS" and isinstance(g.get("mb_release"), dict)]
    tsv_path = out_dir / "stage1_musicbrainz_v8_1.tsv"
    fields = ["mbid", "barcode", "mb_artist", "mb_title", "report_position", "status", "reason"]
    with tsv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for g in ready:
            writer.writerow({k: g.get(k, "") for k in fields})
    snapshot_path = out_dir / "stage1_musicbrainz_v8_1.jsonl"
    temp = snapshot_path.with_suffix(".jsonl.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as f:
        for g in ready:
            f.write(json.dumps(g, ensure_ascii=False, separators=(",", ":")) + "\n")
    temp.replace(snapshot_path)
    counts = Counter(str(g.get("status") or "UNKNOWN") for g in groups)
    summary_path = out_dir / "stage1_musicbrainz_v8_summary.txt"
    with summary_path.open("w", encoding="utf-8-sig") as f:
        f.write("DISCOGS LINK BOT V8 — PHASE 1: MUSICBRAINZ ONLY\n")
        f.write("=" * 58 + "\n")
        f.write(f"Source report: {report_path}\nUnique MBIDs in report: {total:,}\nSelected limit: {limit or 'ALL'}\n")
        f.write(f"MBIDs with progress records: {len(groups):,}\nReady for Discogs Phase 2: {len(ready):,}\n\nSTATUS COUNTS\n")
        for status, count in sorted(counts.items()):
            f.write(f"{status}: {count:,}\n")
        f.write("\nOnly local MusicBrainz Web Service was used. No Discogs API calls, no PostgreSQL/psql, and no MusicBrainz edits.\n")
        f.write(f"Phase 2 snapshot: {snapshot_path.name}\n")
    return tsv_path, snapshot_path, summary_path

def resolve_path(value: str | Path, script_dir: Path) -> Path:
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = script_dir / p
    return p.resolve()

def main() -> int:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="V8.1 Phase 1: read and filter MusicBrainz releases only; no Discogs API.")
    parser.add_argument("report", nargs="?", default="report.txt", help="Album Hunter report (default: report.txt beside this script)")
    parser.add_argument("--mb-base-url", default=os.environ.get("MUSICBRAINZ_BASE_URL", DEFAULT_MB_BASE_URL))
    parser.add_argument("--out-dir", default="discogs_barcode_builder_v8_1_output")
    parser.add_argument("--cache-dir", default="discogs_barcode_builder_v8_1_cache/musicbrainz_release")
    parser.add_argument("--progress", default="discogs_barcode_builder_v8_1_progress.jsonl")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Concurrent local MusicBrainz requests (default: 16)")
    parser.add_argument("--mb-delay", type=float, default=DEFAULT_MB_DELAY, help="Minimum interval between MusicBrainz request starts per worker (default: 0.0s)")
    parser.add_argument("--batch-size", type=int, default=500, help="Number of pending MBIDs submitted per worker batch")
    parser.add_argument("--limit", type=int, default=0, help="First N MBIDs (0 = all)")
    parser.add_argument("--include-already-linked", action="store_true")
    parser.add_argument("--reset", action="store_true", help="Reset progress/state; preserve API cache")
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 16:
        parser.error("--workers must be between 1 and 16")
    if args.mb_delay < 0 or args.batch_size < 1 or args.limit < 0:
        parser.error("--mb-delay, --batch-size and --limit must be non-negative (batch-size must be at least 1)")

    report_path = resolve_path(args.report, script_dir)
    out_dir = resolve_path(args.out_dir, script_dir)
    cache_root = resolve_path(args.cache_dir, script_dir)
    progress_path = resolve_path(args.progress, script_dir)
    state_path = progress_path.with_suffix(progress_path.suffix + ".state.json")
    if not report_path.is_file():
        print(f"ERROR: report file not found: {report_path}", file=sys.stderr)
        return 2
    try:
        mbids = extract_unique_mbids(report_path)
    except OSError as exc:
        print(f"ERROR reading report: {exc}", file=sys.stderr)
        return 2
    if not mbids:
        print("ERROR: no MBIDs found in -> ALBUM rows inside POSSIBILITIES sections.", file=sys.stderr)
        return 2
    selected = mbids[:args.limit] if args.limit else mbids
    signature = {"path": str(report_path), "size": report_path.stat().st_size, "mtime_ns": report_path.stat().st_mtime_ns}
    if args.reset:
        progress_path.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
    if progress_path.exists() and not state_path.exists():
        print("ERROR: progress exists but its state file is missing. Use --reset or another --progress path.", file=sys.stderr)
        return 2
    if state_path.exists():
        try:
            old = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"ERROR: cannot read progress state: {state_path}", file=sys.stderr)
            return 2
        if old != signature:
            print("ERROR: progress belongs to a different report. Use --reset or a new --progress path.", file=sys.stderr)
            return 2
    else:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(signature, ensure_ascii=False, indent=2), encoding="utf-8")

    progress = load_progress(progress_path)
    done = {m for m, g in progress.items() if str(g.get("status") or "") != "ERROR"}
    remaining = [(pos, mbid) for pos, mbid in enumerate(selected, 1) if mbid not in done]
    cache = JsonCache(cache_root)
    limiter = StartRateLimiter(args.mb_delay)
    print("=" * 72)
    print("DISCOGS BARCODE BUILDER V8 — PHASE 1: MUSICBRAINZ ONLY")
    print("=" * 72)
    print(f"Report: {report_path}\nUnique MBIDs in report: {len(mbids):,}\nSelected MBIDs: {len(selected):,}")
    print(f"Already completed: {len(done):,}\nRemaining this run: {len(remaining):,}")
    print(f"Local MusicBrainz Web Service: {args.mb_base_url}")
    print(f"Workers: {args.workers} | MB minimum interval: {args.mb_delay:.2f}s per worker")
    print("Discogs API: NOT USED | Token: NOT REQUIRED")
    print("Filters: Digital Media, existing Discogs relations, missing/invalid barcode")
    print("Read-only. No PostgreSQL/psql and no MusicBrainz edits.\n")

    started = time.monotonic()
    processed = 0
    errors = 0
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
    fatal = False
    try:
        with progress_path.open("a", encoding="utf-8", newline="\n") as progress_stream:
            try:
                for batch_start in range(0, len(remaining), args.batch_size):
                    batch = remaining[batch_start:batch_start + args.batch_size]
                    future_map = {
                        executor.submit(fetch_mb_release, mbid, args.mb_base_url, cache, limiter): (position, mbid)
                        for position, mbid in batch
                    }
                    for future in concurrent.futures.as_completed(future_map):
                        position, mbid = future_map[future]
                        try:
                            release = future.result()
                            group = classify_release(mbid, position, release, args.include_already_linked)
                        except HTTPStatusError as exc:
                            if exc.status == 404:
                                group = classify_release(mbid, position, None, args.include_already_linked)
                            else:
                                group = {"mbid": mbid, "report_position": position, "barcode": "", "status": "ERROR", "reason": f"HTTP_{exc.status}: {exc.body[:200]}"}
                                if exc.status in (401, 403):
                                    fatal = True
                                    print(f"FATAL: Local MusicBrainz API denied access (HTTP {exc.status}); stopping.", file=sys.stderr)
                        except Exception as exc:
                            message = str(exc).replace("\t", " ").replace("\r", " ").replace("\n", " ")[:500]
                            group = {"mbid": mbid, "report_position": position, "barcode": "", "status": "ERROR", "reason": message}
                        processed += 1
                        if group.get("status") == "ERROR":
                            errors += 1
                        append_progress(progress_stream, group, processed)
                        progress[mbid] = group
                        if fatal:
                            for pending in future_map:
                                pending.cancel()
                            break
                        if processed % 100 == 0 or processed == len(remaining):
                            elapsed = max(time.monotonic() - started, 0.001)
                            speed = processed / elapsed
                            eta = (len(remaining) - processed) / speed if speed else 0
                            counts = Counter(str(g.get("status") or "UNKNOWN") for g in progress.values())
                            print(
                                f"[{processed:,}/{len(remaining):,}] ready={counts['READY_FOR_DISCOGS']:,} "
                                f"digital_media={counts['SKIP_DIGITAL_MEDIA']:,} already_linked={counts['SKIP_ALREADY_LINKED']:,} "
                                f"no_barcode={counts['SKIP_NO_BARCODE']:,} not_found={counts['SKIP_MBID_NOT_FOUND']:,} "
                                f"errors={counts['ERROR']:,} | {speed:.2f} MBID/s | ETA {eta/60:.1f} min",
                                flush=True,
                            )
                    if fatal:
                        break
            except KeyboardInterrupt:
                print("\nInterrupted. Progress is saved; rerun this command to resume.", flush=True)
                fatal = True
                for future in future_map:
                    future.cancel()
            finally:
                progress_stream.flush()
                os.fsync(progress_stream.fileno())
    finally:
        executor.shutdown(wait=not fatal, cancel_futures=fatal)

    tsv_path, snapshot_path, summary_path = write_outputs(out_dir, progress, report_path, len(mbids), args.limit)
    counts = Counter(str(g.get("status") or "UNKNOWN") for g in progress.values())
    ready_count = sum(1 for g in progress.values() if g.get("status") == "READY_FOR_DISCOGS")
    print("\n" + "=" * 72)
    print("V8 PHASE 1 FINISHED" if not fatal else "V8 PHASE 1 STOPPED — PROGRESS PRESERVED")
    print("=" * 72)
    print(f"MBIDs in report: {len(mbids):,}\nCompleted MBIDs: {sum(1 for g in progress.values() if g.get('status') != 'ERROR'):,}")
    print(f"Ready for Discogs Phase 2: {ready_count:,}")
    print(f"TSV: {tsv_path}\nJSONL snapshot for Phase 2: {snapshot_path}\nSummary: {summary_path}")
    print("No Discogs API calls. No MusicBrainz edits.")
    return 2 if fatal or errors else 0

if __name__ == "__main__":
    raise SystemExit(main())