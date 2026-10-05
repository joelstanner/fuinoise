from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.forms.models import modelform_factory
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from fuinoise_live.accounts import account_for_twitch
from fuinoise_live.admin import RaidSlotInlineForm
from fuinoise_live.models import (
    DraftSlot,
    Event,
    RaidSlot,
    SlotPreference,
    SlotRequest,
    StreamerAccount,
)
from fuinoise_live.providers import ProviderError, TwitchIdentity
from fuinoise_live.request_views import signup_events
from fuinoise_live.requests import (
    assign_request_to_draft,
    cancel_assignment,
    decline_slot_request,
    save_slot_request,
    withdraw_slot_request,
)
from fuinoise_live.scheduling import (
    DraftSlotInput,
    StaleScheduleError,
    get_schedule_draft,
    publish_schedule_draft,
    save_schedule_draft,
)

from .helpers import create_event


@override_settings(DISCORD_GUILD_ID="9999")
class RequestTests(TestCase):
    def setUp(self):
        self.membership = patch(
            "fuinoise_live.accounts.discord_membership", return_value=True
        ).start()
        self.addCleanup(patch.stopall)
        self.account = account_for_twitch(
            TwitchIdentity("1234", "musician", "Musician")
        )
        self.user = self.account.user
        self.streamer = self.account.streamer
        StreamerAccount.objects.filter(pk=self.account.pk).update(
            participation_status="approved",
            discord_id="5678",
            discord_member=True,
            discord_guild_id="9999",
            discord_checked_at=timezone.now(),
        )
        self.other = account_for_twitch(TwitchIdentity("3333", "other", "Other"))
        self.organizer = get_user_model().objects.create_superuser(
            username="organizer", password=None
        )
        self.start = timezone.now().replace(second=0, microsecond=0) + timedelta(days=2)
        self.event = create_event(
            name="Future train",
            date=self.start.date(),
            event_time_zone="UTC",
            publication_status="published",
        )
        self.slots = [
            RaidSlot.objects.create(
                event=self.event, start=self.start + timedelta(hours=index)
            )
            for index in range(3)
        ]
        self.client.force_login(self.user)

    def submit(self, ids=None, version=None, **kwargs):
        return save_slot_request(
            self.event.pk,
            actor=self.user,
            slot_ids=ids if ids is not None else [s.pk for s in self.slots[:2]],
            expected_schedule_version=self.event.schedule_version,
            expected_request_version=version,
            **kwargs,
        )

    def assign(self, request, draft, index=0):
        slot = draft.slots.get(source_slot=self.slots[index])
        return assign_request_to_draft(
            request.pk,
            slot.pk,
            actor=self.organizer,
            expected_request_version=request.version,
            expected_draft_version=draft.version,
        )

    def publish(self, draft):
        return publish_schedule_draft(
            draft.pk, actor=self.organizer, expected_version=draft.version
        )

    def test_several_preferences_persist_without_reserving_slots(self):
        request = self.submit(notes="Prefer a later set")
        self.assertEqual(request.preferences.count(), 2)
        self.assertEqual(request.status, "submitted")
        self.assertEqual(
            list(request.preferences.values_list("requested_start", flat=True)),
            [s.start for s in self.slots[:2]],
        )
        self.assertFalse(
            RaidSlot.objects.filter(event=self.event, streamer__isnull=False).exists()
        )
        self.assertFalse(DraftSlot.objects.exists())
        request = self.submit(
            ids=[self.slots[2].pk], version=request.version, notes="Updated preference"
        )
        self.assertEqual(SlotRequest.objects.count(), 1)
        self.assertEqual(request.version, 1)
        self.assertEqual(request.preferences.get().slot_id, self.slots[2].pk)

    def test_two_performances_become_visible_only_on_publication_then_one_cancels(self):
        request = self.submit(notes="Private request notes")
        draft = get_schedule_draft(self.event.pk, actor=self.organizer)
        draft = self.assign(request, draft)
        draft = self.assign(request, draft, 1)
        response = self.client.get(reverse("account_home"))
        self.assertEqual(response.context["confirmed_assignments"], [])
        self.assertNotContains(
            self.client.get(reverse("event_detail", args=[self.event.pk])),
            "Private request notes",
        )
        result = self.publish(draft)
        self.assertEqual(set(result.confirmed_slot_ids), {s.pk for s in self.slots[:2]})
        response = self.client.get(reverse("account_home"))
        self.assertEqual(len(response.context["confirmed_assignments"]), 2)
        self.assertContains(response, "Private request notes")
        self.event.refresh_from_db()
        stale = get_schedule_draft(self.event.pk, actor=self.organizer)
        self.membership.side_effect = ProviderError()
        StreamerAccount.objects.filter(pk=self.account.pk).update(
            participation_status="declined"
        )
        response = self.client.post(
            reverse("cancel_performance", args=[self.slots[0].pk]),
            {"version": self.event.schedule_version},
        )
        self.assertEqual(response.status_code, 302)
        self.slots[0].refresh_from_db()
        self.slots[1].refresh_from_db()
        self.assertIsNone(self.slots[0].streamer_id)
        self.assertIsNone(self.slots[0].signup_request_id)
        self.assertEqual(self.slots[1].streamer_id, self.streamer.pk)
        self.assertContains(
            self.client.get(reverse("event_detail", args=[self.event.pk])), "Open slot"
        )
        with self.assertRaises(StaleScheduleError):
            self.publish(stale)

    def test_eligibility_rechecked_and_outage_blocks_submission(self):
        self.membership.return_value = False
        with self.assertRaises(ValidationError):
            self.submit()
        self.assertFalse(SlotRequest.objects.exists())
        self.membership.side_effect = ProviderError()
        with self.assertRaises(ValidationError):
            self.submit()
        self.assertFalse(SlotRequest.objects.exists())
        StreamerAccount.objects.filter(pk=self.account.pk).update(discord_override=True)
        self.submit()

    def test_invalid_or_unavailable_preferences_do_not_change_a_saved_request(self):
        request = self.submit()
        other_event = create_event(date=self.start.date())
        foreign = RaidSlot.objects.create(event=other_event, start=self.start)
        for choices in (
            [],
            [self.slots[0].pk, self.slots[0].pk],
            [foreign.pk],
            [999999],
        ):
            with self.assertRaises(ValidationError):
                self.submit(ids=choices, version=request.version)
        RaidSlot.objects.filter(pk=self.slots[2].pk).update(
            streamer=self.other.streamer
        )
        with self.assertRaises(ValidationError):
            self.submit(ids=[self.slots[2].pk], version=request.version)
        RaidSlot.objects.filter(pk=self.slots[2].pk).update(
            streamer=None, duration_minutes=None
        )
        with self.assertRaises(ValidationError):
            self.submit(ids=[self.slots[2].pk], version=request.version)
        RaidSlot.objects.filter(pk=self.slots[2].pk).update(
            start=timezone.now() - timedelta(hours=1), duration_minutes=60
        )
        with self.assertRaises(ValidationError):
            self.submit(ids=[self.slots[2].pk], version=request.version)
        request.refresh_from_db()
        self.assertEqual(request.version, 0)
        self.assertEqual(request.preferences.count(), 2)

    def test_stale_request_or_schedule_cannot_overwrite_preferences(self):
        request = self.submit()
        updated = self.submit(version=request.version, notes="New notes")
        with self.assertRaises(StaleScheduleError):
            self.submit(version=request.version, notes="Stale notes")
        with self.assertRaises(StaleScheduleError):
            self.submit()
        Event.objects.filter(pk=self.event.pk).update(schedule_version=1)
        with self.assertRaises(StaleScheduleError):
            self.submit(version=updated.version)
        updated.refresh_from_db()
        self.assertEqual(updated.notes, "New notes")

    def test_draft_and_private_events_reject_signup_except_explicit_early_opening(self):
        for status in ("draft", "private"):
            Event.objects.filter(pk=self.event.pk).update(publication_status=status)
            with self.assertRaises(ValidationError):
                self.submit()
        Event.objects.filter(pk=self.event.pk).update(
            publication_status="draft", signup_before_publication=True
        )
        self.submit()
        self.assertEqual(
            self.client.get(reverse("event_detail", args=[self.event.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("event_signup", args=[self.event.pk])).status_code,
            200,
        )
        StreamerAccount.objects.filter(pk=self.account.pk).update(
            participation_status="pending"
        )
        self.assertEqual(
            self.client.get(reverse("event_signup", args=[self.event.pk])).status_code,
            404,
        )

    def test_withdrawal_blocks_new_confirmation_and_keeps_existing_assignments(
        self,
    ):
        request = self.submit()
        draft = self.assign(
            request, get_schedule_draft(self.event.pk, actor=self.organizer)
        )
        withdrawn = withdraw_slot_request(
            request.pk, actor=self.user, expected_version=request.version
        )
        with self.assertRaises(StaleScheduleError):
            self.publish(draft)
        draft.refresh_from_db()
        self.assertEqual(draft.version, 1)
        self.assertIsNone(RaidSlot.objects.get(pk=self.slots[0].pk).streamer_id)
        request = self.submit(version=withdrawn.version)
        draft = self.assign(request, draft)
        self.publish(draft)
        withdraw_slot_request(
            request.pk, actor=self.user, expected_version=request.version
        )
        self.assertEqual(
            RaidSlot.objects.get(pk=self.slots[0].pk).streamer_id, self.streamer.pk
        )
        # An unchanged, already confirmed assignment survives request withdrawal.
        again = self.publish(get_schedule_draft(self.event.pk, actor=self.organizer))
        self.assertEqual(again.confirmed_slot_ids, ())

    def test_updated_preferences_require_organizer_review_before_new_confirmation(self):
        request = self.submit()
        draft = self.assign(
            request, get_schedule_draft(self.event.pk, actor=self.organizer)
        )
        updated = self.submit(ids=[self.slots[2].pk], version=request.version)
        with self.assertRaises(StaleScheduleError):
            self.publish(draft)
        draft = self.assign(updated, draft, 0)
        self.publish(draft)
        self.assertEqual(
            RaidSlot.objects.get(pk=self.slots[0].pk).streamer_id, self.streamer.pk
        )

    def test_eligibility_revoked_after_staging_blocks_new_publication(self):
        request = self.submit()
        draft = self.assign(
            request, get_schedule_draft(self.event.pk, actor=self.organizer)
        )
        StreamerAccount.objects.filter(pk=self.account.pk).update(
            participation_status="declined"
        )
        with self.assertRaises(ValidationError):
            self.publish(draft)
        self.assertIsNone(RaidSlot.objects.get(pk=self.slots[0].pk).streamer_id)

    def test_assignment_ownership_conflicts_and_versions_are_checked(self):
        request = self.submit()
        draft = get_schedule_draft(self.event.pk, actor=self.organizer)
        target = draft.slots.get(source_slot=self.slots[0])
        with self.assertRaises(PermissionDenied):
            assign_request_to_draft(
                request.pk,
                target.pk,
                actor=self.user,
                expected_request_version=0,
                expected_draft_version=0,
            )
        with self.assertRaises(StaleScheduleError):
            assign_request_to_draft(
                request.pk,
                target.pk,
                actor=self.organizer,
                expected_request_version=1,
                expected_draft_version=0,
            )
        with self.assertRaises(StaleScheduleError):
            assign_request_to_draft(
                request.pk,
                target.pk,
                actor=self.organizer,
                expected_request_version=0,
                expected_draft_version=1,
            )
        target.refresh_from_db()
        self.assertIsNone(target.streamer_id)
        target.streamer = self.other.streamer
        target.save()
        with self.assertRaises(ValidationError):
            self.assign(request, draft)
        foreign = get_schedule_draft(
            create_event(date=self.start.date()).pk, actor=self.organizer
        )
        slot = DraftSlot.objects.create(
            draft=foreign, start=self.start, duration_minutes=60
        )
        with self.assertRaises(ValidationError):
            assign_request_to_draft(
                request.pk,
                slot.pk,
                actor=self.organizer,
                expected_request_version=0,
                expected_draft_version=0,
            )

    def test_manual_reassignment_clears_request_provenance(self):
        request = self.submit()
        draft = self.assign(
            request, get_schedule_draft(self.event.pk, actor=self.organizer)
        )
        inputs = [
            DraftSlotInput(
                id=slot.pk,
                start=slot.start,
                streamer_id=slot.streamer_id,
                duration_minutes=slot.duration_minutes,
            )
            for slot in draft.slots.all()
        ]
        inputs[0] = replace(inputs[0], streamer_id=None)
        save_schedule_draft(
            draft.pk, actor=self.organizer, expected_version=draft.version, slots=inputs
        )
        slot = draft.slots.get(source_slot=self.slots[0])
        self.assertIsNone(slot.signup_request_id)
        self.assertIsNone(slot.request_version)

    def test_maintenance_reassignment_clears_previous_request_association(self):
        request = self.submit()
        draft = self.assign(
            request, get_schedule_draft(self.event.pk, actor=self.organizer)
        )
        self.publish(draft)
        slot = RaidSlot.objects.get(pk=self.slots[0].pk)
        Form = modelform_factory(
            RaidSlot,
            form=RaidSlotInlineForm,
            fields=("streamer", "duration_minutes", "raid_slot_note", "replay_url"),
        )
        form = Form(
            instance=slot,
            event=self.event,
            data={
                "streamer": self.other.streamer_id,
                "duration_minutes": 60,
                "start_date": slot.start.date().isoformat(),
                "start_time": slot.start.strftime("%H:%M"),
                "raid_slot_note": "Maintenance change",
                "replay_url": "",
            },
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        slot.refresh_from_db()
        self.assertEqual(slot.streamer_id, self.other.streamer_id)
        self.assertIsNone(slot.signup_request_id)

    def test_wrong_streamer_request_cannot_be_attached_to_a_performance(self):
        request = self.submit()
        draft = get_schedule_draft(self.event.pk, actor=self.organizer)
        draft.slots.filter(source_slot=self.slots[0]).update(
            signup_request=request, request_version=0, streamer=self.other.streamer
        )
        with self.assertRaises(ValidationError):
            self.publish(draft)

    def test_decline_is_organizer_only_and_can_be_resubmitted(self):
        request = self.submit()
        with self.assertRaises(PermissionDenied):
            decline_slot_request(request.pk, actor=self.user, expected_version=0)
        declined = decline_slot_request(
            request.pk,
            actor=self.organizer,
            expected_version=0,
            organizer_notes="Private decline discussion",
        )
        self.assertEqual(declined.status, "declined")
        self.assertNotContains(
            self.client.get(reverse("account_home")), "Private decline discussion"
        )
        resubmitted = self.submit(version=declined.version)
        self.assertEqual(resubmitted.status, "submitted")

    def test_preferences_survive_removed_slots_without_changing_requested_times(self):
        request = self.submit()
        start = self.slots[0].start
        self.slots[0].delete()
        preference = request.preferences.get(requested_start=start)
        self.assertIsNone(preference.slot_id)
        self.assertContains(
            self.client.get(reverse("account_home")), "Schedule changed or slot removed"
        )

    def test_database_rejects_duplicate_requests_and_preferences(self):
        request = self.submit()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SlotRequest.objects.create(event=self.event, streamer=self.streamer)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SlotPreference.objects.create(
                request=request,
                slot=self.slots[0],
                requested_start=self.start,
                requested_duration_minutes=60,
            )

    def test_signup_uses_session_identity_and_keeps_request_details_private(
        self,
    ):
        response = self.client.post(
            reverse("event_signup", args=[self.event.pk]),
            {
                "schedule_version": 0,
                "slots": [s.pk for s in self.slots[:2]],
                "notes": "Private preferences",
                "streamer_id": self.other.streamer_id,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SlotRequest.objects.get().streamer_id, self.streamer.pk)
        self.client.force_login(self.other.user)
        self.assertNotContains(
            self.client.get(reverse("account_home")), "Private preferences"
        )
        self.client.logout()
        self.assertNotContains(
            self.client.get(reverse("event_detail", args=[self.event.pk])),
            "Private preferences",
        )

    def test_withdrawal_and_cancellation_require_post_csrf_and_ownership(self):
        request = self.submit()
        with self.assertRaises(PermissionDenied):
            withdraw_slot_request(request.pk, actor=self.other.user, expected_version=0)
        self.client.force_login(self.other.user)
        url = reverse("withdraw_request", args=[request.pk])
        self.assertEqual(self.client.post(url, {"version": 0}).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(url, {"version": 0}).status_code, 403)
        self.slots[0].streamer = self.streamer
        self.slots[0].save()
        cancel_url = reverse("cancel_performance", args=[self.slots[0].pk])
        self.assertEqual(client.post(cancel_url, {"version": 0}).status_code, 403)
        self.client.force_login(self.other.user)
        self.assertEqual(self.client.post(cancel_url, {"version": 0}).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(cancel_url).status_code, 405)
        self.assertEqual(self.client.post(cancel_url, {}).status_code, 400)

    def test_stale_cancellation_and_completed_performances_leave_schedule_intact(self):
        RaidSlot.objects.filter(pk=self.slots[0].pk).update(streamer=self.streamer)
        Event.objects.filter(pk=self.event.pk).update(schedule_version=1)
        with self.assertRaises(StaleScheduleError):
            cancel_assignment(
                self.slots[0].pk, actor=self.user, expected_schedule_version=0
            )
        RaidSlot.objects.filter(pk=self.slots[0].pk).update(
            start=timezone.now() - timedelta(hours=2)
        )
        with self.assertRaises(ValidationError):
            cancel_assignment(
                self.slots[0].pk, actor=self.user, expected_schedule_version=1
            )
        self.assertEqual(
            RaidSlot.objects.get(pk=self.slots[0].pk).streamer_id, self.streamer.pk
        )

    def test_event_list_requires_future_available_slot_in_same_row(self):
        RaidSlot.objects.filter(event=self.event).update(streamer=self.other.streamer)
        RaidSlot.objects.create(
            event=self.event, start=timezone.now() - timedelta(hours=2)
        )
        self.assertFalse(signup_events(self.streamer).filter(pk=self.event.pk).exists())

    def test_anonymous_unverified_or_ineligible_accounts_cannot_submit(self):
        with self.assertRaises(PermissionDenied):
            save_slot_request(
                self.event.pk,
                actor=AnonymousUser(),
                slot_ids=[self.slots[0].pk],
                expected_schedule_version=0,
                expected_request_version=None,
            )
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("event_signup", args=[self.event.pk])).status_code,
            302,
        )
        self.client.force_login(self.other.user)
        response = self.client.post(
            reverse("event_signup", args=[self.event.pk]),
            {"schedule_version": 0, "slots": [self.slots[0].pk]},
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(SlotRequest.objects.exists())
