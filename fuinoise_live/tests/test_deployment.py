import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.checks import Warning
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.test import SimpleTestCase, TestCase, override_settings


class HealthTests(TestCase):
    def test_health_is_public_uncached_and_read_only(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(self.client.post("/health/").status_code, 405)

    def test_database_failure_returns_generic_503(self):
        with patch(
            "fuinoise_live.health.Event.objects.exists",
            side_effect=OperationalError("secret details"),
        ):
            response = self.client.get("/health/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotContains(response, "secret", status_code=503)

    def test_missing_file_is_unavailable_without_creating_a_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "absent.sqlite3"
            with patch.object(settings, "DATABASES", {"default": {"NAME": database}}):
                response = self.client.get("/health/")
            self.assertEqual(response.status_code, 503)
            self.assertFalse(database.exists())


class ProductionSettingsTests(SimpleTestCase):
    def run_settings(self, changes=None):
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("DJANGO_", "FUINOISE_"))
        }
        env.update(
            PYTHON_DOTENV_DISABLED="1",
            DJANGO_DEBUG="0",
            DJANGO_SECRET_KEY="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            DJANGO_ALLOWED_HOSTS="fuinoise.example",
            FUINOISE_ORIGIN="https://fuinoise.example",
            FUINOISE_DATABASE_PATH="/tmp/fuinoise-data/test.sqlite3",
            FUINOISE_STATIC_ROOT="/tmp/fuinoise-static",
            DJANGO_TRUST_PROXY="1",
        )
        env.update(changes or {})
        code = (
            "import json; from fuinoise import settings as s; "
            "print(json.dumps([s.DEBUG, s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, "
            "s.SECURE_SSL_REDIRECT, 'debug_toolbar' in s.INSTALLED_APPS, "
            "getattr(s, 'SECURE_PROXY_SSL_HEADER', None)]))"
        )
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=settings.BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_secure_settings_and_no_debug_toolbar(self):
        result = self.run_settings()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            [False, True, True, True, False, ["HTTP_X_FORWARDED_PROTO", "https"]],
        )
        result = self.run_settings({"DJANGO_TRUST_PROXY": "0"})
        self.assertIsNone(json.loads(result.stdout)[-1])

    def test_invalid_production_configuration_fails_early(self):
        for changes in (
            {"DJANGO_SECRET_KEY": ""},
            {"DJANGO_SECRET_KEY": "x" * 60},
            {"DJANGO_ALLOWED_HOSTS": "*"},
            {"DJANGO_ALLOWED_HOSTS": ".example"},
            {"DJANGO_ALLOWED_HOSTS": "elsewhere.example"},
            {"FUINOISE_ORIGIN": "http://fuinoise.example"},
            {"FUINOISE_ORIGIN": "https://fuinoise.example/path"},
            {"FUINOISE_ORIGIN": "https://[broken"},
            {"FUINOISE_ORIGIN": "https://fuinoise.example:broken"},
            {"FUINOISE_DATABASE_PATH": "relative.sqlite3"},
            {"FUINOISE_STATIC_ROOT": "relative"},
        ):
            with self.subTest(changes=changes):
                result = self.run_settings(changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ImproperlyConfigured", result.stderr)


class ReleaseCommandTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "persistent.sqlite3"
        self.database.touch()
        self.assets = self.root / "static" / "fuinoise_live" / "organizer"
        self.assets.mkdir(parents=True)
        for name in ("workspace.js", "workspace.css"):
            (self.assets / name).write_text("built asset")
        self.settings = override_settings(
            DEBUG=False,
            STATIC_ROOT=self.root / "static",
        )
        self.database_settings = patch.object(
            settings, "DATABASES", {"default": {"NAME": self.database}}
        )
        self.database_settings.start()
        self.addCleanup(self.database_settings.stop)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.checks = patch(
            "fuinoise_live.management.commands.check_release.run_checks",
            return_value=[],
        )
        self.checks.start()
        self.addCleanup(self.checks.stop)
        self.executor = patch(
            "fuinoise_live.management.commands.check_release.MigrationExecutor"
        )
        self.migrations = self.executor.start().return_value
        self.migrations.migration_plan.return_value = []
        self.addCleanup(self.executor.stop)

    def test_release_passes_with_database_migrations_and_built_assets(self):
        out = io.StringIO()
        call_command("check_release", stdout=out)
        self.assertIn("Release checks passed", out.getvalue())

    def test_missing_database_is_not_created_and_assets_are_required(self):
        self.database.unlink()
        (self.assets / "workspace.js").unlink()
        with self.assertRaises(CommandError) as error:
            call_command("check_release")
        self.assertIn("migrate it first", str(error.exception))
        self.assertIn("workspace.js", str(error.exception))
        self.assertFalse(self.database.exists())

    def test_pending_migrations_and_unreadable_database_block_release(self):
        self.migrations.migration_plan.return_value = ["pending"]
        with self.assertRaisesMessage(CommandError, "pending migrations"):
            call_command("check_release")
        self.migrations.migration_plan.side_effect = OperationalError("private path")
        with self.assertRaisesMessage(CommandError, "cannot be read"):
            call_command("check_release")

    def test_release_database_inside_checkout_is_rejected(self):
        with override_settings(BASE_DIR=self.root):
            with self.assertRaisesMessage(
                CommandError, "outside the application release"
            ):
                call_command("check_release")

    def test_only_documented_hsts_warnings_are_accepted(self):
        warnings = [
            Warning("host-only", id="security.W005"),
            Warning("no preload", id="security.W021"),
        ]
        with patch(
            "fuinoise_live.management.commands.check_release.run_checks",
            return_value=warnings,
        ):
            out = io.StringIO()
            call_command("check_release", stdout=out)
            self.assertIn("Intentional security.W005", out.getvalue())
        with patch(
            "fuinoise_live.management.commands.check_release.run_checks",
            return_value=[Warning("insecure", id="security.W008")],
        ):
            with self.assertRaisesMessage(CommandError, "security.W008"):
                call_command("check_release")

    @override_settings(TWITCH_CLIENT_ID="", DISCORD_BOT_TOKEN="")
    def test_live_preflight_requires_credentials_without_printing_values(self):
        with self.assertRaises(CommandError) as error:
            call_command("check_release", require_integrations=True)
        self.assertIn("Configure TWITCH_CLIENT_ID", str(error.exception))
        self.assertIn("Configure DISCORD_BOT_TOKEN", str(error.exception))

    @override_settings(DEBUG=True)
    def test_debug_is_not_release_ready(self):
        with self.assertRaisesMessage(CommandError, "DJANGO_DEBUG=0"):
            call_command("check_release")
