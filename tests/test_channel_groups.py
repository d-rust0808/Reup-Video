import asyncio

from app.api.channels import (
    _replace_group_members,
    delete_channel_group,
    expand_group_channel_ids,
    record_publish_event,
)
from app.config import settings
from app.core.database import get_db_connection, init_db
from fastapi import HTTPException


def test_group_expand_and_publish_log(tmp_path):
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    now = "2026-08-27T00:00:00+00:00"
    with get_db_connection(db) as conn:
        conn.execute(
            """INSERT INTO channels (channel_id, name, platform, handle, tags, description, color, overlays, status, created_at, updated_at)
               VALUES ('chan_a', 'Page A', 'facebook', '', '[]', '', 'blue', '[]', 'ACTIVE', ?, ?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO channels (channel_id, name, platform, handle, tags, description, color, overlays, status, created_at, updated_at)
               VALUES ('chan_b', 'Page B', 'facebook', '', '[]', '', 'blue', '[]', 'ACTIVE', ?, ?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO channel_groups (group_id, name, notes, color, created_at, updated_at)
               VALUES ('grp_1', 'Xay dung', 'clip dap pha', 'blue', ?, ?)""",
            (now, now),
        )
        conn.execute("INSERT INTO channel_group_members (group_id, channel_id) VALUES ('grp_1', 'chan_a')")
        conn.execute("INSERT INTO channel_group_members (group_id, channel_id) VALUES ('grp_1', 'chan_b')")
        conn.commit()

    mapping = expand_group_channel_ids(db, ["grp_1"])
    assert set(mapping) == {"chan_a", "chan_b"}
    assert mapping["chan_a"]["group_name"] == "Xay dung"

    event_id = record_publish_event(
        db,
        job_id="job_1",
        channel_video_id="cvid_1",
        channel_id="chan_a",
        group_id="grp_1",
        group_name="Xay dung",
        title="Dap pha",
        caption="LH 0777",
        notes="nhom mien nam",
        status="QUEUED",
    )
    assert event_id.startswith("plog_")
    with get_db_connection(db) as conn:
        row = dict(conn.execute("SELECT * FROM publish_log WHERE id = ?", (event_id,)).fetchone())
    assert row["channel_name"] == "Page A"
    assert row["group_name"] == "Xay dung"
    assert row["notes"] == "nhom mien nam"
    assert row["title"] == "Dap pha"


def test_delete_channel_group_removes_members(tmp_path, monkeypatch):
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    now = "2026-08-27T00:00:00+00:00"
    with get_db_connection(db) as conn:
        conn.execute(
            """INSERT INTO channels (channel_id, name, platform, handle, tags, description, color, overlays, status, created_at, updated_at)
               VALUES ('chan_a', 'Page A', 'facebook', '', '[]', '', 'blue', '[]', 'ACTIVE', ?, ?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO channel_groups (group_id, name, notes, color, created_at, updated_at)
               VALUES ('grp_del', 'Xoa di', '', 'blue', ?, ?)""",
            (now, now),
        )
        conn.execute("INSERT INTO channel_group_members (group_id, channel_id) VALUES ('grp_del', 'chan_a')")
        conn.commit()

    monkeypatch.setattr(settings, "DB_PATH", db)
    result = asyncio.run(delete_channel_group("grp_del"))
    assert result["deleted"] is True
    assert result["group_id"] == "grp_del"

    with get_db_connection(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM channel_groups WHERE group_id = 'grp_del'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM channel_group_members WHERE group_id = 'grp_del'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM channels WHERE channel_id = 'chan_a'").fetchone()[0] == 1

    try:
        asyncio.run(delete_channel_group("grp_del"))
        assert False, "expected 404 for already-deleted group"
    except HTTPException as exc:
        assert exc.status_code == 404


def test_replace_group_members_ignores_unknown_ids(tmp_path):
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    now = "2026-08-27T00:00:00+00:00"
    with get_db_connection(db) as conn:
        conn.execute(
            """INSERT INTO channels (channel_id, name, platform, handle, tags, description, color, overlays, status, created_at, updated_at)
               VALUES ('chan_a', 'Page A', 'facebook', '', '[]', '', 'blue', '[]', 'ACTIVE', ?, ?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO channel_groups (group_id, name, notes, color, created_at, updated_at)
               VALUES ('grp_1', 'Review', '', 'blue', ?, ?)""",
            (now, now),
        )
        _replace_group_members(conn, "grp_1", ["chan_a", "chan_a", "chan_missing", ""])
        conn.commit()
        members = [r[0] for r in conn.execute(
            "SELECT channel_id FROM channel_group_members WHERE group_id = 'grp_1'"
        ).fetchall()]
    assert members == ["chan_a"]
