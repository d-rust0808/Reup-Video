import json
from datetime import datetime, timezone

from app.core.database import get_db_connection, init_db
from app.services.facebook_distribution import enqueue_channel_video


def _now():
    return datetime.now(timezone.utc).isoformat()


def _seed_binding(db_path, video_path, *, auto_publish=1, publish_status="READY"):
    now = _now()
    with get_db_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO facebook_connections (
                id, app_id, graph_version, user_id, user_name, scopes,
                app_secret_ref, user_token_ref, status, last_error, created_at, updated_at
            ) VALUES ('facebook_default', 'app_1', 'v24.0', 'user_1', 'User',
                      '["pages_manage_posts"]', 'app-secret-ref', 'user-token-ref',
                      'CONNECTED', '', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO channels (
                channel_id, name, platform, handle, tags, description, color,
                overlays, status, created_at, updated_at
            ) VALUES ('chan_fb', 'Facebook', 'facebook', '', '[]', '', 'blue',
                      '[]', 'ACTIVE', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO facebook_pages (
                page_id, connection_id, name, category, tasks, picture_url,
                page_token_ref, can_publish, last_synced_at, updated_at
            ) VALUES ('page_1', 'facebook_default', 'Page One', '', '["CREATE_CONTENT"]', '',
                      'facebook.page.page_1.token', 1, ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO channel_destinations (
                channel_id, provider, destination_id, auto_publish, created_at, updated_at
            ) VALUES ('chan_fb', 'facebook', 'page_1', ?, ?, ?)
            """,
            (auto_publish, now, now),
        )
        conn.execute(
            """
            INSERT INTO channel_videos (
                id, channel_id, job_id, title, caption, tags, publish_status,
                video_path, notes, created_at, updated_at
            ) VALUES ('cvid_1', 'chan_fb', 'job_1', 'Title', 'Caption', ?, ?, ?, '', ?, ?)
            """,
            (json.dumps(["viral", "review phim"]), publish_status, str(video_path), now, now),
        )
        conn.commit()


def test_facebook_enqueue_is_idempotent_and_builds_caption(tmp_path):
    db_path = str(tmp_path / "jobs.sqlite")
    video_path = tmp_path / "job_1.facebook.mp4"
    video_path.write_bytes(b"video")
    init_db(db_path)
    _seed_binding(db_path, video_path)

    first = enqueue_channel_video(db_path, "cvid_1", require_auto_publish=True)
    second = enqueue_channel_video(db_path, "cvid_1", require_auto_publish=True)

    assert first and first == second
    with get_db_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM distribution_jobs").fetchall()
    assert len(rows) == 1
    assert rows[0]["destination_id"] == "page_1"
    assert rows[0]["source_path"] == str(video_path)
    assert rows[0]["caption"] == "Caption\n\n#viral #reviewphim"


def test_facebook_auto_enqueue_requires_ready_and_enabled(tmp_path):
    disabled_db = str(tmp_path / "disabled.sqlite")
    draft_db = str(tmp_path / "draft.sqlite")
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")

    init_db(disabled_db)
    _seed_binding(disabled_db, video_path, auto_publish=0)
    assert enqueue_channel_video(disabled_db, "cvid_1", require_auto_publish=True) is None
    assert enqueue_channel_video(disabled_db, "cvid_1", require_auto_publish=False)

    init_db(draft_db)
    _seed_binding(draft_db, video_path, publish_status="DRAFT")
    assert enqueue_channel_video(draft_db, "cvid_1", require_auto_publish=True) is None


def test_facebook_worker_persists_upload_phases_and_publish_result(tmp_path, monkeypatch):
    from app.services import facebook_distribution as distribution

    db_path = str(tmp_path / "worker.sqlite")
    video_path = tmp_path / "reel.mp4"
    video_path.write_bytes(b"video")
    init_db(db_path)
    _seed_binding(db_path, video_path)
    distribution_id = enqueue_channel_video(db_path, "cvid_1", require_auto_publish=True)

    calls = []

    class FakeFacebookClient:
        def __init__(self, graph_version, timeout=30.0):
            calls.append(("init", graph_version, timeout))

        def close(self):
            calls.append(("close",))

        def start_reel(self, page_id, token):
            calls.append(("start", page_id, token))
            return {"video_id": "remote_video_1", "upload_url": "https://upload.invalid"}

        def upload_reel_binary(self, upload_url, token, source_path):
            calls.append(("upload", upload_url, token, source_path))

        def finish_reel(self, page_id, token, video_id, description):
            calls.append(("finish", page_id, token, video_id, description))
            return {"success": True}

        def get_video_status(self, video_id, token):
            calls.append(("status", video_id, token))
            return {
                "id": video_id,
                "permalink_url": "https://facebook.invalid/reel/1",
                "status": {"video_status": "ready"},
            }

    monkeypatch.setattr(distribution, "FacebookClient", FakeFacebookClient)
    monkeypatch.setattr(distribution, "get_secret", lambda _ref: "page-token")
    monkeypatch.setattr(distribution, "_validate_reel_file", lambda _path: None)

    worker = distribution.FacebookDistributionWorker(db_path)
    with get_db_connection(db_path) as conn:
        row = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    worker._process_sync(row)

    with get_db_connection(db_path) as conn:
        processing = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    assert processing["status"] == "PROCESSING"
    assert processing["upload_phase"] == "FINISHED"
    assert processing["upload_video_id"] == "remote_video_1"
    assert processing["attempts"] == 1

    worker._process_sync(processing)
    with get_db_connection(db_path) as conn:
        published = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
        channel_video = dict(conn.execute("SELECT * FROM channel_videos WHERE id = 'cvid_1'").fetchone())
    assert published["status"] == "PUBLISHED"
    assert published["permalink"] == "https://facebook.invalid/reel/1"
    assert channel_video["publish_status"] == "PUBLISHED"
    assert [call[0] for call in calls].count("start") == 1
    assert [call[0] for call in calls].count("upload") == 1
    assert [call[0] for call in calls].count("finish") == 1


def test_synced_pages_are_materialized_as_bound_channels(tmp_path, monkeypatch):
    from app.api import facebook
    from app.config import settings

    db_path = str(tmp_path / "pages.sqlite")
    init_db(db_path)
    monkeypatch.setattr(settings, "DB_PATH", db_path)
    monkeypatch.setattr(facebook, "set_secret", lambda _ref, _value: None)

    count = facebook._store_pages([
        {
            "id": "page_123",
            "name": "Page Review",
            "category": "Digital creator",
            "tasks": ["CREATE_CONTENT", "ANALYZE"],
            "access_token": "page-token",
        }
    ])

    assert count == 1
    with get_db_connection(db_path) as conn:
        channel = dict(conn.execute(
            "SELECT * FROM channels WHERE channel_id = 'chan_fb_page_123'"
        ).fetchone())
        binding = dict(conn.execute(
            "SELECT * FROM channel_destinations WHERE channel_id = 'chan_fb_page_123'"
        ).fetchone())
    assert channel["name"] == "Page Review"
    assert channel["platform"] == "facebook"
    assert binding["destination_id"] == "page_123"
    assert binding["auto_publish"] == 0
