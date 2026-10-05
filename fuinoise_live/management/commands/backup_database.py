from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from fuinoise_live.database_backups import BackupError, snapshot_database


class Command(BaseCommand):
    help = (
        "Create and verify a private SQLite snapshot without changing source records."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--output-dir", required=True, type=Path)
        parser.add_argument("--label", default="")

    def handle(self, *args: Any, **options: Any) -> None:
        database = settings.DATABASES["default"]
        if (
            database["ENGINE"] != "django.db.backends.sqlite3"
            or str(database["NAME"]) == ":memory:"
            or str(database["NAME"]).startswith("file:")
        ):
            raise CommandError("Backups require a file-backed SQLite database.")
        try:
            path = snapshot_database(
                Path(database["NAME"]), options["output_dir"], label=options["label"]
            )
        except (BackupError, OSError) as error:
            raise CommandError(str(error)) from None
        self.stdout.write(f"Verified snapshot: {path}")
