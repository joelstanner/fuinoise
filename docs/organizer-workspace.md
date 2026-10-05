# Fuinoise organizer Timeline

The React workspace at `/organizer/` lets authorized organizers create events,
review requests, edit schedules, and publish updates. It uses the existing Django
scheduling services through a same-origin Django REST Framework API. Public
schedules and streamer dashboards still use Django templates.

## Build and run

Install the Python dependencies and build the frontend before opening the
workspace. Use Node 20.19 or a Node 22 release at least 22.12; the CI build uses
Node 22. React, Vite, and browser tooling are pinned in `frontend/package.json`,
with resolved dependencies recorded in its lockfile.

```sh
venv/bin/python -m pip install -r requirements-dev.txt
npm --prefix frontend ci
npm --prefix frontend run build
venv/bin/python manage.py migrate
venv/bin/python manage.py runserver
```

Migrations are normal application setup and upgrade steps. Automated checks use
separate databases and do not migrate or modify the ignored local database.

Sign in and grant the existing organizer role as described in
[account setup](accounts-and-eligibility.md). The **Organize** navigation link
appears for users with both event and slot editing permissions. Event creation
also requires the event creation permission. Create the Fuinoise community in
maintenance Admin if it does not exist yet.

During frontend development, `npm --prefix frontend run watch` rebuilds static
assets when source files change. Reload the Django-served page to see the build.
Generated assets are ignored by Git; deployment must build them before Django
collects static files. No separate frontend server or cross-origin login is
required.

## Editing and publication

Choose an event or create a private event. Add open slots using event-local dates
and times. Select a slot to edit its start, duration, performer, public note, or
replay URL. Event settings include the name, date, description, community, time
zone, default duration, and early signup policy. Changing the event time zone
preserves existing performance instants.

Valid detail and event edits autosave after a short pause. **Save now / retry**
applies a form edit immediately. Unsaved form edits block publication and event
switching. An invalid edit or failed save stays in the form for correction,
retry, or explicit discard; it does not replace the saved timetable.

Select a request to highlight its original preferred slots. Drag its card onto
an open slot, or choose a slot in **Assign to slot** and use **Assign request**.
Repeat to assign several performances from one request. The backend rechecks
eligibility before accepting a request assignment. Manual performer selection
also supports existing lineup records and does not approve a signup request.

Drag a performance by its handle onto a different time. The rail offers
15-minute drop positions; the detail form supports other start times and lengths.
Conflicts produce an error and leave the saved draft intact. Other performances
are never automatically shifted or swapped. On phones, the same changes are
available through the request and detail forms.

The Timeline shows one selected event-local day. Overnight performances continue
onto the next day. Its rail follows elapsed time, so daylight saving days can
have 23 or 25 hours and repeated times have distinct zone labels. Local form
entries that are ambiguous or nonexistent are rejected by the server.

Older slots with unknown lengths require a review of all missing durations
before saving or publishing. The workspace provides a form to review them
together; this does not invent durations for existing records.

**Publish schedule** applies the complete update atomically. Public visitors see
the result and streamers see confirmed performances. Repeating publication does
not create duplicate assignment confirmations. Notification delivery remains
milestone 6.

## Signup before public publication

For an unpublished event, open **Signup and public visibility** and use **Open
or update signup with this lineup**. This explicitly releases the reviewed
metadata and timetable to eligible streamers for preference requests. The public
event stays hidden, and staged performances are not confirmed or shown in the
confirmed-performance dashboard.

Later autosaves remain private. Update the signup lineup explicitly to release
another reviewed version, or publish the event to make it public and confirm its
assignments. The immediate visibility controls can close early signup or make an
event private. They invalidate its existing working draft and require an
explicit reset before further editing.

## Stale drafts and recovery

A cancellation, maintenance edit, or another organizer's update can make a draft
stale. The API rejects outdated version numbers instead of overwriting newer
work. **Reload draft** loads the latest saved private draft. If the underlying
schedule changed, the workspace displays the preserved stale draft and blocks
editing and publication.

Review its private edits before using **Discard draft and reload schedule**.
That action replaces the draft with the current schedule. A canceled performance
cannot be silently restored by publishing an old draft. The Details panel also
provides an explicit reset for a current draft.

## API and verification

All workspace endpoints require an active organizer session, use JSON, and send
responses with caching disabled. Mutations require Django's CSRF token. Input
serializers reject supplied request provenance and source slot IDs; only the
assignment service attaches a reviewed request to a slot.

| Endpoint under `/organizer/api/events/` | Operation |
| --- | --- |
| Collection GET or POST | Load event choices, communities, time zones and performer names, or create a private event. |
| `<id>/draft/` GET or PUT | Read the private draft or save the complete slot list and event fields with its expected version. |
| `<id>/assign/` POST | Assign a request to a draft slot with expected request and draft versions. |
| `<id>/publish/` POST | Publish the reviewed draft with its expected version. |
| `<id>/signup/` POST | Release the reviewed signup timetable without public publication or confirmations. |
| `<id>/reset/` POST | Explicitly discard private edits and rebuild from the current schedule. |
| `<id>/requests/<request_id>/decline/` POST | Decline a request and record private organizer notes. |
| `<id>/visibility/` POST | Immediately close/open the early signup policy or hide an event using its expected schedule version. |
| `<id>/import/preview/` POST | Parse pasted text for review without saving records. |
| `<id>/import/apply/` POST | Apply reviewed slots atomically to the private draft using its expected version. |
| `<id>/twitch/lookup/` POST | Look up a channel and return a short-lived signed match without creating records. |
| `<id>/twitch/refresh/` POST | Refresh stored public information for performers in the saved and working lineups. |

Django tests cover authorization, CSRF, complete-timetable conflicts, private
saves, stale versions, DST validation, request assignment, publication,
cancellation recovery, early signup, and legacy duration review. Frontend unit
tests cover day rails, overnight layouts, DST, and save payloads. CI runs the
Python tests with the 80 percent application coverage minimum, frontend format
checks and unit tests, the production build, and the browser workflow.

```sh
venv/bin/python -m pytest
npm --prefix frontend test
npm --prefix frontend run check
npm --prefix frontend run build
cd frontend
npx playwright install chromium
npm run test:browser
```

The browser check starts Django with a disposable database and test identities.
It exercises drag assignment and movement, rejected overlaps, keyboard edits,
save failures and retry, reload, publication, cancellation recovery, early
signup, event creation and editing, legacy duration review, and desktop/phone
layouts. It also checks private import through publication, Twitch status expiry,
visitor-local times, expanded phone import review, and public rendering without
JavaScript. It stops the server and deletes its database and login fixtures when
finished. Screenshots remain in the reported temporary directory.

Use `PLAYWRIGHT_BROWSER_CHANNEL=chrome` to test an installed Chrome instead of a
downloaded Chromium. Set `PYTHON_BIN` when the project Python executable is not
`venv/bin/python`. See [import and public information](import-and-public-information.md)
for reviewed imports, Twitch configuration, and refresh operations. Live
Twitch/Discord rehearsal, notifications, deployment, backups, and the real pilot
remain later work.

Package compatibility and integration references:
[REST Framework requirements](https://pypi.org/project/djangorestframework/),
[session authentication and CSRF](https://www.django-rest-framework.org/api-guide/authentication/#sessionauthentication),
[React in an existing project](https://react.dev/learn/add-react-to-an-existing-project),
and [Vite requirements](https://vite.dev/guide/).
