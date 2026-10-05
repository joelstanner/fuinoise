from typing import Any
from zoneinfo import ZoneInfo

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import formats, timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .accounts import is_eligible
from .models import Event, RaidSlot, SlotRequest, Streamer
from .requests import (
    cancel_assignment,
    save_slot_request,
    selectable_slots,
    verified_streamer,
    withdraw_slot_request,
)


def signup_events(streamer: Streamer) -> QuerySet[Event]:
    visibility = Q(publication_status=Event.PublicationStatus.PUBLISHED)
    if is_eligible(streamer.account):
        visibility |= Q(
            publication_status=Event.PublicationStatus.DRAFT,
            signup_before_publication=True,
        )
    return (
        Event.objects.filter(visibility)
        .filter(
            Q(raidslot__streamer__isnull=True) | Q(raidslot__streamer=streamer),
            raidslot__start__gt=timezone.now(),
            raidslot__duration_minutes__gt=0,
        )
        .distinct()
        .order_by("date", "id")
    )


def _local_label(start: Any, minutes: int | None, zone: str) -> str:
    local = timezone.localtime(start, ZoneInfo(zone))
    label = str(formats.date_format(local, "D, M j · g:i A T", use_l10n=False))
    return f"{label} · {minutes} minutes" if minutes else label


def dashboard_context(streamer: Streamer) -> dict[str, Any]:
    now = timezone.now()
    upcoming: list[Any] = []
    past: list[Any] = []
    for slot in (
        RaidSlot.objects.filter(
            streamer=streamer,
            event__publication_status=Event.PublicationStatus.PUBLISHED,
        )
        .select_related("event")
        .order_by("start", "id")
    ):
        slot.display_time = _local_label(
            slot.start, slot.duration_minutes, slot.event.event_time_zone
        )
        slot.can_cancel = slot.start > now or (slot.end is not None and slot.end > now)
        (upcoming if slot.can_cancel else past).append(slot)
    requests = list(
        SlotRequest.objects.filter(streamer=streamer)
        .select_related("event")
        .prefetch_related("preferences__slot")
        .order_by("-event__date", "-id")
    )
    for request in requests:
        event = request.event
        request.event_available = (
            event.publication_status == Event.PublicationStatus.PUBLISHED
            or (
                event.publication_status == Event.PublicationStatus.DRAFT
                and event.signup_before_publication
                and is_eligible(streamer.account)
            )
        )
        request.preference_rows = []
        for preference in request.preferences.all():
            slot = preference.slot
            changed = (
                slot is None
                or slot.start != preference.requested_start
                or slot.duration_minutes != preference.requested_duration_minutes
            )
            request.preference_rows.append(
                {
                    "label": _local_label(
                        preference.requested_start,
                        preference.requested_duration_minutes,
                        (
                            event.event_time_zone
                            if request.event_available
                            else streamer.time_zone
                        ),
                    ),
                    "changed": changed,
                }
            )
    return {
        "signup_events": signup_events(streamer),
        "slot_requests": requests,
        "confirmed_assignments": upcoming,
        "past_assignments": list(reversed(past)),
    }


class PreferredSlotField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj: RaidSlot) -> str:
        label = _local_label(obj.start, obj.duration_minutes, obj.event.event_time_zone)
        return f"{label} · Assigned to you" if obj.streamer_id else label


class SlotRequestForm(forms.Form):
    schedule_version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    request_version = forms.IntegerField(
        min_value=0, required=False, widget=forms.HiddenInput
    )
    slots = PreferredSlotField(
        queryset=RaidSlot.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Preferred slots",
        help_text="Choose the times you prefer. These choices do not reserve "
        "slots or confirm a performance.",
    )
    notes = forms.CharField(
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Notes for organizers",
    )

    def __init__(
        self, *args: Any, event: Event, streamer: Streamer, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.fields["slots"].queryset = selectable_slots(
            event, streamer
        ).select_related("event")


@never_cache
@login_required
def event_signup(request: HttpRequest, pk: int) -> HttpResponse:
    streamer = verified_streamer(request.user)
    # Existing published events are readable even while eligibility is pending.
    visibility = Q(publication_status=Event.PublicationStatus.PUBLISHED)
    if is_eligible(streamer.account):
        visibility |= Q(
            publication_status=Event.PublicationStatus.DRAFT,
            signup_before_publication=True,
        )
    event = get_object_or_404(Event.objects.filter(visibility), pk=pk)
    current = (
        SlotRequest.objects.filter(event=event, streamer=streamer)
        .prefetch_related("preferences")
        .first()
    )
    form = SlotRequestForm(
        request.POST if request.method == "POST" else None,
        event=event,
        streamer=streamer,
        initial={
            "schedule_version": event.schedule_version,
            "request_version": current.version if current else None,
            "slots": (
                [p.slot_id for p in current.preferences.all() if p.slot_id]
                if current
                else []
            ),
            "notes": current.notes if current else "",
        },
    )
    status = 200
    if request.method == "POST":
        if form.is_valid():
            try:
                save_slot_request(
                    event.pk,
                    actor=request.user,
                    slot_ids=[slot.pk for slot in form.cleaned_data["slots"]],
                    expected_schedule_version=form.cleaned_data["schedule_version"],
                    expected_request_version=form.cleaned_data["request_version"],
                    notes=form.cleaned_data["notes"],
                )
            except ValidationError as error:
                form.add_error(None, error)
                status = 409
            else:
                messages.success(
                    request,
                    "Your preferences were submitted for organizer review. "
                    "Existing confirmed assignments have not changed.",
                )
                return redirect("account_home")
        if status == 200:
            status = 400
    elif request.method != "GET":
        return HttpResponse(status=405, headers={"Allow": "GET, POST"})
    return render(
        request,
        "fuinoise_live/event_signup.html",
        {
            "event": event,
            "form": form,
            "current_request": current,
            "eligible": is_eligible(streamer.account),
            "has_choices": selectable_slots(event, streamer).exists(),
        },
        status=status,
    )


class RequestVersionForm(forms.Form):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


@login_required
@require_POST
def withdraw_request(request: HttpRequest, pk: int) -> HttpResponse:
    # Scope existence checks to the signed-in streamer, not POST-supplied IDs.
    streamer = verified_streamer(request.user)
    get_object_or_404(SlotRequest, pk=pk, streamer=streamer)
    form = RequestVersionForm(request.POST)
    if not form.is_valid():
        return HttpResponse("Reload the dashboard before withdrawing.", status=400)
    try:
        withdraw_slot_request(
            pk, actor=request.user, expected_version=form.cleaned_data["version"]
        )
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(
            request,
            "Request withdrawn. Confirmed performances stay assigned; "
            "cancel those separately if needed.",
        )
    return redirect("account_home")


@login_required
@require_POST
def cancel_performance(request: HttpRequest, pk: int) -> HttpResponse:
    streamer = verified_streamer(request.user)
    get_object_or_404(
        RaidSlot,
        pk=pk,
        streamer=streamer,
        event__publication_status=Event.PublicationStatus.PUBLISHED,
    )
    form = RequestVersionForm(request.POST)
    if not form.is_valid():
        return HttpResponse("Reload the dashboard before canceling.", status=400)
    try:
        cancel_assignment(
            pk,
            actor=request.user,
            expected_schedule_version=form.cleaned_data["version"],
        )
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(
            request, "Performance canceled. The public slot is open again."
        )
    return redirect("account_home")
