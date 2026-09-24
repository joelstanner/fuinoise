import datetime
from zoneinfo import ZoneInfo

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from fuinoise_live.admin import RaidSlotInlineForm
from fuinoise_live.models import Community, Event, RaidSlot, Streamer

from .helpers import create_event


class EventAdminTests(TestCase):
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
        self.assertContains(response, "Lineup slots")
        self.assertNotIn(RaidSlot, admin.site._registry)

    def test_slot_local_time_does_not_change_active_timezone(self):
        event = create_event(
            date=datetime.date(2026, 9, 23), event_time_zone="US/Pacific"
        )
        streamer = Streamer.objects.create(
            display_name="First",
            twitch_username="first",
        )
        RaidSlot.objects.create(
            event=event,
            streamer=streamer,
            start=datetime.datetime(2026, 9, 23, 20, tzinfo=datetime.timezone.utc),
        )
        user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-password"
        )
        self.client.force_login(user)
        with timezone.override(ZoneInfo("US/Eastern")):
            response = self.client.get(
                reverse("admin:fuinoise_live_event_change", args=[event.pk])
            )
            self.assertEqual(response.status_code, 200)
            slot_form = response.context["inline_admin_formsets"][0].formset.forms[0]
            self.assertEqual(
                slot_form["start_date"].value(), datetime.date(2026, 9, 23)
            )
            self.assertEqual(slot_form["start_time"].value(), "01:00 PM")
            self.assertNotContains(response, "Enter times in the UTC timezone")
            self.assertContains(response, "raid_slot_note")
            self.assertContains(response, "replay_url")
            self.assertEqual(str(timezone.get_current_timezone()), "US/Eastern")

    def test_local_start_rejects_daylight_saving_gap_and_overlap(self):
        event = create_event(
            date=datetime.date(2026, 3, 8),
            event_time_zone="America/Los_Angeles",
        )
        for date, time in (("2026-03-08", "2:30a"), ("2026-11-01", "1:30a")):
            form = RaidSlotInlineForm(
                event=event,
                data={"event": event.pk, "start_date": date, "start_time": time},
            )
            self.assertFalse(form.is_valid())
            self.assertIn("start_time", form.errors)

    def test_admin_orders_slots_by_start_time_without_position_input(self):
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
            RaidSlot.objects.create(event=event, streamer=streamer, start=start)
            for i, start in enumerate(starts, 1)
        ]
        self.client.force_login(user)
        change_page = self.client.get(
            reverse("admin:fuinoise_live_event_change", args=[event.pk])
        )
        self.assertNotContains(change_page, 'name="raidslot_set-0-position"')
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
                        "streamer": str(streamer.pk),
                        "start_date": starts[1 - index].strftime("%Y-%m-%d"),
                        "start_time": starts[1 - index].strftime("%H:%M:%S"),
                        "raid_slot_note": "",
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
            self.assertEqual(slot.start, starts[1 - index])
        self.assertEqual(list(event.raidslot_set.all()), [slots[1], slots[0]])

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
                        "streamer": "" if index == 0 else str(streamer.pk),
                        "start_date": slot.start.strftime("%Y-%m-%d"),
                        "start_time": slot.start.strftime("%H:%M:%S"),
                        "raid_slot_note": "",
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

    def test_admin_adds_slot_without_position(self):
        user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-password"
        )
        event = create_event(date=datetime.date(2026, 9, 23))
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
                "raidslot_set-TOTAL_FORMS": "1",
                "raidslot_set-INITIAL_FORMS": "0",
                "raidslot_set-MIN_NUM_FORMS": "0",
                "raidslot_set-MAX_NUM_FORMS": "1000",
                "raidslot_set-0-streamer": "",
                "raidslot_set-0-start_date": "2026-09-23",
                "raidslot_set-0-start_time": "20:00:00",
                "raidslot_set-0-raid_slot_note": "",
                "raidslot_set-0-replay_url": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            event.raidslot_set.get().start,
            datetime.datetime(2026, 9, 23, 20, tzinfo=datetime.timezone.utc),
        )

    def test_admin_can_add_event_with_unused_prefilled_slot_date(self):
        user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-password"
        )
        community, _ = Community.objects.get_or_create(
            slug="fuinoise", defaults={"name": "Fuinoise"}
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("admin:fuinoise_live_event_add"),
            {
                "date": "2026-09-23",
                "name": "Empty lineup",
                "description": "",
                "event_time_zone": "",
                "publication_status": "draft",
                "community": str(community.pk),
                "organizer_notes": "",
                "raidslot_set-TOTAL_FORMS": "1",
                "raidslot_set-INITIAL_FORMS": "0",
                "raidslot_set-MIN_NUM_FORMS": "0",
                "raidslot_set-MAX_NUM_FORMS": "1000",
                "raidslot_set-0-streamer": "",
                "raidslot_set-0-start_date": "2026-09-23",
                "raidslot_set-0-start_time": "",
                "raidslot_set-0-raid_slot_note": "",
                "raidslot_set-0-replay_url": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Event.objects.get(name="Empty lineup").raidslot_set.exists())

    def test_admin_rejects_duplicate_slot_start(self):
        user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-password"
        )
        event = create_event(date=datetime.date(2026, 9, 23))
        starts = [
            datetime.datetime(2026, 9, 23, hour, tzinfo=datetime.timezone.utc)
            for hour in (20, 21)
        ]
        slots = [
            RaidSlot.objects.create(event=event, start=start)
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
                        "streamer": "",
                        "start_date": "2026-09-23",
                        "start_time": "20:00:00",
                        "raid_slot_note": "",
                        "replay_url": "",
                    }.items()
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        formset = response.context["inline_admin_formsets"][0].formset
        self.assertIn(
            "Each lineup slot needs a different start time",
            str(formset.non_form_errors()),
            (formset.errors, formset.non_form_errors()),
        )
        self.assertEqual(
            list(event.raidslot_set.order_by("start").values_list("start", flat=True)),
            starts,
        )


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
                "default_time_zone": "US/Pacific",
                "description": "A community of live musicians",
                "website": "https://example.com/community",
                "logo_url": "https://example.com/logo.png",
            },
        )
        self.assertEqual(response.status_code, 302)
        community = Community.objects.get(slug="music-collective")
        self.assertEqual(community.description, "A community of live musicians")
        self.assertEqual(community.default_time_zone, "US/Pacific")

        add_page = self.client.get(
            reverse("admin:fuinoise_live_event_add"),
            {"community": community.pk},
        )
        self.assertEqual(add_page.status_code, 200)
        add_form = add_page.context["adminform"].form
        self.assertEqual(add_form["event_time_zone"].value(), "")
        self.assertIn(
            f"&quot;{community.pk}&quot;: &quot;US/Pacific&quot;",
            str(add_form["community"]),
        )
        self.assertContains(add_page, "fuinoise_live/event_admin.js")

        response = self.client.post(
            reverse("admin:fuinoise_live_event_add"),
            {
                "date": "2026-09-23",
                "name": "Community Raid",
                "community": str(community.pk),
                "description": "",
                "organizer_notes": "",
                "event_time_zone": "",
                "publication_status": "draft",
                "raidslot_set-TOTAL_FORMS": "1",
                "raidslot_set-INITIAL_FORMS": "0",
                "raidslot_set-MIN_NUM_FORMS": "0",
                "raidslot_set-MAX_NUM_FORMS": "1000",
                "raidslot_set-0-streamer": "",
                "raidslot_set-0-start_date": "",
                "raidslot_set-0-start_time": "11a",
                "raidslot_set-0-raid_slot_note": "",
                "raidslot_set-0-replay_url": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(name="Community Raid")
        self.assertEqual(event.community, community)
        self.assertEqual(event.event_time_zone, "US/Pacific")
        self.assertEqual(
            event.raidslot_set.get().start,
            datetime.datetime(2026, 9, 23, 18, tzinfo=datetime.timezone.utc),
        )

        response = self.client.post(
            reverse("admin:fuinoise_live_community_change", args=[community.pk]),
            {
                "name": "Music Collective",
                "slug": "music-collective",
                "default_time_zone": "US/Pacific",
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
