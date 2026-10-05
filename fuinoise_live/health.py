"""Small public readiness probe; detailed diagnostics stay in operator commands."""

from pathlib import Path

from django.conf import settings
from django.db import DatabaseError
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from fuinoise_live.models import DiscordDelivery, Event


@require_GET
def health(request: HttpRequest) -> JsonResponse:
    try:
        name = str(settings.DATABASES["default"]["NAME"])
        if not name.startswith((":memory:", "file:")) and not Path(name).is_file():
            ready = False
        else:
            Event.objects.exists()
            DiscordDelivery.objects.exists()
            ready = True
    except DatabaseError:
        ready = False
    response = JsonResponse(
        {"status": "ok" if ready else "unavailable"}, status=200 if ready else 503
    )
    response["Cache-Control"] = "no-store"
    return response
