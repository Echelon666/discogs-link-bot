# Discogs Link Bot — V8.2

A complete, read-only two-phase workflow for finding and verifying Discogs releases for MusicBrainz releases. It is designed to run from a new, empty working folder and does not require any V8 progress, cache, output or helper files.

**Neither script edits MusicBrainz.** They do not submit relationships, votes, approvals or other changes. `AUTO_CANDIDATE` is a verification result, not an automatic MusicBrainz edit and not a guarantee that every physical detail of a pressing (matrix, plant, sleeve or artwork) is identical.

## What to download

Place these three files in one new folder:

- `discogs_barcode_builder.py` — Phase 1, MusicBrainz-only snapshot builder.
- `discogs_bot_verify.py` — Phase 2, Discogs primary-artist guard, barcode search and full V6 verification.
- `README.md` — these instructions.

No other Python modules or third-party packages are required. Python 3.10 or newer is supported.

## First run — from an empty folder

1. Create a new folder for Discogs Link Bot.
2. Copy the two `.py` files into that folder.
3. Put your Album Hunter report in the same folder and name it `report.txt`. Phase 1 uses this default filename automatically.
4. Make sure your local MusicBrainz mirror is running. The default Web Service address is `http://localhost:5000`.
5. Open PowerShell in the new folder.

### Phase 1 — MusicBrainz only

```powershell
py discogs_barcode_builder.py
```

Phase 1 reads unique release MBIDs from `-> ALBUM:` rows inside `POSSIBILITIES` sections. It queries only the local MusicBrainz Web Service; it does not contact Discogs and does not need a Discogs token.

It excludes releases that have:
- a `Digital Media` medium;
- an existing Discogs relationship (unless `--include-already-linked` is used);
- a missing or invalid barcode.

It writes a full MusicBrainz JSONL snapshot for Phase 2 and a compact TSV index.

Useful pilot:

```powershell

```

### Phase 2 — Discogs and full V6 verification

After Phase 1 finishes, run the command below. Phase 2 automatically reads the default JSONL snapshot created by Phase 1:

```powershell
py discogs_bot_verify.py
```

When `DISCOGS_TOKEN` is not set in the environment, the script prompts for it. On Windows, typed or pasted characters are displayed as asterisks. The token is not written to progress, cache or report files.

For each MBID, Phase 2 performs one Discogs barcode search and checks the API's **total result count**:

| Discogs total results | Action |
|---:|---|
| 0 | Mark as `DONE_NO_DISCOGS_RESULTS` |
| 1–100 | Run the Artist Guard; if it passes, verify every direct barcode-search release |
| 101 or more | Skip the MBID as `SKIPPED_TOO_MANY_RESULTS` |

The limit is based on `pagination.items` from Discogs, not the number of rows visible on the first page. Exactly 100 results are allowed.

**V8.2 Artist Guard runs before full V6 verification.** For an MBID with 1–100 direct barcode results, Phase 2 checks the release-level primary Artist IDs from the Discogs release details. V6 verification starts only when the candidates have exactly one distinct, verifiable primary Artist ID in common. If multiple distinct primary Artist IDs are found, the MBID is skipped as `SKIPPED_MULTIPLE_ARTISTS`. If any candidate's primary-artist identity is missing, malformed or otherwise unverifiable, the MBID fails closed as `SKIPPED_ARTIST_UNVERIFIABLE`. In either case, V6 verification is not started for that MBID.

**Master-version expansion is disabled.** Phase 2 does not fetch linked master versions. This prevents a small barcode search from ballooning into thousands of unrelated pressings. Only MBIDs that pass the Artist Guard proceed to the full V6 verification: artist, title, barcode, medium type and quantity, year, country, label, catalog number, existing MusicBrainz Discogs links and the official MusicBrainz Discogs-link blacklist.

The default minimum interval between Discogs API request starts is 1.1 seconds. MBIDs skipped because the total exceeds 100 require only the initial barcode-search request. For results between 1 and 100, release details are requested for the Artist Guard; when it finds multiple or unverifiable primary-artist identities, it stops that MBID before V6 verification.

Pilot:

```powershell

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

Folder: `discogs_bot_verify_v8_2_output\`

- `stage2_verification_report.tsv` — candidate-by-candidate V6 results plus explicit audit rows for MBIDs skipped by the 100-result limit or Artist Guard.
- `stage2_summary.txt` — counts of completed, skipped and candidate statuses.
- `results.txt` — one tab-separated `MBID<TAB>Discogs URL` pair when exactly one direct candidate for that MBID has the final `AUTO_CANDIDATE` status.

If two or more candidates for one MBID have `AUTO_CANDIDATE`, no arbitrary winner is selected and that MBID is omitted from `results.txt`. Skipped MBIDs never enter `results.txt`.

Phase 2 progress and cache:

- `discogs_bot_verify_v8_2_progress.jsonl`
- `discogs_bot_verify_v8_2_cache\discogs_api\`

The progress JSONL is updated after each completed MBID group. The TSV, summary and `results.txt` are refreshed every 100 completed or skipped groups, on Ctrl+C and at normal completion.

## Resume and clean reset

After Ctrl+C, rerun the same Phase 2 command. Completed and skipped MBIDs are not repeated, and cached API responses are reused.

To intentionally start a phase again from zero, add the optional `--reset` switch to that phase's command. Reset removes that phase's progress and state files but preserves its API cache. Delete the relevant cache folder manually only when you explicitly want to discard cached responses too.

## Matching and safety notes

- Phase 1 is MusicBrainz-only. Phase 2 uses the complete snapshot from Phase 1 and does not query the MusicBrainz Web Service again.
- Neither phase makes MusicBrainz edits.
- More than 100 barcode hits are skipped rather than truncated, expanded or guessed.
- Direct results below the limit pass through the V8.2 Artist Guard first; only a single, verifiable primary-artist identity proceeds to full V6 checks.
- Multiple or unverifiable primary-artist identities are skipped before V6 (`SKIPPED_MULTIPLE_ARTISTS` / `SKIPPED_ARTIST_UNVERIFIABLE`).
- No fuzzy matching or Master-version expansion is used.
- Review `stage2_verification_report.tsv` before using any candidate outside this read-only workflow.
