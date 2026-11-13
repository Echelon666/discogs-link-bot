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
