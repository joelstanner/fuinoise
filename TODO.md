# Fuinoise TODO

Keep `Event`, `Streamer`, and `RaidSlot`, their existing data, and the current migrations. Django Admin remains the organizer interface. Build public pages from these records rather than introducing a separate schedule store.

## Completed: Fuinoise musician and raid data

- Added reusable instrument and genre records linked to streamers, plus a streamer time zone, raid availability status, preferences, and private organizer notes.
- Added weekly availability windows in each streamer's time zone. An end time earlier than the start time means the window continues into the following day. These windows are planning information, not booked raid slots.
- Kept `RaidSlot` as the event participation record and its existing unique position as raid order. Added an optional planned handoff time, while retaining the scheduled start and slot notes.
- Added communities, streamer memberships with role and notes, and an event community. Existing events are assigned to the default Fuinoise community by a data migration.
- Exposed the new records in Django Admin and added model, Admin, and migration tests.

## 1. Improve organizer Admin — completed

- Added a unique `RaidSlot` position within each event, backfilled existing slots by start time and ID, and made Admin inline swaps preserve scheduled start times.
- Added event, streamer, and slot Admin lists with filters and search; the event inline shows order, streamer, event-local time, notes, and replay information.
- Event-local time is converted directly without changing the active time zone. Tests cover reordering, Admin rendering, and time zone preservation.

## 2. Add community configuration — completed

- `Community` has the name, description, website, and logo URL needed by the planned public pages; each event belongs to one community. Existing events were backfilled, and Admin exposes the association.
- Admin lists the branding URLs and supports searching descriptions. A workflow test covers creating a community, associating an event, and updating its branding without losing the association.

## 3. Publish event pages — completed

- Added Draft, Published, and Private event states. Existing events become drafts; only Published events are returned by public queries and detail routes.
- Current, upcoming, and historical pages use each event's local calendar date. An event becomes current at local midnight on its date and remains current through the local calendar day of its latest slot start or planned handoff, if that falls later. It moves to history at the next local midnight. This follows daylight saving changes in the event's zone.
- Added public listings and details from ordered raid slots, with community branding and event-local lineup times. Tests cover publication, empty pages, route access, ordering, time-zone boundaries, and overnight slots.
- A train's scheduled time slots can remain open. In Admin, organizers assign or move streamers by editing the streamer on each time slot; the slot's time stays fixed. Public listings and details show both assigned streamers and open slots, so a simple hour-by-hour train remains readable while it is being filled.

## 4. Isolate Twitch integration

- Put Twitch API calls behind a service layer with bounded timeouts and clear failure handling. Keep credentials in deployment configuration.
- Make public pages render from local records when Twitch is unavailable; treat live Twitch data as optional enrichment. Test unavailable and malformed responses.

## 5. Document operation

- Update the README with Python 3.13 local setup, required configuration, migrations, Admin usage, and test commands.
- Document self-hosted deployment: secrets, database, static files, application server, reverse proxy, backups, and upgrade steps.

## Ideas

### Automated handoff orchestrator (TwitchIO + Celery)

- Let streamers explicitly opt in to automatic raids. During registration, obtain and securely store a Twitch OAuth token with the `channel:manage:raids` scope.
- Use a background worker to watch scheduled handoff times and trigger Twitch's Start a Raid API from the outgoing streamer to the next streamer. A bot message could be an alternative or companion to an automatic raid.
- Evaluate TwitchIO for Twitch chat and Helix API integration, and Celery for scheduled background work. Plan for token expiry, revoked consent, schedule changes, and failed handoffs.

### Live slot-validation engine (Twitch EventSub webhooks)

- Expose a verified Twitch EventSub webhook endpoint and subscribe to `stream.online` and `stream.offline` events for participating channels while an event is active.
- Check whether the next streamer is live five minutes before their slot; flag a missed slot and alert the organizer through Discord or text message. Confirm current live status rather than treating a missing webhook alone as proof that a streamer is offline.
- Evaluate `python-twitch-api` for EventSub subscription and webhook handling, including signature verification and SSL setup.

## Verification for every implementation step

- Run `venv/bin/python manage.py check` and `venv/bin/python manage.py test` after Django changes.
- For each model change, create the required migration, then run `venv/bin/python manage.py makemigrations --check --dry-run` to confirm migration consistency.
- Use the test database for verification. Leave the ignored local `db.sqlite3` and all existing uncommitted edits intact.
