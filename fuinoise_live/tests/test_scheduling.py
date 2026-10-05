from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from fuinoise_live.models import Community, DraftSlot, Event, RaidSlot, Streamer
from fuinoise_live.scheduling import (
    DraftSlotInput,
    StaleScheduleError,
    cancel_published_assignment,
    event_local_start,
    get_schedule_draft,
    publish_schedule_draft,
    reset_schedule_draft,
    save_schedule_draft,
    validate_intervals,
)
from fuinoise_live.views import prepare_event

from .helpers import create_event

UTC = timezone.utc
START = datetime(2026, 10, 4, 20, tzinfo=UTC)


class SchedulingTests(TestCase):
    def setUp(self):
        self.organizer = get_user_model().objects.create_superuser(
            username="organizer", email="organizer@example.com", password=None
        )
        self.event = create_event(
            date=START.date(), event_time_zone="UTC", publication_status="published"
        )
        self.streamer = Streamer.objects.create(
            display_name="Musician", twitch_username="musician"
        )
        self.slot = RaidSlot.objects.create(
            event=self.event,
            streamer=self.streamer,
            start=START,
            raid_slot_note="Public note",
            replay_url="https://example.com/replay",
        )

    def draft(self):
        return get_schedule_draft(self.event.pk, actor=self.organizer)

    def inputs(self, draft):
        return [
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

    def save(self, draft, slots, **changes):
        return save_schedule_draft(
            draft.pk,
            expected_version=draft.version,
            slots=slots,
            actor=self.organizer,
            event_changes=changes,
        )

    def publish(self, draft):
        return publish_schedule_draft(
            draft.pk, expected_version=draft.version, actor=self.organizer
        )

    def test_new_slots_use_configurable_default_without_changing_existing(self):
        self.event.default_slot_duration_minutes = 45
        self.event.save()
        slot = RaidSlot.objects.create(
            event=self.event, start=START + timedelta(hours=1)
        )
        self.assertEqual(slot.duration_minutes, 45)
        self.assertEqual(slot.end, START + timedelta(hours=1, minutes=45))
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.duration_minutes, 60)
        draft = self.draft()
        draft = self.save(
            draft,
            self.inputs(draft) + [DraftSlotInput(start=START + timedelta(hours=2))],
            default_slot_duration_minutes=30,
        )
        self.assertEqual(draft.slots.last().duration_minutes, 30)
        self.event.refresh_from_db()
        self.assertEqual(self.event.default_slot_duration_minutes, 45)

    def test_draft_metadata_and_slots_stay_private_until_publication(self):
        draft = self.draft()
        other_community = Community.objects.create(
            name="Private community", slug="other"
        )
        draft = self.save(
            draft,
            [
                replace(
                    self.inputs(draft)[0],
                    start=START + timedelta(hours=2),
                    duration_minutes=90,
                    raid_slot_note="Private note",
                    replay_url="https://example.com/new",
                )
            ],
            name="Private event title",
            description="Private description",
            date=date(2026, 10, 5),
            event_time_zone="Asia/Tokyo",
            community_id=other_community.pk,
            signup_before_publication=True,
        )
        reloaded = self.draft()
        self.assertEqual(reloaded.version, 1)
        self.assertEqual(reloaded.slots.get().duration_minutes, 90)
        response = self.client.get(reverse("event_detail", args=[self.event.pk]))
        for secret in (
            "Private event title",
            "Private description",
            "Private community",
            "Private note",
            "https://example.com/new",
            "Asia/Tokyo",
        ):
            self.assertNotContains(response, secret)
        self.assertContains(response, "Public note")
        result = self.publish(draft)
        self.assertEqual(result.changed_slot_ids, (self.slot.pk,))
        self.assertEqual(result.confirmed_slot_ids, ())
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.start, START + timedelta(hours=2))
        response = self.client.get(reverse("event_detail", args=[self.event.pk]))
        self.assertContains(response, "Private event title")
        self.assertContains(response, "Private note")
        self.assertContains(response, "Private community")
        self.assertContains(response, "Planned: 90 minutes")

    def test_complete_timetable_rejects_overlap_and_rolls_back_draft(self):
        draft = self.draft()
        with self.assertRaisesMessage(ValidationError, "overlap"):
            self.save(
                draft,
                self.inputs(draft)
                + [DraftSlotInput(start=START + timedelta(minutes=30))],
                name="Rejected title",
            )
        draft.refresh_from_db()
        self.assertEqual(draft.version, 0)
        self.assertEqual(draft.name, self.event.name)
        self.assertEqual(draft.slots.count(), 1)
        self.save(
            draft,
            self.inputs(draft) + [DraftSlotInput(start=START + timedelta(hours=1))],
        )

    def test_concurrent_editor_cannot_save_or_publish_an_older_draft(self):
        first = self.draft()
        stale = self.draft()
        first = self.save(first, self.inputs(first), name="First editor")
        for operation in (
            lambda: self.save(stale, self.inputs(stale), name="Second editor"),
            lambda: self.publish(stale),
            lambda: reset_schedule_draft(
                stale.pk, expected_version=stale.version, actor=self.organizer
            ),
        ):
            with self.assertRaises(StaleScheduleError):
                operation()
        first.refresh_from_db()
        self.assertEqual((first.name, first.version), ("First editor", 1))
        self.event.refresh_from_db()
        self.assertEqual(self.event.schedule_version, 0)

    def test_cancellation_reopens_public_slot_and_blocks_old_draft(self):
        draft = self.draft()
        draft = self.save(draft, self.inputs(draft), name="Pending edits")
        self.assertTrue(
            cancel_published_assignment(self.slot.pk, streamer_id=self.streamer.pk)
        )
        self.assertFalse(
            cancel_published_assignment(self.slot.pk, streamer_id=self.streamer.pk)
        )
        self.slot.refresh_from_db()
        self.assertIsNone(self.slot.streamer_id)
        self.assertContains(
            self.client.get(reverse("event_detail", args=[self.event.pk])), "Open slot"
        )
        for operation in (
            lambda: self.publish(draft),
            lambda: self.save(draft, self.inputs(draft)),
            self.draft,
        ):
            with self.assertRaises(StaleScheduleError):
                operation()
        draft.refresh_from_db()
        self.assertEqual(draft.name, "Pending edits")
        reset = reset_schedule_draft(
            draft.pk, expected_version=draft.version, actor=self.organizer
        )
        self.assertIsNone(reset.slots.get().streamer_id)
        self.assertEqual(reset.name, self.event.name)
        self.assertEqual(self.publish(reset).confirmed_slot_ids, ())
        self.slot.refresh_from_db()
        self.assertIsNone(self.slot.streamer_id)

    def test_maintenance_change_invalidates_existing_draft(self):
        for change in ("event", "slot"):
            with self.subTest(change=change):
                draft = self.draft()
                if change == "event":
                    Event.objects.filter(pk=self.event.pk).update(
                        description="Maintenance edit"
                    )
                else:
                    RaidSlot.objects.filter(pk=self.slot.pk).update(
                        raid_slot_note="Maintenance note"
                    )
                with self.assertRaises(StaleScheduleError):
                    self.publish(draft)
                reset_schedule_draft(
                    draft.pk, expected_version=draft.version, actor=self.organizer
                )

    def test_assignment_confirmation_occurs_on_publication_once(self):
        Event.objects.filter(pk=self.event.pk).update(publication_status="draft")
        draft = self.draft()
        draft = self.save(draft, self.inputs(draft))
        self.assertEqual(
            self.client.get(reverse("event_detail", args=[self.event.pk])).status_code,
            404,
        )
        result = self.publish(draft)
        self.assertEqual(result.confirmed_slot_ids, (self.slot.pk,))
        draft = self.draft()
        again = self.publish(draft)
        self.assertEqual(again.confirmed_slot_ids, ())
        self.assertEqual(again.changed_slot_ids, ())
        second = Streamer.objects.create(
            display_name="Second", twitch_username="second"
        )
        draft = self.draft()
        draft = self.save(
            draft, [replace(self.inputs(draft)[0], streamer_id=second.pk)]
        )
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.streamer_id, self.streamer.pk)
        self.assertEqual(self.publish(draft).confirmed_slot_ids, (self.slot.pk,))

    def test_slot_moves_preserve_ids_and_removal_is_explicit(self):
        second = RaidSlot.objects.create(
            event=self.event, start=START + timedelta(hours=1)
        )
        draft = self.draft()
        one, two = self.inputs(draft)
        draft = self.save(
            draft, [replace(one, start=two.start), replace(two, start=one.start)]
        )
        self.publish(draft)
        self.slot.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.slot.start, two.start)
        self.assertEqual(second.start, one.start)
        draft = self.draft()
        keep = [slot for slot in self.inputs(draft) if slot.id == one.id]
        draft = self.save(draft, keep)
        self.assertTrue(RaidSlot.objects.filter(pk=second.pk).exists())
        self.publish(draft)
        self.assertFalse(RaidSlot.objects.filter(pk=second.pk).exists())
        self.assertTrue(RaidSlot.objects.filter(pk=self.slot.pk).exists())

    def test_failure_mid_publication_restores_entire_schedule_and_versions(self):
        draft = self.draft()
        draft = self.save(
            draft,
            [
                replace(self.inputs(draft)[0], start=START + timedelta(hours=1)),
                DraftSlotInput(
                    start=START + timedelta(hours=2), streamer_id=self.streamer.pk
                ),
            ],
            name="New name",
        )
        with patch(
            "fuinoise_live.scheduling.RaidSlot.objects.create",
            side_effect=RuntimeError("Failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.publish(draft)
        self.slot.refresh_from_db()
        self.event.refresh_from_db()
        draft.refresh_from_db()
        self.assertEqual(self.slot.start, START)
        self.assertEqual(self.event.name, "Raid Train")
        self.assertEqual(self.event.schedule_version, 0)
        self.assertEqual(draft.version, 1)
        self.assertEqual(self.event.raidslot_set.count(), 1)
        self.assertEqual(len(self.publish(draft).confirmed_slot_ids), 1)

    def test_legacy_unknown_duration_requires_review_without_altering_public(self):
        RaidSlot.objects.filter(pk=self.slot.pk).update(duration_minutes=None)
        self.slot.refresh_from_db()
        self.slot.save()
        self.assertIsNone(self.slot.duration_minutes)
        draft = self.draft()
        with self.assertRaisesMessage(ValidationError, "Review"):
            self.publish(draft)
        draft = self.save(draft, [replace(self.inputs(draft)[0], duration_minutes=45)])
        self.slot.refresh_from_db()
        self.assertIsNone(self.slot.end)
        self.publish(draft)
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.duration_minutes, 45)

    def test_invalid_slot_references_and_metadata_are_rejected(self):
        draft = self.draft()
        spec = self.inputs(draft)[0]
        other = create_event(date=START.date())
        foreign = get_schedule_draft(other.pk, actor=self.organizer)
        foreign_slot = DraftSlot.objects.create(
            draft=foreign, start=START, duration_minutes=60
        )
        for slots, changes in (
            ([spec, spec], {}),
            ([replace(spec, id=foreign_slot.pk)], {}),
            ([replace(spec, streamer_id=99999)], {}),
            ([replace(spec, duration_minutes=0)], {}),
            ([spec], {"publication_status": "published"}),
            ([spec], {"default_slot_duration_minutes": 0}),
        ):
            with self.assertRaises(ValidationError):
                self.save(draft, slots, **changes)
        draft.refresh_from_db()
        self.assertEqual(draft.version, 0)
        foreign_published = RaidSlot.objects.create(event=other, start=START)
        draft.slots.update(source_slot=foreign_published)
        with self.assertRaisesMessage(ValidationError, "same event"):
            self.publish(draft)

    def test_publication_revalidates_complete_draft(self):
        draft = self.draft()
        DraftSlot.objects.create(
            draft=draft, start=START + timedelta(minutes=30), duration_minutes=60
        )
        with self.assertRaisesMessage(ValidationError, "overlap"):
            self.publish(draft)
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.start, START)

    def test_organizer_permissions_and_cancellation_ownership(self):
        ordinary = get_user_model().objects.create_user(username="visitor")
        draft = self.draft()
        for actor in (AnonymousUser(), ordinary):
            for operation in (
                lambda: get_schedule_draft(self.event.pk, actor=actor),
                lambda: save_schedule_draft(
                    draft.pk, expected_version=0, slots=[], actor=actor
                ),
                lambda: publish_schedule_draft(
                    draft.pk, expected_version=0, actor=actor
                ),
                lambda: reset_schedule_draft(draft.pk, expected_version=0, actor=actor),
            ):
                with self.assertRaises(PermissionDenied):
                    operation()
        with self.assertRaises(PermissionDenied):
            cancel_published_assignment(self.slot.pk, streamer_id=self.streamer.pk + 1)
        Event.objects.filter(pk=self.event.pk).update(publication_status="draft")
        with self.assertRaises(ValidationError):
            cancel_published_assignment(self.slot.pk, streamer_id=self.streamer.pk)

    def test_database_rejects_nonpositive_durations(self):
        for operation in (
            lambda: Event.objects.filter(pk=self.event.pk).update(
                default_slot_duration_minutes=0
            ),
            lambda: RaidSlot.objects.filter(pk=self.slot.pk).update(duration_minutes=0),
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                operation()

    def test_signup_policy(self):
        for status, early, expected in (
            ("published", False, True),
            ("draft", False, False),
            ("draft", True, True),
            ("private", True, False),
        ):
            self.event.publication_status = status
            self.event.signup_before_publication = early
            self.assertEqual(self.event.signup_open, expected)


class IntervalTests(TestCase):
    def test_intervals_compare_actual_instants_and_allow_touching(self):
        start = event_local_start(date(2026, 3, 8), time(1, 30), "America/Los_Angeles")
        end = datetime(2026, 3, 8, 10, 30, tzinfo=UTC)
        validate_intervals([(end, 30), (start, 60)])
        with self.assertRaisesMessage(ValidationError, "overlap"):
            validate_intervals([(start, 60), (end - timedelta(minutes=1), 30)])
        slot = RaidSlot(start=start, duration_minutes=60)
        self.assertEqual(slot.end.astimezone(ZoneInfo("America/Los_Angeles")).hour, 3)
        for day, clock in (
            (date(2026, 3, 8), time(2, 30)),
            (date(2026, 11, 1), time(1, 30)),
        ):
            with self.assertRaises(ValidationError):
                event_local_start(day, clock, "America/Los_Angeles")

    def test_invalid_intervals(self):
        for start, duration in (
            (START.replace(tzinfo=None), 60),
            (START, None),
            (START, 0),
            (START, -1),
            (START, True),
            (START, 1.5),
            (START, 10**20),
        ):
            with self.assertRaises(ValidationError):
                validate_intervals([(start, duration)])

    def test_overnight_end_extends_current_day_but_midnight_is_exclusive(self):
        event = create_event(date=date(2026, 10, 4), event_time_zone="UTC")
        slot = RaidSlot.objects.create(
            event=event,
            start=datetime(2026, 10, 4, 23, 30, tzinfo=UTC),
            duration_minutes=60,
        )
        now = datetime(2026, 10, 5, 12, tzinfo=UTC)
        self.assertEqual(
            prepare_event(Event.objects.get(pk=event.pk), now).public_period, "current"
        )
        slot.duration_minutes = 30
        slot.save()
        self.assertEqual(
            prepare_event(Event.objects.get(pk=event.pk), now).public_period, "history"
        )
        RaidSlot.objects.filter(pk=slot.pk).update(duration_minutes=None)
        self.assertEqual(
            prepare_event(Event.objects.get(pk=event.pk), now).public_period, "history"
        )
