# Discogs Link Bot — V8

A conservative, read-only two-phase pipeline for finding and verifying Discogs release candidates for MusicBrainz releases.

**The scripts do not edit MusicBrainz.** They do not submit relationships, votes, approvals, or other changes. `AUTO_CANDIDATE` is a verification result, not an automatic MusicBrainz edit and not a guarantee that every physical detail of a pressing (for example, matrix, plant, sleeve, or artwork) is identical.

## Requirements

- Python 3.10 or newer (standard library only; no `pip` packages required).
- A local MusicBrainz Web Service mirror for Phase 1. The default URL is `http://localhost:5000`.
- Internet access to the Discogs API and the official MusicBrainz Discogs-link blacklist for Phase 2.
- A Discogs personal API token for Phase 2. Set `DISCOGS_TOKEN` in the environment or enter it at the masked prompt. On Windows, the prompt displays `*` characters rather than the token.

## Workflow

Run both scripts from the same working folder. Put the Album Hunter report in that folder, named `report.txt`, or pass its path as the first argument.

### Phase 1 — MusicBrainz snapshot only

```powershell
py .\discogs_barcode_builder.py .\report.txt
```

The report parser extracts unique release MBIDs from `-> ALBUM:` rows inside `POSSIBILITIES` sections. Phase 1 queries only the local MusicBrainz Web Service; it does not use the Discogs API and does not need a Discogs token.

It filters releases with:

- a `Digital Media` medium;
- an existing Discogs relationship (unless `--include-already-linked` is supplied);
- a missing or invalid barcode.

Eligible releases are saved with their full MusicBrainz response in the JSONL snapshot used by Phase 2. The default concurrency is 16 workers, with no artificial per-worker delay (`--workers` and `--mb-delay` can be overridden).

Useful test run:

```powershell
py .\discogs_barcode_builder.py .\report.txt --limit 50
```

### Phase 2 — Discogs search and V6 verification

After Phase 1 has produced its JSONL snapshot, run:

```powershell
py .\discogs_bot_verify.py .\discogs_barcode_builder_v8_output\stage1_musicbrainz_v8.jsonl
```

When `DISCOGS_TOKEN` is not set, the script asks for it interactively. Characters are masked with asterisks; the token is not printed in the console or written to the output reports.

Phase 2:

1. Loads the complete MusicBrainz release data from the Phase 1 snapshot. It does **not** query the MusicBrainz Web Service again.
2. Fetches the official MusicBrainz Discogs-link blacklist.
3. Searches Discogs by barcode and follows all search-result pages.
4. If the barcode search returns multiple releases, expands every distinct linked Discogs master through all pages of its versions. A single direct search result is retained without master expansion.
5. Applies the conservative V7 medium/year/country summary prefilter, removing only definite contradictions; incomplete or ambiguous summary metadata is retained.
6. Runs the full V6 release-identity verification on the remaining Discogs releases.

The default minimum interval between Discogs API request starts is 1.1 seconds.

Useful test run:

```powershell
py .\discogs_bot_verify.py .\discogs_barcode_builder_v8_output\stage1_musicbrainz_v8.jsonl --limit 50
```

## Output files

### Phase 1

Folder: `discogs_barcode_builder_v8_output\`

- `stage1_musicbrainz_v8.tsv` — compact list of eligible MBID/barcode pairs.
- `stage1_musicbrainz_v8.jsonl` — full MusicBrainz snapshots consumed by Phase 2.
- `stage1_musicbrainz_v8_summary.txt` — counts by Phase 1 status.

### Phase 2

Folder: `discogs_bot_verify_v8_output\`

- `stage2_verification_report.tsv` — detailed candidate-by-candidate verification results.
- `stage2_summary.txt` — summary counts and run metadata.
- `results.txt` — one tab-separated `MBID<TAB>Discogs URL` pair per MBID when **exactly one** candidate for that MBID has the final `AUTO_CANDIDATE` status.

If more than one candidate for an MBID has `AUTO_CANDIDATE`, the script does not choose one arbitrarily: that MBID is omitted from `results.txt`, while all candidates remain visible in the detailed TSV report.

The Phase 2 TSV, summary, and `results.txt` are created at startup and refreshed every 100 completed MBID groups by default. They are also written when the run is interrupted with `Ctrl+C` or finishes normally.

## Resume and cache

Both phases keep append-only progress JSONL files and matching state files in the working folder. API responses are cached separately:

- Phase 1 progress: `discogs_barcode_builder_v8_progress.jsonl`; cache: `discogs_barcode_builder_v8_cache\musicbrainz_release\`.
- Phase 2 progress: `discogs_bot_verify_v8_progress.jsonl`; cache: `discogs_bot_verify_v8_cache\discogs_api\`.

After `Ctrl+C`, rerun the same command to resume. Completed MBID groups are skipped and cached API responses can be reused. Use `--reset` only when you intentionally want to clear that phase's progress and state; its API cache is preserved.

## Safety and matching notes

- Phase 1 is MusicBrainz-only; Phase 2 uses Discogs plus the embedded Phase 1 MusicBrainz data.
- No PostgreSQL or `psql` access is required.
- No MusicBrainz edits are submitted in either phase.
- Matching is deliberately conservative. Missing or ambiguous Discogs summary fields are not treated as contradictions by the prefilter; the full verifier makes the final candidate classification.
- Review `stage2_verification_report.tsv` before using any candidate outside this read-only workflow.