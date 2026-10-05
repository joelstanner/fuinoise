"""Public Twitch data: bounded fetching and durable snapshots, no page-time I/O."""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from .models import Streamer, TwitchSnapshot
from .providers import TWITCH_TOKEN, ProviderError, identity_id, request_json

BATCH_SIZE = 20


@dataclass(frozen=True)
class TwitchProfile:
    id: str
    login: str
    display_name: str
    description: str
    profile_image_url: str


def _text(data: dict[str, Any], key: str, limit: int, *, blank: bool = True) -> str:
    value = data.get(key)
    if not isinstance(value, str) or len(value) > limit or (not blank and not value):
        raise ProviderError("Twitch returned malformed channel information.")
    return value


def parse_profile(data: dict[str, Any]) -> TwitchProfile:
    user_id = identity_id(data)
    login = _text(data, "login", 25, blank=False)
    if not re.fullmatch(r"[a-z0-9_]{1,25}", login):
        raise ProviderError("Twitch returned an invalid channel name.")
    image = _text(data, "profile_image_url", 500)
    parsed = urlsplit(image)
    if image and (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise ProviderError("Twitch returned an invalid profile image URL.")
    return TwitchProfile(
        user_id,
        login,
        _text(data, "display_name", 80, blank=False),
        _text(data, "description", 2000),
        image,
    )


def _config() -> tuple[str, str, str]:
    client = str(settings.TWITCH_CLIENT_ID)
    secret = str(settings.TWITCH_CLIENT_SECRET)
    if not client or not secret:
        raise ProviderError("Twitch information is not configured yet.")
    key = (
        "fuinoise:twitch-app:"
        + hashlib.sha256(f"{client}:{secret}".encode()).hexdigest()
    )
    return client, secret, key


@sensitive_variables()
def _app_token() -> str:
    client, secret, key = _config()
    cached = cache.get(key)
    if isinstance(cached, str) and cached:
        return cached
    payload = request_json(
        TWITCH_TOKEN,
        form={
            "client_id": client,
            "client_secret": secret,
            "grant_type": "client_credentials",
        },
    )
    token = payload.get("access_token")
    expires = payload.get("expires_in")
    if (
        not isinstance(token, str)
        or not token
        or str(payload.get("token_type", "")).lower() != "bearer"
        or type(expires) is not int
        or expires < 1
    ):
        raise ProviderError("Twitch returned an invalid application token.")
    verified = request_json(
        "https://id.twitch.tv/oauth2/validate",
        headers={"Authorization": f"OAuth {token}"},
    )
    remaining = verified.get("expires_in")
    if (
        verified.get("client_id") != client
        or type(remaining) is not int
        or remaining < 1
        or verified.get("user_id")
    ):
        raise ProviderError("Twitch could not verify the application token.")
    # Reacquire/validate before one hour, even if the token itself lasts longer.
    cache.set(key, token, timeout=max(1, min(expires, remaining, 3300) - 30))
    return token


@sensitive_variables()
def _helix(endpoint: str, params: Sequence[tuple[str, str]]) -> dict[str, Any]:
    client, _, key = _config()
    token = _app_token()
    try:
        result: dict[str, Any] = request_json(
            f"https://api.twitch.tv/helix/{endpoint}?{urlencode(params)}",
            headers={"Client-Id": client, "Authorization": f"Bearer {token}"},
        )
        return result
    except ProviderError as error:
        if error.status == 401:
            cache.delete(key)
        raise


def get_profiles(
    *, ids: Sequence[str] = (), logins: Sequence[str] = ()
) -> list[TwitchProfile]:
    if (
        not 1 <= len(ids) + len(logins) <= BATCH_SIZE
        or any(not re.fullmatch(r"[0-9]{1,32}", item) for item in ids)
        or any(not re.fullmatch(r"[a-z0-9_]{1,25}", item) for item in logins)
    ):
        raise ProviderError("Choose up to 20 valid Twitch channels.")
    payload = _helix(
        "users", [("id", item) for item in ids] + [("login", item) for item in logins]
    )
    data = payload.get("data")
    if (
        not isinstance(data, list)
        or len(data) > len(ids) + len(logins)
        or any(not isinstance(item, dict) for item in data)
    ):
        raise ProviderError("Twitch returned an invalid profile list.")
    profiles = [parse_profile(item) for item in data]
    if (
        len({item.id for item in profiles}) != len(profiles)
        or len({item.login for item in profiles}) != len(profiles)
        or any(item.id not in ids and item.login not in logins for item in profiles)
    ):
        raise ProviderError("Twitch returned profiles for different channels.")
    return profiles


def lookup_profile(login: str) -> TwitchProfile:
    profiles = get_profiles(logins=[login.strip().lower()])
    if len(profiles) != 1:
        raise ProviderError("That Twitch channel could not be found.")
    return profiles[0]


def get_live_streams(ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    if not 1 <= len(ids) <= BATCH_SIZE:
        raise ProviderError("Choose up to 20 Twitch channels.")
    payload = _helix(
        "streams", [("user_id", item) for item in ids] + [("first", "100")]
    )
    data = payload.get("data")
    pagination = payload.get("pagination", {})
    if (
        not isinstance(data, list)
        or not isinstance(pagination, dict)
        or pagination.get("cursor")
        or any(not isinstance(item, dict) for item in data)
    ):
        raise ProviderError("Twitch returned incomplete live information.")
    streams = {}
    for item in data:
        user_id = identity_id(item, "user_id")
        if user_id not in ids or user_id in streams or item.get("type") != "live":
            raise ProviderError("Twitch returned invalid live information.")
        try:
            started = datetime.fromisoformat(_text(item, "started_at", 80, blank=False))
        except ValueError as error:
            raise ProviderError("Twitch returned an invalid stream start.") from error
        if timezone.is_naive(started) or started > timezone.now() + timedelta(
            minutes=1
        ):
            raise ProviderError("Twitch returned an invalid stream start.")
        streams[user_id] = {
            "stream_title": _text(item, "title", 500),
            "game_name": _text(item, "game_name", 200),
            "stream_started_at": started,
        }
    return streams


def store_profile(streamer: Streamer, profile: TwitchProfile) -> TwitchSnapshot:
    fields = asdict(profile)
    fields["user_id"] = fields.pop("id")
    snapshot: TwitchSnapshot
    snapshot, _ = TwitchSnapshot.objects.update_or_create(
        streamer=streamer, defaults={**fields, "profile_checked_at": timezone.now()}
    )
    return snapshot


def refresh_streamers(streamer_ids: Sequence[int]) -> tuple[int, int]:
    """Fetch outside transactions; keep schedules and old profiles on failure."""
    streamers = list(Streamer.objects.filter(pk__in=streamer_ids).order_by("pk"))
    updated = failed = 0
    for index in range(0, len(streamers), BATCH_SIZE):
        batch = streamers[index : index + BATCH_SIZE]
        try:
            profiles = get_profiles(
                ids=[item.twitch_id for item in batch if item.twitch_id],
                logins=[item.twitch_username for item in batch if not item.twitch_id],
            )
            by_id = {item.id: item for item in profiles}
            by_login = {item.login: item for item in profiles}
            streams = get_live_streams(list(by_id)) if by_id else {}
        except ProviderError as error:
            with transaction.atomic():
                for streamer in batch:
                    TwitchSnapshot.objects.update_or_create(
                        streamer=streamer,
                        defaults={
                            "status_checked_at": None,
                            "last_attempt_at": timezone.now(),
                            "refresh_error": str(error)[:255],
                        },
                    )
            failed += len(batch)
            continue
        with transaction.atomic():
            for streamer in batch:
                profile = (
                    by_id.get(streamer.twitch_id)
                    if streamer.twitch_id
                    else by_login.get(streamer.twitch_username)
                )
                if profile is None:
                    TwitchSnapshot.objects.update_or_create(
                        streamer=streamer,
                        defaults={
                            "status_checked_at": None,
                            "last_attempt_at": timezone.now(),
                            "refresh_error": "The Twitch channel could not be found.",
                        },
                    )
                    failed += 1
                    continue
                # Reject results if maintenance changed the account while fetching.
                current = Streamer.objects.get(pk=streamer.pk)
                if (
                    current.twitch_id != streamer.twitch_id
                    or current.twitch_username != streamer.twitch_username
                ):
                    failed += 1
                    continue
                snapshot = store_profile(streamer, profile)
                snapshot.is_live = profile.id in streams
                live = streams.get(profile.id, {})
                snapshot.stream_title = live.get("stream_title", "")
                snapshot.game_name = live.get("game_name", "")
                snapshot.stream_started_at = live.get("stream_started_at")
                snapshot.status_checked_at = snapshot.last_attempt_at = timezone.now()
                snapshot.refresh_error = ""
                snapshot.save()
                updated += 1
    return updated, failed


def public_information(streamer: Streamer, now: datetime) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "unknown", "url": streamer.twitch_url}
    try:
        snapshot = streamer.twitch_snapshot
    except TwitchSnapshot.DoesNotExist:
        return result
    matches = (
        snapshot.user_id == streamer.twitch_id
        if streamer.twitch_id
        else snapshot.login == streamer.twitch_username
    )
    if not matches or not snapshot.profile_checked_at:
        return result
    result.update(
        display_name=snapshot.display_name,
        description=snapshot.description,
        image_url=snapshot.profile_image_url,
        url=f"https://www.twitch.tv/{snapshot.login}",
    )
    checked = snapshot.status_checked_at
    if (
        checked
        and timedelta(0)
        <= now - checked
        <= timedelta(seconds=settings.TWITCH_LIVE_MAX_AGE_SECONDS)
        and snapshot.is_live is not None
    ):
        result["status"] = "live" if snapshot.is_live else "offline"
        result["expires_in_ms"] = max(
            0,
            int(
                (
                    timedelta(seconds=settings.TWITCH_LIVE_MAX_AGE_SECONDS)
                    - (now - checked)
                ).total_seconds()
                * 1000
            ),
        )
        if snapshot.is_live:
            result.update(title=snapshot.stream_title, game=snapshot.game_name)
    return result
