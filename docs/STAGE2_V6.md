# Stage 2 v6 — release identity checks

Stage 2 v6 supplements the v5 metadata comparison with mandatory checks on the specific issue/pressing being linked.

## MusicBrainz data requested

For each release MBID, the local Web Service lookup requests:

`inc=artist-credits+url-rels+labels+media`

The release's own `date` and `country` are used for year/country. The `media` array supplies each medium's format. When the primary release date/country is absent, the script only uses the defined release-event fallbacks described below.

## Discogs data read

The specific Discogs Release endpoint supplies the release-level `year`, `released`, `country`, and `formats` array. Each format entry's `name` is compared with MusicBrainz medium formats; `qty` is compared with the number of same-format MusicBrainz media.

## Matching policy

### Medium

- Compare carrier types, not Discogs format descriptions (e.g. `Album`, `Reissue`, `Limited Edition`).
- Explicit aliases include `Digital Media` ↔ `File`.
- Compare quantities for multi-disc editions.
- Missing, unmapped or incomplete format/quantity information is manual, not a match.

### Year

- Use the year from the specific MusicBrainz Release `date`.
- If that date is absent, accept a single unique year from its release events; multiple event years are ambiguous.
- Use Discogs `year`, cross-checking the year parsed from `released` if both are supplied.
- Missing years, conflicting source values and different years are manual.

### Country

- Use the MusicBrainz Release country code; if absent, use release-event area codes only when exactly one unique country code is available.
- Map Discogs country names to ISO alpha-2 codes using the bundled lookup table.
- `UK` maps to `GB`; MusicBrainz special areas `XE` (Europe) and `XW` (Worldwide) are supported.
- Unknown, compound or ambiguous regions are manual. Do not infer that `Europe` equals an individual European country.

## AUTO_CANDIDATE gate

A candidate must pass all previously implemented v5 checks and receive `MATCH` for `media_format_check`, `year_check`, and `country_check`. Any mismatch, missing field, unmappable value, or ambiguity creates a `MANUAL_*` finding and prevents `AUTO_CANDIDATE`.

The verifier evaluates remaining metadata fields even after one mismatch so the report can show all findings for human review.