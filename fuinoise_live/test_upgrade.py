import datetime
from zoneinfo import ZoneInfo

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .admin import RaidSlotInline
from .models import (
    Community,
    CommunityMembership,
    Event,
    Genre,
    Instrument,
    RaidSlot,
    Streamer,
    WeeklyAvailability,
)


def create_event(**kwargs):
    community, _ = Community.objects.get_or_create(
        slug="fuinoise", defaults={"name": "Fuinoise"}
    )
    return Event.objects.create(community=community, **kwargs)


class StreamerIdentityTests(TestCase):
    def test_login_and_url_follow_twitch_identity(self):
        streamer = Streamer.objects.create(
            display_name="Fuinoise name",
            twitch_display_name="Twitch Name",
            twitch_username="  TwitchName  ",
            twitch_id="9876543210",
        )
        self.assertEqual(streamer.twitch_username, "twitchname")
        self.assertEqual(streamer.twitch_url, "https://www.twitch.tv/twitchname")
        self.assertEqual(streamer.display_name, "Fuinoise name")
        self.assertEqual(streamer.twitch_display_name, "Twitch Name")
        streamer.twitch_username = "NewName"
        streamer.save()
        self.assertEqual(streamer.twitch_url, "https://www.twitch.tv/newname")

    def test_login_is_unique_regardless_of_case(self):
        Streamer.objects.create(display_name="First", twitch_username="first")
        with self.assertRaises(IntegrityError):
            Streamer.objects.create(display_name="Second", twitch_username="FIRST")


class AdminSmokeTests(TestCase):
    def test_event_change_page_renders(self):
        user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-password"
        )
        event = create_event(date=datetime.date(2026, 9, 23))

        self.client.force_login(user)
        response = self.client.get(
            reverse("admin:fuinoise_live_event_change", args=[event.pk])
        )

        self.assertEqual(response.status_code, 200)

    def test_slot_local_time_does_not_change_active_timezone(self):
        event = create_event(
            date=datetime.date(2026, 9, 23), event_time_zone="US/Pacific"
        )
        streamer = Streamer.objects.create(
            display_name="First",
            twitch_username="first",
        )
        slot = RaidSlot.objects.create(
            event=event,
            streamer=streamer,
            position=1,
            start=datetime.datetime(2026, 9, 23, 20, tzinfo=datetime.timezone.utc),
        )
        user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-password"
        )
        self.client.force_login(user)
        with timezone.override(ZoneInfo("US/Eastern")):
            rendered = RaidSlotInline(Event, admin.site).event_time_in_event_timezone(
                slot
            )
            self.assertIn("1:00 PM", rendered)
            response = self.client.get(
                reverse("admin:fuinoise_live_event_change", args=[event.pk])
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "1:00 PM PDT")
            self.assertContains(response, "raid_slot_note")
            self.assertContains(response, "replay_url")
            self.assertEqual(str(timezone.get_current_timezone()), "US/Eastern")

    def test_position_is_unique_per_event(self):
        event = create_event(date=datetime.date(2026, 9, 23))
        streamer = Streamer.objects.create(
            display_name="First",
            twitch_username="first",
        )
        start = datetime.datetime(2026, 9, 23, tzinfo=datetime.timezone.utc)
        RaidSlot.objects.create(event=event, streamer=streamer, position=1, start=start)
        with self.assertRaises(IntegrityError):
            RaidSlot.objects.create(
                event=event, streamer=streamer, position=1, start=start
            )

    def test_admin_can_swap_positions_without_changing_start(self):
        user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-password"
        )
        event = create_event(date=datetime.date(2026, 9, 23))
        streamer = Streamer.objects.create(
            display_name="First",
            twitch_username="first",
        )
        starts = [
            datetime.datetime(2026, 9, 23, hour, tzinfo=datetime.timezone.utc)
            for hour in (20, 21)
        ]
        slots = [
            RaidSlot.objects.create(
                event=event, streamer=streamer, position=i, start=start
            )
            for i, start in enumerate(starts, 1)
        ]
        self.client.force_login(user)
        response = self.client.post(
            reverse("admin:fuinoise_live_event_change", args=[event.pk]),
            {
                "date": "2026-09-23",
                "name": event.name,
                "description": "",
                "event_time_zone": event.event_time_zone,
                "publication_status": "draft",
                "community": str(event.community_id),
                "organizer_notes": "",
                "raidslot_set-TOTAL_FORMS": "2",
                "raidslot_set-INITIAL_FORMS": "2",
                "raidslot_set-MIN_NUM_FORMS": "0",
                "raidslot_set-MAX_NUM_FORMS": "1000",
                **{
                    f"raidslot_set-{index}-{field}": value
                    for index, slot in enumerate(slots)
                    for field, value in {
                        "id": str(slot.pk),
                        "event": str(event.pk),
                        "position": str(2 - index),
                        "streamer": str(streamer.pk),
                        "start_0": slot.start.strftime("%Y-%m-%d"),
                        "start_1": slot.start.strftime("%H:%M:%S"),
                        "raid_slot_note": "",
                        "handoff_at_0": "",
                        "handoff_at_1": "",
                        "replay_url": "",
                    }.items()
                },
            },
        )
        self.assertEqual(
            response.status_code,
            302,
            response.context[0]["errors"] if response.context else None,
        )
        for index, slot in enumerate(slots):
            slot.refresh_from_db()
            self.assertEqual(slot.position, 2 - index)
            self.assertEqual(slot.start, starts[index])

    def test_admin_moves_streamer_to_open_time_without_moving_times(self):
        user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-password"
        )
        event = create_event(date=datetime.date(2026, 9, 23))
        streamer = Streamer.objects.create(
            display_name="First", twitch_username="first"
        )
        starts = [
            datetime.datetime(2026, 9, 23, hour, tzinfo=datetime.timezone.utc)
            for hour in (20, 21)
        ]
        slots = [
            RaidSlot.objects.create(
                event=event,
                streamer=streamer if index == 0 else None,
                position=index + 1,
                start=start,
            )
            for index, start in enumerate(starts)
        ]

        self.client.force_login(user)
        response = self.client.post(
            reverse("admin:fuinoise_live_event_change", args=[event.pk]),
            {
                "date": "2026-09-23",
                "name": event.name,
                "description": "",
                "event_time_zone": event.event_time_zone,
                "publication_status": "draft",
                "community": str(event.community_id),
                "organizer_notes": "",
                "raidslot_set-TOTAL_FORMS": "2",
                "raidslot_set-INITIAL_FORMS": "2",
                "raidslot_set-MIN_NUM_FORMS": "0",
                "raidslot_set-MAX_NUM_FORMS": "1000",
                **{
                    f"raidslot_set-{index}-{field}": value
                    for index, slot in enumerate(slots)
                    for field, value in {
                        "id": str(slot.pk),
                        "event": str(event.pk),
                        "position": str(index + 1),
                        "streamer": "" if index == 0 else str(streamer.pk),
                        "start_0": slot.start.strftime("%Y-%m-%d"),
                        "start_1": slot.start.strftime("%H:%M:%S"),
                        "raid_slot_note": "",
                        "handoff_at_0": "",
                        "handoff_at_1": "",
                        "replay_url": "",
                    }.items()
                },
            },
        )
        self.assertEqual(
            response.status_code,
            302,
            response.context[0]["errors"] if response.context else None,
        )
        for index, slot in enumerate(slots):
            slot.refresh_from_db()
            self.assertEqual(slot.start, starts[index])
        self.assertIsNone(slots[0].streamer)
        self.assertEqual(slots[1].streamer, streamer)


class CommunityAdminWorkflowTests(TestCase):
    def test_branding_and_event_association_can_be_managed_in_admin(self):
        user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-password"
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("admin:fuinoise_live_community_add"),
            {
                "name": "Music Collective",
                "slug": "music-collective",
                "description": "A community of live musicians",
                "website": "https://example.com/community",
                "logo_url": "https://example.com/logo.png",
            },
        )
        self.assertEqual(response.status_code, 302)
        community = Community.objects.get(slug="music-collective")
        self.assertEqual(community.description, "A community of live musicians")

        response = self.client.post(
            reverse("admin:fuinoise_live_event_add"),
            {
                "date": "2026-09-23",
                "name": "Community Raid",
                "community": str(community.pk),
                "description": "",
                "organizer_notes": "",
                "event_time_zone": "US/Pacific",
                "publication_status": "draft",
                "raidslot_set-TOTAL_FORMS": "0",
                "raidslot_set-INITIAL_FORMS": "0",
                "raidslot_set-MIN_NUM_FORMS": "0",
                "raidslot_set-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(name="Community Raid")
        self.assertEqual(event.community, community)

        response = self.client.post(
            reverse("admin:fuinoise_live_community_change", args=[community.pk]),
            {
                "name": "Music Collective",
                "slug": "music-collective",
                "description": "Live music and shared raids",
                "website": "https://example.com/new-home",
                "logo_url": "https://example.com/new-logo.png",
            },
        )
        self.assertEqual(response.status_code, 302)
        community.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(community.description, "Live music and shared raids")
        self.assertEqual(community.website, "https://example.com/new-home")
        self.assertEqual(community.logo_url, "https://example.com/new-logo.png")
        self.assertEqual(event.community, community)


class MusicianRaidDataTests(TestCase):
    def test_removing_streamer_keeps_scheduled_time_open(self):
        event = create_event(date=datetime.date(2026, 9, 23))
        streamer = Streamer.objects.create(
            display_name="First", twitch_username="first"
        )
        slot = RaidSlot.objects.create(
            event=event,
            streamer=streamer,
            position=1,
            start=datetime.datetime(2026, 9, 23, 20, tzinfo=datetime.timezone.utc),
        )

        streamer.delete()
        slot.refresh_from_db()
        self.assertIsNone(slot.streamer)
        self.assertIn("Open slot", str(slot))

    def test_musician_profile_schedule_and_membership(self):
        event = create_event(date=datetime.date(2026, 9, 23))
        streamer = Streamer.objects.create(
            display_name="Musician",
            twitch_username="musician",
            time_zone="US/Pacific",
            raid_availability="limited",
            raid_preferences="Ask before 8pm",
        )
        streamer.instruments.add(Instrument.objects.create(name="Guitar"))
        streamer.genres.add(Genre.objects.create(name="Jazz"))
        WeeklyAvailability.objects.create(
            streamer=streamer,
            day_of_week=2,
            start_time=datetime.time(18),
            end_time=datetime.time(21),
        )
        CommunityMembership.objects.create(
            community=event.community, streamer=streamer, role="Member"
        )
        self.assertEqual(streamer.communities.get(), event.community)
        self.assertEqual(streamer.instruments.get().name, "Guitar")
        self.assertEqual(streamer.genres.get().name, "Jazz")
        self.assertEqual(
            streamer.weekly_availability.get().get_day_of_week_display(), "Wednesday"
        )

    def test_handoff_must_follow_start(self):
        event = create_event(date=datetime.date(2026, 9, 23))
        streamer = Streamer.objects.create(
            display_name="First", twitch_username="first"
        )
        start = datetime.datetime(2026, 9, 23, 20, tzinfo=datetime.timezone.utc)
        slot = RaidSlot(
            event=event, streamer=streamer, position=1, start=start, handoff_at=start
        )
        with self.assertRaises(ValidationError):
            slot.full_clean()


class PositionMigrationTests(TransactionTestCase):
    def test_backfill_uses_start_then_id_within_each_event(self):
        executor = MigrationExecutor(connection)
        before = [("fuinoise_live", "0002_alter_event_event_time_zone")]
        after = [
            (
                "fuinoise_live",
                "0008_alter_raidslot_streamer",
            )
        ]
        executor.migrate(before)
        old_apps = executor.loader.project_state(before).apps
        OldEvent = old_apps.get_model("fuinoise_live", "Event")
        OldStreamer = old_apps.get_model("fuinoise_live", "Streamer")
        OldSlot = old_apps.get_model("fuinoise_live", "RaidSlot")
        event = OldEvent.objects.create(date=datetime.date(2026, 9, 23))
        other = OldEvent.objects.create(date=datetime.date(2026, 9, 24))
        streamer = OldStreamer.objects.create(
            display_name="First",
            twitch_username="First",
            twitch_url="https://twitch.tv/first",
            twitch_id=123456789,
        )
        late = datetime.datetime(2026, 9, 23, 21, tzinfo=datetime.timezone.utc)
        early = datetime.datetime(2026, 9, 23, 20, tzinfo=datetime.timezone.utc)
        first = OldSlot.objects.create(event=event, streamer=streamer, start=late)
        second = OldSlot.objects.create(event=event, streamer=streamer, start=early)
        third = OldSlot.objects.create(event=event, streamer=streamer, start=early)
        fourth = OldSlot.objects.create(event=other, streamer=streamer, start=late)

        executor = MigrationExecutor(connection)
        executor.migrate(after)
        NewSlot = executor.loader.project_state(after).apps.get_model(
            "fuinoise_live", "RaidSlot"
        )
        self.assertEqual(
            list(
                NewSlot.objects.filter(event_id=event.pk).values_list("id", "position")
            ),
            [(second.pk, 1), (third.pk, 2), (first.pk, 3)],
        )
        self.assertEqual(NewSlot.objects.get(pk=fourth.pk).position, 1)
        NewStreamer = executor.loader.project_state(after).apps.get_model(
            "fuinoise_live", "Streamer"
        )
        migrated_streamer = NewStreamer.objects.get(pk=streamer.pk)
        self.assertEqual(migrated_streamer.twitch_id, "123456789")
        self.assertEqual(migrated_streamer.twitch_username, "first")
        NewEvent = executor.loader.project_state(after).apps.get_model(
            "fuinoise_live", "Event"
        )
        self.assertEqual(NewEvent.objects.get(pk=event.pk).community.slug, "fuinoise")
        self.assertEqual(NewEvent.objects.get(pk=other.pk).community.slug, "fuinoise")
        self.assertEqual(NewEvent.objects.get(pk=event.pk).publication_status, "draft")
