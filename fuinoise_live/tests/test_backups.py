import hashlib
import io
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from fuinoise_live.database_backups import (
    BackupError,
    restore_snapshot,
    snapshot_database,
    verify_snapshot,
)


class BackupTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.sqlite3"
        self.backups = self.root / "backups"
        self.db = sqlite3.connect(self.source)
        self.addCleanup(self.db.close)
        self.db.executescript(
            "CREATE TABLE django_migrations (app TEXT, name TEXT);"
            "INSERT INTO django_migrations VALUES ('fuinoise_live', '0017');"
            "CREATE TABLE fuinoise_live_event (id INTEGER PRIMARY KEY, name TEXT);"
            "INSERT INTO fuinoise_live_event VALUES (1, 'Private and public records');"
            "CREATE TABLE child (id INTEGER PRIMARY KEY, event_id INTEGER "
            "REFERENCES fuinoise_live_event(id));"
            "INSERT INTO child VALUES (1, 1);"
            "CREATE TABLE fuinoise_live_discorddelivery (id INTEGER PRIMARY KEY, "
            "status TEXT, claim_token TEXT, last_error TEXT, nonce TEXT, content TEXT);"
        )
        for index, status in enumerate(
            ("pending", "sending", "failed", "uncertain", "sent")
        ):
            self.db.execute(
                "INSERT INTO fuinoise_live_discorddelivery VALUES (?, ?, ?, '', ?, ?)",
                (index, status, "claim", f"nonce-{index}", f"message-{index}"),
            )
        self.db.commit()

    def snapshot(self):
        return snapshot_database(self.source, self.backups, label="before upgrade")

    def manifest(self, snapshot):
        path = snapshot.with_suffix(".sqlite3.json")
        return path, json.loads(path.read_text())

    def test_online_snapshot_includes_committed_wal_without_changing_source(self):
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("INSERT INTO fuinoise_live_event VALUES (2, 'WAL record')")
        self.db.commit()
        self.assertTrue(Path(str(self.source) + "-wal").exists())
        snapshot = self.snapshot()
        data = verify_snapshot(snapshot)
        self.assertEqual(data["tables"]["fuinoise_live_event"], 2)
        self.assertEqual(data["migrations"], [["fuinoise_live", "0017"]])
        self.assertEqual(data["label"], "before upgrade")
        self.assertEqual(self.backups.stat().st_mode & 0o777, 0o700)
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            snapshot.with_suffix(".sqlite3.json").stat().st_mode & 0o777, 0o600
        )
        self.assertEqual(self.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(
            self.db.execute(
                "SELECT status FROM fuinoise_live_discorddelivery WHERE id=0"
            ).fetchone()[0],
            "pending",
        )
        with closing(sqlite3.connect(snapshot)) as restored:
            self.assertEqual(
                restored.execute("PRAGMA journal_mode").fetchone()[0], "delete"
            )

    def test_restore_preserves_rows_and_holds_all_unsent_deliveries(self):
        snapshot = self.snapshot()
        destination = self.root / "separate" / "restored.sqlite3"
        self.assertEqual(restore_snapshot(snapshot, destination), 4)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        with closing(sqlite3.connect(destination)) as db:
            self.assertEqual(
                db.execute("SELECT * FROM fuinoise_live_event").fetchall(),
                [(1, "Private and public records")],
            )
            self.assertEqual(db.execute("SELECT * FROM child").fetchall(), [(1, 1)])
            rows = db.execute(
                "SELECT id, status, claim_token, nonce, content "
                "FROM fuinoise_live_discorddelivery ORDER BY id"
            ).fetchall()
            self.assertEqual([row[1] for row in rows], ["uncertain"] * 4 + ["sent"])
            self.assertTrue(all(row[2] is None for row in rows[:4]))
            self.assertEqual(rows[4][2], "claim")
            self.assertEqual(
                [(row[3], row[4]) for row in rows],
                [(f"nonce-{i}", f"message-{i}") for i in range(5)],
            )
        verify_snapshot(snapshot)  # Restore must not modify the original snapshot.

    def test_restore_cannot_overwrite_existing_file_or_dangling_symlink(self):
        snapshot = self.snapshot()
        destination = self.root / "existing"
        destination.write_bytes(b"keep this")
        with self.assertRaises(BackupError):
            restore_snapshot(snapshot, destination)
        self.assertEqual(destination.read_bytes(), b"keep this")
        destination.unlink()
        destination.symlink_to(self.root / "absent")
        with self.assertRaises(BackupError):
            restore_snapshot(snapshot, destination)
        self.assertTrue(destination.is_symlink())

    def test_corrupt_data_cannot_be_verified_or_restored(self):
        snapshot = self.snapshot()
        with snapshot.open("ab") as file:
            file.write(b"damaged")
        destination = self.root / "restored"
        for operation in (
            lambda: verify_snapshot(snapshot),
            lambda: restore_snapshot(snapshot, destination),
        ):
            with self.assertRaises(BackupError):
                operation()
        self.assertFalse(destination.exists())

    def test_orphaned_journal_files_prevent_restore(self):
        snapshot = self.snapshot()
        destination = self.root / "new.sqlite3"
        for suffix in ("-wal", "-shm", "-journal"):
            companion = Path(str(destination) + suffix)
            companion.write_bytes(b"keep this")
            with self.assertRaises(BackupError):
                restore_snapshot(snapshot, destination)
            self.assertEqual(companion.read_bytes(), b"keep this")
            self.assertFalse(destination.exists())
            companion.unlink()

    def test_manifest_missing_invalid_oversized_or_changed_metadata_is_rejected(self):
        snapshot = self.snapshot()
        manifest, data = self.manifest(snapshot)
        for raw in (
            "{",
            "[]",
            " " * 1048577,
            json.dumps({**data, "tables": {}}),
            json.dumps({**data, "migrations": []}),
            json.dumps({**data, "filename": "other"}),
        ):
            manifest.write_text(raw)
            with self.assertRaises(BackupError):
                verify_snapshot(snapshot)
        manifest.unlink()
        with self.assertRaises(BackupError):
            verify_snapshot(snapshot)

    def test_valid_hash_does_not_hide_database_corruption(self):
        snapshot = self.snapshot()
        snapshot.write_bytes(b"not SQLite")
        manifest, data = self.manifest(snapshot)
        data.update(
            bytes=snapshot.stat().st_size,
            sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        )
        manifest.write_text(json.dumps(data))
        with self.assertRaises(BackupError):
            verify_snapshot(snapshot)

    def test_foreign_key_damage_prevents_backup(self):
        self.db.execute("INSERT INTO child VALUES (2, 999)")
        self.db.commit()
        with self.assertRaises(BackupError):
            self.snapshot()
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_missing_or_unmigrated_source_is_rejected(self):
        for source in (self.root / "absent", self.root / "empty"):
            if source.name == "empty":
                sqlite3.connect(source).close()
            with self.assertRaises(BackupError):
                snapshot_database(source, self.backups)
        self.assertFalse((self.root / "absent").exists())
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_private_directory_and_short_labels_are_required(self):
        self.backups.mkdir(mode=0o755)
        self.backups.chmod(0o755)
        with self.assertRaises(BackupError):
            self.snapshot()
        self.backups.chmod(0o700)
        with self.assertRaises(BackupError):
            snapshot_database(self.source, self.backups, label="x" * 81)

    def test_manifest_publish_failure_cleans_partial_snapshot(self):
        original = os.link
        calls = 0

        def fail_manifest(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk failure")
            original(source, target)

        with patch("fuinoise_live.database_backups.os.link", side_effect=fail_manifest):
            with self.assertRaises(BackupError):
                self.snapshot()
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_restore_failure_leaves_no_partial_destination(self):
        snapshot = self.snapshot()
        destination = self.root / "new.sqlite3"
        with patch(
            "fuinoise_live.database_backups._publish",
            side_effect=OSError("disk failure"),
        ):
            with self.assertRaises(BackupError):
                restore_snapshot(snapshot, destination)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob(".fuinoise-*")))

    def test_copy_changed_after_verification_is_rejected(self):
        snapshot = self.snapshot()
        destination = self.root / "new.sqlite3"
        with patch(
            "fuinoise_live.database_backups.shutil.copyfile",
            side_effect=lambda src, dst: Path(dst).write_bytes(b"changed"),
        ):
            with self.assertRaises(BackupError):
                restore_snapshot(snapshot, destination)
        self.assertFalse(destination.exists())

    def test_commands_validate_and_restore_to_separate_database(self):
        configured = {
            "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": self.source}
        }
        with patch.object(settings, "DATABASES", configured):
            out = io.StringIO()
            call_command("backup_database", output_dir=self.backups, stdout=out)
            snapshot = next(self.backups.glob("*.sqlite3"))
            call_command("verify_backup", snapshot, stdout=out)
            call_command(
                "restore_database",
                snapshot,
                destination=self.root / "new.sqlite3",
                stdout=out,
            )
            self.assertIn("4 historical Discord deliveries held", out.getvalue())
            with self.assertRaises(CommandError):
                call_command("restore_database", snapshot, destination=self.source)
            with self.assertRaises(CommandError):
                call_command("verify_backup", self.root / "missing")
        for name in (":memory:", "file:test?mode=memory"):
            with patch.object(
                settings,
                "DATABASES",
                {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": name}},
            ):
                with self.assertRaises(CommandError):
                    call_command("backup_database", output_dir=self.backups)
