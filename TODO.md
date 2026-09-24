# Fuinoise TODO

Keep `Event`, `Streamer`, and `RaidSlot`, their existing data, and the current migrations. Django Admin remains the organizer interface. Build public pages from these records rather than introducing a separate schedule store.

## 1. Improve organizer Admin

- Add an explicit `RaidSlot` position/order field and a migration. Make slots reorderable within an event without changing their scheduled `start` times; define a stable fallback for existing rows and prevent ambiguous ordering.
- Show the order, streamer, start time in the event's time zone, and relevant notes and replay information in the event inline. Add useful Event, Streamer, and RaidSlot lists with filters and search.
- Fix the slot time display by converting with the event's zone directly. Do not call `timezone.activate()` or otherwise change the request's active time zone while rendering Admin.
- Add tests for reordering, Admin display, and preservation of the active time zone.

## 2. Add community configuration

- Add a `Community` model and link each `Event` to one community. Store public name, branding, and site configuration in data rather than Golden Shrimp Guild-specific code.
- Add a data migration that creates a default community and assigns every existing event to it. Preserve event, streamer, and raid slot records and all earlier migrations.
- Expose community fields and event association in Admin; add tests for the migration and organizer workflows.

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
