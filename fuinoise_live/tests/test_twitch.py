import io
from dataclasses import asdict
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from fuinoise_live import twitch
from fuinoise_live.models import RaidSlot, Streamer
from fuinoise_live.providers import ProviderError

from .helpers import create_event

PROFILE = twitch.TwitchProfile(
    "123",
    "musician",
    "Twitch Musician",
    "Music from Twitch",
    "https://static-cdn.jtvnw.net/profile.png",
)


@override_settings(
    TWITCH_CLIENT_ID="client",
    TWITCH_CLIENT_SECRET="secret",
    TWITCH_LIVE_MAX_AGE_SECONDS=180,
)
class TwitchInformationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.streamer = Streamer.objects.create(
            display_name="Chosen musician name",
            twitch_username="musician",
            twitch_id="123",
            organizer_notes="Private local notes",
        )
        self.event = create_event(
            date=timezone.now().date(), publication_status="published"
        )
        self.slot = RaidSlot.objects.create(
            event=self.event,
            streamer=self.streamer,
            start=timezone.now(),
            raid_slot_note="Schedule note",
        )

    def token_responses(self):
        return [
            {"access_token": "app-token", "token_type": "bearer", "expires_in": 7200},
            {"client_id": "client", "expires_in": 7200},
        ]

    @patch("fuinoise_live.twitch.request_json")
    def test_app_token_validation_cache_and_public_profile_fields(self, request):
        request.side_effect = self.token_responses() + [
            {"data": [{**asdict(PROFILE), "email": "never-return@example.com"}]},
            {"data": [asdict(PROFILE)]},
        ]
        result = twitch.get_profiles(ids=["123"])
        self.assertEqual(result, [PROFILE])
        self.assertEqual(twitch.get_profiles(logins=["musician"]), [PROFILE])
        self.assertEqual(request.call_count, 4)
        self.assertEqual(
            request.call_args_list[0].kwargs["form"]["grant_type"], "client_credentials"
        )
        self.assertIn("/validate", request.call_args_list[1].args[0])
        self.assertNotIn("email", asdict(result[0]))
        self.assertNotIn("secret", request.call_args_list[2].args[0])

    @patch("fuinoise_live.twitch.request_json")
    def test_unauthorized_helix_discards_token_and_next_attempt_revalidates(
        self, request
    ):
        request.side_effect = (
            self.token_responses()
            + [ProviderError(status=401)]
            + self.token_responses()
            + [{"data": [asdict(PROFILE)]}]
        )
        with self.assertRaises(ProviderError):
            twitch.get_profiles(ids=["123"])
        self.assertEqual(twitch.get_profiles(ids=["123"]), [PROFILE])
        self.assertEqual(request.call_count, 6)

    @patch("fuinoise_live.twitch.request_json")
    def test_invalid_tokens_are_not_cached(self, request):
        for token, verified in [
            ({"access_token": "x", "token_type": "bearer", "expires_in": True}, None),
            (
                {"access_token": "x", "token_type": "bearer", "expires_in": 7200},
                {"client_id": "wrong", "expires_in": 7200},
            ),
            (
                {"access_token": "x", "token_type": "bearer", "expires_in": 7200},
                {"client_id": "client", "expires_in": 7200, "user_id": "123"},
            ),
        ]:
            request.side_effect = [token] + ([verified] if verified else [])
            with self.assertRaises(ProviderError):
                twitch.get_profiles(ids=["123"])
            self.assertIsNone(cache.get(twitch._config()[2]))

    @patch("fuinoise_live.twitch._helix")
    def test_malformed_unrelated_and_duplicate_profiles_are_rejected(self, helix):
        for data in [
            None,
            [1],
            [{**asdict(PROFILE), "id": "999"}],
            [asdict(PROFILE), asdict(PROFILE)],
            [{**asdict(PROFILE), "profile_image_url": "javascript:alert(1)"}],
            [{**asdict(PROFILE), "description": 5}],
        ]:
            helix.return_value = {"data": data}
            with self.subTest(data=data), self.assertRaises(ProviderError):
                twitch.get_profiles(ids=["123"])
        with self.assertRaises(ProviderError):
            twitch.get_profiles(logins=["https://bad.example"])
        helix.return_value = {"data": []}
        with self.assertRaises(ProviderError):
            twitch.lookup_profile("missing")

    @patch("fuinoise_live.twitch._helix")
    def test_live_empty_response_and_incomplete_or_malformed_streams(self, helix):
        started = (timezone.now() - timedelta(hours=1)).isoformat()
        valid = {
            "user_id": "123",
            "type": "live",
            "started_at": started,
            "title": "Music now",
            "game_name": "Music",
        }
        helix.return_value = {"data": [valid], "pagination": {}}
        self.assertEqual(
            twitch.get_live_streams(["123"])["123"]["stream_title"], "Music now"
        )
        helix.return_value = {"data": [], "pagination": {}}
        self.assertEqual(twitch.get_live_streams(["123"]), {})
        for payload in [
            {"data": [], "pagination": {"cursor": "more"}},
            {"data": [{**valid, "user_id": "999"}]},
            {"data": [{**valid, "started_at": "2026-01-01T12:00:00"}]},
            {"data": [{**valid, "type": "offline"}]},
            {"data": [valid, valid]},
        ]:
            helix.return_value = payload
            with self.subTest(payload=payload), self.assertRaises(ProviderError):
                twitch.get_live_streams(["123"])

    @patch("fuinoise_live.twitch.get_live_streams", return_value={})
    @patch("fuinoise_live.twitch.get_profiles", return_value=[PROFILE])
    def test_refresh_preserves_schedules_names_ownership_and_offline_requires_success(
        self, profiles, streams
    ):
        self.assertEqual(twitch.refresh_streamers([self.streamer.pk]), (1, 0))
        self.streamer.refresh_from_db()
        self.slot.refresh_from_db()
        self.assertEqual(self.streamer.display_name, "Chosen musician name")
        self.assertEqual(self.streamer.organizer_notes, "Private local notes")
        self.assertEqual(self.streamer.twitch_id, "123")
        self.assertEqual(self.slot.streamer_id, self.streamer.pk)
        self.assertEqual(self.slot.raid_slot_note, "Schedule note")
        self.assertEqual(
            twitch.public_information(self.streamer, timezone.now())["status"],
            "offline",
        )

    @patch("fuinoise_live.twitch.get_live_streams", side_effect=ProviderError())
    @patch("fuinoise_live.twitch.get_profiles", return_value=[PROFILE])
    def test_outage_preserves_old_profile_and_marks_live_status_unknown(
        self, profiles, streams
    ):
        snapshot = twitch.store_profile(self.streamer, PROFILE)
        snapshot.is_live = False
        snapshot.status_checked_at = timezone.now()
        snapshot.save()
        self.assertEqual(twitch.refresh_streamers([self.streamer.pk]), (0, 1))
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.description, PROFILE.description)
        self.assertIsNone(snapshot.status_checked_at)
        self.streamer.refresh_from_db()
        self.assertEqual(
            twitch.public_information(self.streamer, timezone.now())["status"],
            "unknown",
        )

    def test_expired_future_or_identity_mismatched_snapshots_are_not_current_status(
        self,
    ):
        snapshot = twitch.store_profile(self.streamer, PROFILE)
        snapshot.is_live = True
        for checked in [
            timezone.now() - timedelta(seconds=181),
            timezone.now() + timedelta(seconds=1),
        ]:
            snapshot.status_checked_at = checked
            snapshot.save()
            self.streamer.refresh_from_db()
            self.assertEqual(
                twitch.public_information(self.streamer, timezone.now())["status"],
                "unknown",
            )
        snapshot.user_id = "999"
        snapshot.save()
        self.streamer.refresh_from_db()
        info = twitch.public_information(self.streamer, timezone.now())
        self.assertNotIn("description", info)
        self.assertEqual(info["url"], self.streamer.twitch_url)

    @patch("fuinoise_live.twitch.get_live_streams", return_value={})
    @patch("fuinoise_live.twitch.get_profiles", return_value=[])
    def test_missing_channels_are_unknown_and_legacy_refresh_does_not_claim_ownership(
        self, profiles, streams
    ):
        self.assertEqual(twitch.refresh_streamers([self.streamer.pk]), (0, 1))
        self.assertFalse(streams.called)
        legacy = Streamer.objects.create(
            display_name="Legacy", twitch_username="musician_old"
        )
        profile = twitch.TwitchProfile(
            "456", "musician_old", "Legacy Twitch", "Description", ""
        )
        profiles.return_value = [profile]
        self.assertEqual(twitch.refresh_streamers([legacy.pk]), (1, 0))
        legacy.refresh_from_db()
        self.assertIsNone(legacy.twitch_id)
        self.assertEqual(
            twitch.public_information(legacy, timezone.now())["description"],
            "Description",
        )

    @patch("fuinoise_live.twitch.request_json")
    def test_public_pages_use_snapshot_without_network_and_escape_provider_text(
        self, request
    ):
        snapshot = twitch.store_profile(
            self.streamer,
            twitch.TwitchProfile(
                "123",
                "musician",
                "Twitch Musician",
                "<script>bad()</script>",
                PROFILE.profile_image_url,
            ),
        )
        snapshot.is_live = True
        snapshot.status_checked_at = timezone.now()
        snapshot.stream_title = "Live music"
        snapshot.save()
        response = self.client.get(reverse("event_detail", args=[self.event.pk]))
        self.assertContains(response, "Live on Twitch")
        self.assertContains(response, "&lt;script&gt;bad()&lt;/script&gt;")
        self.assertContains(response, "Live music")
        self.assertContains(response, "data-visitor-time=")
        self.assertNotContains(response, "Private local notes")
        self.assertFalse(request.called)
        snapshot.status_checked_at = timezone.now() - timedelta(seconds=181)
        snapshot.save()
        response = self.client.get(reverse("event_detail", args=[self.event.pk]))
        self.assertContains(response, "Live status unavailable")
        self.assertNotContains(response, "Streaming: Live music")

    @patch(
        "fuinoise_live.management.commands.refresh_twitch.refresh_streamers",
        return_value=(1, 0),
    )
    def test_operator_refresh_command_scope_and_failures(self, refresh):
        out = io.StringIO()
        call_command("refresh_twitch", event=self.event.pk, stdout=out)
        refresh.assert_called_once_with([self.streamer.pk])
        self.assertIn("Updated 1", out.getvalue())
        refresh.return_value = (0, 1)
        with self.assertRaises(CommandError):
            call_command("refresh_twitch", event=self.event.pk, stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command("refresh_twitch", limit=0, stdout=io.StringIO())

    @override_settings(TWITCH_CLIENT_ID="", TWITCH_CLIENT_SECRET="")
    def test_missing_credentials_keeps_local_schedule_readable(self):
        self.assertEqual(twitch.refresh_streamers([self.streamer.pk]), (0, 1))
        response = self.client.get(reverse("event_detail", args=[self.event.pk]))
        self.assertContains(response, "Chosen musician name")
        self.assertContains(response, "Live status unavailable")
