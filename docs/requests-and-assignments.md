# Fuinoise requests and assignments

Streamers can now submit preferred slots, review their requests and confirmed
performances, withdraw requests, and cancel individual performances. Organizer
assignment operations are available in the React Timeline and its API; see
[workspace setup and usage](organizer-workspace.md). Discord notifications remain milestone 6.

## Streamer workflow

Sign in with Twitch and open **Your account**. Events accepting preferences link
to a signup page, where a streamer can select several future slots and leave
private notes for organizers. Choices do not reserve slots or confirm assignments.
The signup page shows the event's time zone and each slot's planned duration.

Submission rechecks eligibility, including Discord membership unless an organizer
has granted an override. Only open slots or slots already assigned to that
streamer can be selected. Slots assigned to someone else, past start times, and
legacy slots with unknown duration are unavailable. Organizers must review those
legacy durations before opening them for requests.

Published events accept signup by default. A draft event accepts early signup
only when its explicit setting is enabled; only currently eligible streamers can
open that early signup page. This does not expose the event on public routes or
reveal private working schedule edits.

For a new event, organizers explicitly release its reviewed signup timetable
through **Open or update signup with this lineup**. This makes those choices
available before public publication. Later working edits stay private until
another explicit release or publication; early signup does not confirm assignments.

One request is stored per event and streamer. Updating preferences replaces the
previous choices; withdrawn or declined requests can be resubmitted. The server
checks both the request version and the published schedule version before saving.
When either changed, reload and review the current choices before submitting.

The dashboard shows the streamer's own requests and published assignments.
Tentative assignments stay private until publication. Private request notes
never appear on public event pages; organizer notes are absent from streamer
dashboards. Original preference times remain recorded if a slot is moved or
removed, and the dashboard marks that schedule change.

## Assignment and publication operations

`fuinoise_live/requests.py` provides the operations used by the streamer views
and organizer API:

- `save_slot_request` accepts preferred published slot IDs, private notes, and
  expected request and schedule versions for a verified, eligible streamer.
- `assign_request_to_draft` stages one performance for an organizer using the
  expected request and draft versions. Calling it for several slots stages
  several performances from the same request. It rejects occupied slots,
  cross-event assignments, past times, stale data, and invalid intervals.
- `decline_slot_request` records an organizer decision and private notes.
- `withdraw_slot_request` withdraws the signed-in streamer's own request.
- `cancel_assignment` cancels the signed-in streamer's own confirmed performance
  using the expected published schedule version.

Preferences guide the organizer; they do not impose reservations. Assigning or
moving a performance remains an organizer decision. Slot changes use the
existing complete-timetable validation and never automatically shift another
performance.

Each tentative request assignment records the request version reviewed by the
organizer. Publication rejects a new assignment when that request was changed,
withdrawn, or declined, or when the streamer no longer has current eligibility.
The organizer must review the latest request and reapply the assignment or
remove it from the draft. These errors leave the public timetable unchanged.

Publication confirms the final assignments atomically. Republishing an unchanged
assignment does not produce another confirmation. A request's status describes
its submission; confirmed performances are listed separately. Withdrawing or
declining a request blocks new performances from that submission and leaves
already confirmed performances intact.

## Withdrawal and cancellation

The dashboard's **Withdraw this request** action stops consideration of a request.
It does not cancel confirmed performances. The **Cancel this performance** action
immediately reopens only that published slot, retaining its time and duration.
Other performances and the streamer's request remain unchanged.

Cancellation is available for upcoming performances and performances whose
planned interval is still active. Completed performances cannot be canceled.
Withdrawal and cancellation require an active Twitch-associated account but do
not require continuing eligibility or an available Discord service. They use
POST requests with CSRF protection and derive ownership from the signed-in user.
Supplying another streamer's identity does not grant access.

A canceled slot invalidates existing schedule drafts. An explicit organizer
reset rebuilds a draft from the current public schedule and discards private
edits; an older draft cannot restore the canceled assignment. Stale cancellation
forms likewise require reloading the dashboard.

## Data preservation and verification

Migration 0015 adds request records, preference snapshots, and optional request
associations on draft and published slots. Existing slots and private drafts keep
their identities, times, durations, notes, and publication states. Existing drafts
without request associations retain compatible baseline fingerprints.

Automated workflow tests cover preference submission, multiple private
assignments, publication, ownership, eligibility failures, stale requests,
withdrawal, cancellation, removed slots, private data, and migration preservation.
Browser verification uses an isolated database and test identities; it exercises
submission, private assignment visibility, publication, cancellation, and
withdrawal on desktop and phone layouts. The local SQLite database is untouched.

Live Twitch and Discord rehearsal still needs the configuration described in
[account setup](accounts-and-eligibility.md). Organizer browser checks now cover
drag assignment, keyboard controls, save failure recovery, publication, and
cancellation followed by a stale draft. Notification delivery and
the real-event pilot remain release requirements.
