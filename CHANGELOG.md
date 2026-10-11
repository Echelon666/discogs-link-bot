# Changelog

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
