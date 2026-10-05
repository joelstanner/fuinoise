from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from fuinoise_live.database_backups import BackupError, verify_snapshot


class Command(BaseCommand):
    help = "Verify a snapshot checksum, schema, row counts, and SQLite integrity."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("snapshot", type=Path)

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            data = verify_snapshot(options["snapshot"])
        except BackupError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(
            f"Backup verified: {len(data['tables'])} tables; SHA-256 {data['sha256']}."
        )
