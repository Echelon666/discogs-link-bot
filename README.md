# Discogs Link Bot — Read-Only Verification

A conservative, auditable toolset for checking whether a proposed Discogs release URL matches a specific MusicBrainz release.

**Current status: read-only. This repository does not submit edits to MusicBrainz.** `AUTO_CANDIDATE` means a record passed the checks implemented here; it is not, by itself, permission to create a public edit.

## What it does

- **Stage 1 — input preflight:** validates a four-column TSV input, checks MBID and Discogs release URL formats, detects duplicate MBIDs and Discogs-release-ID collisions, and writes review reports. No network requests are made.
- **Stage 2 — metadata verification:** compares a specific MusicBrainz release with the specific Discogs release using the local MusicBrainz Web Service and Discogs API. It checks credited artist, exact title, barcode, existing Discogs release relationships, the official MusicBrainz Discogs release blacklist, and MusicBrainz label/catalog-number data when those fields are present.
- **Auditable output:** creates TSV reports with individual checks and reason-coded statuses.
- **Conservative matching:** avoids fuzzy matching. Uncertain or incomplete cases are routed to a `MANUAL_*` status rather than being approved automatically.

## Scope and safety boundary

This code is intentionally read-only. It does not authenticate to MusicBrainz for editing, submit URL relationships, cast votes, or cancel edits. The future edit-submission stage must be implemented, reviewed, and tested separately before any public edits are attempted.

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

Use only data you are entitled to process. Do not commit your personal input list, reports, or cache to a public repository.

## Run Stage 1 — preflight

Open PowerShell in the project directory:

```powershell
py .\discogs_bot_preflight.py .\your_input_file.txt
```

By default, reports are written to `discogs_bot_preflight_output\`. Supply `--out-dir` to change the location.

## Run Stage 2 — read-only verification

The PowerShell wrapper prompts for the Discogs token using hidden input and removes the temporary environment variable when it exits.

Run a 20-record pilot:

```powershell
.\run_discogs_bot_verify.ps1
```

Run all Stage 1 candidates:

```powershell
.\run_discogs_bot_verify.ps1 -Limit 0
```

Reports are written to `discogs_bot_verify_output\`. The default is a small pilot; inspect its report before scaling up.

The Python script can also be run directly if `DISCOGS_TOKEN` is already set in the environment. Prefer the PowerShell wrapper on Windows to avoid putting the token in command history. Never put tokens in source code, reports, screenshots, issue trackers, or chat.

### Environment configuration

- `DISCOGS_TOKEN` — required for Stage 2.
- `MUSICBRAINZ_BASE_URL` — optional; defaults to `http://localhost:5000`.
- `DISCOGS_USER_AGENT` — optional descriptive User-Agent string for Discogs requests. After creating a public repository, consider including its URL and a public contact method in this value.

## Matching rules

- Release title and credited artist are compared conservatively after Unicode canonical normalization, case-folding, and whitespace normalization. Punctuation, accents, subtitles, and substantive name differences are not discarded.
- Artist-credit variants (Discogs ANV) are exposed in the report to support auditing.
- Barcodes must match exactly, except that a 12-digit UPC-A may match the corresponding 13-digit EAN-13 when the latter has exactly one leading zero. Arbitrary leading zeroes are not removed.
- If MusicBrainz supplies a label and/or catalog number, Stage 2 checks that information against the specific Discogs release. When both are supplied, the label/catalog-number pair must occur on the same Discogs label entry.
- A missing field in MusicBrainz is not considered a mismatch. Missing corresponding Discogs data or any mismatch in a supplied field prevents `AUTO_CANDIDATE` and routes the record to `MANUAL_*`.
- The official MusicBrainz Discogs **release** blacklist is checked. This does not mean that every type of MusicBrainz blacklist is checked.
- Existing MusicBrainz Discogs release relationships are detected to avoid suggesting an already linked record.

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
