"""Pasted lineup review and atomic application to an organizer's private draft."""

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q

from .models import ScheduleDraft, Streamer
from .providers import ProviderError
from .scheduling import (
    DraftSlotInput,
    _assert_current,
    _lock_event,
    _require_organizer,
    event_local_start,
    save_schedule_draft,
)
from .twitch import parse_profile, store_profile

TIME_LABEL = re.compile(
    r"(?<![\w])(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm|a|p)\s*:|"
    r"(?<![\w])(?P<hour24>\d{1,2}):(?P<minute24>\d{2})(?:\s*:)?(?=\s|$)",
    re.IGNORECASE,
)
DATE_LABEL = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4})\b")
SIGNING_SALT = "fuinoise.lineup-profile"


def date_candidates(raw: str) -> list[str]:
    if "-" in raw:
        try:
            return [date.fromisoformat(raw).isoformat()]
        except ValueError:
            return []
    first, second, year = (int(item) for item in re.split(r"[./]", raw))
    found = set()
    for month, day in [(first, second), (second, first)]:
        try:
            found.add(date(year, month, day).isoformat())
        except ValueError:
            pass
    return sorted(found)


def channel_login(raw: str) -> str:
    value = raw.strip().strip("*` ")
    match = re.fullmatch(
        r"(?:https://(?:www\.)?twitch\.tv/|@)?([a-z0-9_]{1,25})/?", value, re.IGNORECASE
    )
    return match[1].lower() if match else ""


def match_streamer(raw: str) -> dict[str, Any]:
    text = raw.strip().strip("*` ")
    if not text:
        return {"streamer_id": None, "matched_name": "Open slot", "needs_match": False}
    login = channel_login(text)
    matches = list(
        Streamer.objects.filter(
            Q(twitch_username__iexact=login) | Q(display_name__iexact=text)
        )[:2]
    )
    if len(matches) == 1:
        return {
            "streamer_id": matches[0].pk,
            "matched_name": matches[0].display_name,
            "needs_match": False,
        }
    return {
        "streamer_id": None,
        "matched_name": "",
        "needs_match": True,
        "login": login,
    }


def preview_lineup(source: str, draft: ScheduleDraft) -> dict[str, Any]:
    if not source.strip() or len(source) > 12000:
        raise ValidationError("Paste a lineup of at most 12000 characters.")
    labels = list(TIME_LABEL.finditer(source))
    if not labels or len(labels) > 100:
        raise ValidationError("Use up to 100 time labels, such as 10a: or 22:30:.")
    prefix = source[: labels[0].start()]
    date_match = DATE_LABEL.search(prefix)
    candidates = (
        date_candidates(date_match[1]) if date_match else [draft.date.isoformat()]
    )
    warnings = []
    if len(candidates) != 1:
        warnings.append(
            "Choose the event date: the pasted date is ambiguous or invalid."
        )
    if date_match:
        prefix = prefix[: date_match.start()] + prefix[date_match.end() :]
    prefix = prefix.strip().strip("*` ")
    name = draft.name
    rows = []
    if prefix:
        title = re.fullmatch(r"(?:title|event)\s*:\s*(.+)", prefix, re.IGNORECASE)
        if title:
            name = title[1].strip()
        else:
            # Preserve untimed material instead of inventing a pre-party start.
            rows.append(
                {
                    "source_name": prefix,
                    "time": "",
                    "day_offset": 0,
                    "issue": "No time label: enter its date/time or exclude this row.",
                    **match_streamer(prefix.split(":", 1)[-1]),
                }
            )
    previous = -1
    offset = 0
    for index, label in enumerate(labels):
        issue = ""
        if label["hour24"] is not None:
            hour, minute = int(label["hour24"]), int(label["minute24"])
            valid = hour <= 23 and minute <= 59
        else:
            hour, minute = int(label["hour"]), int(label["minute"] or 0)
            valid = 1 <= hour <= 12 and minute <= 59
            hour = hour % 12 + (12 if label["ampm"].lower().startswith("p") else 0)
        clock = hour * 60 + minute
        if valid and clock < previous:
            offset += 1
            issue = "Time goes backwards: review the proposed next-day date."
        if not valid:
            issue = "Invalid time label: enter a valid time."
        else:
            previous = clock
        next_start = (
            labels[index + 1].start() if index + 1 < len(labels) else len(source)
        )
        text = source[label.end() : next_start].strip().strip("*` ")
        rows.append(
            {
                "source_name": text,
                "time": f"{hour:02d}:{minute:02d}" if valid else "",
                "day_offset": offset,
                "issue": issue,
                **match_streamer(text),
            }
        )
    return {
        "name": name,
        "date": candidates[0] if len(candidates) == 1 else "",
        "date_candidates": candidates,
        "warnings": warnings,
        "rows": rows,
    }


def profile_from_token(token: str, *, actor: Any, event_id: int) -> Any:
    try:
        payload = signing.loads(token, salt=SIGNING_SALT, max_age=600)
        if payload["actor_id"] != actor.pk or payload["event_id"] != event_id:
            raise ValueError()
        return parse_profile(payload["profile"])
    except (
        signing.BadSignature,
        KeyError,
        TypeError,
        ValueError,
        ProviderError,
    ) as error:
        raise ValidationError(
            "The Twitch match expired or changed. Look up the channel again."
        ) from error


def _new_streamer(profile: Any, display_name: str, actor: Any) -> Streamer:
    existing: Streamer | None = Streamer.objects.filter(twitch_id=profile.id).first()
    if existing:
        return existing
    if not actor.has_perm("fuinoise_live.add_streamer"):
        raise PermissionDenied(
            "Permission to add streamers is required for new Twitch matches."
        )
    if Streamer.objects.filter(twitch_username__iexact=profile.login).exists():
        raise ValidationError(
            "This channel name already belongs to a local record. "
            "Review that record before importing."
        )
    streamer = Streamer(
        display_name=display_name or profile.display_name,
        twitch_username=profile.login,
        twitch_display_name=profile.display_name,
        twitch_id=profile.id,
    )
    streamer.full_clean()
    streamer.save()
    store_profile(streamer, profile)
    return streamer


def apply_lineup(
    draft_id: int,
    *,
    actor: Any,
    expected_version: int,
    event_changes: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    replace: bool,
    reviewed: bool,
) -> ScheduleDraft:
    _require_organizer(actor)
    if not reviewed:
        raise ValidationError(
            "Review the dates, times, and channel matches before importing."
        )
    if not rows or len(rows) > 100:
        raise ValidationError("Review between 1 and 100 imported slots.")
    try:
        with transaction.atomic():
            draft = ScheduleDraft.objects.get(pk=draft_id)
            event = _lock_event(draft.event_id, draft.base_schedule_version)
            _assert_current(draft, event)
            existing = list(draft.slots.all())
            by_start = {slot.start: slot for slot in existing} if replace else {}
            inputs = (
                []
                if replace
                else [
                    DraftSlotInput(
                        id=slot.pk,
                        start=slot.start,
                        streamer_id=slot.streamer_id,
                        duration_minutes=slot.duration_minutes,
                        replay_url=slot.replay_url,
                        raid_slot_note=slot.raid_slot_note,
                    )
                    for slot in existing
                ]
            )
            for row in rows:
                local = row["local_start"]
                start = event_local_start(
                    local.date(), local.time(), str(event_changes["event_time_zone"])
                )
                streamer_id = row.get("streamer_id")
                token = row.get("lookup_token", "")
                if token:
                    if streamer_id is not None:
                        raise ValidationError(
                            "Choose an existing streamer or a Twitch match, once."
                        )
                    profile = profile_from_token(
                        str(token), actor=actor, event_id=event.pk
                    )
                    streamer_id = _new_streamer(
                        profile, str(row.get("display_name", "")), actor
                    ).pk
                original = by_start.get(start)
                inputs.append(
                    DraftSlotInput(
                        id=original.pk if original else None,
                        start=start,
                        streamer_id=streamer_id,
                        duration_minutes=row["duration_minutes"],
                        raid_slot_note=(
                            original.raid_slot_note
                            if original and row.get("raid_slot_note") is None
                            else str(row.get("raid_slot_note") or "")
                        ),
                        replay_url=original.replay_url if original else "",
                    )
                )
            saved: ScheduleDraft = save_schedule_draft(
                draft_id,
                actor=actor,
                expected_version=expected_version,
                event_changes=event_changes,
                slots=inputs,
            )
            return saved
    except IntegrityError as error:
        raise ValidationError(
            "A channel record changed during import. Reload and review the matches."
        ) from error
