# Notifications and Discord delivery

Requests, published assignments, and cancellations now create durable on-site
notifications and queued Discord deliveries. Applying migration 0017 creates the
notification, read-state, outbox, and cooldown tables. It preserves existing
streamers, accounts, Twitch snapshots, and schedules, and does not send alerts
for historical records.

## What triggers a notification

| Change | Notification |
| --- | --- |
| Submit or update slot preferences | Organizers and the submitting streamer receive a review notice. Preferences do not confirm a performance. |
| Withdraw or decline a request | Organizers and the affected streamer receive a status notice. Existing confirmed performances stay assigned. |
| Publish a newly assigned performance | Organizers and the assigned streamer receive a confirmation. |
| Publish a changed start, length, or event information | Organizers and the affected streamer receive an update. |
| Publish a removal or reassignment | The previous streamer receives a removal notice; the new streamer receives confirmation. Organizers receive both. |
| Cancel a published performance | Organizers and the canceling streamer receive a cancellation notice; the public slot reopens. |

Draft edits, dragging, imports, and early signup release send no assignment
confirmation. Republishing unchanged assignments creates no duplicate notices.
Notifications and outbox rows are written inside the successful business
transaction, so a failed or rolled-back operation creates no alert. Provider
requests never occur while saving requests, publishing, or canceling.

Public facts in these notices include the performer, event, scheduled time, and
duration. Private request notes, organizer review reasons, and credentials are
excluded. A performer without a linked Fuinoise login generates the organizer
notice only; no Discord identity or account ownership is invented.

## On-site use

Signed-in users open **Notifications** from the navigation. Streamers see their
own notices; current organizers additionally see the organizer audience. Each
user has independent **Mark as read** state. The inbox is paginated, and each
notification has a private direct link. Recent notices also appear on the
account dashboard. The pages and read controls work without JavaScript.

Organizers follow **Check Discord deliveries** to `/organizer/notifications/`.
The page lists delivery state, attempt count, errors, and receipt time, with a
status filter. Losing scheduling permissions removes access to organizer
notices and delivery controls. Public visitors cannot read either inbox.

## Discord setup and worker

Set these variables in server configuration:

```text
FUINOISE_ORIGIN=https://your-fuinoise-host
DISCORD_BOT_TOKEN=<bot token>
DISCORD_GUILD_ID=<Fuinoise server ID>
DISCORD_ORGANIZER_CHANNEL_ID=<organizer text channel ID>
```

Use a dedicated organizer channel that only the intended organizers can access.
The bot needs View Channel, Send Messages, and Read Message History there. The
worker checks that the configured channel belongs to the Fuinoise server.
Streamers must link Discord and allow private messages from the bot. Blocked
messages or missing connections leave a visible failure while the on-site
notice remains available.

Run the bounded delivery command in a separate process:

```sh
venv/bin/python manage.py deliver_notifications
venv/bin/python manage.py deliver_notifications --limit 50
```

The default limit is 50 due deliveries per invocation; the allowed range is
1–500. It reports attempted, sent, waiting, failed, and uncertain counts. Its
exit status is unsuccessful while any delivery needs attention. Repeat runs
process only due pending records. It uses persisted conditional claims so
overlapping workers cannot send the same row at once. Ordinary web requests do
not send queued messages.

Deployment must schedule this command regularly, initially once per minute,
and monitor unsuccessful runs. Scheduling and live delivery rehearsal remain
milestone 7 work. No Celery or additional queue service is required for this MVP;
the existing database is the durable queue. Monitor backlog and command duration
at the pilot before changing worker capacity.

## Retry and recovery

Each delivery has a stable nonce and stores the Discord channel and message
receipt. Recent sends also use Discord's nonce enforcement. The worker honors
rate-limit waits in a shared database cooldown, conservatively pausing across
routes. Invalid bot authorization pauses requests for an hour. Safe temporary
preparation failures wait at least a minute. Automatic attempts are bounded to
eight per retry cycle; the total attempt count remains recorded.

Definite failures such as missing configuration, rejected permissions, and blocked
private messages show **Needs attention**. Fix the cause, then choose **Queue
retry**. This reuses the existing notification and delivery, preserves its nonce
and recipient, and waits for the worker; it does not immediately send a message.
The shared cooldown still applies after queuing a retry.

A timeout, server failure, or malformed receipt after attempting a message can
mean Discord already received it. These rows show **Delivery needs verification**
and cannot be blindly retried. Check the original Discord channel, copy the
message ID, and choose **Verify delivered message**. Verification reads Discord
and requires the expected bot author, channel, and exact stored content before
recording delivery. It sends no message. If no matching message can be verified,
the record stays uncertain; on-site information remains available.

A stopped worker is recovered after 15 minutes. Claims that stopped during
preparation can safely return to the queue. Claims that may have attempted a
message become uncertain and require the same review. This deliberately favors
an observable unresolved delivery over duplicate external messages; Discord's
short nonce window alone cannot provide indefinite exactly-once delivery.

Recipients are fixed when known. A disconnected or changed Discord connection
does not redirect an old alert to another account. A notification created before
Discord was connected can bind to that user's first valid connection on retry.
Organizer destinations are likewise retained once configured; check pending
records before replacing a channel. Message content is bounded, mentions and
embeds are suppressed, and provider errors do not expose tokens or response text.

## Verification and release follow-up

Tests cover transaction rollback, occurrence uniqueness, draft privacy, unchanged
publication, reassignment, removed slots, direct cancellation, recipient scoping,
read state, CSRF, worker claims, crash recovery, bounded retries, cooldowns,
blocked/mismatched destinations, lost receipts, and migration preservation.
Browser checks cover both inboxes, reading without JavaScript, persisted read
marks, a queued retry, and phone/desktop layouts. Test Discord requests are mocked;
the isolated browser fixture disables the bot token.

Before release, configure a hosted test bot/channel and recurring worker, then
rehearse a real request, publication, update, and cancellation. Verify the channel
alert and affected streamer's private message, a blocked-DM failure and retry,
and the on-site fallback during a provider outage. A live pilot and backup/restore
verification are still required.

API references: [Create Message and nonce enforcement](https://docs.discord.com/developers/resources/message#create-message),
[Create DM](https://docs.discord.com/developers/resources/user#create-dm), and
[rate limits](https://docs.discord.com/developers/topics/rate-limits).
