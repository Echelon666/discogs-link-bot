# MusicBrainz Bot Readiness Checklist

This checklist is a project safety plan, not a statement that the current read-only scripts are already approved for public editing.

Official references:

- Bot guidelines: https://musicbrainz.org/doc/Bots
- Bot Code of Conduct: https://musicbrainz.org/doc/Code_of_Conduct/Bots
- MusicBrainz test server: https://test.musicbrainz.org
- MusicBrainz API: https://musicbrainz.org/doc/MusicBrainz_API

## Required before public edits

- [x] Contact the MusicBrainz team and request that the dedicated bot account type be set to `Bot` (maintainer reports that the team has approved this and changed the account type).
- [x] Confirm that public submissions are being made from the dedicated bot account, not a maintainer's personal account.
- [x] Keep the source repository public.
- [ ] Link the source repository from the bot's user page.
- [ ] State on the bot's user page who maintains the bot.
- [x] Implement a separate Stage 3 submitter without changing the Stage 1/Stage 2 verifier.
- [ ] Confirm the bot login/session behavior and required login policy for each run, including runs where no edits are ultimately made.
- [ ] Complete a one-record votable submission smoke test on the MusicBrainz test server and verify the resulting open edit.
- [ ] Confirm the current blacklist and other policy requirements are implemented for each relevant entity type before edits.
- [x] Include clear edit notes with the Discogs release source, v5 verification reasons, and any normalizations.
- [x] Record accepted submissions locally and stop on ambiguous POST outcomes; never retry an uncertain submission automatically.
- [ ] Review comments and edit notes from the MusicBrainz community and respond in a reasonable time.

## Conservative operating policy for this project

- Maximum planned batch size: 500 edits. This is a project limit, not the official daily limit.
- Maximum planned daily submissions: 500 edits, unless the maintainer re-evaluates this after community review.
- Check the bot's current open-edit count before every batch. Do not approach the recommended 2,000 open edits; the documented hard limit is 2,500.
- Stop immediately if a match is questioned, an unexpected edit result occurs, the open-edit count is uncertain, or the API reports repeated errors.
- Do not automatically resubmit edits that failed or were rejected.
- Do not use a manual-review category as an unattended fallback. Only explicitly approved, high-confidence records may enter the write queue.

## Current limitation

Stage 1 and Stage 2 remain read-only. A separate Stage 3 userscript has been added, but it is **not yet live-tested**. Do not submit production edits until the test-server smoke test above is completed, the bot profile links to this repository and maintainer, and the bot's session/login policy is confirmed. The Stage 3 website endpoint is internal and may change.
