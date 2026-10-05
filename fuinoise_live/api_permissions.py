"""Organizer permissions kept independent of API view initialization."""

from typing import Any

from rest_framework.permissions import BasePermission


class OrganizerPermission(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        user = request.user
        return bool(
            user.is_authenticated
            and user.is_active
            and user.has_perm("fuinoise_live.change_event")
            and user.has_perm("fuinoise_live.change_raidslot")
        )
