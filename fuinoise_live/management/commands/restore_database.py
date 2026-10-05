from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from fuinoise_live.database_backups import BackupError, restore_snapshot


class Command(BaseCommand):
    help = "Restore a verified backup into a NEW file; hold old unsent Discord alerts."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("snapshot", type=Path)
        parser.add_argument("--destination", required=True, type=Path)

    def handle(self, *args: Any, **options: Any) -> None:
        destination = options["destination"]
        if (
            destination.resolve()
            == Path(settings.DATABASES["default"]["NAME"]).resolve()
        ):
            raise CommandError(
                "Choose a separate restore destination, not the configured database."
            )
        try:
            held = restore_snapshot(options["snapshot"], destination)
        except (BackupError, OSError) as error:
            raise CommandError(str(error)) from None
        self.stdout.write(
            f"Restored new database: {destination.absolute()}. "
            f"{held} historical Discord deliveries held for verification."
        )
