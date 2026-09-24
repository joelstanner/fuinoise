# Fuinoise TODO

Keep `Event`, `Streamer`, and `RaidSlot`, their existing data, and the current migrations. Django Admin remains the organizer interface. Build public pages from these records rather than introducing a separate schedule store.

## Completed: Fuinoise musician and raid data

- Added reusable instrument and genre records linked to streamers, plus a streamer time zone, raid availability status, preferences, and private organizer notes.
- Added weekly availability windows in each streamer's time zone. An end time earlier than the start time means the window continues into the following day. These windows are planning information, not booked raid slots.
- Kept `RaidSlot` as the event participation record and its existing unique position as raid order. Added an optional planned handoff time, while retaining the scheduled start and slot notes.
- Added communities, streamer memberships with role and notes, and an event community. Existing events are assigned to the default Fuinoise community by a data migration.
- Exposed the new records in Django Admin and added model, Admin, and migration tests.

## 1. Improve organizer Admin

- Add an explicit `RaidSlot` position/order field and a migration. Make slots reorderable within an event without changing their scheduled `start` times; define a stable fallback for existing rows and prevent ambiguous ordering.
- Show the order, streamer, start time in the event's time zone, and relevant notes and replay information in the event inline. Add useful Event, Streamer, and RaidSlot lists with filters and search.
- Fix the slot time display by converting with the event's zone directly. Do not call `timezone.activate()` or otherwise change the request's active time zone while rendering Admin.
- Add tests for reordering, Admin display, and preservation of the active time zone.

## 2. Add community configuration

- Done: `Community` has name, description, website, and logo URL; each event belongs to one community. Existing events were backfilled, and Admin exposes the association.
- Remaining: define any further public branding or site configuration needed by the public pages and test full organizer workflows for those settings.

## 3. Publish event pages

- Add explicit event publication/status fields and a migration. Define which events are public and how current, upcoming, and historical events are selected, including time-zone and boundary behavior.
- Build public current, upcoming, historical, and event detail pages using the existing event and ordered raid slot records. Show community branding and event time zones clearly. Keep drafts/private events out of public queries.
- Add route, query, and rendering tests for published, unpublished, empty, and boundary cases.

## 4. Isolate Twitch integration

- Put Twitch API calls behind a service layer with bounded timeouts and clear failure handling. Keep credentials in deployment configuration.
- Make public pages render from local records when Twitch is unavailable; treat live Twitch data as optional enrichment. Test unavailable and malformed responses.

## 5. Document operation

- Update the README with Python 3.13 local setup, required configuration, migrations, Admin usage, and test commands.
- Document self-hosted deployment: secrets, database, static files, application server, reverse proxy, backups, and upgrade steps.

## Verification for every implementation step

- Run `venv/bin/python manage.py check` and `venv/bin/python manage.py test` after Django changes.
- For each model change, create the required migration, then run `venv/bin/python manage.py makemigrations --check --dry-run` to confirm migration consistency.
- Use the test database for verification. Leave the ignored local `db.sqlite3` and all existing uncommitted edits intact.
