import datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PositionMigrationTests(TransactionTestCase):
    def test_backfill_uses_start_then_id_within_each_event(self):
        executor = MigrationExecutor(connection)
        before = [("fuinoise_live", "0002_alter_event_event_time_zone")]
        after = [
            (
                "fuinoise_live",
                "0008_alter_raidslot_streamer",
            )
        ]
        executor.migrate(before)
        old_apps = executor.loader.project_state(before).apps
        OldEvent = old_apps.get_model("fuinoise_live", "Event")
        OldStreamer = old_apps.get_model("fuinoise_live", "Streamer")
        OldSlot = old_apps.get_model("fuinoise_live", "RaidSlot")
        event = OldEvent.objects.create(date=datetime.date(2026, 9, 23))
        other = OldEvent.objects.create(date=datetime.date(2026, 9, 24))
        streamer = OldStreamer.objects.create(
            display_name="First",
            twitch_username="First",
            twitch_url="https://twitch.tv/first",
            twitch_id=123456789,
        )
        late = datetime.datetime(2026, 9, 23, 21, tzinfo=datetime.timezone.utc)
        early = datetime.datetime(2026, 9, 23, 20, tzinfo=datetime.timezone.utc)
        first = OldSlot.objects.create(event=event, streamer=streamer, start=late)
        second = OldSlot.objects.create(event=event, streamer=streamer, start=early)
        third = OldSlot.objects.create(event=event, streamer=streamer, start=early)
        fourth = OldSlot.objects.create(event=other, streamer=streamer, start=late)

        executor = MigrationExecutor(connection)
        executor.migrate(after)
        NewSlot = executor.loader.project_state(after).apps.get_model(
            "fuinoise_live", "RaidSlot"
        )
        self.assertEqual(
            list(
                NewSlot.objects.filter(event_id=event.pk).values_list("id", "position")
            ),
            [(second.pk, 1), (third.pk, 2), (first.pk, 3)],
        )
        self.assertEqual(NewSlot.objects.get(pk=fourth.pk).position, 1)
        NewStreamer = executor.loader.project_state(after).apps.get_model(
            "fuinoise_live", "Streamer"
        )
        migrated_streamer = NewStreamer.objects.get(pk=streamer.pk)
        self.assertEqual(migrated_streamer.twitch_id, "123456789")
        self.assertEqual(migrated_streamer.twitch_username, "first")
        NewEvent = executor.loader.project_state(after).apps.get_model(
            "fuinoise_live", "Event"
        )
        self.assertEqual(NewEvent.objects.get(pk=event.pk).community.slug, "fuinoise")
        self.assertEqual(NewEvent.objects.get(pk=other.pk).community.slug, "fuinoise")
        self.assertEqual(NewEvent.objects.get(pk=event.pk).publication_status, "draft")
