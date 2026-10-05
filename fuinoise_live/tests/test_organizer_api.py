from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from fuinoise_live.accounts import account_for_twitch
from fuinoise_live.models import (
    Event,
    RaidSlot,
    ScheduleDraft,
    SlotPreference,
    SlotRequest,
)
from fuinoise_live.providers import TwitchIdentity
from fuinoise_live.scheduling import cancel_published_assignment

from .helpers import create_event


class OrganizerAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="organizer")
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="fuinoise_live",
                codename__in=["change_event", "change_raidslot", "add_event"],
            )
        )
        self.account = account_for_twitch(
            TwitchIdentity("123", "performer", "Performer")
        )
        self.account.participation_status = "approved"
        self.account.discord_override = True
        self.account.save()
        self.start = timezone.now().replace(second=0, microsecond=0) + timedelta(days=3)
        self.event = create_event(
            name="Organizer train",
            date=self.start.date(),
            event_time_zone="UTC",
            publication_status="published",
        )
        self.slots = [
            RaidSlot.objects.create(
                event=self.event, start=self.start + timedelta(hours=i)
            )
            for i in range(2)
        ]
        self.request = SlotRequest.objects.create(
            event=self.event, streamer=self.account.streamer, notes="Private preference"
        )
        SlotPreference.objects.create(
            request=self.request,
            slot=self.slots[0],
            requested_start=self.slots[0].start,
            requested_duration_minutes=60,
        )
        self.client.force_login(self.user)
        self.url = reverse("organizer_draft_api", args=[self.event.pk])
        self.draft = self.client.get(self.url).json()

    def payload(self, draft=None):
        draft = draft or self.draft
        return {
            "version": draft["version"],
            "event": draft["event"].copy(),
            "slots": [
                {
                    key: slot[key]
                    for key in (
                        "id",
                        "start",
                        "streamer_id",
                        "duration_minutes",
                        "replay_url",
                        "raid_slot_note",
                    )
                }
                for slot in draft["slots"]
            ],
        }

    def put(self, payload):
        return self.client.put(self.url, payload, content_type="application/json")

    def post(self, name, data, args=None):
        return self.client.post(
            reverse(name, args=args or [self.event.pk]),
            data,
            content_type="application/json",
        )

    def test_every_route_requires_organizer_permissions(self):
        urls = [
            ("organizer_events_api", [], "get"),
            ("organizer_draft_api", [self.event.pk], "get"),
            ("organizer_draft_api", [self.event.pk], "put"),
            ("organizer_publish_api", [self.event.pk], "post"),
            ("organizer_signup_api", [self.event.pk], "post"),
            ("organizer_reset_api", [self.event.pk], "post"),
            ("organizer_assign_api", [self.event.pk], "post"),
            ("organizer_decline_api", [self.event.pk, self.request.pk], "post"),
            ("organizer_visibility_api", [self.event.pk], "post"),
        ]
        for actor in [None, self.account.user]:
            client = Client()
            if actor:
                client.force_login(actor)
            for name, args, method in urls:
                with self.subTest(actor=actor, route=name, method=method):
                    response = getattr(client, method)(
                        reverse(name, args=args), {}, content_type="application/json"
                    )
                    self.assertEqual(response.status_code, 403)
                    self.assertNotContains(
                        response, "Private preference", status_code=403
                    )
            self.assertEqual(
                client.get(reverse("organizer_workspace")).status_code, 403
            )
        self.assertEqual(self.event.raidslot_set.count(), 2)

    def test_workspace_issues_csrf_token_and_post_requires_it(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.get(reverse("organizer_workspace"))
        self.assertContains(response, "organizer-root")
        self.assertIn("no-store", response["Cache-Control"])
        url = reverse("organizer_publish_api", args=[self.event.pk])
        data = {"version": self.draft["version"]}
        self.assertEqual(
            client.post(url, data, content_type="application/json").status_code, 403
        )
        response = client.post(
            url,
            data,
            content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 200)

    def test_private_event_creation_and_catalog(self):
        catalog = self.client.get(reverse("organizer_events_api")).json()
        self.assertTrue(catalog["can_create"])
        self.assertIn("UTC", catalog["time_zones"])
        fields = self.draft["event"].copy()
        fields["name"] = "Private new event"
        response = self.client.post(
            reverse("organizer_events_api"), fields, content_type="application/json"
        )
        self.assertEqual(response.status_code, 201)
        created = Event.objects.get(pk=response.json()["event_id"])
        self.assertEqual(created.publication_status, "draft")
        self.assertEqual(
            self.client.get(reverse("event_detail", args=[created.pk])).status_code, 404
        )
        self.assertTrue(ScheduleDraft.objects.filter(event=created).exists())

    def test_creation_needs_add_permission_and_valid_community(self):
        self.user.user_permissions.remove(
            Permission.objects.get(
                content_type__app_label="fuinoise_live", codename="add_event"
            )
        )
        response = self.client.post(
            reverse("organizer_events_api"),
            self.draft["event"],
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="fuinoise_live", codename="add_event"
            )
        )
        fields = self.draft["event"].copy()
        fields["community_id"] = 999999
        self.assertEqual(
            self.client.post(
                reverse("organizer_events_api"), fields, content_type="application/json"
            ).status_code,
            400,
        )
        self.assertEqual(Event.objects.count(), 1)

    def test_valid_edit_is_private_and_survives_reload(self):
        payload = self.payload()
        payload["event"]["name"] = "New private name"
        payload["slots"][0]["start"] = (self.start - timedelta(hours=1)).isoformat()
        response = self.put(payload)
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.slots[0].refresh_from_db()
        self.assertEqual(self.event.name, "Organizer train")
        self.assertEqual(self.slots[0].start, self.start)
        loaded = self.client.get(self.url).json()
        self.assertEqual(loaded["event"]["name"], "New private name")
        self.assertEqual(loaded["slots"][0]["start"], payload["slots"][0]["start"])
        self.assertEqual(loaded["version"], 1)

    def test_overlap_rejects_complete_edit_atomically(self):
        payload = self.payload()
        payload["event"]["name"] = "Not saved"
        payload["slots"][0]["duration_minutes"] = 90
        response = self.put(payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("overlap", str(response.json()))
        self.assertEqual(self.client.get(self.url).json(), self.draft)

    def test_stale_version_rejected_without_overwrite(self):
        payload = self.payload()
        payload["event"]["name"] = "First edit"
        self.assertEqual(self.put(payload).status_code, 200)
        payload["event"]["name"] = "Old edit"
        self.assertEqual(self.put(payload).status_code, 409)
        self.assertEqual(
            self.client.get(self.url).json()["event"]["name"], "First edit"
        )

    def test_local_times_overnight_and_dst_validation(self):
        for local, expected in [
            ("2026-03-08T02:30:00", 400),
            ("2026-11-01T01:30:00", 400),
            ("2026-10-10T23:00:00", 200),
        ]:
            payload = self.payload()
            payload["event"]["event_time_zone"] = "America/New_York"
            payload["slots"] = [payload["slots"][0]]
            payload["slots"][0].pop("start")
            payload["slots"][0]["local_start"] = local
            with self.subTest(local=local):
                self.assertEqual(self.put(payload).status_code, expected)
        saved = self.client.get(self.url).json()
        self.assertEqual(saved["slots"][0]["start"], "2026-10-11T03:00:00+00:00")

    def test_malformed_times_and_forged_provenance_rejected(self):
        for change in [
            {"start": "not-a-date"},
            {"start": "2026-10-10T12:00:00"},
            {"local_start": "2026-10-10T12:00:00"},
            {"request_id": self.request.pk},
            {"signup_request_id": self.request.pk},
            {"source_slot_id": self.slots[1].pk},
            {"duration_minutes": 0},
        ]:
            payload = self.payload()
            payload["slots"][0].update(change)
            with self.subTest(change=change):
                self.assertEqual(self.put(payload).status_code, 400)
        payload = self.payload()
        payload["slots"][0].pop("start")
        payload["slots"][0]["local_start"] = "2026-10-10T12:00:00Z"
        self.assertEqual(self.put(payload).status_code, 400)
        self.assertEqual(self.client.get(self.url).json(), self.draft)

    def test_assignment_confirmation_only_on_publication(self):
        assigned = self.post(
            "organizer_assign_api",
            {
                "version": 0,
                "slot_id": self.draft["slots"][0]["id"],
                "request_id": self.request.pk,
                "request_version": 0,
            },
        )
        self.assertEqual(assigned.status_code, 200)
        self.assertFalse(
            self.event.raidslot_set.filter(streamer__isnull=False).exists()
        )
        published = self.post(
            "organizer_publish_api", {"version": assigned.json()["version"]}
        )
        self.assertEqual(published.status_code, 200)
        self.assertEqual(
            published.json()["publication"]["confirmed_slot_ids"], [self.slots[0].pk]
        )
        again = self.post(
            "organizer_publish_api", {"version": published.json()["version"]}
        )
        self.assertEqual(again.json()["publication"]["confirmed_slot_ids"], [])

    def test_assignment_foreign_slot_or_request_is_not_accepted(self):
        other = create_event(date=self.start.date())
        foreign = SlotRequest.objects.create(
            event=other, streamer=self.account.streamer
        )
        data = {
            "version": 0,
            "slot_id": self.draft["slots"][0]["id"],
            "request_id": foreign.pk,
            "request_version": 0,
        }
        self.assertEqual(self.post("organizer_assign_api", data).status_code, 404)
        data["request_id"] = self.request.pk
        data["slot_id"] = 999999
        self.assertEqual(self.post("organizer_assign_api", data).status_code, 404)
        data["slot_id"] = self.draft["slots"][0]["id"]
        data["request_version"] = 2
        self.assertEqual(self.post("organizer_assign_api", data).status_code, 409)

    def test_decline_invalidates_tentative_assignment(self):
        assigned = self.post(
            "organizer_assign_api",
            {
                "version": 0,
                "slot_id": self.draft["slots"][0]["id"],
                "request_id": self.request.pk,
                "request_version": 0,
            },
        ).json()
        declined = self.post(
            "organizer_decline_api",
            {"version": 0, "organizer_notes": "Private review"},
            args=[self.event.pk, self.request.pk],
        )
        self.assertEqual(declined.status_code, 200)
        self.assertEqual(declined.json()["requests"][0]["status"], "declined")
        self.assertEqual(
            self.post(
                "organizer_publish_api", {"version": assigned["version"]}
            ).status_code,
            409,
        )
        self.assertEqual(
            self.post(
                "organizer_decline_api",
                {"version": 0},
                args=[self.event.pk, self.request.pk],
            ).status_code,
            409,
        )
        self.assertNotContains(
            self.client.get(reverse("event_detail", args=[self.event.pk])),
            "Private review",
        )

    def test_cancellation_preserves_but_invalidates_draft_and_explicit_reset(self):
        assigned = self.post(
            "organizer_assign_api",
            {
                "version": 0,
                "slot_id": self.draft["slots"][0]["id"],
                "request_id": self.request.pk,
                "request_version": 0,
            },
        ).json()
        published = self.post(
            "organizer_publish_api", {"version": assigned["version"]}
        ).json()
        cancel_published_assignment(
            self.slots[0].pk, streamer_id=self.account.streamer_id
        )
        stale = self.client.get(self.url).json()
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["slots"][0]["streamer_id"], self.account.streamer_id)
        self.assertEqual(
            self.post(
                "organizer_publish_api", {"version": published["version"]}
            ).status_code,
            409,
        )
        reset = self.post("organizer_reset_api", {"version": stale["version"]})
        self.assertEqual(reset.status_code, 200)
        self.assertFalse(reset.json()["stale"])
        self.assertIsNone(reset.json()["slots"][0]["streamer_id"])
        self.assertEqual(
            self.post("organizer_reset_api", {"version": stale["version"]}).status_code,
            409,
        )

    def test_visibility_and_early_signup_change_immediately(self):
        hidden = self.post(
            "organizer_visibility_api",
            {
                "schedule_version": 0,
                "publication_status": "draft",
                "signup_before_publication": True,
            },
        )
        self.assertEqual(hidden.status_code, 200)
        self.assertTrue(hidden.json()["stale"])
        self.event.refresh_from_db()
        self.assertTrue(self.event.signup_open)
        self.assertEqual(
            self.client.get(reverse("event_detail", args=[self.event.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.post(
                "organizer_visibility_api",
                {
                    "schedule_version": 0,
                    "publication_status": "private",
                    "signup_before_publication": False,
                },
            ).status_code,
            409,
        )
        self.assertEqual(
            self.post(
                "organizer_visibility_api",
                {
                    "schedule_version": 1,
                    "publication_status": "published",
                    "signup_before_publication": False,
                },
            ).status_code,
            400,
        )

    def test_legacy_duration_requires_review_and_deleted_event_returns_404(self):
        RaidSlot.objects.filter(event=self.event).update(duration_minutes=None)
        reset = self.post(
            "organizer_reset_api", {"version": self.draft["version"]}
        ).json()
        self.assertTrue(
            all(slot["duration_minutes"] is None for slot in reset["slots"])
        )
        self.assertEqual(self.put(self.payload(reset)).status_code, 400)
        reviewed = self.payload(reset)
        for slot in reviewed["slots"]:
            slot["duration_minutes"] = 60
        self.assertEqual(self.put(reviewed).status_code, 200)
        self.event.delete()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_new_event_signup_release_keeps_drafts_private_and_confirms_only_on_publish(
        self,
    ):
        fields = self.draft["event"].copy()
        fields["name"] = "Early signup event"
        created = self.client.post(
            reverse("organizer_events_api"), fields, content_type="application/json"
        ).json()
        event_id = created["event_id"]
        url = reverse("organizer_draft_api", args=[event_id])
        payload = self.payload(created)
        payload["slots"] = [
            dict(
                start=self.start.isoformat(),
                duration_minutes=60,
                streamer_id=self.account.streamer_id,
            )
        ]
        saved = self.client.put(url, payload, content_type="application/json").json()
        self.assertFalse(RaidSlot.objects.filter(event_id=event_id).exists())
        signup = self.post(
            "organizer_signup_api", {"version": saved["version"]}, args=[event_id]
        )
        self.assertEqual(signup.status_code, 200)
        released = signup.json()
        self.assertEqual(released["publication"]["confirmed_slot_ids"], [])
        self.assertEqual(released["publication_status"], "draft")
        self.assertTrue(Event.objects.get(pk=event_id).signup_open)
        self.assertEqual(
            Client().get(reverse("event_detail", args=[event_id])).status_code, 404
        )
        streamer = Client()
        streamer.force_login(self.account.user)
        signup_page = streamer.get(reverse("event_signup", args=[event_id]))
        self.assertEqual(signup_page.status_code, 200)
        self.assertEqual(len(signup_page.context["form"].fields["slots"].choices), 1)
        dashboard = streamer.get(reverse("account_home"))
        self.assertEqual(dashboard.context["confirmed_assignments"], [])
        payload = self.payload(released)
        payload["event"]["name"] = "Still private working name"
        saved = self.client.put(url, payload, content_type="application/json").json()
        self.assertEqual(Event.objects.get(pk=event_id).name, "Early signup event")
        published = self.post(
            "organizer_publish_api", {"version": saved["version"]}, args=[event_id]
        ).json()
        self.assertEqual(len(published["publication"]["confirmed_slot_ids"]), 1)
        self.assertEqual(
            len(streamer.get(reverse("account_home")).context["confirmed_assignments"]),
            1,
        )
        self.assertEqual(
            self.post(
                "organizer_signup_api",
                {"version": published["version"]},
                args=[event_id],
            ).status_code,
            400,
        )
