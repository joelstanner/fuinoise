from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from fuinoise_live.discord_delivery import deliver_pending
from fuinoise_live.models import DiscordDelivery


class Command(BaseCommand):
    help = "Deliver due Discord notifications from the durable outbox."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            counts = deliver_pending(limit=options["limit"])
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from None
        self.stdout.write("; ".join(f"{key}: {value}" for key, value in counts.items()))
        if DiscordDelivery.objects.filter(status__in=("failed", "uncertain")).exists():
            raise CommandError(
                "Some Discord deliveries need organizer attention. "
                "On-site notifications remain available."
            )
