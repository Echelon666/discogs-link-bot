# Discogs Link Bot — V7 candidate pipeline (read-only)

This development branch builds on the Stage 2 v6 release-identity verifier. It adds a multi-result Discogs candidate builder and a conservative MusicBrainz identity prefilter. The work remains strictly read-only.

**No MusicBrainz edits, votes, approvals, or relationship changes are submitted.** An AUTO_CANDIDATE is only a candidate that passed the implemented checks; it is not a guarantee that the exact pressing, matrix, plant, sleeve, or artwork is identical.

## What V7 adds

### Stage 1 — barcode search and candidate expansion

- Reads unique release MBIDs from -> ALBUM rows inside POSSIBILITIES sections of the supplied report.
- Retrieves MusicBrainz release data from the local MusicBrainz Web Service (default: http://localhost:5000).
- Searches Discogs by barcode and follows every barcode-search pagination page.
- When the search returns exactly one release, retains that release without expanding its master.
- When the search returns multiple releases, retains every direct result and expands every distinct associated Master through all pages of /masters/{master_id}/versions — the API equivalent of View all versions.
- Deduplicates Release IDs within each MBID.

### Identity prefilter

The builder uses MusicBrainz medium, year, and country only when MusicBrainz supplies a usable, unambiguous value for that field.

It removes a candidate only when the Discogs search/version summary proves a definite contradiction. Missing, ambiguous, generic, or incomplete Discogs summary values are retained for full Stage 2 verification. The prefilter does not try to compare physical medium quantities because summary data is not sufficiently reliable for that check.

### Stage 2 — V6 release verification

The verifier fetches the complete Discogs release and preserves the v6 checks for artist, title, barcode, medium format and physical quantities, release year, country/territory, label, catalog number, existing Discogs relationships, and the official blacklist.

Medium, year, and country are compared only when MusicBrainz supplies usable information for the corresponding field. If MusicBrainz has no information for a field, that field is marked NOT_PRESENT_IN_MUSICBRAINZ and skipped. If MusicBrainz does supply it, missing, ambiguous, unmappable, or conflicting Discogs information remains a manual-review outcome.

## Requirements

- Python 3.10+ (standard library only)
- A local MusicBrainz mirror exposing /ws/2, defaulting to http://localhost:5000
- A Discogs personal API token; the builder and PowerShell wrapper prompt with hidden input if DISCOGS_TOKEN is not already set

The scripts use the MusicBrainz Web Service, not PostgreSQL or psql. Uncached Discogs requests are separated by at least 1.1 seconds. Transient API failures use bounded retries and failures do not become positive matches.

## Additional Windows requirement — Docker Desktop (V7)

To run the V7 pipeline, Windows users following the Docker-based setup need:

- Docker Desktop installed and running.
- A working MusicBrainz mirror deployed using the [MusicBrainz Docker project](https://github.com/metabrainz/musicbrainz-docker).
- The MusicBrainz Web Service accessible at `http://localhost:5000`.

Both Stage 1 and Stage 2 retrieve MusicBrainz release data from this local mirror. They do not retrieve release data from the public MusicBrainz Web Service.

**External Internet access is also required** for:
- Stage 1: Discogs barcode searches and Master Versions requests.
- Stage 2: Discogs release verification requests.
- Stage 2: Downloading the official MusicBrainz Discogs-link blacklist (`https://musicbrainz.org/doc/Bots/Blacklist/Discogs_Links`).

The local MusicBrainz mirror must be running and responding before starting the pipeline.

Docker Desktop and the MusicBrainz mirror must be installed, configured, and started separately. The V7 scripts do not install or manage them.

Docker Desktop is specific to the Windows Docker setup. Users who already have a compatible local MusicBrainz Web Service running by another method do not need to install Docker Desktop.

## Run

Place report.txt next to the scripts and make sure the local MusicBrainz Web Service is available.

Run a 10,000-MBID Stage 1 pilot from a clean progress file:

    py .\discogs_barcode_builder.py .\report.txt --limit 10000 --reset

The API cache is preserved by --reset; only progress/output are reset. To resume or continue with all remaining MBIDs from the same report, rerun without --reset and without a limit:

    py .\discogs_barcode_builder.py .\report.txt

Run Stage 2 against the generated candidate TSV:

    .\run_discogs_bot_verify_v6.ps1 -Limit 50
    .\run_discogs_bot_verify_v6.ps1 -Limit 0

The wrapper defaults to discogs_barcode_builder_v6_output\stage1_v6_candidates.tsv. Reports and the dedicated Stage 2 cache are written to discogs_bot_verify_output_v6\.

## Output files

- stage1_v6_candidates.tsv — one row per retained Discogs Release candidate; direct input for Stage 2
- stage1_v6_candidates.json — detailed per-MBID search and expansion record
- stage1_v6_summary.txt — counts before/after identity prefilter and search/expansion status
- stage2_verification_report.tsv and stage2_summary.txt — detailed Stage 2 verification results

Local inputs, caches, progress files, and generated reports are excluded from Git by .gitignore. Do not commit private collection reports, API tokens, or cache files.

## Tests

Run offline tests:

    py -m unittest discover -s .\tests -v

The tests cover barcode-search pagination, Master Versions pagination, one-result and multi-result behavior, deduplication, progress recovery, the Stage 1 to Stage 2 handoff, and optional medium/year/country checks.

## Status

This is a development branch for evaluation. The V6 branch remains the baseline, and main is not changed by this branch. Community feedback has shown that metadata agreement alone does not prove the exact physical pressing. Treat all outputs as read-only review material.
