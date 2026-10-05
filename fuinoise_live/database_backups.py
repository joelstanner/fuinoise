"""Consistent SQLite snapshots and restoration into new, private files."""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class BackupError(Exception):
    pass


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise BackupError("Choose an existing SQLite database file.")
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)


def _inspect(path: Path) -> dict[str, Any]:
    with closing(_read_connection(path)) as db:
        deadline = time.monotonic() + 60
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackupError("SQLite integrity verification failed.")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise BackupError("SQLite foreign-key verification failed.")
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        if "django_migrations" not in tables or "fuinoise_live_event" not in tables:
            raise BackupError("This is not a migrated Fuinoise database.")
        counts = {}
        for table in tables:
            quoted = table.replace('"', '""')
            counts[table] = db.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0]
        migrations = [
            list(row)
            for row in db.execute(
                "SELECT app, name FROM django_migrations ORDER BY app, name"
            )
        ]
        return {"tables": counts, "migrations": migrations}


def _stage(directory: Path) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".fuinoise-", dir=directory)
    os.close(descriptor)
    return Path(name)


def _sync(path: Path) -> None:
    with path.open("rb") as file:
        os.fsync(file.fileno())


def _publish(stage: Path, target: Path) -> None:
    # Link rather than replace: publication is atomic and cannot overwrite a file.
    os.link(stage, target)
    try:
        stage.unlink()
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        target.unlink(missing_ok=True)
        raise


def snapshot_database(source: Path, directory: Path, *, label: str = "") -> Path:
    source, directory = source.resolve(), directory.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_mode & 0o077:
        raise BackupError("Use a private backup directory (permissions 0700).")
    if len(label) > 80:
        raise BackupError("Use a backup label of at most 80 characters.")
    stage = _stage(directory)
    manifest_stage: Path | None = None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"fuinoise-{stamp}-{uuid.uuid4().hex[:12]}.sqlite3"
    manifest = target.with_suffix(".sqlite3.json")
    published = False
    try:
        deadline = time.monotonic() + 30

        def progress(status: int, remaining: int, total: int) -> None:
            if time.monotonic() > deadline:
                raise BackupError(
                    "Backup timed out; try again when database load is lower."
                )

        with (
            closing(_read_connection(source)) as src,
            closing(sqlite3.connect(stage)) as dst,
        ):
            src.backup(dst, pages=100, progress=progress, sleep=0.05)
            dst.execute("PRAGMA journal_mode=DELETE")
        data = {
            "format": 1,
            "filename": target.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "label": label,
            "sha256": _hash(stage),
            "bytes": stage.stat().st_size,
            **_inspect(stage),
        }
        _sync(stage)
        manifest_stage = _stage(directory)
        manifest_stage.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")
        _sync(manifest_stage)
        _publish(stage, target)
        published = True
        _publish(manifest_stage, manifest)
        return target
    except (OSError, sqlite3.Error, BackupError) as error:
        if published:
            target.unlink(missing_ok=True)
        raise BackupError(
            "Backup failed; no complete snapshot was published."
        ) from error
    finally:
        stage.unlink(missing_ok=True)
        if manifest_stage:
            manifest_stage.unlink(missing_ok=True)


def verify_snapshot(snapshot: Path) -> dict[str, Any]:
    snapshot = snapshot.resolve()
    manifest = snapshot.with_suffix(".sqlite3.json")
    try:
        with manifest.open("rb") as file:
            raw = file.read(1048577)
        if len(raw) > 1048576:
            raise BackupError("The backup manifest is too large.")
        data = json.loads(raw)
        if (
            not isinstance(data, dict)
            or data.get("format") != 1
            or data.get("filename") != snapshot.name
            or data.get("bytes") != snapshot.stat().st_size
            or data.get("sha256") != _hash(snapshot)
        ):
            raise BackupError("The backup does not match its manifest.")
        observed = _inspect(snapshot)
        if observed["tables"] != data.get("tables") or observed[
            "migrations"
        ] != data.get("migrations"):
            raise BackupError("The backup metadata does not match its database.")
        return data
    except (OSError, ValueError, sqlite3.Error, BackupError) as error:
        raise BackupError(
            "Backup verification failed; keep the original database intact."
        ) from error


def restore_snapshot(snapshot: Path, destination: Path) -> int:
    metadata = verify_snapshot(snapshot)
    destination = destination.absolute()
    companions = [
        Path(str(destination) + suffix) for suffix in ("-wal", "-shm", "-journal")
    ]
    if (
        destination.exists()
        or destination.is_symlink()
        or any(path.exists() or path.is_symlink() for path in companions)
    ):
        raise BackupError(
            "Restore requires a new destination without existing "
            "SQLite companion files."
        )
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stage = _stage(destination.parent)
    try:
        shutil.copyfile(snapshot, stage)
        if _hash(stage) != metadata["sha256"]:
            raise BackupError("The backup changed during restore.")
        _inspect(stage)
        held = 0
        with closing(sqlite3.connect(stage)) as db:
            if db.execute(
                "SELECT 1 FROM sqlite_schema WHERE name='fuinoise_live_discorddelivery'"
            ).fetchone():
                # A message may have been sent after this snapshot was taken.
                # Preserve its occurrence/nonce, but never replay the old outbox.
                held = db.execute(
                    "UPDATE fuinoise_live_discorddelivery SET status='uncertain', "
                    "claim_token=NULL, last_error=? WHERE status!='sent'",
                    (
                        "Restored from backup; verify past Discord delivery "
                        "before recovery.",
                    ),
                ).rowcount
                db.commit()
        _inspect(stage)
        _sync(stage)
        _publish(stage, destination)
        return held
    except (OSError, sqlite3.Error, BackupError) as error:
        raise BackupError(
            "Restore failed; the existing database was not changed."
        ) from error
    finally:
        stage.unlink(missing_ok=True)
