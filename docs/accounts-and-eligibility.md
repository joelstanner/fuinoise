# Fuinoise accounts and eligibility

This guide covers setup and operation of Twitch sign-in, Discord account linking,
and organizer participation reviews. The account and review pages are implemented;
live provider verification still needs application credentials and a configured
Fuinoise Discord server. Streamer requests and assignment operations are described
in [the request workflow](requests-and-assignments.md).

## Provider configuration

Set these environment variables before starting Django. The application reads the
process environment; it does not automatically load a `.env` file.

| Variable | Purpose |
| --- | --- |
| `FUINOISE_ORIGIN` | Public HTTPS origin, such as `https://your-domain.example`, without a path or trailing slash. Defaults to `http://localhost:8000` for local development. |
| `TWITCH_CLIENT_ID` | Registered Twitch application's client ID. |
| `TWITCH_CLIENT_SECRET` | Twitch application's secret. |
| `DISCORD_CLIENT_ID` | Discord application's client ID. |
| `DISCORD_CLIENT_SECRET` | Discord application's OAuth secret. |
| `DISCORD_GUILD_ID` | Numeric ID of the Fuinoise Discord server. |
| `DISCORD_BOT_TOKEN` | Token for the application's bot, installed in that server; used to recheck membership. |
| `DJANGO_SECRET_KEY` | Django signing secret. Required when debug is disabled. |
| `DJANGO_DEBUG` | `1` for local development; `0` on a hosted environment. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames Django should accept. Defaults to `localhost,127.0.0.1`. |

Keep secrets in the host's secret configuration. Register these exact redirect
URLs with the respective applications, replacing the origin with yours:

- Twitch: `https://your-domain.example/auth/twitch/callback/`
- Discord: `https://your-domain.example/auth/discord/callback/`

For local testing, register the corresponding `http://localhost:8000` URLs and
use that same hostname in your browser. HTTP is allowed only for loopback origins
with debug enabled. Hosted sign-in requires HTTPS. Secure session and CSRF cookies
are enabled when debug is disabled; a reverse proxy must preserve Django's
ability to recognize HTTPS requests. Full hosting setup remains milestone 7.

Twitch sign-in uses the authorization-code flow and verifies the token's client
and account IDs before associating a user. Discord linking requests `identify`
and `guilds.members.read` to verify the account and its Fuinoise membership.
See [Twitch's authentication guide](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/),
[token validation](https://dev.twitch.tv/docs/authentication/validate-tokens/), and
[Discord's OAuth scopes](https://docs.discord.com/developers/topics/oauth2).

The bot rechecks one linked member using Discord's
[Get Guild Member endpoint](https://docs.discord.com/developers/resources/guild#get-guild-member).
This milestone does not send Discord messages or inspect chat history. Provider
access and refresh tokens are used only during the connection attempt and are
not stored. Django manages the subsequent site session, which expires after
12 hours. Future Twitch API work should use the appropriate independently
validated service credentials.

## Migrations and organizer setup

Apply migrations to the intended environment before trying the new account pages:

```sh
venv/bin/python manage.py migrate
```

Migration 0014 creates account associations and review history. It does not
assign ownership of existing streamer records or change existing Django users.

Have each organizer sign in with Twitch once. Grant the organizer role using that
account's verified Twitch ID:

```sh
venv/bin/python manage.py setup_organizers --twitch-id VERIFIED_TWITCH_ID
```

For an existing maintenance user, use `--username EXISTING_DJANGO_USERNAME` instead.
The command assigns scheduling, participation-review, Discord-override, and
streamer-creation permissions. Rerun it for existing organizers to allow new
Twitch channel matches during import. It can be rerun safely and does not grant
staff or superuser access.
Organizer reviews are at `/organizer/eligibility/`; the account page links to them
when the user has review or override permission.

Existing streamer records are matched by verified Twitch ID. Their IDs, chosen
public names, notes, and schedule relationships remain intact. If a legacy record
has the same Twitch username but no verified ID, sign-in stops for organizer
review. Verify the current Twitch account independently, then set the record's
Twitch ID through maintenance Admin. Usernames alone cannot establish ownership
because they can be renamed or reused.

## Participation reviews

A streamer signs in with Twitch, connects Discord, and appears in the organizer
review list. The organizer checks active participation in Fuinoise Discord and
records an approval, decline, or pending decision with a private reason. Membership
alone does not grant participation approval.

A separate Discord override permits participation without a linked account or
verified membership. It requires its own organizer permission and recorded
reason. Participation approval is still required. Ordinary users cannot grant
themselves approval or an override.

The account page shows the streamer's status and membership result. Review
reasons and history are visible only to authorized organizers. A Discord account
can be connected to only one Twitch account. Switching Discord identities requires
disconnecting first; disconnection resets participation approval and overrides
and records that reset in the history.

Membership displayed as verified must have been checked in the configured server
within the last 15 minutes. A pending membership-screening result does not count
as completed membership. Provider outages, rate limits, missing bot configuration,
and malformed responses leave membership unverified. An old successful check
cannot grant eligibility after a failed refresh.

`require_eligible_streamer` is the backend check used by the request workflow.
It rechecks membership when accepting a request, unless an organizer has granted
a Discord override, and always requires an active Twitch-associated account and
organizer approval. Preference submission and organizer request assignment use
this check before saving. New request-backed assignments also require current
eligibility at publication.

## Verification before live use

Automated tests exercise provider success and failure responses, state expiry and
replay, account ownership, permissions, stale reviews, migration preservation,
and membership failures without live credentials. Browser previews verify the
account and review layouts on desktop and phone widths.

After configuration, complete a live rehearsal: sign in with an existing and a
new Twitch account, connect Discord, check membership, approve participation,
grant and revoke an override, disconnect Discord, and sign out. Confirm that an
ordinary account cannot open review pages and that a user outside the configured
server is ineligible. This rehearsal and hosted HTTPS verification remain
required before the milestone is ready for pilot use.
