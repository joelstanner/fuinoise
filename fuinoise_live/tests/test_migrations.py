import datetime
import hashlib
import json

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from fuinoise_live.models import Event as LiveEvent
from fuinoise_live.scheduling import _fingerprint


class MigrationTestCase(TransactionTestCase):
    def tearDown(self):
        # Historical fixtures intentionally include duplicate starts that newer
        # constraints reject. Clear only the test database before restoring it.
        call_command("flush", interactive=False, verbosity=0)
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()


class PositionMigrationTests(MigrationTestCase):
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


class SchedulingMigrationTests(MigrationTestCase):
    def test_existing_schedules_preserve_data_and_leave_duration_unknown(self):
        executor = MigrationExecutor(connection)
        before = [("fuinoise_live", "0012_alter_raidslot_options_and_more")]
        after = [("fuinoise_live", "0013_draftslot_scheduledraft_and_more")]
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        Community = apps.get_model("fuinoise_live", "Community")
        Event = apps.get_model("fuinoise_live", "Event")
        Streamer = apps.get_model("fuinoise_live", "Streamer")
        Slot = apps.get_model("fuinoise_live", "RaidSlot")
        community = Community.objects.create(name="Music", slug="music")
        streamer = Streamer.objects.create(
            display_name="Music", twitch_username="music"
        )
        event = Event.objects.create(
            date=datetime.date(2026, 10, 4),
            name="Existing event",
            community=community,
            publication_status="published",
            event_time_zone="America/Los_Angeles",
            description="Existing description",
            organizer_notes="Private notes",
        )
        start = datetime.datetime(2026, 10, 4, 20, tzinfo=datetime.timezone.utc)
        for offset, musician in ((0, streamer), (30, None)):
            Slot.objects.create(
                event=event,
                streamer=musician,
                start=start + datetime.timedelta(minutes=offset),
                raid_slot_note="Existing note",
                replay_url="https://example.com/replay",
            )
        fields = ("id", "streamer_id", "start", "raid_slot_note", "replay_url")
        snapshot = list(Slot.objects.filter(event=event).values_list(*fields))
        event_fields = (
            "name",
            "publication_status",
            "date",
            "community_id",
            "event_time_zone",
            "description",
            "organizer_notes",
        )
        event_snapshot = Event.objects.values_list(*event_fields).get(pk=event.pk)
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        NewEvent = apps.get_model("fuinoise_live", "Event")
        NewSlot = apps.get_model("fuinoise_live", "RaidSlot")
        self.assertEqual(
            list(NewSlot.objects.filter(event_id=event.pk).values_list(*fields)),
            snapshot,
        )
        self.assertEqual(
            NewEvent.objects.values_list(*event_fields).get(pk=event.pk), event_snapshot
        )
        self.assertEqual(
            list(NewSlot.objects.values_list("duration_minutes", flat=True)),
            [None, None],
        )
        migrated = NewEvent.objects.get(pk=event.pk)
        self.assertEqual(migrated.default_slot_duration_minutes, 60)
        self.assertEqual(migrated.schedule_version, 0)
        self.assertFalse(migrated.signup_before_publication)
        self.assertFalse(
            apps.get_model("fuinoise_live", "ScheduleDraft").objects.exists()
        )


class AccountMigrationTests(MigrationTestCase):
    def test_account_migration_preserves_musicians_without_inventing_ownership(self):
        executor = MigrationExecutor(connection)
        before = [("fuinoise_live", "0013_draftslot_scheduledraft_and_more")]
        after = [("fuinoise_live", "0014_streameraccount_eligibilityreview")]
        auth_targets = [
            node for node in executor.loader.graph.leaf_nodes() if node[0] == "auth"
        ]
        before.extend(auth_targets)
        after.extend(auth_targets)
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        Streamer = apps.get_model("fuinoise_live", "Streamer")
        streamer = Streamer.objects.create(
            display_name="Existing musician",
            twitch_username="existing",
            twitch_id="1234",
            organizer_notes="Private notes",
        )
        User = apps.get_model("auth", "User")
        user = User.objects.create(username="existing-organizer", is_staff=True)
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        migrated = apps.get_model("fuinoise_live", "Streamer").objects.get(
            pk=streamer.pk
        )
        self.assertEqual(migrated.display_name, "Existing musician")
        self.assertEqual(migrated.twitch_id, "1234")
        self.assertEqual(migrated.organizer_notes, "Private notes")
        self.assertTrue(apps.get_model("auth", "User").objects.get(pk=user.pk).is_staff)
        self.assertFalse(
            apps.get_model("fuinoise_live", "StreamerAccount").objects.exists()
        )
        self.assertFalse(
            apps.get_model("fuinoise_live", "EligibilityReview").objects.exists()
        )


class RequestMigrationTests(MigrationTestCase):
    def test_existing_public_schedule_and_private_draft_survive_request_migration(self):
        executor = MigrationExecutor(connection)
        before = [("fuinoise_live", "0014_streameraccount_eligibilityreview")]
        after = [
            (
                "fuinoise_live",
                "0015_draftslot_request_version_slotrequest_slotpreference_and_more",
            )
        ]
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        Community = apps.get_model("fuinoise_live", "Community")
        Event = apps.get_model("fuinoise_live", "Event")
        Slot = apps.get_model("fuinoise_live", "RaidSlot")
        Draft = apps.get_model("fuinoise_live", "ScheduleDraft")
        DraftSlot = apps.get_model("fuinoise_live", "DraftSlot")
        community = Community.objects.create(name="Music", slug="music")
        event = Event.objects.create(
            date=datetime.date(2026, 10, 4),
            community=community,
            publication_status="published",
            event_time_zone="UTC",
        )
        start = datetime.datetime(2026, 10, 4, 20, tzinfo=datetime.timezone.utc)
        slot = Slot.objects.create(
            event=event,
            start=start,
            duration_minutes=60,
            raid_slot_note="Published note",
            replay_url="https://example.com/replay",
        )
        fields = (
            "date",
            "name",
            "description",
            "community_id",
            "event_time_zone",
            "default_slot_duration_minutes",
            "signup_before_publication",
        )
        event_values = {field: getattr(event, field) for field in fields}
        old_snapshot = {
            "event": event_values,
            "publication_status": "published",
            "slots": [
                {
                    "id": slot.pk,
                    "streamer_id": None,
                    "start": start,
                    "duration_minutes": 60,
                    "replay_url": slot.replay_url,
                    "raid_slot_note": slot.raid_slot_note,
                }
            ],
        }
        fingerprint = hashlib.sha256(
            json.dumps(old_snapshot, sort_keys=True, default=str).encode()
        ).hexdigest()
        draft = Draft.objects.create(
            event=event,
            **event_values,
            version=2,
            base_schedule_version=0,
            base_fingerprint=fingerprint,
        )
        Draft.objects.filter(pk=draft.pk).update(name="Private draft title")
        working = DraftSlot.objects.create(
            draft=draft,
            source_slot=slot,
            start=start + datetime.timedelta(hours=1),
            duration_minutes=90,
            raid_slot_note="Private note",
        )
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        migrated_slot = apps.get_model("fuinoise_live", "RaidSlot").objects.get(
            pk=slot.pk
        )
        migrated_draft = apps.get_model("fuinoise_live", "ScheduleDraft").objects.get(
            pk=draft.pk
        )
        migrated_working = apps.get_model("fuinoise_live", "DraftSlot").objects.get(
            pk=working.pk
        )
        self.assertIsNone(migrated_slot.signup_request_id)
        self.assertIsNone(migrated_working.signup_request_id)
        self.assertEqual(migrated_slot.start, start)
        self.assertEqual(migrated_slot.raid_slot_note, "Published note")
        self.assertEqual(migrated_draft.name, "Private draft title")
        self.assertEqual(migrated_draft.version, 2)
        self.assertEqual(migrated_working.start, start + datetime.timedelta(hours=1))
        self.assertEqual(migrated_working.duration_minutes, 90)
        self.assertEqual(migrated_working.raid_slot_note, "Private note")
        self.assertEqual(_fingerprint(LiveEvent.objects.get(pk=event.pk)), fingerprint)


class TwitchSnapshotMigrationTests(MigrationTestCase):
    def test_enrichment_migration_preserves_lineups_and_does_not_invent_identity(self):
        executor = MigrationExecutor(connection)
        before = [
            (
                "fuinoise_live",
                "0015_draftslot_request_version_slotrequest_slotpreference_and_more",
            )
        ]
        after = [("fuinoise_live", "0016_twitchsnapshot")]
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        community = apps.get_model("fuinoise_live", "Community").objects.create(
            name="Music", slug="music"
        )
        streamer = apps.get_model("fuinoise_live", "Streamer").objects.create(
            display_name="Chosen name",
            twitch_username="legacy",
            organizer_notes="Private notes",
        )
        event = apps.get_model("fuinoise_live", "Event").objects.create(
            date=datetime.date(2026, 10, 4),
            community=community,
            publication_status="published",
        )
        start = datetime.datetime(2026, 10, 4, 20, tzinfo=datetime.timezone.utc)
        slot = apps.get_model("fuinoise_live", "RaidSlot").objects.create(
            event=event,
            streamer=streamer,
            start=start,
            duration_minutes=90,
            raid_slot_note="Published note",
            replay_url="https://example.com/replay",
        )
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        migrated = apps.get_model("fuinoise_live", "Streamer").objects.get(
            pk=streamer.pk
        )
        migrated_slot = apps.get_model("fuinoise_live", "RaidSlot").objects.get(
            pk=slot.pk
        )
        self.assertIsNone(migrated.twitch_id)
        self.assertEqual(migrated.display_name, "Chosen name")
        self.assertEqual(migrated.organizer_notes, "Private notes")
        self.assertEqual(migrated_slot.streamer_id, streamer.pk)
        self.assertEqual(migrated_slot.start, start)
        self.assertEqual(migrated_slot.duration_minutes, 90)
        self.assertEqual(migrated_slot.raid_slot_note, "Published note")
        self.assertEqual(migrated_slot.replay_url, "https://example.com/replay")
        self.assertFalse(
            apps.get_model("fuinoise_live", "TwitchSnapshot").objects.exists()
        )
