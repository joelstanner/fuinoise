from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.checks import ERROR, WARNING, run_checks
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = (
        "Check production security, persistent database, migrations and built assets."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--require-integrations", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        problems = []
        if settings.DEBUG:
            problems.append("Set DJANGO_DEBUG=0 for release checks.")
        # Host-only HSTS with a short initial lifetime is deliberate. Domain-wide
        # HSTS and browser preload need a separate domain ownership decision.
        accepted = {"security.W005", "security.W021"}
        for issue in run_checks(include_deployment_checks=True):
            if issue.level >= ERROR or (
                issue.level >= WARNING and issue.id not in accepted
            ):
                problems.append(f"{issue.id}: {issue.msg}")
            elif issue.id in accepted:
                self.stdout.write(
                    f"Intentional {issue.id}: host-only HSTS; no preload."
                )
        database = Path(settings.DATABASES["default"]["NAME"])
        if not database.is_file():
            problems.append(
                "The configured persistent database does not exist; migrate it first."
            )
        elif database.resolve().is_relative_to(settings.BASE_DIR.resolve()):
            problems.append(
                "Keep the database outside the application release directory."
            )
        else:
            try:
                executor = MigrationExecutor(connection)
                if executor.migration_plan(executor.loader.graph.leaf_nodes()):
                    problems.append(
                        "Apply all pending migrations before starting this release."
                    )
            except DatabaseError:
                problems.append("The configured database cannot be read.")
        for name in ("workspace.js", "workspace.css"):
            asset = Path(settings.STATIC_ROOT) / "fuinoise_live" / "organizer" / name
            if not asset.is_file() or not asset.stat().st_size:
                problems.append(
                    f"Build the frontend and collect static files: missing {name}."
                )
        if options["require_integrations"]:
            for name in (
                "TWITCH_CLIENT_ID",
                "TWITCH_CLIENT_SECRET",
                "DISCORD_CLIENT_ID",
                "DISCORD_CLIENT_SECRET",
                "DISCORD_GUILD_ID",
                "DISCORD_BOT_TOKEN",
                "DISCORD_ORGANIZER_CHANNEL_ID",
            ):
                if not getattr(settings, name):
                    problems.append(f"Configure {name} for live integration rehearsal.")
        if problems:
            raise CommandError("Release checks failed:\n" + "\n".join(problems))
        self.stdout.write(
            "Release checks passed. Provider permissions and HTTPS still "
            "require hosted rehearsal."
        )
