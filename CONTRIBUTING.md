# Contributing

Please keep all code, identifiers, comments, log output, test names, and documentation in English so that the project remains consistent.

## Development checks

No third-party Python packages are required for the current read-only stages. Run:

```powershell
py -m unittest discover -s .\tests -v
```

## Safety rules

- Do not add an unattended MusicBrainz edit-submission path without a dedicated design review and test-server validation.
- Do not relax matching rules merely to increase the number of `AUTO_CANDIDATE` records.
- Preserve the fail-closed behavior: incomplete or ambiguous evidence must not become an automatic candidate.
- Never commit personal data exports, API tokens, caches, full reports, or local MusicBrainz configuration.
- Add unit tests for any change to title, artist credit, barcode, label, catalog-number, blacklist, or existing-link logic.
