import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Event


class AdminSmokeTests(TestCase):
    def test_event_change_page_renders(self):
        user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="test-password"
        )
        event = Event.objects.create(date=datetime.date(2026, 9, 23))

        self.client.force_login(user)
        response = self.client.get(
            reverse("admin:fuinoise_live_event_change", args=[event.pk])
        )

        self.assertEqual(response.status_code, 200)
