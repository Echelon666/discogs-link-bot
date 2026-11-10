# DiscogsBot Stage 3 — Votable Release Link Submitter

Stage 3 is a separate, optional write module for the read-only Stage 1/Stage 2 v5 verifier. It creates **Release → URL → Discogs** relationships as **votable edits**. It never votes, auto-approves, or cancels edits.

> **Status: implemented, not yet live-tested.** The script has not yet completed a signed-in smoke test on the MusicBrainz test server. Do not use the production queue until that test succeeds and its resulting open edit is confirmed as votable.

## Files

- \`DiscogsBot_Submitter.user.js\` — Tampermonkey submitter for \`musicbrainz.org\` and \`test.musicbrainz.org\`.
- \`prepare_discogs_bot_batch.py\` — selects only \`AUTO_CANDIDATE\` rows from a v5 report and creates a JSON queue plus TSV preview.

## Prepare a queue

Run in PowerShell from this folder:

\`\`\`powershell
py .\prepare_discogs_bot_batch.py --input .\stage2_verification_report.tsv --limit 100 --out-dir .\pilot_batch_100
\`\`\`

The limit is 1–150. The selection is evenly spaced through the report's AUTO_CANDIDATE rows to produce a broader sample. The script validates MBIDs, Discogs release URLs, duplicate MBIDs and duplicate Discogs Release IDs. It makes no network requests.

Inspect \`submission_batch_preview.tsv\` before loading \`submission_batch.json\`. Keep both generated files and all verification reports private; do not commit them to this repository.

## Install and run

1. Install the userscript in Tampermonkey.
2. Sign in to MusicBrainz using the dedicated bot account. Confirm the displayed account name before proceeding.
3. On the **test server**, prepare a separate one-item queue using a release MBID that actually exists on that server. Do not assume production MBIDs also exist there.
4. Load the test queue in the Stage 3 panel and run **Check existing links**. This is read-only.
5. Confirm the bot account checkbox and submit the single test item. Confirm the edit appears in the bot's open edits and is votable.
6. Only after the test succeeds, load the reviewed 100-item production queue on \`musicbrainz.org\`, run preflight, review the READY count, confirm the bot account, and type the exact production confirmation phrase.

Production submissions require two explicit confirmations. The script submits one relationship per POST and spaces MusicBrainz requests by at least 1.1 seconds.

The submitter verifies the signed-in username against the server-specific identity: `TestBot` on `test.musicbrainz.org` and `WindowsLover_DiscogsBot` on production `musicbrainz.org`. A personal account such as `WindowsLover` is blocked in the UI and again inside the submission function; the checkbox cannot override this check.

## Safety behavior

- Only \`AUTO_CANDIDATE\` rows are accepted; a queue is limited to 150 items.
- The queue loader validates MBIDs, Discogs release URLs and duplicate identifiers.
- Preflight checks current MusicBrainz URL relationships and skips an exact existing Discogs link or another Discogs release link.
- Each release is checked again immediately before its POST.
- Each edit uses relationship type ID \`76\` (Release-URL / Discogs), edit type \`90\`, and \`makeVotable: 1\`.
- Edit notes include the v5 verification reasons, any safe normalizations, and the public source repository.
- A browser-local ledger records accepted submissions. An ambiguous network result, HTTP 429/503/5xx, or non-JSON success response is marked uncertain and stops the batch. The script never automatically retries an uncertain POST.
- Keep the same browser profile and site storage while the pilot is in progress. Do not clear the local ledger until the bot's open-edit list has been checked.
- The script never votes on the bot's own edits.

## Technical implementation and caveat

The submitter uses the same-site MusicBrainz website endpoints \`/ws/js/entity/{gid}?inc=rels\` and \`/ws/js/edit/create\`, following the request format used by the community-maintained [ISRC Scout userscript](https://github.com/majkinetor/musicbrainz-userscripts/blob/main/userscripts/isrc_scout/isrc_scout.user.js). The active browser session cookie authenticates the request.

This is an internal website API, not a documented stable public API. MusicBrainz may change it. The test-server smoke test is mandatory before production use. If the server's response or request contract differs, stop and update the code; do not experiment against production.

## Public policy and operations

- The bot account must have its account type set to **Bot** by the MusicBrainz team before public submissions.
- Link this public repository from the bot profile and state who maintains it.
- Keep the first production run to the prepared 100-record pilot.
- Monitor the bot's open edits, edit notes and community feedback; investigate false positives and respond promptly.
- Do not increase the batch size until this pilot has been reviewed by the community.

References: [MusicBrainz Bots](https://musicbrainz.org/doc/Bots), [Bot Code of Conduct](https://musicbrainz.org/doc/Code_of_Conduct/Bots), [Editing documentation](https://musicbrainz.org/doc/Edit), [Discogs Release relationship type](https://musicbrainz.org/relationship/4a78823c-1c53-4176-a5f3-58026c76f2bc).
