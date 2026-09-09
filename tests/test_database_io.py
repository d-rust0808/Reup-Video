import os
import sqlite3
import time

import pytest

from app.core.database import (
    RetryingConnection,
    _is_retryable_sqlite_error,
    _retry_sqlite,
    get_db_connection,
    init_db,
    volume_needs_conservative_sqlite,
)


def test_retry_sqlite_retries_disk_io():
    attempts = {"n": 0}

    def boom():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise sqlite3.OperationalError("disk I/O error")
        return 42

    assert _retry_sqlite(boom) == 42
    assert attempts["n"] == 3


def test_retry_sqlite_does_not_retry_integrity():
    def boom():
        raise sqlite3.IntegrityError("UNIQUE constraint failed")

    with pytest.raises(sqlite3.IntegrityError):
        _retry_sqlite(boom)


def test_retry_sqlite_gives_up_on_disk_io(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _delay: None)

    def boom():
        raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        _retry_sqlite(boom)


def test_retryable_error_detection():
    assert _is_retryable_sqlite_error(sqlite3.OperationalError("disk I/O error"))
    assert _is_retryable_sqlite_error(sqlite3.OperationalError("database is locked"))
    assert not _is_retryable_sqlite_error(sqlite3.OperationalError("no such table: jobs"))
    assert not _is_retryable_sqlite_error(ValueError("disk I/O error"))


def test_safe_io_env_overrides(tmp_path, monkeypatch):
    db = str(tmp_path / "jobs.sqlite")
    monkeypatch.setenv("REUP_SQLITE_SAFE_IO", "1")
    assert volume_needs_conservative_sqlite(db) is True
    monkeypatch.setenv("REUP_SQLITE_SAFE_IO", "0")
    assert volume_needs_conservative_sqlite(db) is False


def test_conservative_mode_uses_delete_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("REUP_SQLITE_SAFE_IO", "1")
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    with get_db_connection(db) as conn:
        mode = str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower()
        mmap_size = int(conn.execute("PRAGMA mmap_size;").fetchone()[0])
        count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert mode == "delete"
    assert mmap_size == 0
    assert count == 0
    assert not os.path.isfile(db + "-wal")


def test_wal_mode_when_safe_io_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("REUP_SQLITE_SAFE_IO", "0")
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    with get_db_connection(db) as conn:
        mode = str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower()
        mmap_size = int(conn.execute("PRAGMA mmap_size;").fetchone()[0])
    assert mode == "wal"
    assert mmap_size == 0


def test_empty_wal_recovered_on_init(tmp_path, monkeypatch):
    monkeypatch.setenv("REUP_SQLITE_SAFE_IO", "1")
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    wal = db + "-wal"
    shm = db + "-shm"
    with open(wal, "wb"):
        pass
    with open(shm, "wb") as handle:
        handle.write(b"\0" * 32)
    assert os.path.getsize(wal) == 0
    init_db(db)
    with get_db_connection(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        mode = str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower()
    assert mode == "delete"
    assert not os.path.isfile(wal)


def test_connection_uses_retrying_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("REUP_SQLITE_SAFE_IO", "1")
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    conn = get_db_connection(db)
    try:
        assert isinstance(conn, RetryingConnection)
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        conn.close()
