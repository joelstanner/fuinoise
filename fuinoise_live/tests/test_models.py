import datetime

from django.db import IntegrityError
from django.test import TestCase

from fuinoise_live.models import (
    CommunityMembership,
    Genre,
    Instrument,
    RaidSlot,
    Streamer,
    WeeklyAvailability,
)

from .helpers import create_event


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


class RaidSlotModelTests(TestCase):
    def test_start_time_is_unique_per_event(self):
        event = create_event(date=datetime.date(2026, 9, 23))
        streamer = Streamer.objects.create(
            display_name="First",
            twitch_username="first",
        )
        start = datetime.datetime(2026, 9, 23, tzinfo=datetime.timezone.utc)
        RaidSlot.objects.create(event=event, streamer=streamer, start=start)
        with self.assertRaises(IntegrityError):
            RaidSlot.objects.create(event=event, streamer=streamer, start=start)


class MusicianRaidDataTests(TestCase):
    def test_removing_streamer_keeps_scheduled_time_open(self):
        event = create_event(date=datetime.date(2026, 9, 23))
        streamer = Streamer.objects.create(
            display_name="First", twitch_username="first"
        )
        slot = RaidSlot.objects.create(
            event=event,
            streamer=streamer,
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
