# V7 pipeline — implementation notes

This branch is a read-only candidate-selection experiment on top of the Stage 2 v6 verifier.

## Candidate generation
- Discogs barcode search: type=release, all result pages.
- Exactly one direct result: retain it without master expansion.
- More than one direct result: retain every direct result and fetch every page of every distinct associated Master Versions endpoint.
- Deduplicate Release IDs within each MBID.

## Conservative identity prefilter
- Use medium, year, and country only if MusicBrainz supplies a usable, unambiguous value for that field.
- Remove only a definite contradiction present in the Discogs search/version summary.
- Keep incomplete or ambiguous summary fields for full Stage 2 verification.
- Do not use summary-level medium quantities for rejection; Stage 2 verifies physical quantities using complete release details.

## Stage 2 behavior
The V6 verifier retains its full metadata and safety checks. The local V7 pipeline change makes medium/year/country checks conditional on MusicBrainz supplying data for the corresponding field. When MusicBrainz supplies a field, Discogs must still provide a compatible value; otherwise the outcome remains manual.

## Safety and evaluation
- Read-only: no MusicBrainz edits, votes, or relationship writes.
- Do not interpret AUTO_CANDIDATE as independently proven correctness.
- Report ambiguous candidates separately; do not choose one arbitrarily.
- Local collection reports, progress, caches, and generated result files must remain outside Git.

## Early Digital Media exclusion
- Stage 1 checks the MusicBrainz media list immediately after the release lookup. If any medium format is `Digital Media` (case- and whitespace-insensitive), the MBID is recorded as `SKIP_DIGITAL_MEDIA` and no Discogs relationship check, barcode processing, or Discogs API request is performed.
- Stage 2 repeats the check immediately after the MusicBrainz release lookup and before fetching `/releases/{release_id}` from Discogs.
- The guard is specific to `Digital Media`; physical carriers such as CD, Vinyl, DVD, Blu-ray, and Cassette remain eligible for the normal pipeline.
- To re-evaluate MBIDs already stored as completed in a Stage 1 progress file, start with `--reset` or use a new `--progress` path. The API cache remains available.
