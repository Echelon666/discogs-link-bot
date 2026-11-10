# DiscogsBot Stage 3 — Votable Release Link Submitter

Stage 3 is a separate, optional write module for the read-only Stage 1/Stage 2 v5 verifier. It creates **Release → URL → Discogs** relationships as **votable edits**. It never votes, auto-approves, or cancels edits.

> **Status: test-server smoke test passed; production pilot still gated.** On 2026-11-10, TestBot submitted one test edit (#100327478, Mike Oldfield — Exposed). The edit page displayed `Status: Open` and explicitly stated that the edit was open for voting. This verifies the end-to-end submission path on the test server. Production submissions remain gated on the remaining bot-profile, login-policy and policy-coverage checklist items.

## Files

- \`DiscogsBot_Submitter.user.js\` — Tampermonkey submitter for \`musicbrainz.org\` and \`test.musicbrainz.org\`.
- \`prepare_discogs_bot_batch.py\` — selects only \`AUTO_CANDIDATE\` rows from a v5 report and creates a JSON queue plus TSV preview.

## Prepare a queue

Run in PowerShell from this folder:

\`\`\`powershell
py .\prepare_discogs_bot_batch.py --input "C:\PATH\TO\stage2_verification_report.tsv" --limit 100 --target-server musicbrainz.org --out-dir .\pilot_batch_100
\`\`\`

The limit is 1–150; the first production pilot should use 100. The selection is evenly spaced through the report's AUTO_CANDIDATE rows to produce a broader sample. The script validates MBIDs, Discogs release URLs, duplicate MBIDs and duplicate Discogs Release IDs. It writes a target_server value into the private queue; Stage 3 rejects a queue on any other hostname. It makes no network requests.

Inspect \`submission_batch_preview.tsv\` before loading \`submission_batch.json\`. Keep both generated files and all verification reports private; do not commit them to this repository.

## Install and run

1. Install the userscript in Tampermonkey.
2. Sign in to MusicBrainz using the dedicated bot account. Confirm the displayed account name before proceeding.
3. On the **test server**, prepare a separate one-item queue using a release MBID that actually exists on that server. Do not assume production MBIDs also exist there.
4. Load the test queue in the Stage 3 panel and run **Check existing links**. This is read-only.
5. Confirm the bot account checkbox and submit the single test item. Confirm the edit appears in the bot's open edits and the edit page explicitly says it is open for voting. **Completed on 2026-11-10: test edit #100327478.**
6. Only after the test succeeds, load the reviewed 100-item production queue on \`musicbrainz.org\`, run preflight, review the READY count, confirm the bot account, and type the exact production confirmation phrase.

Production submissions require two explicit confirmations. The script submits one relationship per POST and spaces MusicBrainz requests by at least 1.1 seconds. At every preflight run it confirms the logged-in bot session by requesting the bot's authenticated open-edits page, reads the open-edit total, and downloads the current official Discogs Release blacklist without caching it. A blacklist download or count-reading failure blocks all submissions.

The submitter verifies the signed-in username against the server-specific identity: `TestBot` on `test.musicbrainz.org` and `WindowsLover_DiscogsBot` on production `musicbrainz.org`. A personal account such as `WindowsLover` is blocked in the UI and again inside the submission function; the checkbox cannot override this check.

## Offline tests

Run the Stage 3 batch generator tests from the repository root:

```powershell
py -m unittest discover -s .\stage3\tests -v
```

The tests cover AUTO_CANDIDATE-only selection, server-target metadata, duplicate Discogs Release ID rejection, and the 150-item batch cap.

## Safety behavior

- Only AUTO_CANDIDATE rows from the named Stage 2 v5 report are accepted; a queue is limited to 150 items and must target the exact host on which it is loaded.
- The queue loader validates MBIDs, Discogs release URLs and duplicate identifiers.
- Every preflight downloads the current official Discogs Links blacklist and excludes matching Release MBIDs. A failed or empty blacklist parse fails closed.
- Each run verifies the logged-in username and requests the bot's authenticated open-edits page; an exact open-edit count is required. Approximate counts fail closed.
- Preflight checks current MusicBrainz URL relationships and skips an exact existing Discogs link or another Discogs release link.
- Each release is checked again immediately before its POST.
- Each edit uses relationship type ID \`76\` (Release-URL / Discogs), edit type \`90\`, and \`makeVotable: 1\`.
- Edit notes include the v5 verification reasons, any safe normalizations, and the public source repository.
- A browser-local ledger records accepted submissions and enforces the 1,000-edit daily ceiling together with a manually entered count of same-day bot edits submitted outside this browser. The project blocks batches that would raise open edits above 2,000 (the official hard limit is 2,500). An ambiguous network result, HTTP 429/503/5xx, or non-JSON success response is marked uncertain and stops the batch. The script never automatically retries an uncertain POST.
- Keep the same browser profile and site storage while the pilot is in progress. Do not clear the local ledger until the bot's open-edit list has been checked.
- The script never votes on the bot's own edits.

## Technical implementation and caveat

The submitter uses the same-site MusicBrainz website endpoints \`/ws/js/entity/{gid}?inc=rels\` and \`/ws/js/edit/create\`, following the request format used by the community-maintained [ISRC Scout userscript](https://github.com/majkinetor/musicbrainz-userscripts/blob/main/userscripts/isrc_scout/isrc_scout.user.js). The active browser session cookie authenticates the request.

This is an internal website API, not a documented stable public API. MusicBrainz may change it. The test-server smoke test is mandatory before production use. If the server's response or request contract differs, stop and update the code; do not experiment against production.

## Public policy and operations

- The bot account must have its account type set to **Bot** by the MusicBrainz team before public submissions.
- MusicBrainz's official bot guidance sets a maximum of 1,000 edits per day, a recommended 2,000 open edits and a hard 2,500 open-edit limit. Stage 3 uses the 1,000 daily ceiling and the more conservative 2,000 open-edit project ceiling.
- Log in as the bot for every run, including no-edit runs; Stage 3 checks the authenticated session at preflight.
- Link this public repository from the bot profile and state who maintains it.
- Keep the first production run to the prepared 100-record pilot.
- Monitor the bot's open edits, edit notes and community feedback; investigate false positives and respond promptly.
- Do not increase the batch size until this pilot has been reviewed by the community.

References: [MusicBrainz Bots](https://musicbrainz.org/doc/Bots), [Bot Code of Conduct](https://musicbrainz.org/doc/Code_of_Conduct/Bots), [Editing documentation](https://musicbrainz.org/doc/Edit), [Discogs Release relationship type](https://musicbrainz.org/relationship/4a78823c-1c53-4176-a5f3-58026c76f2bc).
