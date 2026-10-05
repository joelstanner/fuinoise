"""
URL configuration for fuinoise project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from fuinoise_live import (
    account_views,
    health,
    notification_views,
    organizer_api,
    request_views,
    views,
)

urlpatterns = [
    path("health/", health.health, name="health"),
    path("notifications/", notification_views.inbox, name="notification_inbox"),
    path(
        "notifications/<int:pk>/", notification_views.inbox, name="notification_detail"
    ),
    path(
        "notifications/<int:pk>/read/",
        notification_views.mark_read,
        name="notification_read",
    ),
    path(
        "organizer/notifications/",
        notification_views.deliveries,
        name="notification_deliveries",
    ),
    path(
        "organizer/notifications/<int:pk>/",
        notification_views.delivery_action,
        name="notification_delivery_action",
    ),
    path(
        "organizer/api/events/<int:pk>/import/preview/",
        organizer_api.import_preview_api,
        name="organizer_import_preview_api",
    ),
    path(
        "organizer/api/events/<int:pk>/import/apply/",
        organizer_api.import_apply_api,
        name="organizer_import_apply_api",
    ),
    path(
        "organizer/api/events/<int:pk>/twitch/lookup/",
        organizer_api.twitch_lookup_api,
        name="organizer_twitch_lookup_api",
    ),
    path(
        "organizer/api/events/<int:pk>/twitch/refresh/",
        organizer_api.twitch_refresh_api,
        name="organizer_twitch_refresh_api",
    ),
    path(
        "organizer/api/events/<int:pk>/signup/",
        organizer_api.draft_action_api,
        {"action": "signup"},
        name="organizer_signup_api",
    ),
    path("organizer/", organizer_api.workspace, name="organizer_workspace"),
    path(
        "organizer/api/events/", organizer_api.events_api, name="organizer_events_api"
    ),
    path(
        "organizer/api/events/<int:pk>/draft/",
        organizer_api.draft_api,
        name="organizer_draft_api",
    ),
    path(
        "organizer/api/events/<int:pk>/publish/",
        organizer_api.draft_action_api,
        {"action": "publish"},
        name="organizer_publish_api",
    ),
    path(
        "organizer/api/events/<int:pk>/reset/",
        organizer_api.draft_action_api,
        {"action": "reset"},
        name="organizer_reset_api",
    ),
    path(
        "organizer/api/events/<int:pk>/assign/",
        organizer_api.assignment_api,
        name="organizer_assign_api",
    ),
    path(
        "organizer/api/events/<int:pk>/requests/<int:request_id>/decline/",
        organizer_api.decline_api,
        name="organizer_decline_api",
    ),
    path(
        "organizer/api/events/<int:pk>/visibility/",
        organizer_api.visibility_api,
        name="organizer_visibility_api",
    ),
    path("", views.current_events, name="current_events"),
    path("upcoming/", views.upcoming_events, name="upcoming_events"),
    path("history/", views.historical_events, name="historical_events"),
    path("events/<int:pk>/", views.event_detail, name="event_detail"),
    path("account/", account_views.account_home, name="account_home"),
    path("events/<int:pk>/signup/", request_views.event_signup, name="event_signup"),
    path(
        "account/requests/<int:pk>/withdraw/",
        request_views.withdraw_request,
        name="withdraw_request",
    ),
    path(
        "account/performances/<int:pk>/cancel/",
        request_views.cancel_performance,
        name="cancel_performance",
    ),
    path("account/logout/", account_views.account_logout, name="account_logout"),
    path(
        "account/discord/disconnect/",
        account_views.discord_disconnect,
        name="discord_disconnect",
    ),
    path(
        "account/discord/refresh/",
        account_views.discord_refresh,
        name="discord_refresh",
    ),
    path(
        "auth/twitch/start/",
        account_views.oauth_start,
        {"provider": "twitch"},
        name="twitch_login",
    ),
    path(
        "auth/twitch/callback/",
        account_views.oauth_callback,
        {"provider": "twitch"},
        name="twitch_callback",
    ),
    path(
        "auth/discord/start/",
        account_views.oauth_start,
        {"provider": "discord"},
        name="discord_connect",
    ),
    path(
        "auth/discord/callback/",
        account_views.oauth_callback,
        {"provider": "discord"},
        name="discord_callback",
    ),
    path(
        "organizer/eligibility/",
        account_views.eligibility_list,
        name="eligibility_list",
    ),
    path(
        "organizer/eligibility/<int:pk>/",
        account_views.eligibility_detail,
        name="eligibility_detail",
    ),
    path("admin/", admin.site.urls),
]
if settings.DEBUG:
    urlpatterns.append(path("__debug__/", include("debug_toolbar.urls")))
