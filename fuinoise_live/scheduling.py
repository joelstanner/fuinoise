"""Scheduling operations shared by the future API and organizer interface."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as datetime_timezone
from typing import Any
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import DraftSlot, Event, RaidSlot, ScheduleDraft

UTC = datetime_timezone.utc
EVENT_FIELDS = (
    "date",
    "name",
    "description",
    "community_id",
    "event_time_zone",
    "default_slot_duration_minutes",
    "signup_before_publication",
)
SLOT_FIELDS = (
    "streamer_id",
    "start",
    "duration_minutes",
    "replay_url",
    "raid_slot_note",
    "signup_request_id",
)


class StaleScheduleError(ValidationError):
    """The caller's draft or published baseline is no longer current."""


@dataclass(frozen=True)
class DraftSlotInput:
    start: datetime
    streamer_id: int | None = None
    duration_minutes: int | None = None
    replay_url: str = ""
    raid_slot_note: str = ""
    id: int | None = None


@dataclass(frozen=True)
class PublicationResult:
    event_id: int
    schedule_version: int
    confirmed_slot_ids: tuple[int, ...]
    changed_slot_ids: tuple[int, ...]


def event_local_start(day: date, clock: time, zone_name: str) -> datetime:
    zone = ZoneInfo(zone_name)
    wall = datetime.combine(day, clock).replace(tzinfo=None)
    local = wall.replace(tzinfo=zone)
    if local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != wall:
        raise ValidationError("This time does not exist in the event time zone.")
    if local.utcoffset() != wall.replace(tzinfo=zone, fold=1).utcoffset():
        raise ValidationError("This time is ambiguous in the event time zone.")
    return local


def validate_intervals(intervals: Sequence[tuple[datetime, int | None]]) -> None:
    ends: list[tuple[datetime, datetime]] = []
    for start, minutes in intervals:
        if timezone.is_naive(start):
            raise ValidationError("Slot start times must include a time zone.")
        if minutes is None:
            raise ValidationError("Review and enter a planned duration for every slot.")
        if type(minutes) is not int or minutes < 1:
            raise ValidationError("Slot duration must be a positive number of minutes.")
        instant = start.astimezone(UTC)
        try:
            end = instant + timedelta(minutes=minutes)
        except OverflowError as error:
            raise ValidationError(
                "Slot duration extends beyond the supported dates."
            ) from error
        ends.append((instant, end))
    ends.sort()
    for previous, current in zip(ends, ends[1:]):
        if current[0] < previous[1]:
            raise ValidationError(
                "Lineup slots overlap. Change the start time or planned duration."
            )


def _require_organizer(actor: Any) -> None:
    if not (
        actor.is_authenticated
        and actor.is_active
        and actor.has_perm("fuinoise_live.change_event")
        and actor.has_perm("fuinoise_live.change_raidslot")
    ):
        raise PermissionDenied("Organizer scheduling permissions are required.")


def _values(record: Any, fields: Sequence[str]) -> dict[str, Any]:
    return {field: getattr(record, field) for field in fields}


def _fingerprint(event: Event) -> str:
    snapshot = {
        "event": _values(event, EVENT_FIELDS),
        "publication_status": event.publication_status,
        "slots": [
            {
                "id": slot.pk,
                **{
                    key: value
                    for key, value in _values(slot, SLOT_FIELDS).items()
                    if key != "signup_request_id" or value is not None
                },
            }
            for slot in event.raidslot_set.order_by("id")
        ],
    }
    serialized = json.dumps(snapshot, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _lock_event(event_id: int, expected_version: int) -> Event:
    # A conditional write locks the parent even on SQLite, which has no row locks.
    count = Event.objects.filter(pk=event_id, schedule_version=expected_version).update(
        schedule_version=F("schedule_version")
    )
    if not count:
        raise StaleScheduleError("The published schedule changed. Reload the draft.")
    event: Event = Event.objects.get(pk=event_id)
    return event


def _assert_current(draft: ScheduleDraft, event: Event) -> None:
    # Also detect maintenance edits made outside the scheduling operations.
    if draft.base_fingerprint != _fingerprint(event):
        raise StaleScheduleError(
            "The schedule changed since this draft started. Review a fresh draft."
        )


def _claim_draft(draft: ScheduleDraft, expected_version: int) -> None:
    if not ScheduleDraft.objects.filter(pk=draft.pk, version=expected_version).update(
        version=F("version") + 1
    ):
        raise StaleScheduleError("This draft has newer edits. Reload before saving.")
    draft.version = expected_version + 1


def _copy_slots(draft: ScheduleDraft, event: Event) -> None:
    DraftSlot.objects.bulk_create(
        [
            DraftSlot(
                draft=draft,
                source_slot=slot,
                request_version=(
                    slot.signup_request.version if slot.signup_request_id else None
                ),
                **_values(slot, SLOT_FIELDS),
            )
            for slot in event.raidslot_set.all()
        ]
    )


@transaction.atomic
def get_schedule_draft(event_id: int, *, actor: Any) -> ScheduleDraft:
    _require_organizer(actor)
    current = Event.objects.get(pk=event_id)
    event = _lock_event(event_id, current.schedule_version)
    draft: ScheduleDraft
    draft, created = ScheduleDraft.objects.get_or_create(
        event=event,
        defaults={
            **_values(event, EVENT_FIELDS),
            "base_schedule_version": event.schedule_version,
            "base_fingerprint": _fingerprint(event),
        },
    )
    if created:
        _copy_slots(draft, event)
    else:
        if draft.base_schedule_version != event.schedule_version:
            raise StaleScheduleError(
                "The published schedule changed. Reload the draft."
            )
        _assert_current(draft, event)
    return draft


@transaction.atomic
def reset_schedule_draft(
    draft_id: int, *, expected_version: int, actor: Any
) -> ScheduleDraft:
    """Explicitly discard private edits and rebuild from the current schedule."""
    _require_organizer(actor)
    draft: ScheduleDraft = ScheduleDraft.objects.get(pk=draft_id)
    current = Event.objects.get(pk=draft.event_id)
    event = _lock_event(current.pk, current.schedule_version)
    _claim_draft(draft, expected_version)
    for field, value in _values(event, EVENT_FIELDS).items():
        setattr(draft, field, value)
    draft.base_schedule_version = event.schedule_version
    draft.base_fingerprint = _fingerprint(event)
    draft.save()
    draft.slots.all().delete()
    _copy_slots(draft, event)
    return draft


@transaction.atomic
def save_schedule_draft(
    draft_id: int,
    *,
    expected_version: int,
    slots: Sequence[DraftSlotInput],
    actor: Any,
    event_changes: Mapping[str, Any] | None = None,
) -> ScheduleDraft:
    _require_organizer(actor)
    draft: ScheduleDraft = ScheduleDraft.objects.get(pk=draft_id)
    event = _lock_event(draft.event_id, draft.base_schedule_version)
    _assert_current(draft, event)
    _claim_draft(draft, expected_version)
    for field, value in (event_changes or {}).items():
        if field not in EVENT_FIELDS:
            raise ValidationError(f"Cannot edit {field} through a schedule draft.")
        setattr(draft, field, value)
    draft.full_clean()

    existing = {slot.pk: slot for slot in draft.slots.all()}
    seen: set[int] = set()
    prepared: list[DraftSlot] = []
    for spec in slots:
        if spec.id is not None:
            if spec.id not in existing or spec.id in seen:
                raise ValidationError("Each draft slot must belong to this draft once.")
            seen.add(spec.id)
            slot = existing[spec.id]
        else:
            slot = DraftSlot(draft=draft)
        slot.start = spec.start
        if slot.streamer_id != spec.streamer_id:
            slot.signup_request_id = None
            slot.request_version = None
        slot.streamer_id = spec.streamer_id
        slot.duration_minutes = spec.duration_minutes
        if spec.id is None and slot.duration_minutes is None:
            slot.duration_minutes = draft.default_slot_duration_minutes
        slot.replay_url = spec.replay_url
        slot.raid_slot_note = spec.raid_slot_note
        slot.full_clean()
        prepared.append(slot)
    validate_intervals([(slot.start, slot.duration_minutes) for slot in prepared])
    from .requests import validate_request_assignments

    validate_request_assignments(event, prepared)
    draft.slots.exclude(pk__in=seen).delete()
    for slot in prepared:
        slot.save()
    draft.save()
    return draft


@transaction.atomic
def _apply_schedule_draft(
    draft_id: int, *, expected_version: int, actor: Any, for_signup: bool = False
) -> PublicationResult:
    _require_organizer(actor)
    draft = ScheduleDraft.objects.get(pk=draft_id)
    event = _lock_event(draft.event_id, draft.base_schedule_version)
    _assert_current(draft, event)
    _claim_draft(draft, expected_version)
    if for_signup:
        if event.publication_status == Event.PublicationStatus.PUBLISHED:
            raise ValidationError("Signup is already open on this published event.")
        draft.signup_before_publication = True
    draft.full_clean()
    slots = list(draft.slots.all())
    for slot in slots:
        slot.full_clean()
    validate_intervals([(slot.start, slot.duration_minutes) for slot in slots])
    from .requests import validate_request_assignments

    validate_request_assignments(event, slots)
    previous = {slot.pk: slot for slot in event.raidslot_set.all()}
    was_public = event.publication_status == Event.PublicationStatus.PUBLISHED
    metadata_changed = any(
        getattr(event, field) != getattr(draft, field)
        for field in ("name", "description", "date", "event_time_zone", "community_id")
    )
    source_ids = {slot.source_slot_id for slot in slots if slot.source_slot_id}
    if not source_ids.issubset(previous):
        raise ValidationError("Draft slots must refer to slots from the same event.")

    # Park existing rows at distinct instants while applying moves atomically.
    occupied = {slot.start for slot in previous.values()} | {s.start for s in slots}
    parking = datetime(2000, 1, 1, tzinfo=UTC)
    for slot in previous.values():
        while parking in occupied:
            parking += timedelta(seconds=1)
        RaidSlot.objects.filter(pk=slot.pk).update(start=parking)
        occupied.add(parking)
    event.raidslot_set.exclude(pk__in=source_ids).delete()
    confirmed: list[int] = []
    changed: list[int] = []
    for slot in slots:
        values = _values(slot, SLOT_FIELDS)
        old = previous.get(slot.source_slot_id)
        if old is None:
            published = RaidSlot.objects.create(event=event, **values)
            slot.source_slot = published
            slot.save(update_fields=("source_slot",))
        else:
            published = old
            RaidSlot.objects.filter(pk=old.pk).update(**values)
        if old is None or _values(old, SLOT_FIELDS) != values:
            changed.append(published.pk)
        if (
            not for_signup
            and slot.streamer_id is not None
            and (
                old is None
                or old.streamer_id != slot.streamer_id
                or event.publication_status != Event.PublicationStatus.PUBLISHED
            )
        ):
            confirmed.append(published.pk)

    Event.objects.filter(pk=event.pk).update(
        **_values(draft, EVENT_FIELDS),
        publication_status=(
            Event.PublicationStatus.DRAFT
            if for_signup
            else Event.PublicationStatus.PUBLISHED
        ),
        schedule_version=F("schedule_version") + 1,
    )
    event.refresh_from_db()
    draft.base_schedule_version = event.schedule_version
    draft.base_fingerprint = _fingerprint(event)
    draft.save()
    if not for_signup:
        from .notifications import publication_notices

        publication_notices(
            event, previous, was_public=was_public, metadata_changed=metadata_changed
        )
    return PublicationResult(
        event.pk, event.schedule_version, tuple(confirmed), tuple(changed)
    )


def publish_schedule_draft(
    draft_id: int, *, expected_version: int, actor: Any
) -> PublicationResult:
    result: PublicationResult = _apply_schedule_draft(
        draft_id, expected_version=expected_version, actor=actor
    )
    return result


def open_schedule_signup(
    draft_id: int, *, expected_version: int, actor: Any
) -> PublicationResult:
    """Release a reviewed signup timetable without public visibility/confirmation."""
    result: PublicationResult = _apply_schedule_draft(
        draft_id, expected_version=expected_version, actor=actor, for_signup=True
    )
    return result


@transaction.atomic
def cancel_published_assignment(
    slot_id: int, *, streamer_id: int, expected_schedule_version: int | None = None
) -> bool:
    """Internal primitive; callers supply the verified streamer identity."""
    slot = RaidSlot.objects.select_related("event").get(pk=slot_id)
    event = _lock_event(
        slot.event_id,
        (
            slot.event.schedule_version
            if expected_schedule_version is None
            else expected_schedule_version
        ),
    )
    slot.refresh_from_db()
    if event.publication_status != Event.PublicationStatus.PUBLISHED:
        raise ValidationError("Only confirmed, published assignments can be canceled.")
    if slot.streamer_id is None:
        return False
    if slot.streamer_id != streamer_id:
        raise PermissionDenied("A streamer can only cancel their own assignment.")
    RaidSlot.objects.filter(pk=slot.pk).update(streamer=None, signup_request=None)
    Event.objects.filter(pk=event.pk).update(schedule_version=F("schedule_version") + 1)
    event.refresh_from_db()
    from .notifications import assignment_notice

    assignment_notice(event, slot, "canceled")
    # Existing drafts retain their edits but are stale until explicitly reset.
    return True
