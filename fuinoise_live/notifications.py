"""Durable on-site notices and Discord outbox records; never send in a transaction."""

import uuid
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from .models import DiscordDelivery, Event, Notification, RaidSlot, StreamerAccount


def is_organizer(actor: Any) -> bool:
    return bool(
        actor.is_authenticated
        and actor.is_active
        and actor.has_perm("fuinoise_live.change_event")
        and actor.has_perm("fuinoise_live.change_raidslot")
    )


def visible_notifications(actor: Any) -> QuerySet[Notification]:
    if not actor.is_authenticated or not actor.is_active:
        return Notification.objects.none()
    audience = Q(recipient=actor)
    if is_organizer(actor):
        audience |= Q(recipient__isnull=True)
    return Notification.objects.filter(audience)


@transaction.atomic
def record_notice(
    key: str,
    *,
    kind: str,
    event: Event,
    title: str,
    body: str,
    streamer_id: int,
) -> None:
    account = StreamerAccount.objects.filter(streamer_id=streamer_id).first()
    recipients = [(None, str(settings.DISCORD_ORGANIZER_CHANNEL_ID))]
    if account:
        recipients.append((account.user_id, account.discord_id or ""))
    for user_id, target in recipients:
        notice, created = Notification.objects.get_or_create(
            key=f"{key}:{user_id or 'organizers'}",
            defaults={
                "kind": kind,
                "event": event,
                "recipient_id": user_id,
                "title": title,
                "body": body,
            },
        )
        if created:
            DiscordDelivery.objects.create(
                notification=notice,
                nonce=uuid.uuid4().hex[:24],
                target_id=target,
                guild_id=str(settings.DISCORD_GUILD_ID) if user_id is None else "",
            )


def request_notice(request: Any, action: str) -> None:
    labels = {
        "submitted": "Slot preferences submitted",
        "updated": "Slot preferences updated",
        "withdrawn": "Slot request withdrawn",
        "declined": "Slot request declined",
    }
    body = f"{request.streamer.display_name} · {request.event.name}."
    if action in {"submitted", "updated"}:
        body += (
            " Preferences are awaiting review and do not confirm a performance."
            " Existing confirmed performances remain assigned."
        )
    elif action == "withdrawn":
        body += " Confirmed performances remain assigned."
    elif action == "declined":
        body += (
            " This request was declined. "
            "Existing confirmed performances remain assigned."
        )
    record_notice(
        f"request:{request.pk}:{request.version}:{action}",
        kind=f"request_{action}",
        event=request.event,
        title=labels[action],
        body=body,
        streamer_id=request.streamer_id,
    )


def assignment_notice(event: Event, slot: RaidSlot, action: str) -> None:
    local = timezone.localtime(slot.start, ZoneInfo(event.event_time_zone))
    label = local.strftime("%a, %b %d, %Y · %H:%M %Z")
    title = {
        "confirmed": "Performance confirmed",
        "updated": "Confirmed performance updated",
        "removed": "Performance removed from the lineup",
        "canceled": "Performance canceled",
    }[action]
    duration = f" · {slot.duration_minutes} minutes" if slot.duration_minutes else ""
    body = f"{slot.streamer.display_name} · {event.name} · {label}{duration}."
    if action == "canceled":
        body += " The public slot is open again."
    record_notice(
        f"schedule:{event.pk}:{event.schedule_version}:slot:{slot.pk}:{action}",
        kind=f"assignment_{action}",
        event=event,
        title=title,
        body=body,
        streamer_id=slot.streamer_id,
    )


def publication_notices(
    event: Event,
    previous: dict[int, RaidSlot],
    *,
    was_public: bool,
    metadata_changed: bool,
) -> None:
    current = {slot.pk: slot for slot in event.raidslot_set.select_related("streamer")}
    for pk, old in previous.items():
        new = current.get(pk)
        if (
            was_public
            and old.streamer_id
            and (new is None or new.streamer_id != old.streamer_id)
        ):
            assignment_notice(event, old, "removed")
    for pk, new in current.items():
        if new.streamer_id is None:
            continue
        original = previous.get(pk)
        if (
            not was_public
            or original is None
            or original.streamer_id != new.streamer_id
        ):
            assignment_notice(event, new, "confirmed")
        elif metadata_changed or (original.start, original.duration_minutes) != (
            new.start,
            new.duration_minutes,
        ):
            assignment_notice(event, new, "updated")
