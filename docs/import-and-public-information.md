# Reviewed import and public information

The organizer Timeline now accepts pasted lineups. Public schedules show stored
Twitch information and event/visitor-local times. These features extend the
existing records; drafts remain private until explicitly published.

## Import a lineup

Open an event in `/organizer/`, save any pending edits, and expand **Import a pasted
lineup**. Paste the text and choose **Review pasted lineup**. For example:

```text
2026-10-10 Title: Saturday train
10a: musician 11a: 12p: another_channel
```

Time labels accept forms such as `10a:`, `10:30pm:`, and `22:30:`. Empty labels
become open slots. Existing streamers match by Twitch username, channel URL, or
chosen Fuinoise name; ambiguous or missing matches require a selection.

An ambiguous date such as `07.09.2026` requires choosing July 9 or September 7.
Untimed material such as `Pre-Pary: P_chops` becomes a review row: enter its time
or exclude it. Backwards times propose the following calendar day and require
review. Dates and times use the event's time zone. Nonexistent or repeated local
times during daylight saving transitions are rejected instead of guessed.

Review each row's date, time, duration, and channel, then acknowledge the review.
By default the rows append to the draft. **Replace the entire working lineup**
replaces its complete slot list. Slots with the same actual start retain their
existing identity, replay URL, and notes. Conflicts, stale versions, invalid
times, or failed matches reject the entire import, including any new streamer
records. Applying an import never publishes it or confirms assignments.

For an unknown channel, **Look up Twitch channel** fetches public profile data.
Lookup creates no records. Its signed match is bound to the organizer and event
and expires after ten minutes. A successful reviewed import may create a streamer
with its Twitch ID and chosen Fuinoise name; it does not link a login account or
approve participation. Local records with a conflicting username require review
instead of being claimed automatically. Rerun `setup_organizers` for existing
organizers to grant the added `add_streamer` permission; see
[account setup](accounts-and-eligibility.md).

## Twitch configuration and refreshing

Set `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET` in server configuration, using
the same Twitch application as sign-in. Public profile/status fetching uses a
server application token. Tokens stay in the server cache, are validated before
use, and are reacquired before one hour. Failed authorization clears the cached
token. Requests use fixed Twitch endpoints, bounded batches, five-second request
timeouts, and bounded responses. Malformed or incomplete results show unavailable
status rather than being interpreted as offline.

Apply migration 0016 before use. It adds a separate snapshot table and leaves
existing streamer identity, chosen names, notes, assignments, and replay links
intact. Legacy records can be fetched by username without inventing verified
ownership. Records with a stable Twitch ID are fetched by that ID.

**Refresh Twitch info** in the Timeline refreshes performers in both saved and
working lineups, up to 100 channels. It reports unavailable results without
changing the timetable. Operators can also run:

```sh
# Current/upcoming published lineups, bounded to 100 streamers by default.
venv/bin/python manage.py refresh_twitch
# One saved event, including events that are not public.
venv/bin/python manage.py refresh_twitch --event EVENT_ID
# All local streamers; increase the cap explicitly when needed (maximum 1000).
venv/bin/python manage.py refresh_twitch --all --limit 500
```

The command exits unsuccessfully if any refresh fails and reports counts, while
preserving old profiles and schedules. Missing credentials also produce an
actionable error. The default scope includes published events dated today or
later and a two-day buffer for overnight slots. For larger datasets, refresh
specific events so the cap does not repeatedly omit their performers.

Deployment must arrange recurring refreshes while events are active; a two-minute
cadence is an initial setting to verify during the pilot. The default freshness
limit is 180 seconds, configurable through `TWITCH_LIVE_MAX_AGE_SECONDS`. This
milestone provides the command; scheduling it and testing real credentials are
remaining deployment work.

## Public display and failure behavior

Public pages read snapshots locally and make no Twitch request while rendering.
They retain the organizer's chosen performer name and supplement it with Twitch's
display name, biography, avatar, channel link, and fresh live/offline status.
Fresh live snapshots also show the stream title and category. Failed, missing,
expired, or mismatched snapshots show **Live status unavailable**. Older valid
profile information remains readable during outages. Public pages never expose
private organizer notes or provider credentials.

Event times remain visible in the event's zone. A small browser script adds each
visitor's local start/end times with their calendar date and zone, including
overnight and daylight saving boundaries. It also expires live badges and stream
titles on an open page; visitors reload to obtain newer snapshots. Without
JavaScript, the event timetable and channel links remain usable, and status
reflects freshness when the server rendered the page.

## Verification

Django tests cover parsing/review, atomic imports, matching permissions and token
expiry, stale drafts, daylight saving errors, identity preservation, provider
failures, status freshness, public escaping, refresh commands, and migration
preservation. Browser checks cover reviewed import privacy through publication,
phone review, Twitch badge expiry, visitor-local times, and the public fallback
without JavaScript. Unit tests verify local-time formatting at overnight and
repeated-hour boundaries. The real Twitch/Discord rehearsal remains pending.

Provider references: [Get Users](https://dev.twitch.tv/docs/api/reference/#get-users),
[Get Streams](https://dev.twitch.tv/docs/api/reference/#get-streams),
[application tokens](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#client-credentials-grant-flow),
and [token validation](https://dev.twitch.tv/docs/authentication/validate-tokens/).
