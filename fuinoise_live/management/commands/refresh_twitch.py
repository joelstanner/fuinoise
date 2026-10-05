from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from fuinoise_live.models import Streamer
from fuinoise_live.twitch import refresh_streamers


class Command(BaseCommand):
    help = "Refresh stored Twitch profiles and live status without affecting schedules."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--event", type=int)
        parser.add_argument("--all", action="store_true", dest="all_streamers")
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args: Any, **options: Any) -> None:
        if not 1 <= options["limit"] <= 1000:
            raise CommandError("Choose a limit between 1 and 1000 streamers.")
        streamers = Streamer.objects.all()
        if options["event"]:
            streamers = streamers.filter(raidslot__event_id=options["event"])
        elif not options["all_streamers"]:
            # Refresh current/upcoming published schedules; historical profile
            # snapshots remain readable without repeatedly polling old lineups.
            streamers = streamers.filter(
                raidslot__event__publication_status="published"
            ).filter(
                Q(raidslot__event__date__gte=timezone.now().date())
                | Q(raidslot__start__gte=timezone.now() - timedelta(days=2))
            )
        ids = list(
            streamers.order_by("pk")
            .values_list("pk", flat=True)
            .distinct()[: options["limit"]]
        )
        updated, failed = refresh_streamers(ids)
        self.stdout.write(
            f"Updated {updated} Twitch profiles/statuses; {failed} unavailable."
        )
        if failed:
            raise CommandError(
                "Some Twitch information could not be refreshed. "
                "Stored schedules remain intact."
            )
