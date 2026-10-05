import io
import json
from datetime import timedelta
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.test import TestCase, override_settings
from django.utils import timezone

from fuinoise_live.discord_delivery import (
    DeliveryError,
    discord_request,
    notification_content,
)
from fuinoise_live.models import DiscordDelivery, DiscordDispatchState, Notification


@override_settings(
    DISCORD_BOT_TOKEN="secret-bot-token", FUINOISE_ORIGIN="https://fuinoise.example"
)
class DiscordHTTPTests(TestCase):
    def response(self, raw, headers=None):
        response = io.BytesIO(raw)
        response.headers = headers or {}
        return response

    @patch("fuinoise_live.discord_delivery.build_opener")
    def test_json_bot_auth_fixed_endpoint_timeout_and_no_redirects(self, opener):
        opener.return_value.open.return_value = self.response(b'{"id":"123"}')
        self.assertEqual(
            discord_request(
                "/channels/456/messages", payload={"content": "Hello"}, sending=True
            ),
            {"id": "123"},
        )
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(
            request.full_url, "https://discord.com/api/v10/channels/456/messages"
        )
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bot secret-bot-token")
        self.assertEqual(json.loads(request.data), {"content": "Hello"})
        self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 5)
        self.assertEqual(type(opener.call_args.args[0]).__name__, "NoRedirects")

    @patch("fuinoise_live.discord_delivery.build_opener")
    def test_exhausted_bucket_persists_cooldown_before_next_request(self, opener):
        opener.return_value.open.return_value = self.response(
            b'{"id":"123"}',
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-After": "42.5"},
        )
        discord_request("/channels/456")
        self.assertGreater(
            DiscordDispatchState.objects.get(pk=1).pause_until,
            timezone.now() + timedelta(seconds=42),
        )
        with self.assertRaises(DeliveryError) as context:
            discord_request(
                "/channels/456/messages", payload={"content": "Hello"}, sending=True
            )
        self.assertFalse(context.exception.uncertain)
        self.assertGreater(context.exception.retry_after, 42)
        self.assertEqual(opener.return_value.open.call_count, 1)

    @patch("fuinoise_live.discord_delivery.build_opener")
    def test_429_uses_provider_wait_and_shared_pause(self, opener):
        opener.return_value.open.side_effect = HTTPError(
            "https://discord.com",
            429,
            "Rate limited",
            {"Retry-After": "2"},
            io.BytesIO(b'{"retry_after":123.5,"message":"private provider text"}'),
        )
        with self.assertRaises(DeliveryError) as context:
            discord_request("/channels/456/messages", payload={}, sending=True)
        self.assertFalse(context.exception.uncertain)
        self.assertEqual(context.exception.retry_after, 124.5)
        self.assertNotIn("private", str(context.exception))
        self.assertGreater(
            DiscordDispatchState.objects.get(pk=1).pause_until,
            timezone.now() + timedelta(seconds=123),
        )

    @patch("fuinoise_live.discord_delivery.build_opener")
    def test_malformed_rate_limit_falls_back_conservatively(self, opener):
        opener.return_value.open.side_effect = HTTPError(
            "https://discord.com",
            429,
            "Rate limited",
            {"Retry-After": "bad"},
            io.BytesIO(b"not json"),
        )
        with self.assertRaises(DeliveryError) as context:
            discord_request("/channels/456")
        self.assertEqual(context.exception.retry_after, 60)

    @patch("fuinoise_live.discord_delivery.build_opener")
    def test_permanent_error_is_definite_and_authorization_stops_more_requests(
        self, opener
    ):
        for status in [400, 403, 404, 401]:
            with self.subTest(status=status):
                opener.return_value.open.side_effect = HTTPError(
                    "https://discord.com",
                    status,
                    "secret-bot-token",
                    {},
                    io.BytesIO(b"{}"),
                )
                with self.assertRaises(DeliveryError) as context:
                    discord_request("/channels/456/messages", payload={}, sending=True)
                self.assertFalse(context.exception.uncertain)
                self.assertIsNone(context.exception.retry_after)
                self.assertNotIn("secret", str(context.exception))
        self.assertGreater(
            DiscordDispatchState.objects.get(pk=1).pause_until,
            timezone.now() + timedelta(minutes=59),
        )

    @patch("fuinoise_live.discord_delivery.build_opener")
    def test_server_errors_retry_preparation_but_hold_message_outcomes(self, opener):
        opener.return_value.open.side_effect = HTTPError(
            "https://discord.com", 503, "Unavailable", {}, io.BytesIO(b"{}")
        )
        with self.assertRaises(DeliveryError) as context:
            discord_request("/users/@me/channels", payload={"recipient_id": "456"})
        self.assertEqual(context.exception.retry_after, 60)
        self.assertFalse(context.exception.uncertain)
        with self.assertRaises(DeliveryError) as context:
            discord_request("/channels/456/messages", payload={}, sending=True)
        self.assertTrue(context.exception.uncertain)
        self.assertIsNone(context.exception.retry_after)

    @patch("fuinoise_live.discord_delivery.build_opener")
    def test_bad_network_or_receipt_is_uncertain_only_after_message_attempt(
        self, opener
    ):
        for sending in [False, True]:
            for error in [URLError("secret-bot-token"), OSError("secret-bot-token")]:
                with self.subTest(sending=sending, error=type(error)):
                    opener.return_value.open.side_effect = error
                    with self.assertRaises(DeliveryError) as context:
                        discord_request(
                            "/channels/456/messages", payload={}, sending=sending
                        )
                    self.assertEqual(context.exception.uncertain, sending)
                    self.assertNotIn("secret", str(context.exception))
            opener.return_value.open.side_effect = None
            for raw in [b"bad json", b"[]", b"x" * 65537]:
                opener.return_value.open.return_value = self.response(raw)
                with self.assertRaises(DeliveryError) as context:
                    discord_request(
                        "/channels/456/messages", payload={}, sending=sending
                    )
                self.assertEqual(context.exception.uncertain, sending)

    @override_settings(DISCORD_BOT_TOKEN="")
    @patch("fuinoise_live.discord_delivery.build_opener")
    def test_missing_token_fails_without_network(self, opener):
        with self.assertRaises(DeliveryError):
            discord_request("/users/@me")
        opener.assert_not_called()

    def test_content_escapes_markdown_is_bounded_and_uses_trusted_origin(self):
        notice = Notification.objects.create(
            key="test",
            kind="test",
            title="**Title**",
            body="@everyone <@123> `code`\x00" + "a" * 3000,
        )
        delivery = DiscordDelivery.objects.create(notification=notice, nonce="123")
        content = notification_content(delivery)
        self.assertIn(r"\*\*Title\*\*", content)
        self.assertNotIn("\x00", content)
        self.assertLessEqual(len(content), 2000)
        self.assertTrue(
            content.endswith(f"https://fuinoise.example/notifications/{notice.pk}/")
        )
        for origin in [
            "http://public.example",
            "https://user:pass@example.com",
            "https://example.com/path",
            "https://[invalid",
            "",
        ]:
            with (
                self.subTest(origin=origin),
                override_settings(FUINOISE_ORIGIN=origin),
                self.assertRaises(DeliveryError),
            ):
                notification_content(delivery)
        with override_settings(DEBUG=True, FUINOISE_ORIGIN="http://localhost:8000"):
            self.assertIn("localhost:8000", notification_content(delivery))
