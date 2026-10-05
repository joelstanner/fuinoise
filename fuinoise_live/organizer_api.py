"""Same-origin organizer API; scheduling services own all write rules."""

from dataclasses import asdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from django.core import signing
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import exception_handler

from .accounts import is_eligible
from .lineup_import import SIGNING_SALT, apply_lineup, preview_lineup
from .models import (
    Community,
    Event,
    ScheduleDraft,
    SlotRequest,
    Streamer,
    StreamerAccount,
    timezone_choices,
)
from .requests import assign_request_to_draft, decline_slot_request
from .scheduling import (
    DraftSlotInput,
    StaleScheduleError,
    _lock_event,
    _require_organizer,
    event_local_start,
    get_schedule_draft,
    open_schedule_signup,
    publish_schedule_draft,
    reset_schedule_draft,
    save_schedule_draft,
)
from .twitch import lookup_profile, refresh_streamers


def api_exception_handler(error: Exception, context: Any) -> Any:
    if isinstance(error, ValidationError):
        return Response(
            {"errors": error.messages},
            status=409 if isinstance(error, StaleScheduleError) else 400,
        )
    if isinstance(error, ObjectDoesNotExist):
        return Response({"errors": ["This record no longer exists."]}, status=404)
    return exception_handler(error, context)


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError(
                    {key: "This field cannot be supplied." for key in sorted(unknown)}
                )
        return super().to_internal_value(data)


class VersionSerializer(StrictSerializer):
    version = serializers.IntegerField(min_value=0)


class EventFieldsSerializer(StrictSerializer):
    name = serializers.CharField(max_length=255)
    date = serializers.DateField()
    description = serializers.CharField(allow_blank=True, default="")
    community_id = serializers.IntegerField(min_value=1)
    event_time_zone = serializers.ChoiceField(choices=timezone_choices())
    default_slot_duration_minutes = serializers.IntegerField(min_value=1, default=60)
    signup_before_publication = serializers.BooleanField(default=False)


class SlotSerializer(StrictSerializer):
    id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    start = serializers.CharField(required=False)
    local_start = serializers.CharField(required=False)
    duration_minutes = serializers.IntegerField(min_value=1, allow_null=True)
    streamer_id = serializers.IntegerField(min_value=1, allow_null=True)
    replay_url = serializers.URLField(allow_blank=True, default="")
    raid_slot_note = serializers.CharField(max_length=255, allow_blank=True, default="")

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        if ("start" in data) == ("local_start" in data):
            raise serializers.ValidationError("Supply start or local_start, once.")
        field = "start" if "start" in data else "local_start"
        try:
            parsed = datetime.fromisoformat(data[field])
        except ValueError as error:
            raise serializers.ValidationError(
                "Enter a valid ISO date and time."
            ) from error
        if field == "start" and timezone.is_naive(parsed):
            raise serializers.ValidationError("Start must include its UTC offset.")
        if field == "local_start" and timezone.is_aware(parsed):
            raise serializers.ValidationError("Local time must have no UTC offset.")
        data[field] = parsed
        return data


class SaveSerializer(VersionSerializer):
    slots = SlotSerializer(many=True)
    event = EventFieldsSerializer()


class AssignmentSerializer(VersionSerializer):
    request_id = serializers.IntegerField(min_value=1)
    request_version = serializers.IntegerField(min_value=0)
    slot_id = serializers.IntegerField(min_value=1)


class DeclineSerializer(VersionSerializer):
    organizer_notes = serializers.CharField(
        max_length=2000, allow_blank=True, default=""
    )


class VisibilitySerializer(StrictSerializer):
    schedule_version = serializers.IntegerField(min_value=0)
    publication_status = serializers.ChoiceField(choices=["draft", "private"])
    signup_before_publication = serializers.BooleanField()


class PreviewSerializer(StrictSerializer):
    source = serializers.CharField(max_length=12000)


class LookupSerializer(StrictSerializer):
    login = serializers.RegexField(r"^[a-zA-Z0-9_]{1,25}$")


class ImportRowSerializer(StrictSerializer):
    local_start = serializers.CharField()
    duration_minutes = serializers.IntegerField(min_value=1)
    streamer_id = serializers.IntegerField(min_value=1, allow_null=True)
    lookup_token = serializers.CharField(
        required=False, allow_blank=True, max_length=6000
    )
    display_name = serializers.CharField(
        required=False, allow_blank=True, max_length=80
    )
    raid_slot_note = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=255
    )

    def validate_local_start(self, value: str) -> datetime:
        try:
            local = datetime.fromisoformat(value)
        except ValueError as error:
            raise serializers.ValidationError(
                "Enter a valid local date and time."
            ) from error
        if timezone.is_aware(local):
            raise serializers.ValidationError(
                "Enter the date/time in the event zone without an offset."
            )
        return local


class ImportSerializer(VersionSerializer):
    event = EventFieldsSerializer()
    rows = ImportRowSerializer(many=True)
    replace = serializers.BooleanField()
    reviewed = serializers.BooleanField()


def validated(cls: Any, request: Any) -> dict[str, Any]:
    serializer = cls(data=request.data)
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def event_fields(event: Any) -> dict[str, Any]:
    return {
        "name": event.name,
        "date": event.date.isoformat(),
        "description": event.description,
        "community_id": event.community_id,
        "event_time_zone": event.event_time_zone,
        "default_slot_duration_minutes": event.default_slot_duration_minutes,
        "signup_before_publication": event.signup_before_publication,
    }


def draft_payload(draft: ScheduleDraft, *, stale: bool = False) -> dict[str, Any]:
    event = Event.objects.get(pk=draft.event_id)
    zone = ZoneInfo(draft.event_time_zone)
    accounts = {
        account.streamer_id: account
        for account in StreamerAccount.objects.select_related("user", "streamer")
        .filter(streamer__slot_requests__event=event)
        .distinct()
    }
    requests = []
    for item in event.slot_requests.select_related("streamer").prefetch_related(
        "preferences"
    ):
        account = accounts.get(item.streamer_id)
        requests.append(
            {
                "id": item.pk,
                "streamer_id": item.streamer_id,
                "name": item.streamer.display_name,
                "status": item.status,
                "version": item.version,
                "notes": item.notes,
                "organizer_notes": item.organizer_notes,
                "eligible": account is not None and is_eligible(account),
                "preferences": [
                    {
                        "slot_id": preference.slot_id,
                        "start": preference.requested_start.isoformat(),
                        "duration_minutes": preference.requested_duration_minutes,
                    }
                    for preference in item.preferences.all()
                ],
            }
        )
    return {
        "id": draft.pk,
        "event_id": event.pk,
        "version": draft.version,
        "schedule_version": event.schedule_version,
        "publication_status": event.publication_status,
        "stale": stale,
        "event": event_fields(draft),
        "slots": [
            {
                "id": slot.pk,
                "source_slot_id": slot.source_slot_id,
                "start": slot.start.isoformat(),
                "local_start": slot.start.astimezone(zone)
                .replace(tzinfo=None)
                .isoformat(),
                "streamer_id": slot.streamer_id,
                "name": slot.streamer.display_name if slot.streamer else "Open slot",
                "duration_minutes": slot.duration_minutes,
                "replay_url": slot.replay_url,
                "raid_slot_note": slot.raid_slot_note,
                "request_id": slot.signup_request_id,
            }
            for slot in draft.slots.select_related("streamer")
        ],
        "requests": requests,
    }


@never_cache
@ensure_csrf_cookie
def workspace(request: HttpRequest) -> HttpResponse:
    _require_organizer(request.user)
    return render(request, "fuinoise_live/organizer.html")


@never_cache
@api_view(["GET", "POST"])
@transaction.atomic
def events_api(request: Any) -> Response:
    if request.method == "POST":
        if not request.user.has_perm("fuinoise_live.add_event"):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied("Event creation permission is required.")
        values = validated(EventFieldsSerializer, request)
        event = Event(**values)
        event.full_clean()
        event.save()
        draft = get_schedule_draft(event.pk, actor=request.user)
        return Response(draft_payload(draft), status=201)
    return Response(
        {
            "events": [
                {
                    "id": event.pk,
                    "name": event.name,
                    "date": event.date.isoformat(),
                    "publication_status": event.publication_status,
                }
                for event in Event.objects.order_by("-date", "-id")
            ],
            "communities": list(
                Community.objects.values("id", "name", "default_time_zone")
            ),
            "time_zones": [name for name, _ in timezone_choices()],
            "streamers": list(
                Streamer.objects.order_by("display_name").values("id", "display_name")
            ),
            "can_create": request.user.has_perm("fuinoise_live.add_event"),
        }
    )


@never_cache
@api_view(["GET", "PUT"])
@transaction.atomic
def draft_api(request: Any, pk: int) -> Response:
    event = get_object_or_404(Event, pk=pk)
    if request.method == "GET":
        try:
            draft = get_schedule_draft(event.pk, actor=request.user)
        except StaleScheduleError:
            draft = get_object_or_404(ScheduleDraft, event=event)
            return Response(draft_payload(draft, stale=True))
        return Response(draft_payload(draft))
    data = validated(SaveSerializer, request)
    draft = get_object_or_404(ScheduleDraft, event=event)
    zone = data["event"]["event_time_zone"]
    slots = []
    for values in data["slots"]:
        local = values.pop("local_start", None)
        if local is not None:
            values["start"] = event_local_start(local.date(), local.time(), zone)
        slots.append(DraftSlotInput(**values))
    saved = save_schedule_draft(
        draft.pk,
        expected_version=data["version"],
        slots=slots,
        event_changes=data["event"],
        actor=request.user,
    )
    return Response(draft_payload(saved))


@never_cache
@api_view(["POST"])
@transaction.atomic
def draft_action_api(request: Any, pk: int, action: str) -> Response:
    draft = get_object_or_404(ScheduleDraft, event_id=pk)
    data = validated(VersionSerializer, request)
    if action == "reset":
        saved = reset_schedule_draft(
            draft.pk, expected_version=data["version"], actor=request.user
        )
        return Response(draft_payload(saved))
    operation = open_schedule_signup if action == "signup" else publish_schedule_draft
    result = operation(draft.pk, expected_version=data["version"], actor=request.user)
    draft.refresh_from_db()
    return Response(
        {
            **draft_payload(draft),
            "publication": {
                "confirmed_slot_ids": result.confirmed_slot_ids,
                "changed_slot_ids": result.changed_slot_ids,
            },
        }
    )


@never_cache
@api_view(["POST"])
def assignment_api(request: Any, pk: int) -> Response:
    data = validated(AssignmentSerializer, request)
    draft = get_object_or_404(ScheduleDraft, event_id=pk)
    get_object_or_404(draft.slots, pk=data["slot_id"])
    get_object_or_404(SlotRequest, pk=data["request_id"], event_id=pk)
    # The service completes provider checks before taking any database locks.
    saved = assign_request_to_draft(
        data["request_id"],
        data["slot_id"],
        actor=request.user,
        expected_request_version=data["request_version"],
        expected_draft_version=data["version"],
    )
    return Response(draft_payload(saved))


@never_cache
@api_view(["POST"])
@transaction.atomic
def decline_api(request: Any, pk: int, request_id: int) -> Response:
    get_object_or_404(SlotRequest, pk=request_id, event_id=pk)
    data = validated(DeclineSerializer, request)
    decline_slot_request(
        request_id,
        actor=request.user,
        expected_version=data["version"],
        organizer_notes=data["organizer_notes"],
    )
    draft = get_object_or_404(ScheduleDraft, event_id=pk)
    # Return current requests even when a prior publication/cancellation made it stale.
    try:
        get_schedule_draft(pk, actor=request.user)
    except StaleScheduleError:
        return Response(draft_payload(draft, stale=True))
    return Response(draft_payload(draft))


@never_cache
@api_view(["POST"])
@transaction.atomic
def visibility_api(request: Any, pk: int) -> Response:
    get_object_or_404(Event, pk=pk)
    data = validated(VisibilitySerializer, request)
    event = _lock_event(pk, data["schedule_version"])
    event.publication_status = data["publication_status"]
    event.signup_before_publication = data["signup_before_publication"]
    event.schedule_version += 1
    event.full_clean()
    event.save(
        update_fields=(
            "publication_status",
            "signup_before_publication",
            "schedule_version",
        )
    )
    draft = get_object_or_404(ScheduleDraft, event=event)
    return Response(draft_payload(draft, stale=True))


@never_cache
@api_view(["POST"])
def import_preview_api(request: Any, pk: int) -> Response:
    draft = get_schedule_draft(pk, actor=request.user)
    data = validated(PreviewSerializer, request)
    return Response(preview_lineup(data["source"], draft))


@never_cache
@api_view(["POST"])
def twitch_lookup_api(request: Any, pk: int) -> Response:
    get_object_or_404(Event, pk=pk)
    data = validated(LookupSerializer, request)
    from .providers import ProviderError

    try:
        profile = lookup_profile(data["login"])
    except ProviderError as error:
        return Response({"errors": [str(error)]}, status=503)
    token = signing.dumps(
        {"actor_id": request.user.pk, "event_id": pk, "profile": asdict(profile)},
        salt=SIGNING_SALT,
    )
    existing = Streamer.objects.filter(twitch_id=profile.id).first()
    if (
        existing is None
        and Streamer.objects.filter(twitch_username__iexact=profile.login).exists()
    ):
        return Response(
            {
                "errors": [
                    "This channel name belongs to an existing local record. "
                    "Select and review that record instead."
                ]
            },
            status=409,
        )
    return Response(
        {
            "profile": asdict(profile),
            "streamer_id": existing.pk if existing else None,
            "lookup_token": "" if existing else token,
        }
    )


@never_cache
@api_view(["POST"])
def import_apply_api(request: Any, pk: int) -> Response:
    draft = get_object_or_404(ScheduleDraft, event_id=pk)
    data = validated(ImportSerializer, request)
    saved = apply_lineup(
        draft.pk,
        actor=request.user,
        expected_version=data["version"],
        event_changes=data["event"],
        rows=data["rows"],
        replace=data["replace"],
        reviewed=data["reviewed"],
    )
    return Response(draft_payload(saved))


@never_cache
@api_view(["POST"])
def twitch_refresh_api(request: Any, pk: int) -> Response:
    event = get_object_or_404(Event, pk=pk)
    ids = set(
        event.raidslot_set.exclude(streamer=None).values_list("streamer_id", flat=True)
    )
    draft = ScheduleDraft.objects.filter(event=event).first()
    if draft:
        ids.update(
            draft.slots.exclude(streamer=None).values_list("streamer_id", flat=True)
        )
    if len(ids) > 100:
        raise ValidationError(
            "Use the refresh_twitch command for more than 100 channels."
        )
    updated, failed = refresh_streamers(sorted(ids))
    return Response({"updated": updated, "failed": failed})
