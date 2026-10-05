import io
from dataclasses import asdict
from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from fuinoise_live.lineup_import import SIGNING_SALT, apply_lineup, preview_lineup
from fuinoise_live.models import RaidSlot, Streamer, TwitchSnapshot
from fuinoise_live.providers import ProviderError
from fuinoise_live.scheduling import StaleScheduleError, get_schedule_draft
from fuinoise_live.twitch import TwitchProfile

from .helpers import create_event


class LineupImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="organizer")
        call_command(
            "setup_organizers", username=self.user.username, stdout=io.StringIO()
        )
        self.first = Streamer.objects.create(
            display_name="P_chops", twitch_username="p_chops", twitch_id="123"
        )
        self.second = Streamer.objects.create(
            display_name="ActuallySparky", twitch_username="actuallysparky"
        )
        self.day = timezone.now().date() + timedelta(days=3)
        self.event = create_event(
            date=self.day, publication_status="published", event_time_zone="UTC"
        )
        self.start = datetime.fromisoformat(f"{self.day}T10:00:00+00:00")
        self.slot = RaidSlot.objects.create(
            event=self.event,
            start=self.start,
            streamer=self.first,
            replay_url="https://example.com/replay",
            raid_slot_note="Preserved note",
        )
        self.draft = get_schedule_draft(self.event.pk, actor=self.user)
        self.client.force_login(self.user)

    def fields(self, **changes):
        return {
            "name": self.draft.name,
            "date": self.day,
            "description": "",
            "community_id": self.event.community_id,
            "event_time_zone": "UTC",
            "default_slot_duration_minutes": 60,
            "signup_before_publication": False,
            **changes,
        }

    def row(self, time="10:00", streamer_id=None, day=None, **extras):
        return {
            "local_start": datetime.fromisoformat(f"{day or self.day}T{time}:00"),
            "streamer_id": streamer_id,
            "duration_minutes": 60,
            **extras,
        }

    def apply(self, rows, **kwargs):
        return apply_lineup(
            self.draft.pk,
            actor=self.user,
            expected_version=self.draft.version,
            event_changes=self.fields(),
            rows=rows,
            replace=True,
            reviewed=True,
            **kwargs,
        )

    def token(self, profile=None, actor_id=None, event_id=None):
        profile = profile or TwitchProfile(
            "999", "new_channel", "New channel", "Twitch biography", ""
        )
        return signing.dumps(
            {
                "actor_id": actor_id or self.user.pk,
                "event_id": event_id or self.event.pk,
                "profile": asdict(profile),
            },
            salt=SIGNING_SALT,
        )

    def test_unmatched_channels_prefill_only_recognizable_twitch_usernames(self):
        parsed = preview_lineup(
            "10a: Unknown_Login 11a: @another_channel "
            "12p: https://www.twitch.tv/Third_Channel/ 1p: A performer name",
            self.draft,
        )
        self.assertTrue(all(row["needs_match"] for row in parsed["rows"]))
        self.assertEqual(
            [row["login"] for row in parsed["rows"]],
            ["unknown_login", "another_channel", "third_channel", ""],
        )

    def test_example_preserves_untimed_text_empty_slots_and_ambiguous_date(self):
        parsed = preview_lineup(
            "*07.09.2026* Pre-Pary: P_chops 10a: 11a: ActuallySparky 12p: 1p:",
            self.draft,
        )
        self.assertEqual(parsed["date"], "")
        self.assertEqual(parsed["date_candidates"], ["2026-07-09", "2026-09-07"])
        self.assertTrue(parsed["warnings"])
        self.assertEqual(parsed["rows"][0]["streamer_id"], self.first.pk)
        self.assertEqual(parsed["rows"][0]["time"], "")
        self.assertEqual(parsed["rows"][1]["matched_name"], "Open slot")
        self.assertEqual(parsed["rows"][2]["streamer_id"], self.second.pk)
        self.assertEqual(parsed["rows"][3]["time"], "12:00")
        self.assertEqual(self.draft.slots.count(), 1)

    def test_iso_date_title_and_overnight_times(self):
        parsed = preview_lineup(
            "2026-10-10 Title: Night train\n11p: @p_chops 00:30: 1:30a: https://twitch.tv/actuallysparky",
            self.draft,
        )
        self.assertEqual(parsed["date"], "2026-10-10")
        self.assertEqual(parsed["name"], "Night train")
        self.assertEqual(
            [row["time"] for row in parsed["rows"]], ["23:00", "00:30", "01:30"]
        )
        self.assertEqual([row["day_offset"] for row in parsed["rows"]], [0, 1, 1])
        self.assertTrue(parsed["rows"][1]["issue"])
        self.assertEqual(parsed["rows"][2]["streamer_id"], self.second.pk)

    def test_invalid_times_unknown_names_and_input_limits_require_review(self):
        parsed = preview_lineup(
            "23.09.2026 25p: unknown_channel 14:75: 2p: unmatched", self.draft
        )
        self.assertEqual(parsed["date"], "2026-09-23")
        self.assertEqual(parsed["rows"][0]["time"], "")
        self.assertTrue(parsed["rows"][0]["needs_match"])
        self.assertEqual(parsed["rows"][1]["time"], "")
        for source in ["", "name without a time", "x" * 12001, "10a: " * 101]:
            with self.subTest(source=source[:20]), self.assertRaises(ValidationError):
                preview_lineup(source, self.draft)

    def test_replacement_keeps_stable_slot_identity_notes_replay_and_public_privacy(
        self,
    ):
        old = self.draft.slots.get()
        saved = self.apply(
            [self.row(streamer_id=self.first.pk), self.row(time="11:00")]
        )
        self.assertEqual(saved.slots.get(start=self.start).pk, old.pk)
        self.assertEqual(saved.slots.get(start=self.start).source_slot_id, self.slot.pk)
        self.assertEqual(
            saved.slots.get(start=self.start).raid_slot_note, "Preserved note"
        )
        self.assertEqual(
            saved.slots.get(start=self.start).replay_url, "https://example.com/replay"
        )
        self.assertEqual(saved.slots.count(), 2)
        self.assertEqual(self.event.raidslot_set.count(), 1)
        self.assertIsNone(saved.slots.order_by("start").last().streamer_id)

    def test_append_import_and_conflicts_are_atomic(self):
        saved = apply_lineup(
            self.draft.pk,
            actor=self.user,
            expected_version=0,
            event_changes=self.fields(),
            rows=[self.row(time="11:00")],
            replace=False,
            reviewed=True,
        )
        self.assertEqual(saved.slots.count(), 2)
        with self.assertRaises(ValidationError):
            apply_lineup(
                self.draft.pk,
                actor=self.user,
                expected_version=saved.version,
                event_changes=self.fields(name="Not saved"),
                rows=[self.row(time="10:30")],
                replace=False,
                reviewed=True,
            )
        saved.refresh_from_db()
        self.assertNotEqual(saved.name, "Not saved")
        self.assertEqual(saved.slots.count(), 2)

    def test_dst_invalid_local_entries_and_stale_or_unreviewed_import_rejected(self):
        for local in ["2026-03-08T02:30:00", "2026-11-01T01:30:00"]:
            with self.subTest(local=local), self.assertRaises(ValidationError):
                apply_lineup(
                    self.draft.pk,
                    actor=self.user,
                    expected_version=0,
                    event_changes=self.fields(event_time_zone="America/New_York"),
                    rows=[
                        {
                            "local_start": datetime.fromisoformat(local),
                            "duration_minutes": 60,
                            "streamer_id": None,
                        }
                    ],
                    replace=True,
                    reviewed=True,
                )
        with self.assertRaises(ValidationError):
            apply_lineup(
                self.draft.pk,
                actor=self.user,
                expected_version=0,
                event_changes=self.fields(),
                rows=[self.row()],
                replace=True,
                reviewed=False,
            )
        with self.assertRaises(StaleScheduleError):
            apply_lineup(
                self.draft.pk,
                actor=self.user,
                expected_version=99,
                event_changes=self.fields(),
                rows=[self.row()],
                replace=True,
                reviewed=True,
            )
        self.assertEqual(self.draft.slots.count(), 1)

    def test_new_verified_match_is_created_only_on_successful_atomic_import(self):
        token = self.token()
        with self.assertRaises(ValidationError):
            self.apply([self.row(lookup_token=token), self.row(time="10:30")])
        self.assertFalse(Streamer.objects.filter(twitch_id="999").exists())
        saved = self.apply(
            [self.row(lookup_token=token), self.row(time="11:00", lookup_token=token)]
        )
        created = Streamer.objects.get(twitch_id="999")
        self.assertEqual(saved.slots.filter(streamer=created).count(), 2)
        self.assertTrue(TwitchSnapshot.objects.filter(streamer=created).exists())
        self.assertEqual(self.event.raidslot_set.get().streamer_id, self.first.pk)

    def test_forged_cross_user_event_or_expired_twitch_matches_rejected(self):
        for token in ["forged", self.token(actor_id=999), self.token(event_id=999)]:
            with self.subTest(token=token[:15]), self.assertRaises(ValidationError):
                self.apply([self.row(lookup_token=token)])
        token = self.token()
        with (
            patch(
                "django.core.signing.time.time",
                return_value=timezone.now().timestamp() + 601,
            ),
            self.assertRaises(ValidationError),
        ):
            self.apply([self.row(lookup_token=token)])
        self.assertFalse(Streamer.objects.filter(twitch_id="999").exists())

    def test_import_does_not_claim_legacy_or_other_verified_channel(self):
        profile = TwitchProfile("999", "p_chops", "Reused name", "", "")
        with self.assertRaises(ValidationError):
            self.apply([self.row(lookup_token=self.token(profile))])
        self.first.refresh_from_db()
        self.assertEqual(self.first.twitch_id, "123")
        other = get_user_model().objects.create_user(username="scheduler")
        from django.contrib.auth.models import Permission

        other.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="fuinoise_live",
                codename__in=["change_event", "change_raidslot"],
            )
        )
        with self.assertRaises(PermissionDenied):
            apply_lineup(
                self.draft.pk,
                actor=other,
                expected_version=0,
                event_changes=self.fields(),
                rows=[self.row(lookup_token=self.token(actor_id=other.pk))],
                replace=True,
                reviewed=True,
            )

    @patch("fuinoise_live.organizer_api.lookup_profile")
    def test_lookup_api_is_read_only_and_provider_failures_are_clear(self, lookup):
        lookup.return_value = TwitchProfile(
            "999", "new_channel", "New channel", "Info", ""
        )
        response = self.client.post(
            reverse("organizer_twitch_lookup_api", args=[self.event.pk]),
            {"login": "new_channel"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["lookup_token"])
        self.assertFalse(Streamer.objects.filter(twitch_id="999").exists())
        lookup.side_effect = ProviderError()
        self.assertEqual(
            self.client.post(
                reverse("organizer_twitch_lookup_api", args=[self.event.pk]),
                {"login": "new_channel"},
                content_type="application/json",
            ).status_code,
            503,
        )

    def test_import_api_csrf_authorization_validation_and_review(self):
        preview_url = reverse("organizer_import_preview_api", args=[self.event.pk])
        apply_url = reverse("organizer_import_apply_api", args=[self.event.pk])
        preview = self.client.post(
            preview_url,
            {"source": "10a: p_chops 11a:"},
            content_type="application/json",
        )
        self.assertEqual(preview.status_code, 200)
        values = self.fields()
        values["date"] = str(self.day)
        payload = {
            "version": 0,
            "event": values,
            "replace": True,
            "reviewed": False,
            "rows": [
                {
                    "local_start": f"{self.day}T10:00",
                    "duration_minutes": 60,
                    "streamer_id": self.first.pk,
                }
            ],
        }
        self.assertEqual(
            self.client.post(
                apply_url, payload, content_type="application/json"
            ).status_code,
            400,
        )
        payload["reviewed"] = True
        self.assertEqual(
            self.client.post(
                apply_url, payload, content_type="application/json"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                apply_url, payload, content_type="application/json"
            ).status_code,
            409,
        )
        payload["rows"][0]["local_start"] = "not-a-time"
        self.assertEqual(
            self.client.post(
                apply_url, payload, content_type="application/json"
            ).status_code,
            400,
        )
        for name in [
            "organizer_import_preview_api",
            "organizer_import_apply_api",
            "organizer_twitch_lookup_api",
            "organizer_twitch_refresh_api",
        ]:
            url = reverse(name, args=[self.event.pk])
            self.assertEqual(
                Client().post(url, {}, content_type="application/json").status_code, 403
            )
            secure = Client(enforce_csrf_checks=True)
            secure.force_login(self.user)
            self.assertEqual(
                secure.post(url, {}, content_type="application/json").status_code, 403
            )
