# Fuinoise TODO

Keep `Event`, `Streamer`, and `RaidSlot`, their existing data, and the current migrations. Django Admin remains the organizer interface. Build public pages from these records rather than introducing a separate schedule store.

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
- Current, upcoming, and historical pages use each event's local calendar date. An event becomes current at local midnight on its date and remains current through the local calendar day of its latest slot start. It moves to history at the next local midnight. This follows daylight saving changes in the event's zone.
- Added public listings and details from ordered raid slots, with community branding and event-local lineup times. Tests cover publication, empty pages, route access, ordering, time-zone boundaries, and overnight slots.
- A train's scheduled time slots can remain open. In Admin, organizers assign or move streamers by editing the streamer on each time slot; the slot's time stays fixed. Public listings and details show both assigned streamers and open slots, so a simple hour-by-hour train remains readable while it is being filled.

## 4. Isolate Twitch integration

- Put Twitch API calls behind a service layer with bounded timeouts and clear failure handling. Keep credentials in deployment configuration.
- Make public pages render from local records when Twitch is unavailable; treat live Twitch data as optional enrichment. Test unavailable and malformed responses.

## 5. Document operation

- Update the README with Python 3.13 local setup, required configuration, migrations, Admin usage, and test commands.
- Document self-hosted deployment: secrets, database, static files, application server, reverse proxy, backups, and upgrade steps.

## Ideas

### Create an event from a pasted lineup

- Add an organizer text field that accepts a pasted schedule such as `*07.09.2026* Pre-Pary: P_chops 10a: 11a: ActuallySparky 12p: 1p: 2p: RottingCircuits 3p: Karmalizing 4p: Vjpcat 5p: 6p: 7p: JaniceRoberta 8p: 9p: Mroovki 10p:`.
- Parse the event date, title, time labels, and streamer names; pre-populate the event and slots in the community’s time zone. Keep empty time labels as open slots and let the organizer review ambiguous text and streamer matches before saving.

### Refactor Admin and public front ends

- Plan a substantial redesign of the organizer Admin workflow and public pages as the product grows.

### Archive old events

- Old events will clutter the organizer view over time. Consider archive options that keep historical events available without filling the main Admin event list.

### REST API

- Likely use FastAPI for future REST API endpoints; decide the API boundary and how it will share the existing Django models and data before implementation.

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
