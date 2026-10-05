"""Streamer preferences and private organizer assignment operations."""

from collections.abc import Sequence
from typing import Any

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q, QuerySet
from django.utils import timezone

from .accounts import is_eligible, require_eligible_streamer
from .models import (
    DraftSlot,
    Event,
    RaidSlot,
    ScheduleDraft,
    SlotPreference,
    SlotRequest,
    Streamer,
    StreamerAccount,
)
from .scheduling import (
    DraftSlotInput,
    StaleScheduleError,
    _assert_current,
    _lock_event,
    _require_organizer,
    cancel_published_assignment,
    save_schedule_draft,
)


def verified_streamer(actor: Any) -> Streamer:
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied("Sign in with Twitch first.")
    account = (
        StreamerAccount.objects.select_related("streamer").filter(user=actor).first()
    )
    if account is None or not account.streamer.twitch_id:
        raise PermissionDenied("A verified Twitch account is required.")
    streamer: Streamer = account.streamer
    return streamer


def selectable_slots(event: Event, streamer: Streamer) -> QuerySet[RaidSlot]:
    return (
        RaidSlot.objects.filter(
            event=event, start__gt=timezone.now(), duration_minutes__gt=0
        )
        .filter(Q(streamer__isnull=True) | Q(streamer=streamer))
        .order_by("start", "id")
    )


def _lock_eligible(streamer_id: int) -> None:
    account = (
        StreamerAccount.objects.select_related("user", "streamer")
        .filter(streamer_id=streamer_id)
        .first()
    )
    if account is None or not StreamerAccount.objects.filter(
        pk=account.pk, version=account.version
    ).update(version=F("version")):
        raise ValidationError("The streamer's eligibility changed. Review it again.")
    account.refresh_from_db()
    if not is_eligible(account):
        raise ValidationError(
            "The streamer needs current eligibility before a new assignment "
            "can be confirmed."
        )


def _claim_request(request: SlotRequest, expected_version: int | None) -> None:
    if expected_version is None or not SlotRequest.objects.filter(
        pk=request.pk, version=expected_version
    ).update(version=F("version") + 1):
        raise StaleScheduleError(
            "This request has newer changes. Reload before saving."
        )
    request.version = expected_version + 1


def save_slot_request(
    event_id: int,
    *,
    actor: Any,
    slot_ids: Sequence[int],
    expected_schedule_version: int,
    expected_request_version: int | None,
    notes: str = "",
) -> SlotRequest:
    # Provider checks finish before acquiring schedule locks.
    streamer = require_eligible_streamer(actor)
    with transaction.atomic():
        event = _lock_event(event_id, expected_schedule_version)
        _lock_eligible(streamer.pk)
        if not event.signup_open:
            raise ValidationError("Signup is not open for this event.")
        if (
            not slot_ids
            or any(type(value) is not int for value in slot_ids)
            or len(set(slot_ids)) != len(slot_ids)
        ):
            raise ValidationError("Choose one or more different preferred slots.")
        slots = list(selectable_slots(event, streamer).filter(pk__in=slot_ids))
        if len(slots) != len(slot_ids):
            raise ValidationError(
                "Some choices are no longer available. Reload the signup page."
            )
        request: SlotRequest | None = SlotRequest.objects.filter(
            event=event, streamer=streamer
        ).first()
        if request is None:
            if expected_request_version is not None:
                raise StaleScheduleError(
                    "This request no longer exists. Reload the signup page."
                )
            request = SlotRequest(event=event, streamer=streamer)
        else:
            _claim_request(request, expected_request_version)
        request.status = SlotRequest.Status.SUBMITTED
        request.notes = notes
        request.full_clean()
        request.save()
        request.preferences.all().delete()
        SlotPreference.objects.bulk_create(
            [
                SlotPreference(
                    request=request,
                    slot=slot,
                    requested_start=slot.start,
                    requested_duration_minutes=slot.duration_minutes,
                )
                for slot in slots
            ]
        )
        return request


@transaction.atomic
def withdraw_slot_request(
    request_id: int, *, actor: Any, expected_version: int
) -> SlotRequest:
    streamer = verified_streamer(actor)
    request: SlotRequest = SlotRequest.objects.select_related("event").get(
        pk=request_id
    )
    if request.streamer_id != streamer.pk:
        raise PermissionDenied("You can only withdraw your own request.")
    _lock_event(request.event_id, request.event.schedule_version)
    _claim_request(request, expected_version)
    request.status = SlotRequest.Status.WITHDRAWN
    request.save(update_fields=("status", "version", "updated_at"))
    return request


@transaction.atomic
def decline_slot_request(
    request_id: int, *, actor: Any, expected_version: int, organizer_notes: str = ""
) -> SlotRequest:
    _require_organizer(actor)
    request: SlotRequest = SlotRequest.objects.select_related("event").get(
        pk=request_id
    )
    _lock_event(request.event_id, request.event.schedule_version)
    _claim_request(request, expected_version)
    request.status = SlotRequest.Status.DECLINED
    request.organizer_notes = organizer_notes
    request.full_clean()
    request.save()
    return request


def validate_request_assignments(event: Event, slots: Sequence[DraftSlot]) -> None:
    """An old preference submission cannot silently become a new assignment."""
    pending = []
    published = {slot.pk: slot for slot in event.raidslot_set.all()}
    for slot in slots:
        if slot.signup_request_id is None:
            continue
        request = slot.signup_request
        if request.event_id != event.pk or request.streamer_id != slot.streamer_id:
            raise ValidationError(
                "Request assignments must match the event and streamer."
            )
        original = published.get(slot.source_slot_id)
        already_confirmed = (
            event.publication_status == Event.PublicationStatus.PUBLISHED
            and original is not None
            and (
                original.streamer_id == slot.streamer_id
                and original.signup_request_id == request.pk
            )
        )
        if not already_confirmed:
            if (
                request.status != SlotRequest.Status.SUBMITTED
                or request.version != slot.request_version
            ):
                raise StaleScheduleError(
                    "The streamer changed or withdrew this request. "
                    "Review it before assigning."
                )
            pending.append(request.streamer_id)
    for streamer_id in sorted(set(pending)):
        _lock_eligible(streamer_id)


def assign_request_to_draft(
    request_id: int,
    draft_slot_id: int,
    *,
    actor: Any,
    expected_request_version: int,
    expected_draft_version: int,
) -> ScheduleDraft:
    _require_organizer(actor)
    request: SlotRequest = SlotRequest.objects.select_related(
        "streamer__account__user"
    ).get(pk=request_id)
    if (
        request.status != SlotRequest.Status.SUBMITTED
        or request.version != expected_request_version
    ):
        raise StaleScheduleError("The request changed. Reload before assigning.")
    account = (
        StreamerAccount.objects.select_related("user")
        .filter(streamer_id=request.streamer_id)
        .first()
    )
    if account is None:
        raise ValidationError("A verified Twitch account is required for this request.")
    require_eligible_streamer(account.user)
    with transaction.atomic():
        target: DraftSlot = DraftSlot.objects.select_related("draft").get(
            pk=draft_slot_id
        )
        draft = target.draft
        event = _lock_event(draft.event_id, draft.base_schedule_version)
        _assert_current(draft, event)
        request.refresh_from_db()
        if request.event_id != event.pk:
            raise ValidationError("The request and slot must belong to the same event.")
        if (
            request.status != SlotRequest.Status.SUBMITTED
            or request.version != expected_request_version
        ):
            raise StaleScheduleError("The request changed. Reload before assigning.")
        if target.streamer_id not in (None, request.streamer_id):
            raise ValidationError(
                "This slot is assigned to another streamer. Reopen it before assigning."
            )
        if target.start <= timezone.now():
            raise ValidationError("Choose a future performance time.")
        _lock_eligible(request.streamer_id)
        DraftSlot.objects.filter(pk=target.pk).update(
            streamer_id=request.streamer_id,
            signup_request=request,
            request_version=request.version,
        )
        inputs = [
            DraftSlotInput(
                id=slot.pk,
                start=slot.start,
                streamer_id=slot.streamer_id,
                duration_minutes=slot.duration_minutes,
                replay_url=slot.replay_url,
                raid_slot_note=slot.raid_slot_note,
            )
            for slot in draft.slots.all()
        ]
        saved: ScheduleDraft = save_schedule_draft(
            draft.pk, expected_version=expected_draft_version, actor=actor, slots=inputs
        )
        return saved


def cancel_assignment(
    slot_id: int, *, actor: Any, expected_schedule_version: int
) -> bool:
    streamer = verified_streamer(actor)
    with transaction.atomic():
        slot: RaidSlot = RaidSlot.objects.select_related("event").get(pk=slot_id)
        _lock_event(slot.event_id, expected_schedule_version)
        if slot.streamer_id != streamer.pk:
            raise PermissionDenied(
                "You can only cancel your own confirmed performance."
            )
        now = timezone.now()
        if slot.start <= now and (slot.end is None or slot.end <= now):
            raise ValidationError("Completed performances cannot be canceled.")
        return bool(
            cancel_published_assignment(
                slot_id,
                streamer_id=streamer.pk,
                expected_schedule_version=expected_schedule_version,
            )
        )
