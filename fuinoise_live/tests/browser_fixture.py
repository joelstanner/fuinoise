"""Isolated database/server for the organizer browser checks, never local data."""

import json
import os
import sys
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from pathlib import Path

import django

preview = Path(os.environ["FUINOISE_BROWSER_TEST_DIR"])
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fuinoise.settings")
from django.conf import settings  # noqa: E402

settings.DATABASES["default"]["NAME"] = preview / "browser.sqlite3"
settings.DEBUG = True
settings.SESSION_COOKIE_SECURE = False
settings.CSRF_COOKIE_SECURE = False
settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
settings.INTERNAL_IPS = []
settings.DISCORD_BOT_TOKEN = ""
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402

from fuinoise_live.accounts import account_for_twitch  # noqa: E402
from fuinoise_live.models import (  # noqa: E402
    Community,
    Event,
    RaidSlot,
    SlotPreference,
    SlotRequest,
    TwitchSnapshot,
)
from fuinoise_live.providers import TwitchIdentity  # noqa: E402
from fuinoise_live.requests import withdraw_slot_request  # noqa: E402
from fuinoise_live.scheduling import cancel_published_assignment  # noqa: E402

if sys.argv[1] == "serve":
    call_command("migrate", verbosity=0)
    organizer = get_user_model().objects.create_superuser(
        "browser_organizer", password=None
    )
    account = account_for_twitch(
        TwitchIdentity("12345", "browser_musician", "Browser Musician")
    )
    account.participation_status = "approved"
    account.discord_override = True
    account.save()
    TwitchSnapshot.objects.create(
        streamer=account.streamer,
        user_id=account.streamer.twitch_id,
        login=account.streamer.twitch_username,
        display_name="Twitch Browser Musician",
        description="A biography pulled from Twitch",
        profile_checked_at=timezone.now(),
        is_live=True,
        status_checked_at=timezone.now(),
        stream_title="Browser live music",
    )
    community = Community.objects.create(
        name="Browser community", slug="browser", default_time_zone="UTC"
    )
    day = timezone.now().date() + timedelta(days=3)
    event = Event.objects.create(
        name="Browser train",
        date=day,
        community=community,
        event_time_zone="UTC",
        publication_status="published",
    )
    slots = [
        RaidSlot.objects.create(
            event=event,
            start=datetime.combine(
                day, datetime.min.time(), tzinfo=datetime_timezone.utc
            )
            + timedelta(hours=10 + i),
        )
        for i in range(3)
    ]
    request = SlotRequest.objects.create(
        event=event, streamer=account.streamer, notes="Private browser preference"
    )
    for slot in slots[:2]:
        SlotPreference.objects.create(
            request=request,
            slot=slot,
            requested_start=slot.start,
            requested_duration_minutes=60,
        )
    legacy = Event.objects.create(
        name="Legacy browser train",
        date=day,
        community=community,
        event_time_zone="UTC",
        publication_status="published",
    )
    for slot in slots:
        RaidSlot.objects.create(event=legacy, start=slot.start)
    RaidSlot.objects.filter(event=legacy).update(duration_minutes=None)
    client = Client()
    client.force_login(organizer)
    streamer_client = Client()
    streamer_client.force_login(account.user)
    fixture = {
        "event_id": event.pk,
        "legacy_id": legacy.pk,
        "request_id": request.pk,
        "streamer_id": account.streamer_id,
        "user_id": account.user_id,
        "day": day.isoformat(),
        "session_id": client.cookies["sessionid"].value,
        "streamer_session_id": streamer_client.cookies["sessionid"].value,
    }
    (preview / "fixture.json").write_text(json.dumps(fixture))
    call_command(
        "runserver", f"127.0.0.1:{sys.argv[2]}", use_reloader=False, verbosity=0
    )
else:
    fixture = json.loads((preview / "fixture.json").read_text())
    if sys.argv[1] == "cancel":
        slot = (
            RaidSlot.objects.filter(
                event_id=fixture["event_id"], streamer_id=fixture["streamer_id"]
            )
            .order_by("start")
            .first()
        )
        cancel_published_assignment(slot.pk, streamer_id=fixture["streamer_id"])
    elif sys.argv[1] == "withdraw":
        request = SlotRequest.objects.get(pk=fixture["request_id"])
        withdraw_slot_request(
            request.pk,
            actor=get_user_model().objects.get(pk=fixture["user_id"]),
            expected_version=request.version,
        )
    elif sys.argv[1] == "fail_notifications":
        from fuinoise_live.discord_delivery import deliver_pending

        deliver_pending()
    else:
        raise ValueError("Unknown browser fixture operation.")
