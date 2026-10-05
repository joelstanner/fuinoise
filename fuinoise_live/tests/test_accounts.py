import io
import json
import time
from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from fuinoise_live import accounts, providers
from fuinoise_live.models import RaidSlot, Streamer, StreamerAccount
from fuinoise_live.scheduling import get_schedule_draft

from .helpers import create_event

TWITCH = providers.TwitchIdentity("1234", "musician", "Musician")
DISCORD = providers.DiscordIdentity("5678", "music-discord", True, "9999")
CONFIG = {
    "TWITCH_CLIENT_ID": "twitch-client",
    "TWITCH_CLIENT_SECRET": "twitch-secret",
    "DISCORD_CLIENT_ID": "discord-client",
    "DISCORD_CLIENT_SECRET": "discord-secret",
    "DISCORD_GUILD_ID": "9999",
    "DISCORD_BOT_TOKEN": "bot-secret",
    "FUINOISE_ORIGIN": "https://fuinoise.example",
    "DEBUG": False,
}


@override_settings(**CONFIG)
class AccountTests(TestCase):
    def setUp(self):
        self.account = accounts.account_for_twitch(TWITCH)
        self.user = self.account.user
        self.organizer = get_user_model().objects.create_user(username="organizer")
        call_command(
            "setup_organizers", username=self.organizer.username, stdout=io.StringIO()
        )

    def link(self, identity=DISCORD):
        self.account = accounts.link_discord(
            self.account.pk,
            actor=self.user,
            expected_version=self.account.version,
            identity=identity,
        )
        return self.account

    def approve(self):
        self.account = accounts.review_participation(
            self.account.pk,
            actor=self.organizer,
            expected_version=self.account.version,
            status="approved",
            reason="Active community participant",
        )

    def test_twitch_account_is_stable_and_has_no_password_or_staff_privileges(self):
        again = accounts.account_for_twitch(TWITCH)
        self.assertEqual(again.pk, self.account.pk)
        self.assertEqual(again.user_id, self.user.pk)
        self.assertFalse(self.user.has_usable_password())
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        self.assertEqual(self.user.get_all_permissions(), set())
        self.assertFalse(accounts.is_eligible(again))

    def test_existing_twitch_id_preserves_streamer_and_schedule_relationships(self):
        event = create_event(date=timezone.now().date())
        slot = RaidSlot.objects.create(
            event=event, streamer=self.account.streamer, start=timezone.now()
        )
        self.account.streamer.display_name = "Organizer chosen name"
        self.account.streamer.organizer_notes = "Private musician notes"
        self.account.streamer.save()
        updated = accounts.account_for_twitch(
            providers.TwitchIdentity("1234", "new_login", "New Twitch Name")
        )
        slot.refresh_from_db()
        updated.streamer.refresh_from_db()
        self.assertEqual(updated.streamer_id, slot.streamer_id)
        self.assertEqual(updated.streamer.display_name, "Organizer chosen name")
        self.assertEqual(updated.streamer.organizer_notes, "Private musician notes")
        self.assertEqual(updated.streamer.twitch_username, "new_login")
        self.assertEqual(updated.streamer.twitch_display_name, "New Twitch Name")

    def test_legacy_name_alone_never_claims_an_existing_streamer(self):
        legacy = Streamer.objects.create(
            display_name="Legacy", twitch_username="legacy"
        )
        users_before = get_user_model().objects.count()
        with self.assertRaises(accounts.AccountConflict):
            accounts.account_for_twitch(
                providers.TwitchIdentity("3333", "legacy", "Legacy")
            )
        legacy.refresh_from_db()
        self.assertIsNone(legacy.twitch_id)
        self.assertEqual(get_user_model().objects.count(), users_before)
        self.assertFalse(StreamerAccount.objects.filter(streamer=legacy).exists())
        legacy.twitch_id = "3333"
        legacy.save()
        self.assertEqual(
            accounts.account_for_twitch(
                providers.TwitchIdentity("3333", "legacy", "Legacy")
            ).streamer_id,
            legacy.pk,
        )

    def test_display_name_collision_does_not_merge_accounts(self):
        another = accounts.account_for_twitch(
            providers.TwitchIdentity("3333", "different_login", "Musician")
        )
        self.assertNotEqual(another.user_id, self.user.pk)
        self.assertNotEqual(another.streamer_id, self.account.streamer_id)
        self.assertEqual(another.streamer.display_name, "Musician (3333)")

    def test_inactive_user_cannot_sign_in_or_request(self):
        self.user.is_active = False
        self.user.save()
        with self.assertRaises(PermissionDenied):
            accounts.account_for_twitch(TWITCH)
        with self.assertRaises(PermissionDenied):
            accounts.require_eligible_streamer(self.user)

    def test_discord_link_and_organizer_approval_are_both_required(self):
        self.link()
        self.assertFalse(accounts.is_eligible(self.account))
        self.approve()
        self.account = StreamerAccount.objects.select_related("streamer", "user").get(
            pk=self.account.pk
        )
        self.assertTrue(accounts.is_eligible(self.account))
        with patch(
            "fuinoise_live.accounts.discord_membership", return_value=True
        ) as membership:
            self.assertEqual(
                accounts.require_eligible_streamer(self.user).pk,
                self.account.streamer_id,
            )
            membership.assert_called_once_with(DISCORD.id)
        review = self.account.reviews.get(action="participation_review")
        self.assertEqual(review.actor_id, self.organizer.pk)
        self.assertEqual(review.reason, "Active community participant")

    def test_discord_override_requires_separate_permission_and_approval(self):
        with self.assertRaises(PermissionDenied):
            accounts.set_discord_override(
                self.account.pk,
                actor=self.user,
                expected_version=0,
                enabled=True,
                reason="Self override",
            )
        self.account = accounts.set_discord_override(
            self.account.pk,
            actor=self.organizer,
            expected_version=0,
            enabled=True,
            reason="Approved exception",
        )
        self.assertFalse(accounts.is_eligible(self.account))
        self.approve()
        with patch("fuinoise_live.accounts.discord_membership") as membership:
            self.assertEqual(
                accounts.require_eligible_streamer(self.user).pk,
                self.account.streamer_id,
            )
            membership.assert_not_called()
        self.account = accounts.set_discord_override(
            self.account.pk,
            actor=self.organizer,
            expected_version=self.account.version,
            enabled=False,
            reason="Exception ended",
        )
        self.assertFalse(accounts.is_eligible(self.account))
        self.assertEqual(
            self.account.reviews.filter(action="discord_override").count(), 2
        )

    def test_membership_outage_departure_expiration_and_wrong_guild_fail_closed(self):
        self.link()
        self.approve()
        for member in (False, None):
            with patch(
                "fuinoise_live.accounts.discord_membership", return_value=member
            ):
                with self.assertRaises(ValidationError):
                    accounts.require_eligible_streamer(self.user)
            self.account.refresh_from_db()
            self.assertEqual(self.account.discord_member, member)
        with patch(
            "fuinoise_live.accounts.discord_membership",
            side_effect=providers.ProviderError(),
        ):
            self.account.discord_member = True
            self.account.save()
            with self.assertRaises(ValidationError):
                accounts.require_eligible_streamer(self.user)
        self.account.refresh_from_db()
        self.assertIsNone(self.account.discord_member)
        self.account.discord_member = True
        self.account.discord_checked_at = timezone.now() - timedelta(minutes=16)
        self.assertFalse(accounts.is_eligible(self.account))
        self.account.discord_checked_at = timezone.now() + timedelta(minutes=1)
        self.assertFalse(accounts.is_eligible(self.account))
        self.account.discord_checked_at = timezone.now()
        self.account.discord_guild_id = "other-guild"
        self.assertFalse(accounts.is_eligible(self.account))

    def test_another_user_cannot_link_or_unlink_this_account(self):
        other = get_user_model().objects.create_user(username="other")
        for operation in (
            lambda: accounts.link_discord(
                self.account.pk, actor=other, expected_version=0, identity=DISCORD
            ),
            lambda: accounts.unlink_discord(
                self.account.pk, actor=other, expected_version=0
            ),
        ):
            with self.assertRaises(PermissionDenied):
                operation()
        self.account.refresh_from_db()
        self.assertIsNone(self.account.discord_id)

    def test_discord_identity_cannot_be_shared_or_replaced_without_disconnect(self):
        self.link()
        other = accounts.account_for_twitch(
            providers.TwitchIdentity("3333", "other", "Other")
        )
        with self.assertRaises(accounts.AccountConflict):
            accounts.link_discord(
                other.pk, actor=other.user, expected_version=0, identity=DISCORD
            )
        with self.assertRaises(accounts.AccountConflict):
            accounts.link_discord(
                self.account.pk,
                actor=self.user,
                expected_version=self.account.version,
                identity=providers.DiscordIdentity("4444", "different", True, "9999"),
            )
        self.account.refresh_from_db()
        self.assertEqual(self.account.discord_id, DISCORD.id)

    def test_disconnect_resets_approval_and_override_with_history(self):
        self.link()
        self.approve()
        self.account = accounts.set_discord_override(
            self.account.pk,
            actor=self.organizer,
            expected_version=self.account.version,
            enabled=True,
            reason="Exception",
        )
        self.account = accounts.unlink_discord(
            self.account.pk, actor=self.user, expected_version=self.account.version
        )
        self.assertIsNone(self.account.discord_id)
        self.assertIsNone(self.account.discord_checked_at)
        self.assertFalse(self.account.discord_override)
        self.assertEqual(self.account.participation_status, "pending")
        self.assertEqual(self.account.reviews.count(), 4)
        self.link()
        self.assertFalse(accounts.is_eligible(self.account))

    def test_review_versions_and_reasons_prevent_stale_or_unrecorded_changes(self):
        for operation in (
            lambda: accounts.review_participation(
                self.account.pk,
                actor=self.organizer,
                expected_version=0,
                status="approved",
                reason="",
            ),
            lambda: accounts.review_participation(
                self.account.pk,
                actor=self.organizer,
                expected_version=0,
                status="invented",
                reason="Reason",
            ),
            lambda: accounts.set_discord_override(
                self.account.pk,
                actor=self.organizer,
                expected_version=0,
                enabled=True,
                reason=" ",
            ),
        ):
            with self.assertRaises(ValidationError):
                operation()
        self.approve()
        with self.assertRaises(accounts.AccountConflict):
            accounts.set_discord_override(
                self.account.pk,
                actor=self.organizer,
                expected_version=0,
                enabled=True,
                reason="Stale override",
            )
        self.account.refresh_from_db()
        self.assertFalse(self.account.discord_override)
        self.assertEqual(self.account.reviews.count(), 1)

    def test_identity_associations_are_unique_in_the_database(self):
        other = accounts.account_for_twitch(
            providers.TwitchIdentity("3333", "other", "Other")
        )
        self.link()
        for changes in (
            {"user_id": self.user.pk},
            {"streamer_id": self.account.streamer_id},
            {"discord_id": DISCORD.id},
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                StreamerAccount.objects.filter(pk=other.pk).update(**changes)

    def test_organizer_setup_is_idempotent_and_grants_only_intended_role(self):
        call_command("setup_organizers", twitch_id=TWITCH.id, stdout=io.StringIO())
        call_command("setup_organizers", twitch_id=TWITCH.id, stdout=io.StringIO())
        user = get_user_model().objects.get(pk=self.user.pk)
        self.assertEqual(user.groups.count(), 1)
        self.assertTrue(user.has_perm("fuinoise_live.review_eligibility"))
        self.assertTrue(user.has_perm("fuinoise_live.override_discord_requirement"))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        get_schedule_draft(create_event(date=timezone.now().date()).pk, actor=user)
        with self.assertRaises(CommandError):
            call_command("setup_organizers", twitch_id="unknown", stdout=io.StringIO())


@override_settings(**CONFIG)
class AccountViewTests(TestCase):
    def setUp(self):
        self.account = accounts.account_for_twitch(TWITCH)
        self.user = self.account.user

    def start(self, provider="twitch"):
        route = "twitch_login" if provider == "twitch" else "discord_connect"
        response = self.client.post(reverse(route))
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlsplit(response.url).query)["state"][0]

    def callback(self, state, provider="twitch", **params):
        route = "twitch_callback" if provider == "twitch" else "discord_callback"
        return self.client.get(
            reverse(route), {"state": state, "code": "authorization-code", **params}
        )

    @patch("fuinoise_live.account_views.providers.twitch_identity", return_value=TWITCH)
    def test_twitch_sign_in_creates_session_and_rejects_callback_replay(self, identity):
        state = self.start()
        self.callback(state)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
        response = self.client.get(reverse("account_home"))
        self.assertContains(response, "Musician")
        self.assertNotContains(response, "Review streamer eligibility")
        self.callback(state)
        identity.assert_called_once_with("authorization-code")

    @patch("fuinoise_live.account_views.providers.twitch_identity")
    def test_missing_bad_expired_or_denied_state_never_calls_provider(self, identity):
        self.callback("missing")
        state = self.start()
        self.callback("wrong")
        state = self.start()
        session = self.client.session
        session["oauth_twitch"]["created"] = time.time() - 601
        session.save()
        self.callback(state)
        state = self.start()
        self.callback(state, error="access_denied")
        identity.assert_not_called()
        self.assertNotIn("_auth_user_id", self.client.session)

    @patch("fuinoise_live.account_views.providers.twitch_identity")
    def test_callback_cannot_switch_account_after_local_session_changes(self, identity):
        state = self.start()
        self.client.force_login(self.user)
        self.callback(state)
        identity.assert_not_called()
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_oauth_start_requires_post_and_csrf_and_uses_fixed_callback(self):
        self.assertEqual(self.client.get(reverse("twitch_login")).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse("twitch_login")).status_code, 403)
        client.get(reverse("account_home"))
        response = client.post(
            reverse("twitch_login"),
            {
                "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
                "next": "https://attacker.example",
            },
        )
        params = parse_qs(urlsplit(response.url).query)
        self.assertEqual(
            params["redirect_uri"], ["https://fuinoise.example/auth/twitch/callback/"]
        )
        self.assertNotIn("client_secret", params)
        self.assertNotIn("next", params)

    def test_missing_provider_configuration_is_an_actionable_local_error(self):
        with override_settings(TWITCH_CLIENT_SECRET=""):
            response = self.client.post(reverse("twitch_login"), follow=True)
        self.assertContains(response, "Twitch connection is not configured yet")
        self.assertNotIn("oauth_twitch", self.client.session)

    @patch(
        "fuinoise_live.account_views.providers.twitch_identity",
        side_effect=providers.ProviderError(),
    )
    def test_provider_outage_never_creates_a_login(self, identity):
        state = self.start()
        response = self.callback(state)
        self.assertRedirects(response, reverse("account_home"))
        self.assertNotIn("_auth_user_id", self.client.session)

    @patch(
        "fuinoise_live.account_views.providers.discord_identity", return_value=DISCORD
    )
    def test_discord_links_only_current_authenticated_twitch_account(self, identity):
        response = self.client.post(reverse("discord_connect"))
        self.assertRedirects(response, reverse("account_home"))
        self.client.force_login(self.user)
        state = self.start("discord")
        self.callback(
            state,
            "discord",
            account_id="999999",
            discord_override="true",
            participation_status="approved",
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.discord_id, DISCORD.id)
        self.assertFalse(self.account.discord_override)
        self.assertEqual(self.account.participation_status, "pending")
        self.assertContains(self.client.get(reverse("account_home")), "music-discord")
        identity.assert_called_once()

    @patch("fuinoise_live.account_views.providers.discord_identity")
    def test_discord_callback_requires_same_user_that_started_it(self, identity):
        self.client.force_login(self.user)
        state = self.start("discord")
        other = get_user_model().objects.create_user(username="other")
        self.client.force_login(other)
        self.callback(state, "discord")
        identity.assert_not_called()
        self.account.refresh_from_db()
        self.assertIsNone(self.account.discord_id)

    def test_logout_and_disconnect_are_post_only(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("account_logout")).status_code, 405)
        self.assertEqual(
            self.client.get(reverse("discord_disconnect")).status_code, 405
        )
        self.client.post(reverse("account_logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_review_and_private_history_are_hidden_from_ordinary_users(self):
        organizer = get_user_model().objects.create_user(username="organizer")
        call_command("setup_organizers", username="organizer", stdout=io.StringIO())
        accounts.review_participation(
            self.account.pk,
            actor=organizer,
            expected_version=0,
            status="approved",
            reason="Private eligibility reason",
        )
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("eligibility_list")).status_code, 403)
        self.assertEqual(
            self.client.get(
                reverse("eligibility_detail", args=[self.account.pk])
            ).status_code,
            403,
        )
        self.assertNotContains(
            self.client.get(reverse("account_home")), "Private eligibility reason"
        )
        self.assertNotContains(
            self.client.get(reverse("current_events")), "music-discord"
        )
        self.client.force_login(organizer)
        self.assertContains(
            self.client.get(reverse("eligibility_detail", args=[self.account.pk])),
            "Private eligibility reason",
        )
        self.assertContains(self.client.get(reverse("eligibility_list")), "Musician")

    def test_separate_review_permission_cannot_grant_discord_override(self):
        reviewer = get_user_model().objects.create_user(username="reviewer")
        reviewer.user_permissions.add(
            Permission.objects.get(
                codename="review_eligibility", content_type__app_label="fuinoise_live"
            )
        )
        self.client.force_login(reviewer)
        url = reverse("eligibility_detail", args=[self.account.pk])
        self.assertNotContains(self.client.get(url), "Save Discord requirement")
        response = self.client.post(
            url,
            {
                "action": "override",
                "override-version": 0,
                "override-enabled": "on",
                "override-reason": "Unauthorized",
            },
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.post(
            url,
            {
                "action": "participation",
                "participation-version": 0,
                "participation-status": "approved",
                "participation-reason": "Active participant",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.assertEqual(self.account.participation_status, "approved")
        response = self.client.post(
            url,
            {
                "action": "participation",
                "participation-version": 0,
                "participation-status": "declined",
                "participation-reason": "Stale decision",
            },
        )
        self.assertEqual(response.status_code, 409)
        self.account.refresh_from_db()
        self.assertEqual(self.account.participation_status, "approved")


@override_settings(**CONFIG)
class ProviderTests(TestCase):
    @patch("fuinoise_live.providers.request_json")
    def test_twitch_validates_app_and_profile_identity_without_requesting_email(
        self, request
    ):
        token = {"access_token": "access", "token_type": "bearer"}
        verified = {
            "client_id": "twitch-client",
            "user_id": "1234",
            "expires_in": 300,
            "login": "musician",
        }
        profile = {
            "data": [{"id": "1234", "login": "musician", "display_name": "Musician"}]
        }
        request.side_effect = [token, verified, profile]
        self.assertEqual(providers.twitch_identity("code"), TWITCH)
        self.assertEqual(
            request.call_args_list[1].kwargs["headers"]["Authorization"], "OAuth access"
        )
        self.assertEqual(
            request.call_args_list[2].kwargs["headers"]["Client-Id"], "twitch-client"
        )
        params = parse_qs(
            urlsplit(providers.authorization_url("twitch", "state")).query,
            keep_blank_values=True,
        )
        self.assertEqual(params["scope"], [""])
        for changed in (
            dict(verified, client_id="wrong"),
            dict(verified, expires_in=0),
            dict(verified, user_id="wrong"),
        ):
            request.side_effect = [token, changed, profile]
            with self.assertRaises(providers.ProviderError):
                providers.twitch_identity("code")
        request.side_effect = [
            token,
            verified,
            {"data": [{"id": "5678", "login": "musician", "display_name": "Musician"}]},
        ]
        with self.assertRaises(providers.ProviderError):
            providers.twitch_identity("code")

    @patch("fuinoise_live.providers.request_json")
    def test_discord_scopes_and_screening_membership(self, request):
        token = {
            "access_token": "access",
            "token_type": "Bearer",
            "scope": "identify guilds.members.read",
        }
        profile = {"id": "5678", "username": "music-discord"}
        member = {"user": {"id": "5678"}, "pending": False}
        request.side_effect = [token, profile, member]
        self.assertEqual(providers.discord_identity("code"), DISCORD)
        request.side_effect = [token, profile, dict(member, pending=True)]
        self.assertFalse(providers.discord_identity("code").member)
        request.side_effect = [
            token,
            profile,
            providers.ProviderError(status=404, code=10007),
        ]
        self.assertFalse(providers.discord_identity("code").member)
        request.side_effect = [token, profile, providers.ProviderError(status=429)]
        self.assertIsNone(providers.discord_identity("code").member)
        request.side_effect = [dict(token, scope="identify")]
        with self.assertRaises(providers.ProviderError):
            providers.discord_identity("code")

    @patch("fuinoise_live.providers.request_json")
    def test_bot_membership_distinguishes_absent_member_from_invalid_configuration(
        self, request
    ):
        request.return_value = {"user": {"id": "5678"}, "pending": False}
        self.assertTrue(providers.discord_membership("5678"))
        self.assertEqual(
            request.call_args.args[0],
            "https://discord.com/api/v10/guilds/9999/members/5678",
        )
        self.assertEqual(
            request.call_args.kwargs["headers"]["Authorization"], "Bot bot-secret"
        )
        request.side_effect = providers.ProviderError(status=404, code=10004)
        with self.assertRaises(providers.ProviderError):
            providers.discord_membership("5678")
        request.side_effect = None
        request.return_value = {"user": {"id": "wrong"}}
        with self.assertRaises(providers.ProviderError):
            providers.discord_membership("5678")

    def test_origin_requires_https_except_loopback_development(self):
        for origin, debug in (
            ("https://user:password@example.com", True),
            ("https://example.com/subpath", True),
            ("http://example.com", True),
            ("http://localhost:8000", False),
        ):
            with override_settings(FUINOISE_ORIGIN=origin, DEBUG=debug):
                with self.assertRaises(providers.ProviderError):
                    providers.authorization_url("twitch", "state")
        with override_settings(FUINOISE_ORIGIN="http://localhost:8000", DEBUG=True):
            self.assertIn("localhost", providers.authorization_url("twitch", "state"))

    @patch("fuinoise_live.providers.request_json")
    def test_malformed_token_responses_fail_without_exposing_provider_data(
        self, request
    ):
        for payload in (
            {},
            {"access_token": 123, "token_type": "bearer"},
            {"access_token": "secret", "token_type": 123},
        ):
            request.return_value = payload
            with self.assertRaises(providers.ProviderError) as error:
                providers.exchange_code("twitch", "code")
            self.assertNotIn("secret", str(error.exception))

    @patch("fuinoise_live.providers.build_opener")
    def test_transport_bounds_timeout_payload_size_and_sanitizes_failures(self, opener):
        response = MagicMock()
        response.__enter__.return_value = response
        opener.return_value.open.return_value = response
        response.read.return_value = json.dumps({"value": True}).encode()
        self.assertEqual(providers.request_json("https://example.com"), {"value": True})
        self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 5)
        self.assertEqual(
            response.read.call_args.args, (providers.MAX_RESPONSE_BYTES + 1,)
        )
        for body in (b"bad json", b"[]", b" " * (providers.MAX_RESPONSE_BYTES + 1)):
            response.read.return_value = body
            with self.assertRaises(providers.ProviderError):
                providers.request_json("https://example.com")
        for error in (
            URLError("secret leaked upstream"),
            TimeoutError("secret"),
            HTTPError(
                "https://example.com", 429, "secret", {}, io.BytesIO(b'{"code":123}')
            ),
        ):
            opener.return_value.open.side_effect = error
            with self.assertRaises(providers.ProviderError) as raised:
                providers.request_json("https://example.com")
            self.assertNotIn("secret", str(raised.exception))
        self.assertEqual(raised.exception.status, 429)
        self.assertEqual(raised.exception.code, 123)
        self.assertIsNone(
            providers.NoRedirects().redirect_request(
                None, None, 302, "Found", {}, "https://other.example"
            )
        )
