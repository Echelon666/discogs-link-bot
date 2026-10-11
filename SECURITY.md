# Security Policy

## Scope

This project currently contains read-only preflight and metadata verification scripts. It does not submit MusicBrainz edits.

## Reporting a security issue

Do not publish API tokens, passwords, session cookies, private reports, or unredacted local configuration in an issue. Rotate a token immediately if it may have been exposed. Until a dedicated private reporting channel is configured, contact the maintainer through the public profile associated with the repository without including secrets.

## Secret handling

- Supply the Discogs token at runtime through `DISCOGS_TOKEN`.
- Prefer the included PowerShell wrapper, which requests hidden input and clears the temporary environment variable when finished.
- Never hard-code tokens or credentials in source code.
- Keep personal input files, verification reports, and cache directories out of version control.
