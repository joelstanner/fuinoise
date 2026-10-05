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

from django.contrib import admin
from django.urls import include, path

from fuinoise_live import account_views, request_views, views

urlpatterns = [
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
    path("__debug__/", include("debug_toolbar.urls")),
]
