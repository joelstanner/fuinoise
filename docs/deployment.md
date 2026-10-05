# Fuinoise deployment and recovery

This runbook prepares a single Linux server for the MVP. Caddy terminates HTTPS
and serves static files; Gunicorn runs Django on loopback; systemd runs the web
process and recurring jobs. SQLite data and backups live outside each application
release. A host and domain have not been selected, so these files are prepared
defaults. Actual public HTTPS, Linux service operation, off-site recovery, live
providers, and the event pilot remain [release checks](pilot-runbook.md).

## Server layout and prerequisites

Use Python 3.13, Node 22.12 or newer in the 22 series, Git, systemd, and
[Caddy 2](https://caddyserver.com/docs/install). Install the pinned production
Python requirements, including Gunicorn 26.0.0. The server needs outbound HTTPS
for Twitch and Discord, inbound ports 80 and 443 for Caddy, and restricted SSH
for administration. Point the chosen domain's DNS at this server before requesting
a public certificate. Caddy handles [automatic HTTPS](https://caddyserver.com/docs/quick-starts/https).

| Path | Purpose and access |
| --- | --- |
| `/srv/fuinoise/releases/<commit>` | One immutable code release, its virtual environment and built static files. |
| `/srv/fuinoise/current` | Symlink to the running release. |
| `/srv/fuinoise/data/fuinoise.sqlite3` | Persistent database; only the `fuinoise` account can access the directory. |
| `/srv/fuinoise/backups` | Private snapshot and manifest pairs; never served by Caddy. |
| `/etc/fuinoise/fuinoise.env` | Private production settings and credentials. |

Use a local persistent disk. The prepared configuration uses one application
worker with four threads, a 20-second SQLite lock timeout, and immediate write
transactions. Keep all database users on this server. Monitor lock errors during
the pilot; multiple application hosts or sustained write contention require a
database/worker design review before scaling.

As a server administrator, create the service account and directories:

```sh
sudo useradd --system --home-dir /srv/fuinoise --shell /usr/sbin/nologin fuinoise
sudo install -d -o root -g fuinoise -m 0755 /srv/fuinoise /srv/fuinoise/releases
sudo install -d -o fuinoise -g fuinoise -m 0700 /srv/fuinoise/data /srv/fuinoise/backups
sudo install -d -o root -g fuinoise -m 0750 /etc/fuinoise
```

## Production settings

Copy `deploy/fuinoise.env.example` to `/etc/fuinoise/fuinoise.env`, owned by
`root:fuinoise` with mode `0640`. Edit it on the server. Use plain `NAME=value`
assignments without `export`, command substitutions, shell expansion, or spaces
around `=`. Keep secrets out of the repository, screenshots, tickets and logs.
Generate a secret locally on the server, for example with
`python3.13 -c 'import secrets; print(secrets.token_urlsafe(64))'`, and paste it
directly into the private file. Retain the same secret across upgrades and recovery.

Required settings are `DJANGO_DEBUG=0`, a strong `DJANGO_SECRET_KEY`, explicit
`DJANGO_ALLOWED_HOSTS`, the matching `https://` `FUINOISE_ORIGIN`, an absolute
`FUINOISE_DATABASE_PATH`, and an absolute `FUINOISE_STATIC_ROOT`. The example uses
the persistent database path above and `/srv/fuinoise/current/staticfiles`.
Production settings reject missing or malformed values early. The debug toolbar
is excluded when debug is off, and HTTPS redirects and secure session/CSRF cookies
are enabled. HSTS starts at one hour for this hostname; subdomain inclusion and
browser preload are intentionally off until domain ownership and rollout are reviewed.

Enable `DJANGO_TRUST_PROXY=1` only with the supplied loopback Gunicorn listener and
a trusted reverse proxy. Caddy replaces the forwarded scheme header; the app port
must remain inaccessible from the public network. Do not expose Gunicorn directly.
Only collected static files are served by Caddy. Its configuration filters URI and
headers from runtime logs, while Gunicorn access logs omit query strings, request
bodies, and headers so OAuth callback values stay out of access logs.

Use [account setup](accounts-and-eligibility.md) for Twitch/Discord applications,
exact callback URLs, membership checks, and organizer roles. Use
[notification setup](notifications.md) for bot permissions, the organizer channel,
private messages and delivery recovery. Configure provider credentials in the
private environment file. `check_release --require-integrations` checks that
settings exist; it cannot prove provider permissions or successful deliveries.

## Build and start a release

From a verified checkout on the server, choose the exact committed revision to
deploy. These commands package only committed files, excluding the developer's
database, virtual environment, credentials and ignored frontend build output:

```sh
release_commit=$(git rev-parse HEAD)
release=/srv/fuinoise/releases/$release_commit
sudo install -d -o root -g fuinoise -m 0755 "$release"
git archive "$release_commit" | sudo tar -x -C "$release"
sudo python3.13 -m venv "$release/venv"
sudo "$release/venv/bin/python" -m pip install -r "$release/requirements-prod.txt"
sudo sh -c 'cd "$1/frontend" && npm ci --no-audit --no-fund && npm run build' sh "$release"
sudo install -o root -g root -m 0755 "$release/deploy/manage.sh" /usr/local/bin/fuinoise-manage
```

For the first deployment, create `current` before using the helper:

```sh
sudo ln -s "$release" /srv/fuinoise/current
sudo -u fuinoise /usr/local/bin/fuinoise-manage "$release" migrate --noinput
```

Collect static files as the administrator into this release, with a readable
umask. The explicit override avoids writing through `current` to another release:

```sh
sudo sh -c '
  set -eu
  umask 022
  set -a
  . /etc/fuinoise/fuinoise.env
  set +a
  export FUINOISE_STATIC_ROOT="$1/staticfiles"
  cd "$1"
  exec venv/bin/python manage.py collectstatic --noinput
' sh "$release"
sudo -u fuinoise /usr/local/bin/fuinoise-manage "$release" check_release
```

`check_release` checks Django deployment/security checks, database existence and
placement, applied migrations, and the collected organizer JS/CSS. It accepts and
prints only the deliberate HSTS warnings `security.W005` and `security.W021`.
Other warnings and errors block the release. Django's raw `check --deploy` will
still report those two warnings; do not enable domain-wide HSTS or preload merely
to hide them. This follows the concerns in the
[Django deployment checklist](https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/).

Install the service files and proxy configuration, then edit the installed
Caddyfile to replace `fuinoise.example` with the actual hostname, matching the
origin and allowed hosts:

```sh
sudo install -m 0644 "$release"/deploy/systemd/* /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/fuinoise*.service /etc/systemd/system/fuinoise*.timer
sudo install -m 0644 "$release/deploy/Caddyfile" /etc/caddy/Caddyfile
sudoedit /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now fuinoise.service
sudo systemctl restart caddy
sudo systemctl enable --now fuinoise-backup.timer
sudo systemctl start fuinoise-backup.service
```

Verify HTTPS externally, redirects from HTTP, `/health/`, public schedules,
organizer assets and login. `/health/` returns an uncached, generic `ok` or HTTP 503
after reading the event and Discord delivery tables. It checks basic database
readiness; it does not prove provider health or an entire event workflow.

After initial live provider checks, enable the integration jobs for the remaining
hosted rehearsal and pilot:

```sh
sudo -u fuinoise /usr/local/bin/fuinoise-manage /srv/fuinoise/current check_release --require-integrations
sudo systemctl enable --now fuinoise-delivery.timer fuinoise-twitch.timer
sudo systemctl list-timers 'fuinoise-*'
```

Delivery runs each minute in batches of 25; Twitch refresh runs every two minutes.
Systemd prevents overlapping executions of each service. Backup runs daily near
03:00 UTC and catches up after downtime; successful backup is followed by expired
session cleanup. No Celery service is required for this MVP configuration.

## Upgrades and rollback

Build the new release and its collected static files before the maintenance window.
Check its tests and migrations, retain the old release, and announce a short
maintenance period to organizers. Stop timers and running jobs before changing
the schema:

```sh
sudo systemctl stop fuinoise-delivery.timer fuinoise-twitch.timer fuinoise-backup.timer
sudo systemctl stop fuinoise.service fuinoise-delivery.service fuinoise-twitch.service fuinoise-backup.service
sudo -u fuinoise /usr/local/bin/fuinoise-manage /srv/fuinoise/current backup_database --output-dir /srv/fuinoise/backups --label before-upgrade
sudo -u fuinoise /usr/local/bin/fuinoise-manage "$release" migrate --noinput
sudo -u fuinoise /usr/local/bin/fuinoise-manage "$release" check_release --require-integrations
sudo ln -s "$release" /srv/fuinoise/current.next
sudo mv -Tf /srv/fuinoise/current.next /srv/fuinoise/current
sudo systemctl start fuinoise.service
```

If service, helper or proxy definitions changed, update the installed copies,
preserve the actual hostname and private settings, validate the units/Caddyfile,
and reload systemd/Caddy before starting the new release.

Remove any stale `current.next` left by an interrupted previous attempt after
checking what it points to. Check HTTPS health, assets, login and a published
lineup before restarting the timers. If a check fails, keep jobs stopped and
inspect the service journal. Swap back to the old code only if it is compatible
with the current schema. Do not reverse migrations or restore over the active
database casually. A database rollback loses changes made after the snapshot and
must follow the separate-file recovery procedure below.

The management helper loads the private environment and points static checks at
the selected release's own `staticfiles`, so the new release is checked before
changing `current`. It does not change the configured persistent database path.

## Backups and separate restoration

The backup command uses SQLite's
[online backup API](https://docs.python.org/3.13/library/sqlite3.html#sqlite3.Connection.backup)
to include committed data even if a source database uses WAL. It verifies SQLite
integrity and foreign keys, records migrations and every table's row count, and
publishes a standalone `.sqlite3` plus a `.sqlite3.json` manifest with SHA-256 and
size. The source records are not changed. Backup directories must be mode `0700`;
new snapshot, manifest and restore files are mode `0600`. Keep each pair together.
Record the current release commit alongside each off-site copy; the manifest records
the schema migrations, not the application commit or environment secrets.

```sh
sudo -u fuinoise /usr/local/bin/fuinoise-manage /srv/fuinoise/current backup_database --output-dir /srv/fuinoise/backups --label manual
sudo -u fuinoise /usr/local/bin/fuinoise-manage /srv/fuinoise/current verify_backup /srv/fuinoise/backups/SNAPSHOT.sqlite3
sudo -u fuinoise /usr/local/bin/fuinoise-manage /srv/fuinoise/current restore_database /srv/fuinoise/backups/SNAPSHOT.sqlite3 --destination /srv/fuinoise/data/restored-REVIEW.sqlite3
```

Replace the placeholder names with the selected snapshot and a new restore path.
Restoration refuses an existing file, a dangling symlink, existing SQLite companion
files, or the configured live database path. It verifies the copied data before
publication. All historical
Discord deliveries whose status is not `sent` become `uncertain`, with claims
cleared. A message might have been sent after the snapshot was taken; replaying
that older queue could duplicate alerts. On-site notices and all schedule/account
records remain available. Review held messages through the delivery interface;
verify actual receipts before recovery. Messages without a verifiable destination
or receipt stay held. Sent receipts are preserved.

To replace a failed production database, stop the app, timers and running jobs,
take a snapshot of any readable current database, and restore into a separate file.
Retain the original database and any journal/WAL files together. Use the code
revision recorded for the snapshot, apply forward migrations to the restored file
if needed, then change `FUINOISE_DATABASE_PATH` in the private environment file to
the reviewed restored path. Run `check_release` against that path and start the
app. Verify public and private records before restarting jobs. Never delete or
copy a live SQLite file or its journal files during this procedure.

Local snapshots protect against mistakes; loss of the server requires an off-site
copy. Before the pilot, choose a private encrypted off-site destination, copy the
snapshot/manifest pairs and protect the separate environment-secret recovery
material. Set an explicit retention policy; the supplied timer does not prune or
upload backups. Suggested initial policy: daily copies, 14 daily and 8 weekly
recovery points, plus the pre-upgrade snapshot. Verify an off-site pair by restoring
it on a separate host/path with delivery disabled. Record the date, snapshot,
code revision, recovered records, recovery time and any missing changes. A daily
schedule can lose up to roughly a day of changes; choose a shorter interval if the
pilot requires it. Preserve one verified off-site recovery point before pruning.

## Operation and verification

Monitor public HTTPS `/health/`, failed services, disk space, backup age and off-site
copy age. Review `journalctl -u fuinoise.service` and the delivery, Twitch and backup
service journals. On a delivery failure, use the organizer delivery page and the
[retry/recovery rules](notifications.md#retry-and-recovery); do not reset queues
directly. Track repeated SQLite lock errors and growing queues during the pilot.
Restrict journal access and avoid adding query strings or provider responses to
logs. Backups contain private account, schedule and session data.

For local release verification, build the frontend and install production and
development dependencies, then run:

```sh
venv/bin/python scripts/verify_deployment.py
```

The script creates fresh temporary databases and credentials, disables all external
provider credentials, migrates, seeds public/private records, collects static
files, starts/restarts Gunicorn, checks HTTPS-related application behavior, backs
up, restores separately, compares all other table contents, and serves the restored
database. It never reads the local `db.sqlite3`. Pass `--caddy /path/to/caddy` to
also verify HTTPS and static serving with a temporary local CA, without installing
trust into the computer. Caddy 2.11.7 was used for that local check. CI runs the
application rehearsal and Linux unit-file validation; actual systemd sandboxing,
public certificates and provider delivery still require the hosted rehearsal.
