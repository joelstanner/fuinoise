"""Bounded Discord delivery with claims and conservative lost-receipt handling."""

import json
import math
import re
import uuid
from datetime import timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import F
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from .models import DiscordDelivery, DiscordDispatchState, StreamerAccount
from .notifications import is_organizer
from .providers import DISCORD_API, MAX_RESPONSE_BYTES, NoRedirects

MAX_ATTEMPTS = 8


class DeliveryError(Exception):
    def __init__(
        self, message: str, *, uncertain: bool = False, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.uncertain = uncertain
        self.retry_after = retry_after


def snowflake(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,32}", value):
        raise DeliveryError("Discord returned an invalid identity.")
    return value


def _seconds(value: Any, default: float = 60) -> float:
    try:
        number = float(value)
        if math.isfinite(number) and 0 <= number <= 604800:
            return max(1, number) + 1
    except (ValueError, TypeError):
        pass
    return default


def _pause(seconds: float) -> None:
    deadline = timezone.now() + timedelta(seconds=seconds)
    DiscordDispatchState.objects.get_or_create(pk=1)
    DiscordDispatchState.objects.filter(pk=1, pause_until__lt=deadline).update(
        pause_until=deadline
    )


@sensitive_variables()
def discord_request(
    path: str, *, payload: dict[str, Any] | None = None, sending: bool = False
) -> dict[str, Any]:
    token = str(settings.DISCORD_BOT_TOKEN)
    if not token:
        raise DeliveryError("Configure the Discord bot token before delivery.")
    pause = DiscordDispatchState.objects.filter(pk=1).first()
    if pause and pause.pause_until > timezone.now():
        raise DeliveryError(
            "Discord cooldown; delivery will wait.",
            retry_after=(pause.pause_until - timezone.now()).total_seconds(),
        )
    request = Request(
        f"{DISCORD_API}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Fuinoise/1.0",
        },
        method="POST" if payload is not None else "GET",
    )
    try:
        with build_opener(NoRedirects()).open(request, timeout=5) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if response.headers.get("X-RateLimit-Remaining") == "0":
                _pause(_seconds(response.headers.get("X-RateLimit-Reset-After")))
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except HTTPError as error:
        if error.code == 429:
            retry = error.headers.get("Retry-After")
            try:
                data = json.loads(error.read(MAX_RESPONSE_BYTES))
                if isinstance(data, dict):
                    retry = data.get("retry_after", retry)
            except (ValueError, OSError):
                pass
            delay = _seconds(retry)
            _pause(delay)
            raise DeliveryError(
                "Discord rate limit; delivery will wait.", retry_after=delay
            ) from None
        if error.code == 401:
            _pause(3600)
        ambiguous = sending and (error.code >= 500 or 300 <= error.code < 400)
        raise DeliveryError(
            f"Discord rejected delivery (HTTP {error.code}).",
            uncertain=ambiguous,
            retry_after=60 if error.code >= 500 and not sending else None,
        ) from None
    except (URLError, OSError, ValueError):
        raise DeliveryError(
            "Discord did not return a verified response.",
            uncertain=sending,
            retry_after=None if sending else 60,
        ) from None


def notification_content(delivery: DiscordDelivery) -> str:
    origin = str(settings.FUINOISE_ORIGIN)
    try:
        parsed = urlsplit(origin)
    except ValueError:
        raise DeliveryError(
            "Configure a valid site origin before Discord delivery."
        ) from None
    local_http = (
        settings.DEBUG
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    if (
        len(origin) > 400
        or not parsed.netloc
        or not (parsed.scheme == "https" or local_http)
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise DeliveryError("Configure a valid site origin before Discord delivery.")
    notice = delivery.notification
    text = f"{notice.title}\n{notice.body}"
    text = "".join(char for char in text if char >= " " or char == "\n")
    text = re.sub(r"([\\_*~`|>])", r"\\\1", text)
    return f"{text[:1500]}\n{origin}/notifications/{notice.pk}/"


def _prepare(delivery: DiscordDelivery) -> None:
    notice = delivery.notification
    if notice.recipient_id is None:
        target = delivery.target_id or str(settings.DISCORD_ORGANIZER_CHANNEL_ID)
        guild = delivery.guild_id or str(settings.DISCORD_GUILD_ID)
        try:
            target, guild = snowflake(target), snowflake(guild)
        except DeliveryError:
            raise DeliveryError(
                "Configure the organizer Discord channel and Fuinoise server IDs."
            ) from None
        channel = discord_request(f"/channels/{target}")
        if (
            channel.get("id") != target
            or channel.get("guild_id") != snowflake(guild)
            or channel.get("type")
            not in {
                0,
                5,
            }
        ):
            raise DeliveryError(
                "Choose an organizer text channel in the Fuinoise server."
            )
        delivery.target_id, delivery.guild_id, delivery.channel_id = (
            target,
            guild,
            target,
        )
    else:
        account = StreamerAccount.objects.filter(user_id=notice.recipient_id).first()
        if account is None or not account.user.is_active or not account.discord_id:
            raise DeliveryError(
                "The recipient needs an active connected Discord account."
            )
        target = delivery.target_id or account.discord_id
        if target != account.discord_id:
            raise DeliveryError(
                "The Discord connection changed; the old alert will not be redirected."
            )
        # Persist the recipient before opening a channel; retries never change it.
        delivery.target_id = snowflake(target)
        DiscordDelivery.objects.filter(
            pk=delivery.pk, claim_token=delivery.claim_token
        ).update(target_id=target)
        channel = discord_request(
            "/users/@me/channels", payload={"recipient_id": target}
        )
        recipients = channel.get("recipients")
        if (
            channel.get("type") != 1
            or not isinstance(recipients, list)
            or len(recipients) != 1
            or not isinstance(recipients[0], dict)
            or recipients[0].get("id") != target
        ):
            raise DeliveryError("Discord returned a channel for a different recipient.")
        delivery.channel_id = snowflake(channel.get("id"))
    if not delivery.content:
        delivery.content = notification_content(delivery)
    DiscordDelivery.objects.filter(
        pk=delivery.pk, claim_token=delivery.claim_token
    ).update(
        target_id=delivery.target_id,
        guild_id=delivery.guild_id,
        channel_id=delivery.channel_id,
        content=delivery.content,
    )


def _send_claimed(delivery: DiscordDelivery) -> None:
    try:
        _prepare(delivery)
        if not DiscordDelivery.objects.filter(
            pk=delivery.pk,
            claim_token=delivery.claim_token,
            status=DiscordDelivery.Status.SENDING,
        ).exists():
            return
        if delivery.notification.recipient_id is not None:
            if not StreamerAccount.objects.filter(
                user_id=delivery.notification.recipient_id,
                discord_id=delivery.target_id,
                user__is_active=True,
            ).exists():
                raise DeliveryError("The Discord connection changed before sending.")
        if not DiscordDelivery.objects.filter(
            pk=delivery.pk,
            claim_token=delivery.claim_token,
            status=DiscordDelivery.Status.SENDING,
        ).update(message_attempted_at=timezone.now()):
            return
        response = discord_request(
            f"/channels/{delivery.channel_id}/messages",
            payload={
                "content": delivery.content,
                "nonce": delivery.nonce,
                "enforce_nonce": True,
                "allowed_mentions": {"parse": [], "replied_user": False},
                "flags": 4,
            },
            sending=True,
        )
        try:
            message_id = snowflake(response.get("id"))
            if (
                response.get("channel_id") != delivery.channel_id
                or str(response.get("nonce")) != delivery.nonce
            ):
                raise DeliveryError("Discord returned a mismatched message receipt.")
        except DeliveryError as error:
            raise DeliveryError(str(error), uncertain=True) from None
    except DeliveryError as error:
        if error.uncertain:
            status = DiscordDelivery.Status.UNCERTAIN
        elif error.retry_after is not None and delivery.cycle_attempts < MAX_ATTEMPTS:
            status = DiscordDelivery.Status.PENDING
        else:
            status = DiscordDelivery.Status.FAILED
        if error.retry_after:
            _pause(error.retry_after)
        DiscordDelivery.objects.filter(
            pk=delivery.pk, claim_token=delivery.claim_token
        ).update(
            status=status,
            last_error=str(error)[:255],
            next_attempt_at=timezone.now() + timedelta(seconds=error.retry_after or 60),
            claim_token=None,
        )
        return
    DiscordDelivery.objects.filter(
        pk=delivery.pk, claim_token=delivery.claim_token
    ).update(
        status=DiscordDelivery.Status.SENT,
        message_id=message_id,
        delivered_at=timezone.now(),
        last_error="",
        claim_token=None,
    )


def deliver_pending(*, limit: int = 50) -> dict[str, int]:
    if not 1 <= limit <= 500:
        raise ValidationError("Choose a delivery limit between 1 and 500.")
    # Crashed workers may have sent a message. Never blindly reclaim these rows.
    abandoned = DiscordDelivery.objects.filter(
        status=DiscordDelivery.Status.SENDING,
        claimed_at__lt=timezone.now() - timedelta(minutes=15),
    )
    abandoned.filter(message_attempted_at__isnull=True).update(
        status=DiscordDelivery.Status.PENDING,
        claim_token=None,
        last_error="Worker stopped during preparation; safe to retry.",
    )
    abandoned.filter(message_attempted_at__isnull=False).update(
        status=DiscordDelivery.Status.UNCERTAIN,
        last_error="Worker stopped before recording a receipt; verify delivery.",
        claim_token=None,
    )
    DiscordDelivery.objects.filter(
        status=DiscordDelivery.Status.PENDING,
        cycle_attempts__gte=MAX_ATTEMPTS,
    ).update(
        status=DiscordDelivery.Status.FAILED,
        last_error="Automatic attempt limit reached; review before retrying.",
    )
    counts = {"attempted": 0, "sent": 0, "pending": 0, "failed": 0, "uncertain": 0}
    ids = list(
        DiscordDelivery.objects.filter(
            status=DiscordDelivery.Status.PENDING, next_attempt_at__lte=timezone.now()
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )
    for pk in ids:
        if DiscordDispatchState.objects.filter(
            pk=1, pause_until__gt=timezone.now()
        ).exists():
            break
        token = uuid.uuid4()
        if not DiscordDelivery.objects.filter(
            pk=pk,
            status=DiscordDelivery.Status.PENDING,
            next_attempt_at__lte=timezone.now(),
        ).update(
            status=DiscordDelivery.Status.SENDING,
            claim_token=token,
            claimed_at=timezone.now(),
            message_attempted_at=None,
            attempts=F("attempts") + 1,
            cycle_attempts=F("cycle_attempts") + 1,
        ):
            continue
        delivery: DiscordDelivery = DiscordDelivery.objects.select_related(
            "notification"
        ).get(pk=pk)
        _send_claimed(delivery)
        delivery.refresh_from_db()
        counts["attempted"] += 1
        if delivery.status in counts:
            counts[delivery.status] += 1
    return counts


def retry_delivery(delivery_id: int, *, actor: Any) -> None:
    if not is_organizer(actor):
        raise PermissionDenied("Organizer scheduling permissions are required.")
    if not DiscordDelivery.objects.filter(
        pk=delivery_id, status=DiscordDelivery.Status.FAILED
    ).update(
        status=DiscordDelivery.Status.PENDING,
        next_attempt_at=timezone.now(),
        cycle_attempts=0,
        last_error="",
    ):
        raise ValidationError(
            "Only definite failures can be retried. Verify uncertain deliveries first."
        )


def verify_receipt(delivery_id: int, message_id: str, *, actor: Any) -> None:
    if not is_organizer(actor):
        raise PermissionDenied("Organizer scheduling permissions are required.")
    delivery: DiscordDelivery = DiscordDelivery.objects.get(pk=delivery_id)
    if (
        delivery.status != DiscordDelivery.Status.UNCERTAIN
        or not delivery.channel_id
        or not delivery.content
    ):
        raise ValidationError("This delivery has no uncertain message to verify.")
    try:
        bot = discord_request("/users/@me")
        message = discord_request(
            f"/channels/{snowflake(delivery.channel_id)}/messages/{snowflake(message_id)}"
        )
        if (
            message.get("id") != message_id
            or message.get("channel_id") != delivery.channel_id
            or not isinstance(message.get("author"), dict)
            or message["author"].get("id") != snowflake(bot.get("id"))
            or message.get("content") != delivery.content
        ):
            raise DeliveryError("That message does not match this notification.")
    except DeliveryError as error:
        raise ValidationError(str(error)) from None
    DiscordDelivery.objects.filter(
        pk=delivery.pk, status=DiscordDelivery.Status.UNCERTAIN
    ).update(
        status=DiscordDelivery.Status.SENT,
        message_id=message_id,
        delivered_at=timezone.now(),
        last_error="",
    )
