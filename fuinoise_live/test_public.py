import datetime
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from .models import Community, Event, RaidSlot, Streamer

UTC = datetime.timezone.utc


def event(name, date, zone="UTC", status="published"):
    community, _ = Community.objects.update_or_create(
        slug="fuinoise",
        defaults={
            "name": "Fuinoise",
            "description": "Music across the world",
            "website": "https://example.com",
            "logo_url": "https://example.com/logo.png",
        },
    )
    return Event.objects.create(
        name=name,
        date=date,
        community=community,
        event_time_zone=zone,
        publication_status=status,
    )


class PublicEventTests(TestCase):
    def test_empty_list_pages_and_unannounced_lineup(self):
        for route in ("current_events", "upcoming_events", "historical_events"):
            response = self.client.get(reverse(route))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "No ")

        published = event("No slots yet", datetime.date(2026, 9, 23))
        response = self.client.get(reverse("event_detail", args=[published.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "lineup has not been announced")

    @patch("fuinoise_live.views.timezone.now")
    def test_only_published_events_appear_anywhere(self, now):
        now.return_value = datetime.datetime(2026, 9, 23, 12, tzinfo=UTC)
        published = event("Published event", datetime.date(2026, 9, 23))
        draft = event("Draft secret", datetime.date(2026, 9, 23), status="draft")
        private = event("Private secret", datetime.date(2026, 9, 23), status="private")

        response = self.client.get(reverse("current_events"))
        self.assertContains(response, published.name)
        self.assertNotContains(response, draft.name)
        self.assertNotContains(response, private.name)
        for hidden in (draft, private):
            self.assertEqual(
                self.client.get(reverse("event_detail", args=[hidden.pk])).status_code,
                404,
            )

    @patch("fuinoise_live.views.timezone.now")
    def test_lists_use_each_events_local_calendar_date(self, now):
        now.return_value = datetime.datetime(2026, 9, 24, 6, 30, tzinfo=UTC)
        current = event("Pacific today", datetime.date(2026, 9, 23), "US/Pacific")
        past = event("Tokyo yesterday", datetime.date(2026, 9, 23), "Asia/Tokyo")
        future = event("UTC tomorrow", datetime.date(2026, 9, 25), "UTC")

        for route, expected in (
            ("current_events", current),
            ("upcoming_events", future),
            ("historical_events", past),
        ):
            response = self.client.get(reverse(route))
            self.assertEqual(
                [item.pk for item in response.context["events"]], [expected.pk]
            )

    @patch("fuinoise_live.views.timezone.now")
    def test_midnight_boundaries_account_for_daylight_saving(self, now):
        published = event("Spring event", datetime.date(2026, 3, 8), "US/Pacific")
        expected = (
            (datetime.datetime(2026, 3, 8, 7, 59, tzinfo=UTC), "upcoming"),
            (datetime.datetime(2026, 3, 8, 8, 0, tzinfo=UTC), "current"),
            (datetime.datetime(2026, 3, 9, 6, 59, tzinfo=UTC), "current"),
            (datetime.datetime(2026, 3, 9, 7, 0, tzinfo=UTC), "history"),
        )
        for instant, period in expected:
            now.return_value = instant
            response = self.client.get(
                reverse(f"{period}_events")
                if period != "history"
                else reverse("historical_events")
            )
            self.assertEqual(
                [item.pk for item in response.context["events"]], [published.pk]
            )

    @patch("fuinoise_live.views.timezone.now")
    def test_overnight_slot_start_extends_current_event(self, now):
        published = event("Overnight", datetime.date(2026, 9, 23), "US/Pacific")
        musician = Streamer.objects.create(
            display_name="Musician", twitch_username="musician"
        )
        RaidSlot.objects.create(
            event=published,
            streamer=musician,
            start=datetime.datetime(2026, 9, 24, 9, tzinfo=UTC),
        )
        now.return_value = datetime.datetime(2026, 9, 25, 6, 59, tzinfo=UTC)
        self.assertContains(self.client.get(reverse("current_events")), published.name)
        now.return_value = datetime.datetime(2026, 9, 25, 7, tzinfo=UTC)
        self.assertContains(
            self.client.get(reverse("historical_events")), published.name
        )

    @patch("fuinoise_live.views.timezone.now")
    def test_detail_renders_branding_local_times_and_start_order(self, now):
        now.return_value = datetime.datetime(2026, 9, 23, 20, tzinfo=UTC)
        published = event("Jazz relay", datetime.date(2026, 9, 23), "US/Pacific")
        published.organizer_notes = "Do not publish this"
        published.save()
        first = Streamer.objects.create(
            display_name="First musician", twitch_username="first"
        )
        second = Streamer.objects.create(
            display_name="Second musician", twitch_username="second"
        )
        RaidSlot.objects.create(
            event=published,
            streamer=second,
            start=datetime.datetime(2026, 9, 23, 20, tzinfo=UTC),
            raid_slot_note="Piano set",
            replay_url="https://example.com/replay",
        )
        RaidSlot.objects.create(
            event=published,
            streamer=first,
            start=datetime.datetime(2026, 9, 23, 21, tzinfo=UTC),
        )
        RaidSlot.objects.create(
            event=published,
            streamer=None,
            start=datetime.datetime(2026, 9, 23, 22, tzinfo=UTC),
        )

        response = self.client.get(reverse("event_detail", args=[published.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Music across the world")
        self.assertContains(response, "https://example.com/logo.png")
        self.assertContains(response, "US/Pacific")
        self.assertContains(response, "2:00 PM PDT")
        self.assertContains(response, "Piano set")
        self.assertContains(response, "Open slot")
        self.assertContains(response, "3:00 PM PDT")
        self.assertContains(response, "https://example.com/replay")
        self.assertNotContains(response, "Do not publish this")
        self.assertLess(
            response.content.index(b"Second musician"),
            response.content.index(b"First musician"),
        )

        listing = self.client.get(reverse("current_events"))
        self.assertContains(listing, "Open slot")
        self.assertContains(listing, "First musician")
        self.assertLess(
            listing.content.index(b"Second musician"),
            listing.content.index(b"First musician"),
        )
