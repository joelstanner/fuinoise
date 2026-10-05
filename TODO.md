# Fuinoise TODO

The agreed release target and architecture are in [the MVP definition](docs/mvp-definition.md). Keep `Event`, `Streamer`, and `RaidSlot`, their existing data, and the current migrations; extend them as needed for the agreed workflows. Build public pages from these records. React powers the custom organizer Timeline workspace through a Django REST Framework API; public and streamer pages use Django templates. Django Admin remains available for maintenance.

## Remaining MVP work

Follow [the implementation plan](docs/mvp-implementation-plan.md) for milestone
order and acceptance checks. The scheduling backend milestone is complete;
accounts and eligibility are implemented, with live provider rehearsal pending
configuration. Streamer requests, dashboards, cancellation, and backend organizer
assignment operations, the React Timeline, and its API are implemented. Reviewed
pasted import, Twitch information, and public visitor-local times are implemented;
see [workspace documentation](docs/organizer-workspace.md) and
[import and public information](docs/import-and-public-information.md).
Assignments become confirmed to streamers on publication; draft edits send no
confirmation. On-site notifications and the Discord outbox are implemented;
see [notification operations](docs/notifications.md). Scheduled delivery and live
Discord rehearsal still need production configuration.

- [x] Add flexible slot durations with a configurable one-hour default, overlap validation, and overnight handling. Migration leaves legacy durations unknown until reviewed.
- [x] Add backend working schedule drafts, atomic explicit publication, configurable signup opening, and protection against publishing stale assignments after a cancellation.
- [x] Implement Twitch sign-in, Discord account linking and server membership checks, organizer eligibility approval, and Discord requirement overrides. Setup and live-rehearsal steps are in [account documentation](docs/accounts-and-eligibility.md).
- [ ] Configure Twitch and Discord credentials and the Fuinoise server, then complete live sign-in, linking, membership, and review verification on HTTPS.
- [x] Add multiple-slot preferences, organizer assignment of multiple performances, streamer dashboards, and account-linked direct cancellation that reopens slots. See [workflow documentation](docs/requests-and-assignments.md).
- [x] Build the React Timeline workspace and Django REST Framework endpoints: event editing, drag assignment and performance movement, conflicts, detail controls, private autosaves, publication, reviewed early signup, and explicit stale-draft recovery. Browser checks cover save failures, keyboard controls, and phone layouts.
- [x] Add pasted-lineup import with review of parsed times and streamer matches. Keep empty time labels as open slots and require correction of ambiguous input. Imports save atomically to private drafts.
- [x] Pull streamer information from Twitch and show live/offline status through a bounded service layer with failure handling. Keep local schedules readable during an outage; expired or failed checks show unavailable status.
- [x] Show event and visitor-local times on public schedules; verify essential workflows on phones and desktop browsers, including expanded import review and public rendering without JavaScript.
- [x] Add on-site notifications and Discord organizer-channel alerts plus private messages to affected streamers. Delivery claims, recorded receipts, bounded retries, and uncertain-outcome review protect against duplicates.
- [ ] Configure the organizer Discord channel and recurring delivery worker, then verify real channel alerts, private messages, and recovery during the hosted rehearsal.
- [ ] Configure and document public deployment, persistent data, upgrades, backups, and restoration.
- [ ] Complete a real pilot event and the release acceptance checks in the MVP definition.

The completed sections below describe the existing foundation. They do not mark
the expanded MVP as finished.

## Completed: Fuinoise musician and raid data

- Added reusable instrument and genre records linked to streamers, plus a streamer time zone, raid availability status, preferences, and private organizer notes.
- Added weekly availability windows in each streamer's time zone. An end time earlier than the start time means the window continues into the following day. These windows are planning information, not booked raid slots.
- Kept `RaidSlot` as the event participation record, with a scheduled start and slot notes. Removed the separate planned handoff time because a gap before the next slot does not establish when a streamer stops. Lineup order now follows each slot's start time.
- Added communities, streamer memberships with role and notes, and an event community. Existing events are assigned to the default Fuinoise community by a data migration.
- Exposed the new records in Django Admin and added model, Admin, and migration tests.

## 1. Improve organizer Admin — completed

- Added a unique `RaidSlot` position within each event and backfilled existing slots; later removed it when start times became the sole schedule order.
- Added event and streamer Admin lists with filters and search; lineup slots are edited within an event. The inline shows the streamer, local start date and time, notes, and replay information.
- Event-local entry uses the event's time zone, which defaults to its community's time zone. The date fills from the event date and can be changed for overnight slots. Tests cover local-time conversion, reordering, Admin rendering, and time zone preservation.

## 2. Add community configuration — completed

- `Community` has the name, description, website, and logo URL needed by the planned public pages; each event belongs to one community. Existing events were backfilled, and Admin exposes the association.
- Admin lists the branding URLs and supports searching descriptions. A workflow test covers creating a community, associating an event, and updating its branding without losing the association.

## 3. Publish event pages — completed

- Added Draft, Published, and Private event states. Existing events become drafts; only Published events are returned by public queries and detail routes.
- Current, upcoming, and historical pages use each event's local calendar date. An event becomes current at local midnight on its date and remains current through the final occupied local calendar day of its planned slots. A slot ending exactly at midnight does not extend into the next day. Legacy slots with unknown duration retain classification by their start day. This follows daylight saving changes in the event's zone.
- Added public listings and details from ordered raid slots, with community branding and event-local lineup times. Tests cover publication, empty pages, route access, ordering, time-zone boundaries, and overnight slots.
- A train's scheduled time slots can remain open. In Admin, organizers assign or move streamers by editing the streamer on each time slot; the slot's time stays fixed. Public listings and details show both assigned streamers and open slots, so a simple hour-by-hour train remains readable while it is being filled.

## Twitch integration requirements

- Put Twitch API calls behind a service layer with bounded timeouts and clear failure handling. Keep credentials in deployment configuration.
- Add Twitch sign-in, Twitch-sourced streamer information, and live/offline status for the MVP. Make public schedules render from local records when Twitch is unavailable; represent unavailable status clearly. Test unavailable and malformed responses.

## Operation requirements

- Update the README with Python 3.13 local setup, required configuration, migrations, Admin usage, and test commands.
- Document self-hosted deployment: secrets, database, static files, application server, reverse proxy, backups, and upgrade steps.

## Implementation references and later ideas

### Pasted lineup import reference

- Pasted-lineup import is now required for the MVP. An example input is `*07.09.2026* Pre-Pary: P_chops 10a: 11a: ActuallySparky 12p: 1p: 2p: RottingCircuits 3p: Karmalizing 4p: Vjpcat 5p: 6p: 7p: JaniceRoberta 8p: 9p: Mroovki 10p:`.
- Parse the event date, title, time labels, and streamer names; pre-populate the event and slots in the community’s time zone. Keep empty time labels as open slots and let the organizer review ambiguous text and streamer matches before saving.

### Organizer and public front ends

- The custom React Timeline workspace is required for the MVP. Public and streamer pages use Django templates. Further public-page redesigns can follow the pilot.

### Archive old events

- Old events will clutter the organizer view over time. Consider archive options that keep historical events available without filling the main Admin event list.

### REST API

- Django REST Framework endpoints for the organizer workspace are required for the MVP. The earlier tentative FastAPI direction is superseded. Additional public API consumers are later work.

### Embedded viewing and other deferred features

- Embedded Twitch viewing with automatic following of actual raid handoffs is a stretch goal. Behavior for raids outside the event lineup remains undecided.
- Dedicated public streamer profile pages, email notifications, and independently administered communities are later work.

### Automated handoff orchestrator (TwitchIO + Celery)

- Let streamers explicitly opt in to automatic raids. During registration, obtain and securely store a Twitch OAuth token with the `channel:manage:raids` scope.
- Use a background worker to watch upcoming slot starts and trigger Twitch's Start a Raid API from the outgoing streamer to the next streamer when an explicit transition is intended. A bot message could be an alternative or companion to an automatic raid.
- Evaluate TwitchIO for Twitch chat and Helix API integration, and Celery for scheduled background work. Plan for token expiry, revoked consent, schedule changes, and failed handoffs.

### Live slot-validation engine (Twitch EventSub webhooks)

- Expose a verified Twitch EventSub webhook endpoint and subscribe to `stream.online` and `stream.offline` events for participating channels while an event is active.
- Check whether the next streamer is live five minutes before their slot; flag a missed slot and alert the organizer through Discord or text message. Confirm current live status rather than treating a missing webhook alone as proof that a streamer is offline.
- Evaluate `python-twitch-api` for EventSub subscription and webhook handling, including signature verification and SSL setup.

## Verification for every implementation step

- Run `venv/bin/python manage.py check` and `venv/bin/python manage.py test` after Django changes.
- For each model change, create the required migration, then run `venv/bin/python manage.py makemigrations --check --dry-run` to confirm migration consistency.
- Use the test database for verification. Leave the ignored local `db.sqlite3` and all existing uncommitted edits intact.
