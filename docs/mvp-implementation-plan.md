# Fuinoise MVP implementation plan

This plan implements [the agreed MVP definition](mvp-definition.md). Complete
each milestone with its acceptance checks before moving to dependent work. The
existing application is the foundation; the interactive prototype is a design
reference, not production code.

## Current foundation

The project already has communities, streamers, events, open lineup slots,
event-local Admin entry, and public current/upcoming/history pages. Public views
read `Event` and `RaidSlot` records directly. The scheduling backend now adds
planned durations, private working snapshots, atomic publication, and version
checks. Twitch account association, Discord linking and membership checks, and
custom organizer participation reviews are implemented. Live provider rehearsal
still needs credentials and the Fuinoise server configuration; see
[account setup](accounts-and-eligibility.md). Streamer requests, confirmed-assignment
dashboards, cancellation, and backend organizer assignment operations are now
implemented; see [workflow details](requests-and-assignments.md). The custom
Timeline, scheduling API endpoints, and notification delivery remain future
milestones.

Milestone 1 is implemented and verified with Django tests, including migration
preservation, draft privacy, complete interval validation, stale writes, atomic
rollback, and cancellation followed by an old publication attempt.

## Implementation sequence

| Milestone | Deliverable | Evidence of completion |
| --- | --- | --- |
| 1. Scheduling foundation | Flexible durations, overlap rules, working drafts, explicit publication, signup settings, and version checks. | Draft changes survive reload without changing the public lineup; conflicting and stale writes are rejected; cancellation cannot be undone by an older draft. |
| 2. Accounts and eligibility | Twitch sign-in, Discord linking and membership checks, streamer identity association, organizer permissions, participation approval, and overrides. | Eligible streamers can participate; unauthorized users cannot approve eligibility, use overrides, or edit another account's assignments. |
| 3. Requests and assignments | Several preferred slots per request, multiple approved performances, streamer template dashboards, direct cancellation, and backend assignment operations. | A streamer submits preferences; an organizer assigns performances; the streamer cancels one and the published slot reopens. |
| 4. Organizer Timeline | Django REST Framework endpoints and a React workspace, including event editing, dragging, detail controls, autosave, and publication. | Requests can be dragged into slots and performances moved through time; conflicts leave the saved schedule intact; keyboard controls support the same edits. |
| 5. Import and public information | Pasted-lineup review and import, Twitch-sourced streamer information, live status, and event/visitor-local time display. | Ambiguous imports require correction; empty labels become open slots; overnight times are correct; schedules remain usable during Twitch failures. |
| 6. Notifications | On-site notifications, Discord organizer-channel alerts, and streamer private messages with recorded delivery status and retry handling. | Requests, confirmed assignments, and cancellations reach the correct recipients; failed deliveries are visible and retries do not create duplicate notifications. |
| 7. Deployment and pilot | Production configuration, frontend build, persistent storage, operating documentation, backup restoration, and a real event pilot. | A separate restore succeeds and organizers and streamers complete an actual event without developer assistance or a blocking workflow issue. |

Start production configuration during integration work so OAuth callbacks and
Discord delivery can be checked on a hosted test environment before the pilot.
Make package and worker choices when their milestones begin; verify compatibility
with the project's pinned Django and Python versions before adding dependencies.

## First milestone

### Slot durations and interval rules

- Add an explicit planned duration to slots and a configurable event default,
  initially one hour. Treat this as planned schedule length, not proof of when a
  Twitch stream or raid actually ends.
- Validate the complete resulting timetable, including unchanged slots, whenever
  an assignment or time changes. Back-to-back slots are valid; overlapping slots
  are not. Do not automatically swap or shift other performances.
- Use actual dated instants for overnight schedules and event-local entry. Reject
  ambiguous or nonexistent local times with an actionable error.
- Preserve existing IDs, start times, assignments, replay links, notes, and
  publication states. Migration 0013 leaves existing durations unknown. New
  slots use the configurable event default; older slots require explicit
  duration review before a draft can be saved or published. Existing public
  schedules stay readable while awaiting review.
- Update maintenance Admin and public period classification for explicit slot
  intervals. A slot ending exactly at midnight occupies only the preceding day;
  unknown legacy durations retain classification by start day. Tests cover both.

### Working drafts and publication

- Keep the published schedule readable independently of organizer autosaves.
  Draft slot changes and event fields that appear publicly must remain private
  until publication.
- Preserve stable slot identity across draft and published representations so
  requests, assignments, and cancellations can refer to the correct slot. Choose
  the draft storage structure before writing its migration; it must use the
  existing Django data model as the source of truth.
- Add backend operations for reading a draft, saving a valid change, and
  publishing a complete update. Apply publication atomically so visitors never
  see half of an update.
- Give drafts a version and record the published version they were based on.
  Reject stale changes rather than overwriting a newer organizer edit.
- Treat a direct streamer cancellation as an immediate update to the relevant
  assignment. Synchronize or invalidate affected drafts so later publication
  cannot silently restore the canceled streamer.
- Add the per-event signup setting: publication opens signup by default, with an
  option to open signup to eligible streamers while the public event is a draft.

The first milestone ends with tested scheduling and publication operations. The
custom Timeline and external account integrations arrive in later milestones.

### Implemented backend operations

`fuinoise_live/scheduling.py` provides organizer-authorized draft read, save,
reset, and publication operations. A save supplies the complete resulting slot
list and the caller's expected draft version. Omitted slots are removed from the
working draft; only explicit publication applies removals to the public lineup.
Published slot IDs remain stable when performances are moved or reassigned.

The account-linked cancellation endpoint derives the verified streamer identity
from the signed-in user, reopens that streamer's published slot immediately, and
invalidates older drafts. An explicit reset discards private edits and
rebuilds the draft from the current public schedule; stale saves never silently
discard or overwrite those edits.

Publication returns changed slot IDs and newly confirmed assignment IDs for
later notification integration. Repeated publication of unchanged assignments
returns no new confirmations. Notification delivery is milestone 6.

Maintenance Admin edits continue to update canonical records directly. They
invalidate an existing working draft, including changes detected outside these
operations. Admin is for maintenance; routine draft editing will use the custom
Timeline. The event's early-signup policy is available in maintenance Admin and
on new event records. The streamer signup page now uses this policy and keeps
private working schedule edits hidden.

## Assignment confirmation

An assignment becomes confirmed to the streamer when the organizer publishes the
schedule update. Dragging a card or autosaving a working draft does not confirm
the assignment, notify the streamer, or expose it on their dashboard. Publication
confirms the final assignments and triggers the relevant notifications. Publishing
an unchanged assignment must not send a duplicate confirmation.

## Verification and data preservation

Use the test database for model, migration, workflow, and failure checks. Leave
the ignored local `db.sqlite3` and existing edits intact. Cover overlapping
intervals, overnight and daylight saving cases, draft privacy, stale saves,
atomic publication, and cancellation followed by an old publication attempt.

After Django changes, run `venv/bin/python manage.py check` and
`venv/bin/python manage.py test`. After model changes, add migrations and run
`venv/bin/python manage.py makemigrations --check --dry-run`. Before committing,
run `venv/bin/black --check .`, `venv/bin/ruff check .`, and
`venv/bin/mypy fuinoise fuinoise_live`.

During frontend implementation, add focused browser checks for card assignment,
performance movement, rejected drops, save failures, publication, keyboard
editing, and phone layouts. Verify Twitch and Discord failure cases without
depending on live services for routine tests; use real integrations during the
hosted rehearsal and pilot.

## Deferred scope

Embedded viewing, automatic following of actual Twitch raids, dedicated public
streamer profiles, email notifications, automatic raid initiation, and independent
community administration remain outside the required milestones.
