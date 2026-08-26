from app.api.channels import expand_group_channel_ids, record_publish_event
from app.core.database import get_db_connection, init_db


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
