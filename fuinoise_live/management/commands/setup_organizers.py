from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fuinoise_live.models import StreamerAccount


class Command(BaseCommand):
    help = "Grant Fuinoise organizer permissions to an existing account."

    def add_arguments(self, parser: Any) -> None:
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--twitch-id")
        target.add_argument("--username")

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        if options["twitch_id"]:
            account = (
                StreamerAccount.objects.select_related("user")
                .filter(streamer__twitch_id=options["twitch_id"])
                .first()
            )
            if account is None:
                raise CommandError(
                    "That Twitch account must sign in once before being "
                    "made an organizer."
                )
            user = account.user
        else:
            user = get_user_model().objects.filter(username=options["username"]).first()
            if user is None:
                raise CommandError("The specified user does not exist.")
        group, _ = Group.objects.get_or_create(name="Fuinoise organizers")
        codes = (
            "add_event",
            "change_event",
            "add_raidslot",
            "change_raidslot",
            "delete_raidslot",
            "review_eligibility",
            "override_discord_requirement",
        )
        permissions = Permission.objects.filter(
            content_type__app_label="fuinoise_live", codename__in=codes
        )
        if permissions.count() != len(codes):
            raise CommandError(
                "Run migrations before configuring organizer permissions."
            )
        group.permissions.set(permissions)
        user.groups.add(group)
        self.stdout.write(
            self.style.SUCCESS(
                "Organizer permissions granted. Staff and superuser access "
                "were not changed."
            )
        )
