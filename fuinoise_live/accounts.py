"""Verified account association and organizer-controlled participation rules."""

import secrets
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import EligibilityReview, Streamer, StreamerAccount
from .providers import (
    DiscordIdentity,
    ProviderError,
    TwitchIdentity,
    discord_membership,
)


class AccountConflict(ValidationError):
    pass


@transaction.atomic
def account_for_twitch(identity: TwitchIdentity) -> StreamerAccount:
    streamer = Streamer.objects.filter(twitch_id=identity.id).first()
    if streamer is None:
        # Twitch names can be reused. A legacy name alone is not proof that a
        # returning Twitch account owns that musician's history.
        if Streamer.objects.filter(twitch_username__iexact=identity.login).exists():
            raise AccountConflict(
                "An organizer needs to verify your existing streamer record "
                "before sign-in."
            )
        display = identity.display_name
        if Streamer.objects.filter(display_name=display).exists():
            display = f"{display[:40]} ({identity.id})"
        if Streamer.objects.filter(display_name=display).exists():
            display = f"{identity.login} ({secrets.token_hex(8)})"
        streamer = Streamer.objects.create(
            display_name=display,
            twitch_username=identity.login,
            twitch_display_name=identity.display_name,
            twitch_id=identity.id,
        )
    else:
        Streamer.objects.filter(pk=streamer.pk).update(twitch_id=F("twitch_id"))
        if (
            Streamer.objects.filter(twitch_username__iexact=identity.login)
            .exclude(pk=streamer.pk)
            .exists()
        ):
            raise AccountConflict(
                "An organizer needs to resolve a Twitch name conflict before sign-in."
            )
    account: StreamerAccount | None = (
        StreamerAccount.objects.select_related("user", "streamer")
        .filter(streamer=streamer)
        .first()
    )
    if account is None:
        user = get_user_model().objects.create_user(
            username=f"twitch_{secrets.token_hex(16)}", password=None
        )
        account = StreamerAccount.objects.create(user=user, streamer=streamer)
    if not account.user.is_active:
        raise PermissionDenied("This Fuinoise account is inactive.")
    # Preserve the organizer-chosen public name and all existing relationships.
    streamer.twitch_username = identity.login
    streamer.twitch_display_name = identity.display_name
    streamer.save(update_fields=("twitch_username", "twitch_display_name"))
    return account


def _require_permission(actor: Any, permission: str) -> None:
    if (
        not actor.is_authenticated
        or not actor.is_active
        or not actor.has_perm(f"fuinoise_live.{permission}")
    ):
        raise PermissionDenied("Organizer permission is required.")


def _owned_account(account_id: int, actor: Any) -> StreamerAccount:
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied("Sign in with Twitch first.")
    account: StreamerAccount = StreamerAccount.objects.get(pk=account_id)
    if account.user_id != actor.pk:
        raise PermissionDenied("You can only change your own connected accounts.")
    return account


def _claim(account: StreamerAccount, expected_version: int) -> None:
    if not StreamerAccount.objects.filter(
        pk=account.pk, version=expected_version
    ).update(version=F("version") + 1):
        raise AccountConflict("This account has newer changes. Reload before saving.")
    account.version = expected_version + 1


def _record(
    account: StreamerAccount, actor: Any, action: str, reason: str = ""
) -> None:
    EligibilityReview.objects.create(
        account=account,
        actor=actor,
        action=action,
        participation_status=account.participation_status,
        discord_override=account.discord_override,
        reason=reason,
    )


@transaction.atomic
def link_discord(
    account_id: int, *, actor: Any, expected_version: int, identity: DiscordIdentity
) -> StreamerAccount:
    account = _owned_account(account_id, actor)
    _claim(account, expected_version)
    if account.discord_id and account.discord_id != identity.id:
        raise AccountConflict(
            "Disconnect the previous Discord account before linking a different one."
        )
    if (
        StreamerAccount.objects.filter(discord_id=identity.id)
        .exclude(pk=account.pk)
        .exists()
    ):
        raise AccountConflict(
            "This Discord account is already connected to another Twitch account."
        )
    account.discord_id = identity.id
    account.discord_username = identity.username
    account.discord_member = identity.member
    account.discord_guild_id = identity.guild
    account.discord_checked_at = timezone.now()
    account.full_clean()
    account.save()
    _record(account, actor, "discord_linked")
    return account


@transaction.atomic
def unlink_discord(
    account_id: int, *, actor: Any, expected_version: int
) -> StreamerAccount:
    account = _owned_account(account_id, actor)
    _claim(account, expected_version)
    account.discord_id = None
    account.discord_username = ""
    account.discord_member = None
    account.discord_guild_id = ""
    account.discord_checked_at = None
    account.participation_status = StreamerAccount.ParticipationStatus.PENDING
    account.discord_override = False
    account.save()
    _record(
        account,
        actor,
        "discord_unlinked",
        "Participation requires a new organizer review after disconnection.",
    )
    return account


@transaction.atomic
def review_participation(
    account_id: int, *, actor: Any, expected_version: int, status: str, reason: str
) -> StreamerAccount:
    _require_permission(actor, "review_eligibility")
    if status not in StreamerAccount.ParticipationStatus.values or not reason.strip():
        raise ValidationError("Choose a participation decision and record a reason.")
    account: StreamerAccount = StreamerAccount.objects.get(pk=account_id)
    _claim(account, expected_version)
    account.participation_status = status
    account.save(update_fields=("participation_status", "version"))
    _record(account, actor, "participation_review", reason.strip())
    return account


@transaction.atomic
def set_discord_override(
    account_id: int, *, actor: Any, expected_version: int, enabled: bool, reason: str
) -> StreamerAccount:
    _require_permission(actor, "override_discord_requirement")
    if type(enabled) is not bool or not reason.strip():
        raise ValidationError("Record a reason when changing the Discord requirement.")
    account: StreamerAccount = StreamerAccount.objects.get(pk=account_id)
    _claim(account, expected_version)
    account.discord_override = enabled
    account.save(update_fields=("discord_override", "version"))
    _record(account, actor, "discord_override", reason.strip())
    return account


def membership_current(account: StreamerAccount) -> bool:
    checked = account.discord_checked_at
    now = timezone.now()
    return bool(
        account.discord_id
        and account.discord_member is True
        and checked
        and account.discord_guild_id == settings.DISCORD_GUILD_ID
        and checked <= now
        and checked
        >= now - timedelta(seconds=settings.DISCORD_MEMBERSHIP_MAX_AGE_SECONDS)
    )


def is_eligible(account: StreamerAccount) -> bool:
    return bool(
        account.user.is_active
        and account.streamer.twitch_id
        and account.participation_status == StreamerAccount.ParticipationStatus.APPROVED
        and (account.discord_override or membership_current(account))
    )


def refresh_membership(account: StreamerAccount) -> StreamerAccount:
    member: bool | None = None
    if account.discord_id:
        try:
            member = discord_membership(account.discord_id)
        except ProviderError:
            pass
    with transaction.atomic():
        _claim(account, account.version)
        account.discord_member = member
        account.discord_guild_id = settings.DISCORD_GUILD_ID
        account.discord_checked_at = timezone.now()
        account.save(
            update_fields=(
                "discord_member",
                "discord_guild_id",
                "discord_checked_at",
                "version",
            )
        )
    return account


def require_eligible_streamer(actor: Any) -> Streamer:
    """Use before accepting requests; membership is rechecked at submission."""
    if not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied("Sign in with Twitch before requesting slots.")
    account: StreamerAccount | None = (
        StreamerAccount.objects.select_related("user", "streamer")
        .filter(user=actor)
        .first()
    )
    if account is None:
        raise PermissionDenied("A verified Twitch account is required.")
    if not account.discord_override:
        account = refresh_membership(account)
    if not is_eligible(account):
        raise ValidationError(
            "Slot requests require organizer approval and verified Discord "
            "membership, or an organizer's Discord override."
        )
    streamer: Streamer = account.streamer
    return streamer
