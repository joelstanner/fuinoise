import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class EnvironmentSettingsTests(SimpleTestCase):
    def run_settings(self, dotenv=None, environment=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "fuinoise"
            package.mkdir()
            shutil.copyfile(
                settings.BASE_DIR / "fuinoise/settings.py", package / "settings.py"
            )
            if dotenv is not None:
                (root / ".env").write_text(dotenv)
            working = root / "working"
            working.mkdir()
            (working / ".env").write_text("TWITCH_CLIENT_ID=wrong-directory\n")
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("DJANGO_", "FUINOISE_", "TWITCH_", "DISCORD_"))
                and key != "PYTHON_DOTENV_DISABLED"
            }
            env.update(environment or {})
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import json, runpy, sys; s = runpy.run_path(sys.argv[1]); "
                    "print(json.dumps([s[k] for k in "
                    "['DEBUG', 'TWITCH_CLIENT_ID', 'TWITCH_CLIENT_SECRET', "
                    "'FUINOISE_ORIGIN']]))",
                    str(package / "settings.py"),
                ],
                cwd=working,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

    def test_project_root_dotenv_loads_before_settings_from_other_directory(self):
        self.assertEqual(
            self.run_settings(
                "# Test credentials\nTWITCH_CLIENT_ID=file-client\n"
                "TWITCH_CLIENT_SECRET='test secret # literal'\n"
                "FUINOISE_ORIGIN=http://127.0.0.1:8000\n"
            ),
            [True, "file-client", "test secret # literal", "http://127.0.0.1:8000"],
        )

    def test_existing_environment_including_empty_values_takes_precedence(self):
        self.assertEqual(
            self.run_settings(
                "TWITCH_CLIENT_ID=file-client\nTWITCH_CLIENT_SECRET=file-secret\n",
                {"TWITCH_CLIENT_ID": "environment-client", "TWITCH_CLIENT_SECRET": ""},
            ),
            [True, "environment-client", "", "http://localhost:8000"],
        )

    def test_missing_dotenv_preserves_local_defaults(self):
        self.assertEqual(self.run_settings(), [True, "", "", "http://localhost:8000"])
