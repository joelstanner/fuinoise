from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.db.models import Prefetch, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import formats, timezone

from .models import Event, RaidSlot


def published_events() -> QuerySet[Event]:
    slots = RaidSlot.objects.select_related("streamer").order_by("start", "id")
    return (
        Event.objects.filter(publication_status=Event.PublicationStatus.PUBLISHED)
        .select_related("community")
        .prefetch_related(Prefetch("raidslot_set", queryset=slots))
        .order_by("date", "id")
    )


def prepare_event(event: Any, now: datetime) -> Any:
    """Classify by local calendar days, extending through the final slot day."""
    zone = ZoneInfo(event.event_time_zone)
    today = timezone.localtime(now, zone).date()
    last_day = event.date
    event.public_slots = list(event.raidslot_set.all())
    for slot in event.public_slots:
        slot.local_start = timezone.localtime(slot.start, zone)
        slot.local_start_display = formats.date_format(
            slot.local_start, "D, M j · g:i A T", use_l10n=False
        )
        slot.local_start_short = formats.date_format(
            slot.local_start, "D g:i A", use_l10n=False
        )
        slot.local_start_iso = slot.local_start.isoformat()
        last_day = max(last_day, slot.local_start.date())
    if today < event.date:
        event.public_period = "upcoming"
    elif today <= last_day:
        event.public_period = "current"
    else:
        event.public_period = "history"
    return event


def event_list(request: HttpRequest, period: str) -> HttpResponse:
    now = timezone.now()
    events = [
        event
        for event in (prepare_event(event, now) for event in published_events())
        if event.public_period == period
    ]
    if period == "history":
        events.reverse()
    titles = {
        "current": "Current events",
        "upcoming": "Upcoming",
        "history": "Past events",
    }
    return render(
        request,
        "fuinoise_live/event_list.html",
        {"events": events, "period": period, "title": titles[period]},
    )


def current_events(request: HttpRequest) -> HttpResponse:
    return event_list(request, "current")


def upcoming_events(request: HttpRequest) -> HttpResponse:
    return event_list(request, "upcoming")


def historical_events(request: HttpRequest) -> HttpResponse:
    return event_list(request, "history")


def event_detail(request: HttpRequest, pk: int) -> HttpResponse:
    event = get_object_or_404(published_events(), pk=pk)
    return render(
        request,
        "fuinoise_live/event_detail.html",
        {"event": prepare_event(event, timezone.now())},
    )
