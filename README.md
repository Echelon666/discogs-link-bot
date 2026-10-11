# Discogs Link Bot — Read-Only Verification (v5)

A conservative, auditable toolset for checking whether a proposed Discogs release URL matches a specific MusicBrainz release.

**Current status: read-only. This repository does not submit edits to MusicBrainz or authenticate to MusicBrainz as an editing bot.** `AUTO_CANDIDATE` means a record passed the checks implemented here; it is not, by itself, permission to create a public edit.

## What it does

- **Stage 1 — input preflight:** validates a four-column TSV input, checks MBID and Discogs release URL formats, detects duplicate MBIDs and Discogs-release-ID collisions, and writes review reports. No network requests are made.
- **Stage 2 — metadata verification:** compares a specific MusicBrainz release with the specific Discogs release using the local MusicBrainz Web Service and Discogs API.
- **All-field diagnostics:** evaluates artist, title, barcode, existing Discogs release relationships, the official MusicBrainz Discogs release blacklist, label and catalog-number fields before assigning the primary status. Additional findings are retained in the report.
- **Conservative normalization:** applies explicitly defined typography/punctuation equivalences without fuzzy matching or deleting meaningful words, accents, digits or word order.
- **Auditable output:** creates TSV reports with individual checks, normalization actions and reason-coded statuses.

## Scope and safety boundary

This code is intentionally read-only. It does not authenticate to MusicBrainz for editing, submit URL relationships, cast votes, or cancel edits. A future edit-submission stage must be implemented, reviewed, and tested separately before any public edits are attempted.

Do not treat any `AUTO_CANDIDATE` as guaranteed correct. Community-maintained metadata can contain errors on either service. The report is a selection aid, not a substitute for a carefully tested editing workflow.

## Requirements

- Python 3.10 or newer.
- Windows PowerShell for the included `.ps1` wrapper (the Python scripts themselves use the standard library only).
- A local MusicBrainz mirror exposing the `/ws/2` API, defaulting to `http://localhost:5000`.
- A Discogs personal API token for Stage 2.
- Internet access to read Discogs release data and the official MusicBrainz Discogs release blacklist.

The tool makes no MusicBrainz database writes. Discogs requests are throttled (default minimum interval 1.1 seconds between uncached requests) and transient HTTP failures are retried with backoff.

## Input format

The input is a UTF-8 text file containing **four tab-separated columns per line and no header**:

1. MusicBrainz release MBID
2. Barcode (digits only)
3. Direct Discogs release URL (`https://www.discogs.com/release/ID-...`)
4. A display label for the artist and release, used for audit/context only

Use only data you are entitled to process. Do not commit personal input lists, reports, or cache to a public repository.

## Run Stage 1 — preflight

Open PowerShell in the project directory:

```powershell
py .\discogs_bot_preflight.py .\your_input_file.txt
```

By default, reports are written to `discogs_bot_preflight_output\`. Supply `--out-dir` to change the location.

## Run Stage 2 — read-only verification

The PowerShell wrapper prompts for the Discogs token using hidden input and removes the temporary environment variable when it exits.

Run a 20-record pilot first:

```powershell
.\run_discogs_bot_verify.ps1
```

Run all Stage 1 candidates after reviewing the pilot report:

```powershell
.\run_discogs_bot_verify.ps1 -Limit 0
```

Reports are written to `discogs_bot_verify_output\`; cached API responses are stored under `discogs_bot_verify_output\cache\`. The default is a small pilot. Inspect its report before scaling up.

The Python script can also be run directly if `DISCOGS_TOKEN` is already set in the environment. Prefer the PowerShell wrapper on Windows to avoid putting the token in command history. Never put tokens in source code, reports, screenshots, issue trackers, or chat.

### Environment configuration

- `DISCOGS_TOKEN` — required for Stage 2.
- `MUSICBRAINZ_BASE_URL` — optional; defaults to `http://localhost:5000`.
- `DISCOGS_USER_AGENT` — optional descriptive User-Agent string for Discogs requests. The default includes this public repository URL.

## Matching rules

- **Artist credit:** compares Discogs release credit, including ANV when supplied, conservatively. Only explicitly defined typography variants are normalized; accents and meaningful name differences are not discarded.
- **Release title:** allows exact text, defined typography/whitespace normalization, and punctuation-only equivalence that preserves letters, digits, accents, apostrophes and word order. It does not remove subtitles or words and does not use fuzzy matching.
- **Barcode:** must match exactly, except that a 12-digit UPC-A may match the corresponding 13-digit EAN-13 when the latter has exactly one leading zero. Arbitrary leading zeroes are not removed.
- **Catalog number:** case and whitespace/defined dash-glyph variants are handled conservatively. Punctuation is not generally stripped.
- **Label:** a terminal `Records` suffix is considered only under a defined rule requiring the catalog number to corroborate the same Discogs label entry. Similar label names alone are not sufficient.
- **Label/catalog pair:** when both fields are supplied by MusicBrainz, they must match on the same Discogs label entry.
- **Missing values:** a missing field in MusicBrainz is not itself a mismatch; missing corresponding Discogs data or mismatch in a field supplied by MusicBrainz prevents `AUTO_CANDIDATE` and routes the record to a `MANUAL_*` status.
- **Blacklist:** the official MusicBrainz Discogs **release** blacklist is checked. This does not mean that every type of MusicBrainz blacklist is checked.
- **Existing relationship:** a matching MusicBrainz Discogs release relationship is detected to avoid suggesting an already linked record.
- **Fail closed:** API failures or malformed responses are not treated as successful matches.

## Statuses

- `AUTO_CANDIDATE` — passed the checks implemented by this read-only verifier; requires a separate approval/write workflow.
- `MANUAL_*` — uncertain, incomplete, or inconsistent metadata; excluded from unattended approval.
- `REJECT_*` — a strong contradiction or invalid/missing target release was found.
- `BLOCKED_*` — release is present on the official MusicBrainz Discogs release blacklist.
- `ALREADY_LINKED` — the MusicBrainz release already has a matching Discogs release relationship.
- `ERROR_*` — API, fetch, or response validation error. Do not auto-approve.

## Offline tests

Run the unit tests from the project directory:

```powershell
py -m unittest discover -s .\tests -v
```

The tests validate comparison logic only. They do not replace live API testing or confirm that any proposed edit is correct.

## MusicBrainz bot readiness

Read [`docs/MUSICBRAINZ_BOT_READINESS.md`](docs/MUSICBRAINZ_BOT_READINESS.md) before planning any edit submission. MusicBrainz requires bot owners to contact the team to have the account type set to `Bot`, test carefully on the test server, open-source the bot code, and link the source from the bot profile. This repository currently contains only the read-only stages.

## License

This project is released under the MIT License. See [`LICENSE`](LICENSE).
