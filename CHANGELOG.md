# Changelog

## v0.3.1 — refine medium and territory interpretation from pilot audit

- Compare `Digital Media` and Discogs `File` by presence; ignore `File` quantity because it counts files/tracks, not physical discs.
- Ignore Discogs `Box Set` and `All Media` as packaging/aggregate descriptors rather than additional carriers.
- Normalize HDCD/SHM-CD and sized-vinyl names to their physical carrier; use Discogs descriptions to distinguish DVD-Audio from DVD-Video, and leave generic unspecified DVD subtypes manual.
- Treat overlapping broad areas (Europe, Worldwide, and known compound regions) as ambiguous rather than claiming a definite country mismatch.
- Expand offline coverage for the above rules and preserve the read-only/fail-closed behavior.

## v0.3.0 — Stage 2 v6 release-identity verification

- Retain the v5 conservative artist, title, barcode, label, catalog-number, blacklist, and existing-relationship checks.
- Add mandatory comparison of MusicBrainz medium formats and counts against Discogs release formats and quantities.
- Add a specific-release year check using MusicBrainz Release date and Discogs `year` / `released` values; ambiguous source values route to manual review.
- Add a specific-release country/area check with conservative ISO alpha-2 mapping and explicit MusicBrainz special-area handling.
- Add detailed `media_format_check`, `year_check`, and `country_check` report columns, raw source values, and reason-coded `MANUAL_*` outcomes.
- Require all three new checks to match before assigning `AUTO_CANDIDATE`.
- Use a dedicated v6 output and cache directory; do not reuse v5 cache data because the MusicBrainz request now includes media.
- Add offline tests for format aliases/counts, year conflicts, country aliases/ambiguity, and end-to-end status selection.
- Keep Stage 2 read-only; no MusicBrainz edits are submitted.

## v0.2.0 — v5 conservative verification

- Evaluate all available metadata fields before assigning the primary status.
- Add conservative title punctuation normalization while preserving meaningful words, accents, apostrophes and word order.
- Normalize defined typography variants and catalog-number whitespace/dash glyphs without stripping meaningful catalog punctuation.
- Permit a terminal `Records` label suffix variant only when the catalog number corroborates the same Discogs label entry.
- Add normalization actions and all-field findings to the verification report.
- Keep the tool read-only; no MusicBrainz edit submission or editing authentication is implemented.

## v0.1.0 — initial public read-only stages

- Add candidate input preflight and read-only metadata verification.
- Add barcode, artist credit, label/catalog-number, blacklist and existing-link checks.

## V7 pipeline development — 2026-11-13 (read-only)

- Add a Stage 1 barcode candidate builder that follows every Discogs search page.
- Keep one direct result as-is; for multiple direct results, retain them and expand every distinct associated Master through every /masters/{id}/versions page.
- Deduplicate Discogs Release IDs per MusicBrainz release.
- Add a conservative Stage 1 prefilter using usable MusicBrainz medium, year, and country values; remove only definite contradictions in Discogs summaries and retain missing/ambiguous summaries for Stage 2.
- Adapt the Stage 2 v6 verifier so medium/year/country checks are skipped only when MusicBrainz has no usable value for that field. Missing or uncertain Discogs data remains manual when MusicBrainz supplies the field.
- Add offline tests for multi-result expansion, pagination, progress recovery, pipeline handoff, and optional identity checks.
- Keep all stages read-only. No MusicBrainz edits are submitted.
- Exclude local reports, caches, progress files, and private collection data from the repository.
