import secrets
import time
from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_GET, require_POST

from . import accounts, providers
from .models import StreamerAccount


def _account(request: HttpRequest) -> Any:
    if not request.user.is_authenticated:
        return None
    return (
        StreamerAccount.objects.select_related("streamer", "user")
        .filter(user=request.user)
        .first()
    )


@never_cache
@require_GET
def account_home(request: HttpRequest) -> HttpResponse:
    account = _account(request)
    from .notifications import visible_notifications
    from .request_views import dashboard_context

    return render(
        request,
        "fuinoise_live/account.html",
        {
            "account": account,
            "eligible": accounts.is_eligible(account) if account else False,
            "membership_current": (
                accounts.membership_current(account) if account else False
            ),
            "can_review": request.user.has_perm("fuinoise_live.review_eligibility")
            or request.user.has_perm("fuinoise_live.override_discord_requirement"),
            **(dashboard_context(account.streamer) if account else {}),
            "recent_notifications": visible_notifications(request.user)[:5],
        },
    )


@never_cache
@require_POST
def oauth_start(request: HttpRequest, provider: str) -> HttpResponse:
    if provider == "discord" and _account(request) is None:
        return redirect("account_home")
    if provider == "twitch" and request.user.is_authenticated:
        messages.error(request, "Sign out before using another Twitch account.")
        return redirect("account_home")
    state = secrets.token_urlsafe(32)
    try:
        url = providers.authorization_url(provider, state)
    except providers.ProviderError as error:
        messages.error(request, str(error))
        return redirect("account_home")
    request.session[f"oauth_{provider}"] = {
        "state": state,
        "created": time.time(),
        "user_id": request.user.pk,
    }
    return redirect(url)


@never_cache
@require_GET
@sensitive_variables()
def oauth_callback(request: HttpRequest, provider: str) -> HttpResponse:
    state = request.session.pop(f"oauth_{provider}", None)
    supplied = request.GET.get("state", "")
    if (
        not isinstance(state, dict)
        or not supplied
        or (
            not secrets.compare_digest(supplied, state["state"])
            or not 0 <= time.time() - state["created"] <= 600
            or state["user_id"] != request.user.pk
        )
    ):
        messages.error(
            request,
            "This connection attempt expired or could not be verified. "
            "Please start again.",
        )
        return redirect("account_home")
    if request.GET.get("error") or not request.GET.get("code"):
        messages.error(
            request, "Connection was canceled. You can try again when ready."
        )
        return redirect("account_home")
    try:
        if provider == "twitch":
            identity = providers.twitch_identity(request.GET["code"])
            account = accounts.account_for_twitch(identity)
            login(
                request,
                account.user,
                backend="django.contrib.auth.backends.ModelBackend",
            )
            messages.success(request, "Signed in with Twitch.")
        elif provider == "discord":
            account = _account(request)
            if account is None:
                raise PermissionDenied("Sign in with Twitch first.")
            discord = providers.discord_identity(request.GET["code"])
            accounts.link_discord(
                account.pk,
                actor=request.user,
                expected_version=account.version,
                identity=discord,
            )
            messages.success(
                request, "Discord connected. Your participation status is shown below."
            )
        else:
            raise providers.ProviderError("Unknown connection provider.")
    except providers.ProviderError as error:
        messages.error(request, str(error))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    except IntegrityError:
        messages.error(
            request,
            "This account could not be connected. Reload and try again, "
            "or ask an organizer to review the existing account.",
        )
    response = redirect("account_home")
    response["Referrer-Policy"] = "no-referrer"
    return response


class VersionForm(forms.Form):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


@login_required
@require_POST
def discord_disconnect(request: HttpRequest) -> HttpResponse:
    account = _account(request)
    form = VersionForm(request.POST)
    if account is None:
        raise PermissionDenied("A Twitch account is required.")
    if not form.is_valid():
        return HttpResponse("Reload the account page before disconnecting.", status=400)
    try:
        accounts.unlink_discord(
            account.pk,
            actor=request.user,
            expected_version=form.cleaned_data["version"],
        )
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(
            request,
            "Discord disconnected. Organizer approval and Discord overrides "
            "have been reset.",
        )
    return redirect("account_home")


@login_required
@require_POST
def discord_refresh(request: HttpRequest) -> HttpResponse:
    account = _account(request)
    if account is None:
        raise PermissionDenied("A Twitch account is required.")
    try:
        accounts.refresh_membership(account)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    return redirect("account_home")


@require_POST
def account_logout(request: HttpRequest) -> HttpResponse:
    logout(request)
    return redirect("current_events")


class ParticipationForm(VersionForm):
    status = forms.ChoiceField(choices=StreamerAccount.ParticipationStatus.choices)
    reason = forms.CharField(
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Private review reason",
    )


class OverrideForm(VersionForm):
    enabled = forms.BooleanField(required=False, label="Override Discord requirement")
    reason = forms.CharField(
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Private override reason",
    )


def _review_permission(request: HttpRequest) -> None:
    if (
        not request.user.is_authenticated
        or not request.user.is_active
        or not (
            request.user.has_perm("fuinoise_live.review_eligibility")
            or request.user.has_perm("fuinoise_live.override_discord_requirement")
        )
    ):
        raise PermissionDenied("Organizer permission is required.")


@never_cache
@require_GET
def eligibility_list(request: HttpRequest) -> HttpResponse:
    _review_permission(request)
    return render(
        request,
        "fuinoise_live/eligibility_list.html",
        {
            "accounts": StreamerAccount.objects.select_related(
                "streamer", "user"
            ).order_by("participation_status", "streamer__display_name"),
        },
    )


@never_cache
def eligibility_detail(request: HttpRequest, pk: int) -> HttpResponse:
    _review_permission(request)
    account = get_object_or_404(
        StreamerAccount.objects.select_related("streamer", "user"), pk=pk
    )
    participation = ParticipationForm(
        initial={"version": account.version, "status": account.participation_status},
        prefix="participation",
    )
    override = OverrideForm(
        initial={"version": account.version, "enabled": account.discord_override},
        prefix="override",
    )
    status = 200
    if request.method == "POST":
        action = request.POST.get("action")
        if action not in ("participation", "override"):
            return HttpResponse("Choose a review action.", status=400)
        form = (
            ParticipationForm(request.POST, prefix=action)
            if action == "participation"
            else OverrideForm(request.POST, prefix=action)
        )
        try:
            accounts._require_permission(
                request.user,
                (
                    "review_eligibility"
                    if action == "participation"
                    else "override_discord_requirement"
                ),
            )
            if form.is_valid():
                params = {
                    "actor": request.user,
                    "expected_version": form.cleaned_data["version"],
                    "reason": form.cleaned_data["reason"],
                }
                if action == "participation":
                    accounts.review_participation(
                        account.pk, status=form.cleaned_data["status"], **params
                    )
                else:
                    accounts.set_discord_override(
                        account.pk, enabled=form.cleaned_data["enabled"], **params
                    )
                messages.success(request, "Review saved.")
                return redirect("eligibility_detail", pk=account.pk)
        except ValidationError as error:
            form.add_error(None, error)
            status = 409
        if action == "participation":
            participation = form
        else:
            override = form
        if status == 200:
            status = 400
    elif request.method != "GET":
        return HttpResponse(status=405, headers={"Allow": "GET, POST"})
    return render(
        request,
        "fuinoise_live/eligibility_detail.html",
        {
            "account": account,
            "participation_form": participation,
            "override_form": override,
            "eligible": accounts.is_eligible(account),
            "membership_current": accounts.membership_current(account),
            "reviews": account.reviews.select_related(
                "actor__streamer_account__streamer"
            ).all()[:50],
        },
        status=status,
    )
