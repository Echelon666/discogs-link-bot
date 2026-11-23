# Discogs Link Bot — V8.1

A complete, read-only two-phase workflow for finding and verifying Discogs releases for MusicBrainz releases. It is designed to run from a new, empty working folder and does not require any V8 progress, cache, output or helper files.

**Neither script edits MusicBrainz.** They do not submit relationships, votes, approvals or other changes. `AUTO_CANDIDATE` is a verification result, not an automatic MusicBrainz edit and not a guarantee that every physical detail of a pressing (matrix, plant, sleeve or artwork) is identical.

## What to download

Place these three files in one new folder:

- `discogs_barcode_builder.py` — Phase 1, MusicBrainz-only snapshot builder.
- `discogs_bot_verify.py` — Phase 2, Discogs barcode search and full V6 verification.
- `README.md` — these instructions.

No other Python modules or third-party packages are required. Python 3.10 or newer is supported.

## First run — from an empty folder

1. Create a new folder for Discogs Link Bot.
2. Copy the two `.py` files into that folder.
3. Put your Album Hunter report in the same folder and name it `report.txt` (or pass its path in the command).
4. Make sure your local MusicBrainz mirror is running. The default Web Service address is `http://localhost:5000`.
5. Open PowerShell in the new folder.

### Phase 1 — MusicBrainz only

```powershell
py .\discogs_barcode_builder.py .\report.txt
```

Phase 1 reads unique release MBIDs from `-> ALBUM:` rows inside `POSSIBILITIES` sections. It queries only the local MusicBrainz Web Service; it does not contact Discogs and does not need a Discogs token.

It excludes releases that have:
- a `Digital Media` medium;
- an existing Discogs relationship (unless `--include-already-linked` is used);
- a missing or invalid barcode.

It writes a full MusicBrainz JSONL snapshot for Phase 2 and a compact TSV index.

Useful pilot:

```powershell
py .\discogs_barcode_builder.py .\report.txt --limit 50
```

### Phase 2 — Discogs and full V6 verification

After Phase 1 finishes, run:

```powershell
py .\discogs_bot_verify.py .\discogs_barcode_builder_v8_1_output\stage1_musicbrainz_v8_1.jsonl
```

When `DISCOGS_TOKEN` is not set in the environment, the script prompts for it. On Windows, typed or pasted characters are displayed as asterisks. The token is not written to progress, cache or report files.

For each MBID, Phase 2 performs one Discogs barcode search and checks the API's **total result count**:

| Discogs total results | Action |
|---:|---|
| 0 | Mark as `DONE_NO_DISCOGS_RESULTS` |
| 1–100 | Verify every direct barcode-search release |
| 101 or more | Skip the MBID as `SKIPPED_TOO_MANY_RESULTS` |

The limit is based on `pagination.items` from Discogs, not the number of rows visible on the first page. Exactly 100 results are allowed.

**Master-version expansion is disabled.** Phase 2 does not fetch linked master versions. This prevents a small barcode search from ballooning into thousands of unrelated pressings. Every accepted direct result receives the full V6 verification: artist, title, barcode, medium type and quantity, year, country, label, catalog number, existing MusicBrainz Discogs links and the official MusicBrainz Discogs-link blacklist. No preliminary identity prefilter is applied.

The default minimum interval between Discogs API request starts is 1.1 seconds. A skipped MBID requires only its initial barcode-search request; no release-detail requests are sent for it.

Pilot:

```powershell
py .\discogs_bot_verify.py .\discogs_barcode_builder_v8_1_output\stage1_musicbrainz_v8_1.jsonl --limit 50
```

## Files created automatically

You do not need to create these folders or files yourself. The scripts create them on first run.

### Phase 1

Folder: `discogs_barcode_builder_v8_1_output\`

- `stage1_musicbrainz_v8_1.tsv` — eligible MBID/barcode pairs.
- `stage1_musicbrainz_v8_1.jsonl` — full MusicBrainz snapshots consumed by Phase 2.
- `stage1_musicbrainz_v8_1_summary.txt` — Phase 1 counts.

Phase 1 progress and cache:

- `discogs_barcode_builder_v8_1_progress.jsonl`
- `discogs_barcode_builder_v8_1_cache\musicbrainz_release\`

### Phase 2

Folder: `discogs_bot_verify_v8_1_output\`

- `stage2_verification_report.tsv` — candidate-by-candidate V6 results plus explicit rows for MBIDs skipped due to more than 100 barcode hits.
- `stage2_summary.txt` — counts of completed, skipped and candidate statuses.
- `results.txt` — one tab-separated `MBID<TAB>Discogs URL` pair when exactly one direct candidate for that MBID has the final `AUTO_CANDIDATE` status.

If two or more candidates for one MBID have `AUTO_CANDIDATE`, no arbitrary winner is selected and that MBID is omitted from `results.txt`. Skipped MBIDs never enter `results.txt`.

Phase 2 progress and cache:

- `discogs_bot_verify_v8_1_progress.jsonl`
- `discogs_bot_verify_v8_1_cache\discogs_api\`

The progress JSONL is updated after each completed MBID group. The TSV, summary and `results.txt` are refreshed every 100 completed or skipped groups, on Ctrl+C and at normal completion.

## Resume and clean reset

After Ctrl+C, rerun the same Phase 2 command. Completed and skipped MBIDs are not repeated, and cached API responses are reused.

To intentionally start a phase again from zero, use its `--reset` option:

```powershell
py .\discogs_barcode_builder.py .\report.txt --reset
py .\discogs_bot_verify.py .\discogs_barcode_builder_v8_1_output\stage1_musicbrainz_v8_1.jsonl --reset
```

Reset removes that phase's progress and state files but preserves its API cache. Delete the relevant cache folder manually only when you explicitly want to discard cached responses too.

## Matching and safety notes

- Phase 1 is MusicBrainz-only. Phase 2 uses the complete snapshot from Phase 1 and does not query the MusicBrainz Web Service again.
- Neither phase makes MusicBrainz edits.
- More than 100 barcode hits are skipped rather than truncated, expanded or guessed.
- All direct results below the limit receive the full V6 identity checks. No fuzzy matching, Master expansion or preliminary V7 identity filter is used.
- Review `stage2_verification_report.tsv` before using any candidate outside this read-only workflow.
