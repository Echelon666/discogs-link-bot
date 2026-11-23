# Discogs Link Bot — V8.1 Phase 2 update

A conservative, read-only two-phase pipeline for finding and verifying Discogs releases for MusicBrainz releases.

**Neither script edits MusicBrainz.** They do not submit relationships, votes, approvals, or other changes. `AUTO_CANDIDATE` is a verification result, not an automatic MusicBrainz edit and not a guarantee that every physical detail of a pressing (for example, matrix, plant, sleeve, or artwork) is identical.

## Requirements

- Python 3.10 or newer (standard library only; no `pip` packages required).
- A local MusicBrainz Web Service mirror for Phase 1. The default URL is `http://localhost:5000`.
- Internet access to the Discogs API and the official MusicBrainz Discogs-link blacklist for Phase 2.
- A Discogs personal API token for Phase 2. Set `DISCOGS_TOKEN` in the environment or enter it at the masked prompt. On Windows, the prompt displays `*` characters rather than the token.

## Workflow

Run both scripts from the same working folder. Put the Album Hunter report in that folder, named `report.txt`, or pass its path as the first argument.

### Phase 1 — MusicBrainz snapshot only (unchanged V8)

```powershell
py .\discogs_barcode_builder.py .\report.txt
```

The report parser extracts unique release MBIDs from `-> ALBUM:` rows inside `POSSIBILITIES` sections. Phase 1 queries only the local MusicBrainz Web Service; it does not use the Discogs API and does not need a Discogs token.

It filters releases with:
- a `Digital Media` medium;
- an existing Discogs relationship (unless `--include-already-linked` is supplied);
- a missing or invalid barcode.

Eligible releases are saved with their full MusicBrainz response in the JSONL snapshot used by Phase 2. The default concurrency is 16 workers, with no artificial per-worker delay (`--workers` and `--mb-delay` can be overridden).

Pilot:

```powershell
py .\discogs_barcode_builder.py .\report.txt --limit 50
```

### Phase 2 — Direct Discogs barcode results + full V6 verification (V8.1)

```powershell
py .\discogs_bot_verify.py .\discogs_barcode_builder_v8_output\stage1_musicbrainz_v8.jsonl
```

When `DISCOGS_TOKEN` is not set, the script asks for it interactively. Characters are masked with asterisks; the token is not printed in the console or written to the output reports.

For every MBID, Phase 2:
1. Loads the complete MusicBrainz release data from the Phase 1 JSONL snapshot. It does **not** query the MusicBrainz Web Service again.
2. Fetches the official MusicBrainz Discogs-link blacklist.
3. Performs one Discogs barcode search request with `per_page=100` and reads the API's total result count.
4. If the total is **1–100**, uses only the direct barcode-search release results. It does not expand linked Discogs masters or fetch their versions.
5. If the total is **greater than 100**, skips the MBID with status `SKIPPED_TOO_MANY_RESULTS`. No release-detail requests are made for that MBID.
6. Every accepted direct result receives the complete V6 identity verification: artist, title, barcode, medium type and quantity, year, country, label, catalog number, existing MusicBrainz Discogs links and the official blacklist.

Exactly 100 results are allowed. Only a count greater than 100 is skipped.

The default minimum interval between Discogs API request starts is 1.1 seconds. A result search uses one request per barcode; each accepted direct result requires its own release-detail request unless it is already cached.

Pilot:

```powershell
py .\discogs_bot_verify.py .\discogs_barcode_builder_v8_output\stage1_musicbrainz_v8.jsonl --limit 50
```

## Output files

### Phase 1

Folder: `discogs_barcode_builder_v8_output\`

- `stage1_musicbrainz_v8.tsv` — eligible MBID/barcode pairs.
- `stage1_musicbrainz_v8.jsonl` — full MusicBrainz snapshots consumed by Phase 2.
- `stage1_musicbrainz_v8_summary.txt` — counts by Phase 1 status.

### Phase 2

Folder: `discogs_bot_verify_v8_output\`

- `stage2_verification_report.tsv` — detailed candidate checks; includes explicit `SKIPPED_TOO_MANY_RESULTS` rows for barcodes with more than 100 results.
- `stage2_summary.txt` — counts of completed, skipped and candidate statuses.
- `results.txt` — one tab-separated `MBID<TAB>Discogs URL` pair per MBID only when exactly one candidate for that MBID has the final `AUTO_CANDIDATE` status.

If more than one direct result for an MBID has `AUTO_CANDIDATE`, the script does not choose one arbitrarily: that MBID is omitted from `results.txt`, while all its candidate results remain visible in the detailed TSV report. Skipped MBIDs never enter `results.txt`.

The Phase 2 TSV, summary and `results.txt` are created at startup and refreshed every 100 completed or skipped MBID groups by default. They are also written on `Ctrl+C` and at normal completion.

## Resume and cache

Phase 1 keeps its existing progress and MusicBrainz cache. Phase 2 V8.1 uses a **new progress file** so results from the old master-expansion algorithm are not mixed with the new direct-barcode policy:

- Phase 1 progress: `discogs_barcode_builder_v8_progress.jsonl`; cache: `discogs_barcode_builder_v8_cache\musicbrainz_release\`.
- Phase 2 V8.1 progress: `discogs_bot_verify_v8_1_progress.jsonl`; cache: `discogs_bot_verify_v8_cache\discogs_api\`.

The Discogs API cache is deliberately reused, so previously fetched barcode searches and release details may be reused. The old `discogs_bot_verify_v8_progress.jsonl` is left untouched.

After `Ctrl+C`, rerun the same command to resume. Completed and skipped MBID groups are skipped. Use `--reset` only when intentionally clearing the selected phase's progress and state; its API cache is preserved.

## Safety and matching notes

- Phase 1 is MusicBrainz-only; Phase 2 uses Discogs plus the embedded Phase 1 MusicBrainz data.
- No PostgreSQL or `psql` access is required.
- No MusicBrainz edits are submitted in either phase.
- The 100-result cap is a workload safeguard, not an identity rule. More than 100 barcode hits are skipped rather than guessed or truncated.
- All direct results under the cap are checked by the conservative V6 verifier; no fuzzy matching or master-version expansion is used.
- Review `stage2_verification_report.tsv` before using any candidate outside this read-only workflow.
