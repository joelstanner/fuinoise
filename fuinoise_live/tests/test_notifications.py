import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from fuinoise_live.accounts import account_for_twitch
from fuinoise_live.discord_delivery import (
    DeliveryError,
    deliver_pending,
    retry_delivery,
    verify_receipt,
)
from fuinoise_live.models import (
    DiscordDelivery,
    DiscordDispatchState,
    Notification,
    NotificationRead,
    RaidSlot,
)
from fuinoise_live.notifications import record_notice, visible_notifications
from fuinoise_live.providers import TwitchIdentity
from fuinoise_live.requests import (
    decline_slot_request,
    save_slot_request,
    withdraw_slot_request,
)
from fuinoise_live.scheduling import (
    DraftSlotInput,
    cancel_published_assignment,
    get_schedule_draft,
    open_schedule_signup,
    publish_schedule_draft,
    save_schedule_draft,
)

from .helpers import create_event


@override_settings(
    DISCORD_ORGANIZER_CHANNEL_ID="999",
    DISCORD_GUILD_ID="888",
    FUINOISE_ORIGIN="https://fuinoise.example",
    DISCORD_BOT_TOKEN="test-bot-token",
)
class NotificationTests(TestCase):
    def setUp(self):
        self.account = account_for_twitch(
            TwitchIdentity("123", "performer", "Performer")
        )
        self.account.discord_id = "456"
        self.account.participation_status = "approved"
        self.account.discord_override = True
        self.account.save()
        self.user = self.account.user
        self.other = account_for_twitch(TwitchIdentity("124", "other", "Other"))
        self.organizer = get_user_model().objects.create_superuser(
            "organizer", password=None
        )
        self.start = timezone.now().replace(second=0, microsecond=0) + timedelta(days=3)
        self.event = create_event(
            date=self.start.date(),
            name="Notification train",
            event_time_zone="UTC",
            publication_status="published",
        )
        self.slot = RaidSlot.objects.create(event=self.event, start=self.start)
        self.client.force_login(self.user)

    def notice(self):
        record_notice(
            "occurrence:1",
            kind="test",
            event=self.event,
            title="A notice",
            body="Public facts only",
            streamer_id=self.account.streamer_id,
        )
        return DiscordDelivery.objects.select_related("notification").get(
            notification__recipient=self.user
        )

    def draft(self, performer=None, start=None):
        draft = get_schedule_draft(self.event.pk, actor=self.organizer)
        return save_schedule_draft(
            draft.pk,
            actor=self.organizer,
            expected_version=draft.version,
            slots=[
                DraftSlotInput(
                    id=draft.slots.get().pk,
                    streamer_id=(performer or self.account.streamer).pk,
                    start=start or self.start,
                    duration_minutes=60,
                )
            ],
        )

    def publish(self, draft):
        return publish_schedule_draft(
            draft.pk, actor=self.organizer, expected_version=draft.version
        )

    def submit(self, version=None):
        self.event.refresh_from_db()
        return save_slot_request(
            self.event.pk,
            actor=self.user,
            slot_ids=[self.slot.pk],
            expected_schedule_version=self.event.schedule_version,
            expected_request_version=version,
            notes="Private request notes",
        )

    def dm_only(self):
        row = self.notice()
        DiscordDelivery.objects.filter(notification__recipient__isnull=True).delete()
        return row

    def send_response(self, row, path, *, payload=None, sending=False):
        if path == "/users/@me/channels":
            return {"id": "777", "type": 1, "recipients": [{"id": "456"}]}
        if path == "/channels/999":
            return {"id": "999", "type": 0, "guild_id": "888"}
        self.assertTrue(sending)
        return {"id": "555", "channel_id": "777", "nonce": row.nonce}

    def test_occurrence_and_outbox_are_atomic_and_unique(self):
        self.notice()
        self.notice()
        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(DiscordDelivery.objects.count(), 2)
        with self.assertRaises(RuntimeError), transaction.atomic():
            record_notice(
                "rollback",
                kind="test",
                event=self.event,
                title="Rolled back",
                body="Body",
                streamer_id=self.account.streamer_id,
            )
            raise RuntimeError()
        self.assertFalse(
            Notification.objects.filter(key__startswith="rollback").exists()
        )

    def test_requests_update_withdraw_decline_and_private_notes(self):
        request = self.submit()
        self.assertEqual(
            Notification.objects.filter(kind="request_submitted").count(), 2
        )
        request = self.submit(request.version)
        self.assertEqual(Notification.objects.filter(kind="request_updated").count(), 2)
        request = withdraw_slot_request(
            request.pk, actor=self.user, expected_version=request.version
        )
        self.assertEqual(
            Notification.objects.filter(kind="request_withdrawn").count(), 2
        )
        request = withdraw_slot_request(
            request.pk, actor=self.user, expected_version=request.version
        )
        self.assertEqual(
            Notification.objects.filter(kind="request_withdrawn").count(), 2
        )
        request = self.submit(request.version)
        request = decline_slot_request(
            request.pk,
            actor=self.organizer,
            expected_version=request.version,
            organizer_notes="Private decline reason",
        )
        decline_slot_request(
            request.pk,
            actor=self.organizer,
            expected_version=request.version,
            organizer_notes="Changed private reason",
        )
        self.assertEqual(
            Notification.objects.filter(kind="request_declined").count(), 2
        )
        for notice in Notification.objects.all():
            self.assertNotIn("Private", notice.body)

    def test_failed_request_does_not_notify(self):
        with self.assertRaises(ValidationError):
            save_slot_request(
                self.event.pk,
                actor=self.user,
                slot_ids=[-1],
                expected_schedule_version=0,
                expected_request_version=None,
            )
        self.assertEqual(Notification.objects.count(), 0)

    def test_draft_and_early_signup_do_not_confirm(self):
        self.event.publication_status = "draft"
        self.event.save()
        draft = self.draft()
        self.assertEqual(Notification.objects.count(), 0)
        open_schedule_signup(
            draft.pk, actor=self.organizer, expected_version=draft.version
        )
        self.assertEqual(Notification.objects.count(), 0)
        draft.refresh_from_db()
        self.publish(draft)
        self.assertEqual(
            Notification.objects.filter(kind="assignment_confirmed").count(), 2
        )

    def test_publication_confirms_once_and_move_notifies_update(self):
        draft = self.draft()
        self.publish(draft)
        draft.refresh_from_db()
        self.publish(draft)
        self.assertEqual(Notification.objects.count(), 2)
        draft.refresh_from_db()
        draft = save_schedule_draft(
            draft.pk,
            actor=self.organizer,
            expected_version=draft.version,
            slots=[
                DraftSlotInput(
                    id=draft.slots.get().pk,
                    start=self.start + timedelta(hours=1),
                    streamer_id=self.account.streamer_id,
                    duration_minutes=90,
                )
            ],
        )
        self.assertEqual(Notification.objects.count(), 2)
        self.publish(draft)
        self.assertEqual(
            Notification.objects.filter(kind="assignment_updated").count(), 2
        )
        self.assertIn(
            "90 minutes",
            Notification.objects.filter(kind="assignment_updated").first().body,
        )

    def test_reassignment_removes_old_and_confirms_new(self):
        draft = self.draft()
        self.publish(draft)
        draft.refresh_from_db()
        draft = save_schedule_draft(
            draft.pk,
            actor=self.organizer,
            expected_version=draft.version,
            slots=[
                DraftSlotInput(
                    id=draft.slots.get().pk,
                    start=self.start,
                    streamer_id=self.other.streamer_id,
                    duration_minutes=60,
                )
            ],
        )
        self.publish(draft)
        self.assertEqual(
            Notification.objects.filter(
                kind="assignment_removed", recipient=self.user
            ).count(),
            1,
        )
        self.assertEqual(
            Notification.objects.filter(
                kind="assignment_confirmed", recipient=self.other.user
            ).count(),
            1,
        )
        draft.refresh_from_db()
        draft = save_schedule_draft(
            draft.pk, actor=self.organizer, expected_version=draft.version, slots=[]
        )
        self.publish(draft)
        self.assertEqual(
            Notification.objects.filter(
                kind="assignment_removed", recipient=self.other.user
            ).count(),
            1,
        )

    def test_metadata_change_notifies_and_rollback_restores_everything(
        self,
    ):
        draft = self.draft()
        self.publish(draft)
        draft.refresh_from_db()
        draft = save_schedule_draft(
            draft.pk,
            actor=self.organizer,
            expected_version=draft.version,
            slots=[
                DraftSlotInput(
                    id=draft.slots.get().pk,
                    start=self.start,
                    streamer_id=self.account.streamer_id,
                    duration_minutes=60,
                )
            ],
            event_changes={"name": "Renamed event"},
        )
        with (
            patch(
                "fuinoise_live.notifications.record_notice", side_effect=RuntimeError()
            ),
            self.assertRaises(RuntimeError),
        ):
            self.publish(draft)
        self.event.refresh_from_db()
        self.assertEqual(self.event.name, "Notification train")
        self.assertEqual(Notification.objects.count(), 2)
        self.publish(draft)
        self.assertIn(
            "Renamed event",
            Notification.objects.filter(kind="assignment_updated").first().body,
        )

    def test_cancellation_notifies_once_and_blocks_old_draft(self):
        draft = self.draft()
        self.publish(draft)
        self.event.refresh_from_db()
        self.assertTrue(
            cancel_published_assignment(
                self.slot.pk,
                streamer_id=self.account.streamer_id,
                expected_schedule_version=self.event.schedule_version,
            )
        )
        self.event.refresh_from_db()
        self.assertFalse(
            cancel_published_assignment(
                self.slot.pk,
                streamer_id=self.account.streamer_id,
                expected_schedule_version=self.event.schedule_version,
            )
        )
        self.assertEqual(
            Notification.objects.filter(kind="assignment_canceled").count(), 2
        )
        with self.assertRaises(ValidationError):
            self.publish(draft)
        self.assertEqual(
            Notification.objects.filter(kind="assignment_confirmed").count(), 2
        )

    def test_manual_streamer_without_account_only_alerts_organizers(self):
        from fuinoise_live.models import Streamer

        streamer = Streamer.objects.create(
            display_name="Legacy", twitch_username="legacy"
        )
        record_notice(
            "manual",
            kind="test",
            event=self.event,
            title="Legacy",
            body="Body",
            streamer_id=streamer.pk,
        )
        self.assertEqual(Notification.objects.count(), 1)
        self.assertIsNone(Notification.objects.get().recipient_id)

    def test_private_inbox_read_marks_csrf_and_current_roles(self):
        row = self.notice()
        inbox = reverse("notification_inbox")
        self.assertEqual(visible_notifications(self.user).count(), 1)
        self.assertEqual(visible_notifications(self.organizer).count(), 1)
        self.assertEqual(visible_notifications(self.other.user).count(), 0)
        read = reverse("notification_read", args=[row.notification_id])
        self.assertEqual(self.client.get(read).status_code, 405)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.user)
        self.assertEqual(secure.post(read).status_code, 403)
        self.assertContains(self.client.get(inbox), "1 unread")
        self.client.post(read)
        self.client.post(read)
        self.assertEqual(NotificationRead.objects.count(), 1)
        self.assertContains(self.client.get(inbox), "0 unread")
        self.client.force_login(self.other.user)
        self.assertEqual(self.client.post(read).status_code, 404)
        self.assertNotContains(self.client.get(inbox), "Public facts only")
        self.organizer.is_superuser = False
        self.organizer.save()
        self.assertEqual(visible_notifications(self.organizer).count(), 0)
        self.client.logout()
        self.assertEqual(self.client.get(inbox).status_code, 302)

    def test_on_site_escaping_pagination_and_no_provider_calls(self):
        row = self.notice()
        Notification.objects.filter(pk=row.notification_id).update(
            body="<script>attack</script>"
        )
        for index in range(35):
            record_notice(
                f"many:{index}",
                kind="test",
                event=self.event,
                title="More",
                body="Body",
                streamer_id=self.account.streamer_id,
            )
        with patch("fuinoise_live.discord_delivery.discord_request") as network:
            response = self.client.get(reverse("notification_inbox") + "?page=2")
            self.assertContains(response, "&lt;script&gt;attack&lt;/script&gt;")
            self.assertContains(response, "Page 2 of 2")
            self.assertContains(
                self.client.get(reverse("account_home")), "Recent notifications"
            )
            network.assert_not_called()

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_worker_sends_once_and_overlapping_worker_cannot_claim(self, remote):
        row = self.dm_only()
        nested = []

        def response(path, **kwargs):
            if kwargs.get("sending"):
                nested.append(deliver_pending())
            return self.send_response(row, path, **kwargs)

        remote.side_effect = response
        self.assertEqual(deliver_pending()["sent"], 1)
        self.assertEqual(nested[0]["attempted"], 0)
        self.assertEqual(deliver_pending()["attempted"], 0)
        row.refresh_from_db()
        self.assertEqual(row.message_id, "555")
        payload = remote.call_args.kwargs["payload"]
        self.assertTrue(payload["enforce_nonce"])
        self.assertEqual(payload["allowed_mentions"]["parse"], [])
        self.assertIn(f"/notifications/{row.notification_id}/", payload["content"])

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_worker_checks_guild_and_dm_recipient(self, remote):
        self.notice()
        remote.return_value = {"id": "999", "type": 0, "guild_id": "wrong"}
        result = deliver_pending()
        self.assertEqual(result["failed"], 2)
        self.assertFalse(
            any(call.kwargs.get("sending") for call in remote.call_args_list)
        )

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_disconnected_or_changed_discord_account_never_redirects(self, remote):
        row = self.dm_only()
        self.account.discord_id = "457"
        self.account.save()
        self.assertEqual(deliver_pending()["failed"], 1)
        remote.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(row.target_id, "456")
        retry_delivery(row.pk, actor=self.organizer)
        self.account.discord_id = None
        self.account.save()
        self.assertEqual(deliver_pending()["failed"], 1)
        remote.assert_not_called()

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_missing_connection_can_be_added_before_first_attempt(self, remote):
        self.account.discord_id = None
        self.account.save()
        row = self.dm_only()
        self.assertEqual(deliver_pending()["failed"], 1)
        self.account.discord_id = "456"
        self.account.save()
        retry_delivery(row.pk, actor=self.organizer)
        remote.side_effect = lambda path, **kwargs: self.send_response(
            row, path, **kwargs
        )
        self.assertEqual(deliver_pending()["sent"], 1)

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_rate_limit_waits_globally_and_retries_same_occurrence(self, remote):
        row = self.dm_only()
        remote.side_effect = DeliveryError("Rate limit", retry_after=123)
        self.assertEqual(deliver_pending()["pending"], 1)
        self.assertEqual(deliver_pending()["attempted"], 0)
        row.refresh_from_db()
        self.assertGreater(row.next_attempt_at, timezone.now() + timedelta(seconds=120))
        DiscordDispatchState.objects.update(pause_until=timezone.now())
        DiscordDelivery.objects.filter(pk=row.pk).update(next_attempt_at=timezone.now())
        remote.side_effect = lambda path, **kwargs: self.send_response(
            row, path, **kwargs
        )
        self.assertEqual(deliver_pending()["sent"], 1)
        self.assertEqual(Notification.objects.count(), 2)

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_definite_failure_can_retry_but_uncertain_cannot(self, remote):
        row = self.dm_only()
        remote.side_effect = DeliveryError("Permission denied")
        self.assertEqual(deliver_pending()["failed"], 1)
        retry_delivery(row.pk, actor=self.organizer)
        remote.side_effect = lambda path, **kwargs: (
            self.send_response(row, path, **kwargs)
            if not kwargs.get("sending")
            else (_ for _ in ()).throw(DeliveryError("Lost response", uncertain=True))
        )
        self.assertEqual(deliver_pending()["uncertain"], 1)
        with self.assertRaises(ValidationError):
            retry_delivery(row.pk, actor=self.organizer)
        self.assertEqual(deliver_pending()["attempted"], 0)
        self.assertEqual(Notification.objects.count(), 2)

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_malformed_message_receipt_is_held(self, remote):
        row = self.dm_only()
        remote.side_effect = lambda path, **kwargs: (
            {"id": "555", "channel_id": "another", "nonce": row.nonce}
            if kwargs.get("sending")
            else self.send_response(row, path, **kwargs)
        )
        self.assertEqual(deliver_pending()["uncertain"], 1)
        self.assertEqual(deliver_pending()["attempted"], 0)

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_crashed_worker_is_held_and_bounded_retries_end(self, remote):
        row = self.dm_only()
        DiscordDelivery.objects.filter(pk=row.pk).update(
            status="sending",
            claimed_at=timezone.now() - timedelta(minutes=16),
            message_attempted_at=timezone.now() - timedelta(minutes=16),
        )
        deliver_pending()
        row.refresh_from_db()
        self.assertEqual(row.status, "uncertain")
        remote.assert_not_called()
        DiscordDelivery.objects.filter(pk=row.pk).update(
            status="pending", cycle_attempts=7
        )
        remote.side_effect = DeliveryError("Rate limit", retry_after=2)
        self.assertEqual(deliver_pending()["failed"], 1)

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_lost_receipt_requires_exact_bot_channel_content_match(self, remote):
        row = self.dm_only()
        DiscordDelivery.objects.filter(pk=row.pk).update(
            status="uncertain", channel_id="777", content="Exact content"
        )
        remote.side_effect = [
            {"id": "333"},
            {
                "id": "555",
                "channel_id": "777",
                "author": {"id": "333"},
                "content": "Exact content",
            },
        ]
        verify_receipt(row.pk, "555", actor=self.organizer)
        row.refresh_from_db()
        self.assertEqual(row.status, "sent")
        with self.assertRaises(ValidationError):
            verify_receipt(row.pk, "555", actor=self.organizer)
        DiscordDelivery.objects.filter(pk=row.pk).update(status="uncertain")
        remote.side_effect = [
            {"id": "333"},
            {
                "id": "555",
                "channel_id": "777",
                "author": {"id": "444"},
                "content": "Exact content",
            },
        ]
        with self.assertRaises(ValidationError):
            verify_receipt(row.pk, "555", actor=self.organizer)
        with self.assertRaises(PermissionDenied):
            verify_receipt(row.pk, "555", actor=self.user)

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_organizer_delivery_view_retries_queue_without_sending(self, remote):
        row = self.dm_only()
        self.assertEqual(
            self.client.get(reverse("notification_deliveries")).status_code, 403
        )
        self.client.force_login(self.organizer)
        DiscordDelivery.objects.filter(pk=row.pk).update(status="failed")
        self.assertContains(
            self.client.get(reverse("notification_deliveries") + "?status=failed"),
            "1 need attention",
        )
        action = reverse("notification_delivery_action", args=[row.pk])
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.organizer)
        self.assertEqual(secure.post(action, {"action": "retry"}).status_code, 403)
        self.assertEqual(self.client.post(action, {"action": "retry"}).status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.status, "pending")
        remote.assert_not_called()
        self.assertEqual(self.client.get(action).status_code, 405)
        self.assertEqual(
            self.client.post(action, {"action": "invalid"}).status_code, 400
        )
        with self.assertRaises(PermissionDenied):
            retry_delivery(row.pk, actor=self.user)

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_command_reports_failures_and_validates_bounds(self, remote):
        self.dm_only()
        remote.side_effect = DeliveryError("Permission denied")
        with self.assertRaises(CommandError):
            call_command("deliver_notifications", stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command("deliver_notifications", limit=0)
        with self.assertRaises(ValidationError):
            deliver_pending(limit=501)

    def test_history_survives_deleted_event_and_inactive_user_cannot_read(self):
        self.notice()
        self.event.delete()
        self.assertEqual(Notification.objects.count(), 2)
        self.assertTrue(all(n.event_id is None for n in Notification.objects.all()))
        self.user.is_active = False
        self.user.save()
        self.assertEqual(visible_notifications(self.user).count(), 0)

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_crash_before_message_is_safe_to_retry_and_keeps_attempt_count(
        self, remote
    ):
        row = self.dm_only()
        DiscordDelivery.objects.filter(pk=row.pk).update(
            status="sending",
            claimed_at=timezone.now() - timedelta(minutes=16),
            attempts=3,
            cycle_attempts=3,
        )
        remote.side_effect = lambda path, **kwargs: self.send_response(
            row, path, **kwargs
        )
        self.assertEqual(deliver_pending()["sent"], 1)
        row.refresh_from_db()
        self.assertEqual(row.attempts, 4)
        self.assertIsNotNone(row.message_attempted_at)

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_notification_detail_is_private_and_verify_action_is_read_only(
        self, remote
    ):
        row = self.dm_only()
        detail = reverse("notification_detail", args=[row.notification_id])
        self.assertContains(self.client.get(detail), "Public facts only")
        self.client.force_login(self.other.user)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.client.force_login(self.organizer)
        action = reverse("notification_delivery_action", args=[row.pk])
        self.assertEqual(self.client.post(action, {"action": "retry"}).status_code, 302)
        self.assertContains(
            self.client.get(reverse("notification_deliveries")),
            "Only definite failures",
        )
        DiscordDelivery.objects.filter(pk=row.pk).update(
            status="uncertain", channel_id="777", content="Exact content"
        )
        remote.side_effect = [
            {"id": "333"},
            {
                "id": "555",
                "channel_id": "777",
                "author": {"id": "333"},
                "content": "Exact content",
            },
        ]
        self.assertEqual(
            self.client.post(
                action, {"action": "verify", "message_id": "555"}
            ).status_code,
            302,
        )
        row.refresh_from_db()
        self.assertEqual(row.status, "sent")
        self.assertTrue(
            all(call.kwargs.get("payload") is None for call in remote.call_args_list)
        )

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_account_disconnect_during_preparation_blocks_message(self, remote):
        row = self.dm_only()

        def response(path, **kwargs):
            self.account.discord_id = None
            self.account.save()
            return self.send_response(row, path, **kwargs)

        remote.side_effect = response
        self.assertEqual(deliver_pending()["failed"], 1)
        self.assertEqual(remote.call_count, 1)

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_lost_claim_during_preparation_blocks_message(self, remote):
        row = self.dm_only()

        def response(path, **kwargs):
            DiscordDelivery.objects.filter(pk=row.pk).update(
                status="uncertain", claim_token=None
            )
            return self.send_response(row, path, **kwargs)

        remote.side_effect = response
        self.assertEqual(deliver_pending()["uncertain"], 1)
        self.assertEqual(remote.call_count, 1)

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_preparation_crashes_respect_attempt_limit_and_retry_preserves_history(
        self, remote
    ):
        row = self.dm_only()
        DiscordDelivery.objects.filter(pk=row.pk).update(
            status="sending",
            claimed_at=timezone.now() - timedelta(minutes=16),
            cycle_attempts=8,
            attempts=8,
        )
        self.assertEqual(deliver_pending()["attempted"], 0)
        row.refresh_from_db()
        self.assertEqual(row.status, "failed")
        retry_delivery(row.pk, actor=self.organizer)
        row.refresh_from_db()
        self.assertEqual(row.attempts, 8)
        self.assertEqual(row.cycle_attempts, 0)
        remote.assert_not_called()

    @patch("fuinoise_live.discord_delivery.discord_request")
    def test_organizer_message_goes_to_configured_channel_and_missing_config_is_clear(
        self, remote
    ):
        self.notice()
        DiscordDelivery.objects.filter(notification__recipient=self.user).delete()
        row = DiscordDelivery.objects.get()
        remote.side_effect = [
            {"id": "999", "type": 0, "guild_id": "888"},
            {"id": "555", "channel_id": "999", "nonce": row.nonce},
        ]
        self.assertEqual(deliver_pending()["sent"], 1)
        self.assertEqual(remote.call_args.args[0], "/channels/999/messages")
        remote.reset_mock()
        DiscordDelivery.objects.filter(pk=row.pk).update(
            status="pending",
            target_id="",
            guild_id="",
            channel_id="",
        )
        with override_settings(DISCORD_ORGANIZER_CHANNEL_ID="", DISCORD_GUILD_ID=""):
            self.assertEqual(deliver_pending()["failed"], 1)
        row.refresh_from_db()
        self.assertIn("Configure the organizer", row.last_error)
        remote.assert_not_called()
