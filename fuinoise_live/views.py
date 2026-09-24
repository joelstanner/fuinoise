from zoneinfo import ZoneInfo

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, render
from django.utils import formats, timezone

from .models import Event, RaidSlot


def published_events():
    slots = RaidSlot.objects.select_related("streamer").order_by("position", "id")
    return (
        Event.objects.filter(publication_status=Event.PublicationStatus.PUBLISHED)
        .select_related("community")
        .prefetch_related(Prefetch("raidslot_set", queryset=slots))
        .order_by("date", "id")
    )


def prepare_event(event, now):
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
        if slot.handoff_at:
            slot.local_handoff = timezone.localtime(slot.handoff_at, zone)
            slot.local_handoff_display = formats.date_format(
                slot.local_handoff, "D, M j · g:i A T", use_l10n=False
            )
            slot.local_handoff_iso = slot.local_handoff.isoformat()
            last_day = max(last_day, slot.local_handoff.date())
        else:
            slot.local_handoff = None

    if today < event.date:
        event.public_period = "upcoming"
    elif today <= last_day:
        event.public_period = "current"
    else:
        event.public_period = "history"
    return event


def event_list(request, period):
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


def current_events(request):
    return event_list(request, "current")


def upcoming_events(request):
    return event_list(request, "upcoming")


def historical_events(request):
    return event_list(request, "history")


def event_detail(request, pk):
    event = get_object_or_404(published_events(), pk=pk)
    return render(
        request,
        "fuinoise_live/event_detail.html",
        {"event": prepare_event(event, timezone.now())},
    )
