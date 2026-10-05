# Fuinoise MVP definition and architecture

Agreed scope as of October 4, 2026. This document defines the release target;
it does not mean the features below are already implemented. See [the project
TODO](../TODO.md) for remaining work.

The [implementation plan](mvp-implementation-plan.md) describes milestone order
and the scheduling foundation to implement first.

Fuinoise lets its organizers prepare and publish raid-train schedules through
a custom interface. Eligible streamers request slots, organizers approve
assignments, and public visitors find schedules and Twitch channels. A successful
real pilot event is required before the MVP is finished.

## Audience

The first release serves Fuinoise organizers, participating streamers, and public
visitors. Support for independently administered communities is later work.
Existing community, musician, and availability records remain available.

## Organizer workflow

- Authorized organizers use a custom Timeline workspace for routine scheduling.
  Django Admin remains available for maintenance.
- Organizers create and edit event names, descriptions, dates, and time zones.
- They can edit the timetable manually or import a pasted lineup. Imports show
  parsed times and streamer matches for review before saving; empty times remain
  open slots, and ambiguous input requires correction.
- Request cards can be dragged into timed slots. Scheduled performance cards can
  also be dragged along the timeline to change their start times.
- Individual slots can have different durations. The default is one hour and can
  be changed. Organizers can leave slots open and replace or remove assignments.
- Overlapping performances produce a visible conflict. A conflicting move is not
  saved; it does not automatically swap cards or shift other performances.
- Valid organizer edits autosave to a private working draft. The public schedule
  changes when the organizer explicitly publishes an update.
- Assignments become confirmed to streamers on publication of the schedule
  update. Dragging a card or autosaving a draft sends no assignment confirmation.
- Publishing an event opens signup and displays the developing lineup by default.
  A per-event setting allows signup to eligible streamers before public
  publication.
- Organizers review eligibility and slot requests, approve assignments, correct
  schedules, and withdraw events from public view. Private organizer information
  stays private.

## Streamer workflow

- Streamers sign in with Twitch and connect Discord before requesting slots.
- Eligibility requires membership in the Fuinoise Discord server plus organizer
  approval based on participation. Organizers can override the Discord requirement.
- Streamers submit several preferred slots. Organizers may assign multiple
  performances to one streamer within an event.
- Streamers can see their requests and confirmed assignments on their dashboard.
  Tentative organizer draft assignments remain private until publication.
- Streamers can cancel their own assigned slots directly, immediately reopening
  them. Other assignment changes go through organizers outside the site.
- Displayed streamer information comes from Twitch for the MVP. Dedicated public
  streamer profile pages are later work.

## Public experience

- Visitors can browse current, upcoming, and past events and open an event's
  ordered lineup without signing in.
- Schedules display both event time and visitor-local time, including overnight
  events and daylight saving transitions.
- Lineups show assigned streamers, open slots, Twitch channel links, live/offline
  status, and optional replay links.
- Essential pages work on phones and desktop browsers.
- Schedules remain readable when Twitch is unavailable. Unavailable live-status
  data must not be presented as a confirmed offline status.

## Notifications

Requests, confirmed assignments, and cancellations appear in the relevant on-site
dashboards. Assignment confirmation notifications are triggered by publication,
not by draft edits. Discord notifications go to an organizer channel and affected
streamers by private message. Email notifications are deferred.

## Architecture

- **Django** owns accounts, permissions, event and scheduling records, validation,
  working drafts, and publication. Preserve existing records and migrations;
  extend the model through migrations as required.
- **React** powers the organizer Timeline workspace, including card movement,
  selection, details, conflicts, and save state.
- **Django REST Framework** exposes the endpoints needed by the workspace.
  Server-side rules remain authoritative even when the browser previews a move.
- **Django templates** render public schedules, streamer signup, and streamer
  dashboards. Browser-side JavaScript handles visitor-local times and other
  small interactions where needed.
- Serve the workspace and its API on the same origin using Django session
  authentication and CSRF protection. Keep the frontend source in the same
  repository and include its build in deployment.
- Twitch and Discord integrations run behind backend service boundaries, with
  credentials held in deployment configuration. Worker infrastructure, frontend
  tooling, package versions, database deployment, and hosting are implementation
  decisions still to be made.

The API choice is Django REST Framework. The earlier tentative FastAPI idea is
superseded for the MVP. A React frontend for the entire site is outside this scope.

## Release acceptance

The MVP is finished when all of the following are verified:

- An actual pilot event completes signup, eligibility review, assignment approval,
  publication, and participation through the intended interfaces. Organizers and
  streamers can perform their normal tasks without developer assistance.
- Request assignment and performance movement both work through drag-and-drop in
  the Timeline workspace. Time and duration edits also have keyboard-accessible
  controls.
- Conflicts leave the saved schedule intact. Draft edits survive a reload and
  remain private until publication. Draft assignments send no confirmation;
  publication confirms the final assignments and triggers their notifications.
- Imports, flexible durations, overnight schedules, and both displayed time zones
  behave correctly.
- Direct cancellation reopens the affected slot and updates the public schedule
  and relevant notifications. Publishing an older working draft must not silently
  reinstate a canceled assignment.
- Permission checks prevent unauthorized changes and disclosure of private data.
  Account linking, eligibility, and organizer overrides work as defined above.
- On-site and Discord notifications reach their intended recipients during the
  pilot. Integration failures are visible to operators and do not corrupt the
  stored schedule.
- The public deployment uses HTTPS and preserves records across restarts and
  upgrades. Setup, operation, upgrades, and backups are documented.
- A backup is successfully restored into a separate environment.
- Project quality checks, Django checks, tests, and migration checks pass. No
  unresolved issue blocks the core pilot workflow.

## Stretch goals and later work

- Embedded Twitch viewing and automatic following of actual Twitch raid handoffs.
  Visitors should not have to choose each successive streamer. Scheduled slot
  times are not the desired handoff trigger. Behavior for raids outside the lineup
  remains undecided.
- Dedicated public streamer profile pages and email notifications.
- Automatic initiation of Twitch raids and advance missed-slot alerts.
- Independently administered communities and additional public API consumers.
