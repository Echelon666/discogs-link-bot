# MusicBrainz Bot Readiness Checklist

This checklist is a project safety plan. Stage 3 is implemented separately, but is not approved for production use until the remaining test-server and bot-profile requirements below are complete.

Official references:

- Bot guidelines: https://musicbrainz.org/doc/Bots
- Bot Code of Conduct: https://musicbrainz.org/doc/Code_of_Conduct/Bots
- MusicBrainz test server: https://test.musicbrainz.org
- MusicBrainz API: https://musicbrainz.org/doc/MusicBrainz_API

## Required before public edits

- [x] Contact the MusicBrainz team and request that the dedicated bot account type be set to `Bot` (maintainer reports that the team has approved this and changed the account type).
- [x] Require the dedicated bot account, not a maintainer's personal account, for any future production submission.
- [x] Keep the source repository public.
- [ ] Link the source repository from the bot's user page.
- [ ] State on the bot's user page who maintains the bot.
- [x] Implement a separate Stage 3 submitter without changing the Stage 1/Stage 2 verifier.
- [ ] Confirm the bot login/session behavior and required login policy for each run, including runs where no edits are ultimately made.
- [x] Complete a one-record votable submission smoke test on the MusicBrainz test server and verify the resulting open edit. Verified 2026-11-10: test edit #100327478 (Mike Oldfield — Exposed) is Open and the server explicitly states that it is open for voting.
- [ ] Confirm the current blacklist and other policy requirements are implemented for each relevant entity type before edits.
- [x] Include clear edit notes with the Discogs release source, v5 verification reasons, and any normalizations.
- [x] Record accepted submissions locally and stop on ambiguous POST outcomes; never retry an uncertain submission automatically.
- [ ] Review comments and edit notes from the MusicBrainz community and respond in a reasonable time.

## Conservative operating policy for this project

- Maximum batch size during the pilot phase: 150 edits; the first production pilot is capped at 100. These are project limits, not official MusicBrainz limits.
- Maximum planned daily submissions during the pilot phase: 150 edits. Re-evaluate only after community review and a review of the bot's open-edit count.
- Check the bot's current open-edit count before every batch. Do not approach the recommended 2,000 open edits; the documented hard limit is 2,500.
- Stop immediately if a match is questioned, an unexpected edit result occurs, the open-edit count is uncertain, or the API reports repeated errors.
- Do not automatically resubmit edits that failed or were rejected.
- Do not use a manual-review category as an unattended fallback. Only explicitly approved, high-confidence records may enter the write queue.

## Current limitation

Stage 1 and Stage 2 remain read-only. Stage 3 passed a one-record live smoke test on the MusicBrainz test server on 2026-11-10: edit #100327478 was created by TestBot and the edit page explicitly confirmed that it was open for voting. Production submission is still gated on linking this repository and its maintainer from the bot profile, confirming the bot's session/login policy, and reviewing the relevant blacklist/policy coverage. The Stage 3 website endpoint is internal and may change.
