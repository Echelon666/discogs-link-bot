# MusicBrainz Bot Readiness Checklist

This checklist is a project safety plan, not a statement that the current read-only scripts are already approved for public editing.

Official references:

- Bot guidelines: https://musicbrainz.org/doc/Bots
- Bot Code of Conduct: https://musicbrainz.org/doc/Code_of_Conduct/Bots
- MusicBrainz test server: https://test.musicbrainz.org
- MusicBrainz API: https://musicbrainz.org/doc/MusicBrainz_API

## Required before public edits

- [ ] Contact the MusicBrainz team and request that the dedicated bot account type be set to `Bot`.
- [ ] Do not submit edits until the account type has been set to `Bot`.
- [ ] Make the source repository public and link it from the bot's user page.
- [ ] State on the bot's user page who maintains the bot.
- [ ] Implement the edit-submission stage separately from this read-only verifier.
- [ ] Implement the required MusicBrainz login behavior for every bot run, including runs where no edits are ultimately made.
- [ ] Test the submission workflow carefully on the MusicBrainz test server.
- [ ] Confirm the current blacklist and other policy requirements are implemented for each relevant entity type before edits.
- [ ] Include clear edit notes with the Discogs release source and the evidence used for the match.
- [ ] Record each attempted edit and its result; handle retries idempotently so an interrupted run does not duplicate edits.
- [ ] Review comments and edit notes from the MusicBrainz community and respond in a reasonable time.

## Conservative operating policy for this project

- Maximum planned batch size: 500 edits. This is a project limit, not the official daily limit.
- Maximum planned daily submissions: 500 edits, unless the maintainer re-evaluates this after community review.
- Check the bot's current open-edit count before every batch. Do not approach the recommended 2,000 open edits; the documented hard limit is 2,500.
- Stop immediately if a match is questioned, an unexpected edit result occurs, the open-edit count is uncertain, or the API reports repeated errors.
- Do not automatically resubmit edits that failed or were rejected.
- Do not use a manual-review category as an unattended fallback. Only explicitly approved, high-confidence records may enter the write queue.

## Current limitation

The present repository is read-only and does not submit MusicBrainz edits or authenticate to MusicBrainz as a bot. The checkboxes related to submission must remain unchecked until a separate write stage is developed and tested.
