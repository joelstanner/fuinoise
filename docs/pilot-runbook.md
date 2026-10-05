# Fuinoise hosted rehearsal and pilot

The MVP is complete when organizers and streamers finish an actual event without
developer assistance or a blocking workflow issue, and production operation and
recovery have been verified. Local implementation and automated tests do not
complete these checks. Record evidence here or in a private release record; never
include credentials, OAuth codes, session cookies or private message contents.

## Release record

| Item | Required evidence |
| --- | --- |
| Host and public domain | Chosen server, domain and operator. Pending. |
| Deployed revision | Exact commit and release date. Pending. |
| Integration configuration | Twitch app, Discord app/server/channel and callback configuration verified privately. Pending. |
| Backup destination and policy | Private off-site storage, retention and operator. Pending. |
| Rehearsal and pilot | Event, organizer, participating streamers, dates and results. Pending. |

## Hosted rehearsal

Use a small test event and consenting accounts. Follow [deployment and recovery](deployment.md).

- [ ] Deploy the tested revision with debug off, persistent data, collected frontend
  assets and the supplied proxy/service setup. Run `check_release --require-integrations`.
- [ ] Confirm a trusted public HTTPS certificate and HTTP redirect; check `/health/`
  externally and verify that the app port and private files are inaccessible publicly.
- [ ] Restart the web service and reboot the host. Confirm published/private records,
  accounts and queued alerts survive; timers resume and successful backups appear.
- [ ] Complete real Twitch sign-in, Discord linking and membership checks. Confirm
  a nonmember needs an organizer override and ordinary users cannot grant one.
- [ ] Grant organizer permissions to verified accounts. Confirm the custom Timeline
  works without maintenance Admin or staff/superuser access.
- [ ] Create/import an event, correct ambiguous input, request multiple preferences,
  assign/move performances, and publish. Verify drafts remain private and changes
  become public and confirmed only on publication.
- [ ] Cancel one confirmed performance from its streamer account. Confirm the slot
  reopens immediately and an older draft cannot restore it.
- [ ] Verify on-site notices, an actual organizer-channel Discord alert and a private
  message to the intended streamer. Check recorded receipts and that repeated
  unchanged publication does not send another confirmation.
- [ ] Verify blocked private messages and a safe known-failure retry. Review an
  uncertain outcome using receipt verification; do not force a resend.
- [ ] Check Twitch profile/live refresh and expired status. Temporarily disable the
  refresh job and confirm public schedules remain readable with unavailable status.
- [ ] Use desktop and phone accounts, keyboard Timeline controls, visitor-local
  times, and public pages without JavaScript. Visitors can follow the schedule
  without an account or required interaction; an embedded player remains stretch scope.
- [ ] Copy a verified snapshot and manifest to the chosen off-site destination.
  Recover that pair into a separate database/host with outgoing delivery disabled.
  Compare published/private schedules, accounts, requests, notices and migration
  state. Confirm historical unsent messages are held and record recovery time.
- [ ] Rehearse an upgrade and the compatible rollback/recovery path. Confirm the
  environment secret and persistent data are retained across releases.

## Real event pilot

- [ ] Choose the actual event and name the organizing operator. Give participants
  the public and account links and the ordinary workflow instructions.
- [ ] Organizers complete eligibility reviews, import/create the lineup, process
  requests, edit the Timeline and publish without developer assistance.
- [ ] Streamers sign in/link, request preferences, see confirmed performances and
  notices, and complete the event workflow. Exercise a cancellation if appropriate.
- [ ] Visitors follow the published event and see clear times, streamer information,
  and open slots. Observe the site on phones during the event.
- [ ] The operator checks health, recurring jobs, actual notification receipts and
  backup/off-site copy age. Record outages, delivery failures and database lock errors.
- [ ] Record issues, affected workflows and severity. Resolve blocking issues and
  repeat affected checks before declaring the MVP finished.
- [ ] Record the successful event, participants' workflow confirmation, deployed
  revision, hosted rehearsal evidence and separate recovery evidence.

Every box is still pending on the actual host. Local verification has covered
production startup/restart, secure application behavior, a Caddy HTTPS/static proxy
using a temporary CA, and restoration of all 31 database tables with historical
Discord delivery held. This is preparation for the hosted checks above.
