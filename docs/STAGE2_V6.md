# Stage 2 v6 — release identity checks

Stage 2 v6 supplements the v5 metadata comparison with mandatory checks on the specific issue/pressing being linked.

## MusicBrainz data requested

For each release MBID, the local Web Service lookup requests:

`inc=artist-credits+url-rels+labels+media`

The release's own `date` and `country` are used for year/country. The `media` array supplies each medium's format. When the primary release date/country is absent, the script only uses the defined release-event fallbacks described below.

## Discogs data read

The specific Discogs Release endpoint supplies the release-level `year`, `released`, `country`, and `formats` array. Each format entry's `name` and relevant `descriptions` are used to compare the carrier/subtype with MusicBrainz media. For physical carriers, Discogs `qty` is compared with the corresponding MusicBrainz medium count. For `File`, `qty` is the number of digital files/tracks, not the number of physical media, so the digital format is compared by presence only and that quantity is ignored.

## Matching policy

### Medium

- Compare physical carrier types, not marketing/packaging descriptors such as `Album`, `Reissue`, `Limited Edition`, `Box Set`, or aggregate `All Media`.
- Explicit aliases include `Digital Media` ↔ `File`; `File` quantity is ignored because it counts files/tracks, not discs.
- CD carrier variants (including HDCD and SHM-CD) map to CD; vinyl size variants map to Vinyl.
- DVD-Audio and DVD-Video are distinct subtypes. Discogs descriptions are used when available; a generic DVD without a matching subtype is manual/unmappable.
- Compare physical quantities for multi-disc editions.
- Missing, unmapped or incomplete physical format/quantity information is manual, not a match.

### Year

- Use the year from the specific MusicBrainz Release `date`.
- If that date is absent, accept a single unique year from its release events; multiple event years are ambiguous.
- Use Discogs `year`, cross-checking the year parsed from `released` if both are supplied.
- Missing years, conflicting source values and different years are manual.

### Country

- Use the MusicBrainz Release country code; if absent, use release-event area codes only when exactly one unique country code is available.
- Map Discogs country names to ISO alpha-2 codes using the bundled lookup table.
- `UK` maps to `GB`; MusicBrainz special areas `XE` (Europe) and `XW` (Worldwide) are supported.
- A broader region that overlaps a specific country is manual/ambiguous, not a definite mismatch. For example, Discogs `Europe` versus MusicBrainz `GB` or `DE` does not prove different territories.
- `Worldwide` versus a specific country is also manual/ambiguous, not a mismatch.
- Known compound territories such as `UK & Europe`, `UK & Ireland`, and `Australia & New Zealand` are recognized as broad territories and remain manual when their member areas overlap the MusicBrainz country.
- Only demonstrably disjoint specific countries/territories are classified as mismatches. Unknown compound areas remain unmappable/manual.

## AUTO_CANDIDATE gate

A candidate must pass all previously implemented v5 checks and receive `MATCH` for `media_format_check`, `year_check`, and `country_check`. Any mismatch, missing field, unmappable value, or ambiguity creates a `MANUAL_*` finding and prevents `AUTO_CANDIDATE`.

The verifier evaluates remaining metadata fields even after one mismatch so the report can show all findings for human review.