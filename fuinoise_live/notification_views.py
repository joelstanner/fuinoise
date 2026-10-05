from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .discord_delivery import retry_delivery, verify_receipt
from .models import DiscordDelivery, NotificationRead
from .notifications import is_organizer, visible_notifications


@never_cache
@login_required
@require_GET
def inbox(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    rows = visible_notifications(request.user).annotate(
        is_read=Exists(
            NotificationRead.objects.filter(
                notification_id=OuterRef("pk"), user=request.user
            )
        )
    )
    notice = get_object_or_404(rows, pk=pk) if pk is not None else None
    return render(
        request,
        "fuinoise_live/notifications.html",
        {
            "page": (
                [notice]
                if notice
                else Paginator(rows, 30).get_page(request.GET.get("page"))
            ),
            "detail": notice is not None,
            "unread_count": rows.filter(is_read=False).count(),
        },
    )


@never_cache
@login_required
@require_POST
def mark_read(request: HttpRequest, pk: int) -> HttpResponse:
    notice = get_object_or_404(visible_notifications(request.user), pk=pk)
    NotificationRead.objects.get_or_create(notification=notice, user=request.user)
    return redirect("notification_inbox")


def _organizer(request: HttpRequest) -> None:
    if not is_organizer(request.user):
        raise PermissionDenied("Organizer scheduling permissions are required.")


@never_cache
@login_required
@require_GET
def deliveries(request: HttpRequest) -> HttpResponse:
    _organizer(request)
    rows = DiscordDelivery.objects.select_related(
        "notification__recipient__streamer_account__streamer"
    ).order_by("-notification__created_at", "-pk")
    state = request.GET.get("status", "")
    if state in DiscordDelivery.Status.values:
        rows = rows.filter(status=state)
    return render(
        request,
        "fuinoise_live/deliveries.html",
        {
            "page": Paginator(rows, 30).get_page(request.GET.get("page")),
            "statuses": DiscordDelivery.Status.choices,
            "selected_status": state,
            "needs_attention": DiscordDelivery.objects.filter(
                status__in=("failed", "uncertain")
            ).count(),
        },
    )


@never_cache
@login_required
@require_POST
def delivery_action(request: HttpRequest, pk: int) -> HttpResponse:
    _organizer(request)
    get_object_or_404(DiscordDelivery, pk=pk)
    action = request.POST.get("action")
    try:
        if action == "retry":
            retry_delivery(pk, actor=request.user)
            messages.success(request, "Retry queued for the next delivery run.")
        elif action == "verify":
            verify_receipt(pk, request.POST.get("message_id", ""), actor=request.user)
            messages.success(request, "Discord message verified; delivery recorded.")
        else:
            return HttpResponse("Choose a delivery action.", status=400)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    return redirect("notification_deliveries")
