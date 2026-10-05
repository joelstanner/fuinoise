"""Rehearse a release in temporary storage; never use configured production data."""

import argparse
import json
import os
import secrets
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent

SEED = """
from datetime import date, datetime, timezone
from django.contrib.auth import get_user_model
from django.core.management import call_command
from fuinoise_live.models import (
    Community, Streamer, StreamerAccount, Event, RaidSlot,
    Notification, DiscordDelivery, SlotRequest, SlotPreference,
)
from fuinoise_live.scheduling import get_schedule_draft
community = Community.objects.create(name='Rehearsal', slug='rehearsal')
streamer = Streamer.objects.create(
    display_name='Rehearsal musician', twitch_username='rehearsal', twitch_id='100',
)
user = get_user_model().objects.create_user(username='rehearsal')
StreamerAccount.objects.create(user=user, streamer=streamer)
call_command('setup_organizers', username=user.username)
event = Event.objects.create(
    name='Rehearsal published lineup', date=date.today(),
    community=community, publication_status='published',
)
slot = RaidSlot.objects.create(
    event=event, streamer=streamer,
    start=datetime.now(timezone.utc), duration_minutes=60,
)
signup = SlotRequest.objects.create(
    event=event, streamer=streamer, notes='Private preferred performance',
)
SlotPreference.objects.create(
    request=signup, slot=slot, requested_start=slot.start,
    requested_duration_minutes=60,
)
draft = get_schedule_draft(event.pk, actor=user)
draft.name = 'Rehearsal private edit'
draft.save()
for index, status in enumerate(('pending', 'sending', 'failed', 'uncertain', 'sent')):
    notice = Notification.objects.create(
        key=f'rehearsal:{index}', kind='confirmed', event=event, recipient=user,
        title='Rehearsal', body='Private message',
    )
    DiscordDelivery.objects.create(
        notification=notice, nonce=f'rehearsal-{index}', status=status,
        target_id='100', guild_id='123', channel_id='456', content='Private message',
        message_id='789' if status == 'sent' else '', attempts=index,
    )
"""


def port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def manage(env: dict[str, str], *arguments: str) -> None:
    result = subprocess.run(
        [sys.executable, "manage.py", *arguments],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)


@contextmanager
def service(
    command: list[str], env: dict[str, str], log: Path
) -> Iterator[subprocess.Popen[bytes]]:
    with log.open("ab") as output:
        process = subprocess.Popen(
            command, cwd=ROOT, env=env, stdout=output, stderr=output
        )
        try:
            yield process
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def request(
    url: str,
    *,
    secure: bool = True,
    host: str = "fuinoise.example",
    context: ssl.SSLContext | None = None,
    method: str = "GET",
) -> tuple[int, Any, bytes]:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        NoRedirect(),
        urllib.request.HTTPSHandler(context=context),
    )
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "Host": host,
            "X-Forwarded-Proto": "https" if secure else "http",
        },
    )
    try:
        response = opener.open(req, timeout=3)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.code, response.headers, response.read()


def wait_ready(url: str, process: subprocess.Popen[bytes], **kwargs: Any) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("The rehearsal server stopped unexpectedly.")
        try:
            if request(url + "/health/", **kwargs)[0] == 200:
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise RuntimeError("The rehearsal server did not become ready.")


def check_http(base: str, **kwargs: Any) -> None:
    code, headers, body = request(base + "/health/?code=REHEARSAL_SECRET", **kwargs)
    assert code == 200 and json.loads(body) == {"status": "ok"}
    assert headers["Cache-Control"] == "no-store"
    assert "max-age=3600" in headers["Strict-Transport-Security"]
    code, _, body = request(base + "/events/1/", **kwargs)
    assert code == 200 and b"Rehearsal published lineup" in body
    assert b"Rehearsal private edit" not in body
    code, headers, _ = request(base + "/account/", **kwargs)
    assert code == 200 and "Secure" in headers["Set-Cookie"]
    assert request(base + "/account/logout/", method="POST", **kwargs)[0] == 403
    assert request(base + "/__debug__/", **kwargs)[0] == 404


def rows(database: Path) -> dict[str, list[tuple[Any, ...]]]:
    with closing(sqlite3.connect(database)) as db:
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            table: db.execute(
                'SELECT * FROM "' + table.replace('"', '""') + '" ORDER BY rowid'
            ).fetchall()
            for table in tables
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--caddy",
        type=Path,
        help="Optional local Caddy binary for temporary HTTPS/static-file rehearsal.",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="fuinoise-release-") as temporary:
        root = Path(temporary)
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("DJANGO_", "FUINOISE_", "TWITCH_", "DISCORD_"))
        }
        database = root / "data" / "fuinoise.sqlite3"
        database.parent.mkdir(mode=0o700)
        env.update(
            DJANGO_DEBUG="0",
            DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
            DJANGO_ALLOWED_HOSTS="fuinoise.example,localhost",
            DJANGO_TRUST_PROXY="1",
            FUINOISE_ORIGIN="https://fuinoise.example",
            FUINOISE_DATABASE_PATH=str(database),
            FUINOISE_STATIC_ROOT=str(root / "static"),
            FUINOISE_PORT=str(port()),
        )
        manage(env, "migrate", "--noinput")
        manage(env, "shell", "-c", SEED)
        manage(env, "collectstatic", "--noinput")
        manage(env, "check_release")
        command = [
            sys.executable,
            "-m",
            "gunicorn",
            "-c",
            "deploy/gunicorn.conf.py",
            "fuinoise.wsgi:application",
        ]
        base = "http://127.0.0.1:" + env["FUINOISE_PORT"]
        log = root / "gunicorn.log"
        for _ in range(2):
            with service(command, env, log) as process:
                wait_ready(base, process)
                check_http(base)
                assert request(base + "/health/", secure=False)[0] == 301
                assert request(base + "/health/", host="untrusted.example")[0] == 400
                if args.caddy:
                    caddy_port = port()
                    config = (ROOT / "deploy/Caddyfile").read_text()
                    config = config.replace(
                        "{\n",
                        "{\n\tadmin off\n\tskip_install_trust\n"
                        "\tauto_https disable_redirects\n",
                        1,
                    )
                    config = config.replace(
                        "fuinoise.example {",
                        f"https://localhost:{caddy_port} {{\n\ttls internal",
                    )
                    config = config.replace(
                        "/srv/fuinoise/current/staticfiles", env["FUINOISE_STATIC_ROOT"]
                    )
                    config = config.replace(
                        "127.0.0.1:8001", "127.0.0.1:" + env["FUINOISE_PORT"]
                    )
                    path = root / "Caddyfile"
                    path.write_text(config)
                    caddy_env = {
                        **env,
                        "XDG_DATA_HOME": str(root / "caddy-data"),
                        "XDG_CONFIG_HOME": str(root / "caddy-config"),
                    }
                    with service(
                        [
                            str(args.caddy.resolve()),
                            "run",
                            "--config",
                            str(path),
                            "--adapter",
                            "caddyfile",
                        ],
                        caddy_env,
                        root / "caddy.log",
                    ) as proxy:
                        https = f"https://localhost:{caddy_port}"
                        wait_ready(
                            https,
                            proxy,
                            host="localhost",
                            context=ssl._create_unverified_context(),
                        )
                        context = ssl.create_default_context(
                            cafile=str(
                                root / "caddy-data/caddy/pki/authorities/local/root.crt"
                            )
                        )
                        check_http(https, host="localhost", context=context)
                        # Caddy must replace a client-supplied insecure scheme.
                        assert (
                            request(
                                https + "/health/",
                                secure=False,
                                host="localhost",
                                context=context,
                            )[0]
                            == 200
                        )
                        for name in ("workspace.js", "workspace.css"):
                            code, headers, body = request(
                                https + "/static/fuinoise_live/organizer/" + name,
                                host="localhost",
                                context=context,
                            )
                            assert (
                                code == 200
                                and body
                                and headers["Cache-Control"] == "no-cache"
                            )
        assert "REHEARSAL_SECRET" not in log.read_text()
        before = rows(database)
        manage(
            env,
            "backup_database",
            "--output-dir",
            str(root / "backups"),
            "--label",
            "rehearsal",
        )
        snapshot = next((root / "backups").glob("*.sqlite3"))
        manage(env, "verify_backup", str(snapshot))
        restored = root / "data" / "restored.sqlite3"
        manage(env, "restore_database", str(snapshot), "--destination", str(restored))
        after = rows(restored)
        for table, content in before.items():
            if table != "fuinoise_live_discorddelivery":
                assert after[table] == content, f"Changed restored records in {table}"
        with closing(sqlite3.connect(restored)) as db:
            columns = [
                row[1]
                for row in db.execute(
                    "PRAGMA table_info(fuinoise_live_discorddelivery)"
                )
            ]
            statuses = [
                row[0]
                for row in db.execute(
                    "SELECT status FROM fuinoise_live_discorddelivery ORDER BY id"
                )
            ]
        table = "fuinoise_live_discorddelivery"
        preserved = [
            index
            for index, column in enumerate(columns)
            if column not in {"status", "claim_token", "last_error"}
        ]
        for old, new in zip(before[table], after[table], strict=True):
            assert all(old[index] == new[index] for index in preserved)
            if old[columns.index("status")] == "sent":
                assert old == new
        assert statuses == ["uncertain"] * 4 + ["sent"]
        env["FUINOISE_DATABASE_PATH"] = str(restored)
        manage(env, "check_release")
        with service(command, env, log) as process:
            wait_ready(base, process)
            check_http(base)
        print(
            "Production rehearsal passed: startup/restart, security, "
            "static collection, "
            f"{len(before)} restored tables and held historical alerts."
        )
        if args.caddy:
            print(
                "Caddy HTTPS/static proxy rehearsal passed with a temporary local CA. "
                "Public DNS/certificate and Linux services remain hosted checks."
            )


if __name__ == "__main__":
    main()
