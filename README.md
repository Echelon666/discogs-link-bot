# Discogs Link Bot — Stage 2 v6 (Read-Only Verification)

A conservative, auditable toolset for checking whether a proposed Discogs release URL matches one specific MusicBrainz release. Stage 2 v6 retains the v5 checks and adds mandatory release-identity checks for **medium format/count, release year, and release country**.

**Stage 1 and Stage 2 are read-only.** They do not submit MusicBrainz edits, vote, approve, or cancel edits. `AUTO_CANDIDATE` means that all checks implemented by the verifier passed; it is not, by itself, permission to create a public edit.

## What it does

- **Stage 1 — input preflight:** validates a four-column TSV input, checks MBID and Discogs release URL formats, detects duplicate MBIDs and Discogs-release-ID collisions, and writes review reports. No network requests are made.
- **Stage 2 v6 — release verification:** compares one MusicBrainz Release with the specific Discogs Release URL supplied in the Stage 1 candidate file.
- **Metadata checks:** artist credit, title, barcode, existing Discogs relationships, official MusicBrainz Discogs-release blacklist, label, and catalog number.
- **Release-identity checks (new in v6):** medium carrier/physical quantity, specific release year, and specific release country/area.
- **Conservative normalization:** explicit typography, title-punctuation, catalog whitespace, and limited label-suffix equivalences. No fuzzy matching and no deletion of meaningful words, accents, digits, or word order.
- **Auditable output:** TSV report with individual checks, normalization actions, details, and reason-coded statuses.

Do not treat any `AUTO_CANDIDATE` as guaranteed correct. Community-maintained metadata can contain errors on either service. A report is a selection aid, not a substitute for a carefully reviewed editing workflow.

## Requirements

- Python 3.10 or newer.
- Windows PowerShell for the included `.ps1` wrapper; the Python verifier itself uses the standard library only.
- A local MusicBrainz mirror exposing `/ws/2`, normally `http://localhost:5000`.
- A Discogs personal API token. The PowerShell wrapper accepts it as hidden input and removes the temporary environment variable at exit.
- Internet access to the Discogs API and the official MusicBrainz Discogs-release blacklist.

Uncached Discogs requests are spaced by at least 1.1 seconds. Transient HTTP failures use bounded retries. API and parsing failures fail closed and never become `AUTO_CANDIDATE`.

## Input format

The Stage 1 input is a UTF-8 TSV file with a header and columns including `mbid`, `barcode`, `discogs_url`, and `discogs_release_id`. The verifier also accepts a reviewed candidate TSV exported from a previous Stage 2 report, provided those columns are present. The exact selected input file is processed; v6 does not silently choose a new candidate set.

Keep personal input lists, reports, API tokens, and cache files out of the public repository.

## Run Stage 1 — preflight

Open PowerShell in the project directory:

```powershell
py .\discogs_bot_preflight.py .\your_input_file.txt
```

By default, reports are written to `discogs_bot_preflight_output\`.

## Run Stage 2 v6 — recommended verifier

Run a small pilot first:

```powershell
.\run_discogs_bot_verify_v6.ps1
```

Run all Stage 1 candidates after reviewing the pilot:

```powershell
.\run_discogs_bot_verify_v6.ps1 -Limit 0
```

The wrapper prompts for the Discogs token using hidden input. It does not put the token in command history or source code.

### Recheck an already-reviewed set without changing its membership

For example, to recheck the same 100 releases that were selected and manually reviewed from v5:

1. Copy the private `stage2_AUTO_CANDIDATE_first100.tsv` into the project directory.
2. Run:

```powershell
.\run_discogs_bot_verify_v6.ps1 -InputFile ".\stage2_AUTO_CANDIDATE_first100.tsv" -Limit 0
```

This verifies all rows in that exact 100-row file, preserving its MBIDs and order. It does **not** select the first 100 records again from the full candidate list.

### Legacy v5

`discogs_bot_verify.py` and `run_discogs_bot_verify.ps1` are retained for reproducibility of earlier v5 runs. Use the v6 wrapper for new candidate selection and rechecks because v5 does not check medium, year, or country.

## v6 release-identity rules

### Medium format and quantity

MusicBrainz medium `format` values are compared with Discogs release `formats[].name` and relevant subtype descriptions. Physical carrier quantities must agree. `Digital Media` ↔ `File` compares digital presence only: Discogs `File` quantity is a file/track count, not a physical-disc count, so it is deliberately ignored. Packaging/aggregate entries such as `Box Set` and `All Media` are reported but are not counted as additional carriers. CD carrier variants such as HDCD/SHM-CD and vinyl size variants normalize to their physical carrier. DVD-Audio and DVD-Video remain distinct; a generic DVD without a matching subtype description is manual/unmappable. Unknown formats and missing/invalid physical Discogs quantities go to manual review. Multi-disc physical counts must agree.

### Release year

The year of the specific MusicBrainz Release `date` is used; release-event years are a fallback only when the primary date is absent and exactly one year is available. Discogs `year` is checked against the year from `released` when both are present. Conflicting values, missing years, or different years go to manual review. Release Group first-release dates are not used.

### Release country/area

The MusicBrainz Release country code is compared with the Discogs `country` field after conservative ISO alpha-2 mapping. If MusicBrainz has no direct country, release-event area codes are used only when exactly one unique country code is available. Exact equivalents include `UK` ↔ `GB`, `Europe` ↔ `XE`, and `Worldwide` ↔ `XW`. A broad territory such as Europe/Worldwide, or a known compound territory such as `UK & Europe`, is not treated as a definite mismatch against a country it may include: it is routed to `MANUAL_COUNTRY_AMBIGUOUS`. Only demonstrably disjoint country/territory values are classified as mismatches. Unknown regions and ambiguous multi-country release events remain manual.

### Statuses added in v6

- `MANUAL_MEDIA_FORMAT_MISSING`, `MANUAL_MEDIA_FORMAT_UNMAPPABLE`, `MANUAL_MEDIA_FORMAT_MISMATCH`
- `MANUAL_YEAR_MISSING`, `MANUAL_YEAR_AMBIGUOUS`, `MANUAL_YEAR_MISMATCH`
- `MANUAL_COUNTRY_MISSING`, `MANUAL_COUNTRY_AMBIGUOUS`, `MANUAL_COUNTRY_UNMAPPABLE`, `MANUAL_COUNTRY_MISMATCH`

All findings are retained in `all_findings`; the primary `status` contains only the first-priority reason. Missing, ambiguous, unmappable, or mismatched v6 identity data prevents `AUTO_CANDIDATE`.

## Output and cache

Reports are written to `discogs_bot_verify_output_v6\`:

- `stage2_verification_report.tsv` — detailed row-by-row results, including v5 and v6 checks.
- `stage2_summary.txt` — counts by primary status.

A dedicated cache is stored under `discogs_bot_verify_output_v6\cache\`. v6 does not reuse v5 MusicBrainz cache files because it requests additional `media` data. Keep the v6 cache intact during a run and do not run another process against the same v6 cache concurrently.

## Offline tests

Run from the project directory:

```powershell
py -m unittest discover -s .\tests -v
```

The v6 tests cover medium mapping/counts, file-count handling, packaging descriptors, DVD subtypes, year extraction/conflicts, country/territory overlap, and end-to-end status selection. Offline tests do not replace a live recheck against the local MusicBrainz mirror and Discogs API.

## Safety boundary

This repository's Stage 1 / Stage 2 verification code is read-only. It does not authenticate to MusicBrainz for editing, submit URL relationships, cast votes, or cancel edits. Any separate edit-submission stage must be maintained and reviewed independently.

## License

This project is released under the MIT License. See [`LICENSE`](LICENSE).