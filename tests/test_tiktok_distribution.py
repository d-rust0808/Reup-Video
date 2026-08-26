import json
from datetime import datetime, timedelta, timezone

from app.core.database import get_db_connection, init_db
from app.services.tiktok_client import parse_auth_code, pick_privacy, plan_chunks
from app.services.tiktok_distribution import enqueue_tiktok_video


def test_plan_chunks_and_privacy():
    assert plan_chunks(4_000_000) == (4_000_000, 1)
    assert plan_chunks(64 * 1024 * 1024)[1] == 1
    chunk, count = plan_chunks(70 * 1024 * 1024)
    assert chunk == 10 * 1024 * 1024
    assert count == 7
    assert pick_privacy(["SELF_ONLY"]) == "SELF_ONLY"
    assert pick_privacy(["SELF_ONLY", "PUBLIC_TO_EVERYONE"]) == "PUBLIC_TO_EVERYONE"
    assert parse_auth_code("https://example.com/cb?code=abc%2Fdef&state=x") == "abc/def" or parse_auth_code(
        "https://example.com/cb?code=abc/def"
    ) == "abc/def"
    assert parse_auth_code("plaincode") == "plaincode"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _seed_tiktok(db_path, video_path, *, auto_publish=1, publish_status="READY"):
    now = _now()
    with get_db_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO tiktok_connections (
                id, client_key, redirect_uri, client_secret_ref, status, last_error, created_at, updated_at
            ) VALUES ('tiktok_default', 'ck', 'https://example.com/cb', 'tiktok.secret', 'CONNECTED', '', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO channels (
                channel_id, name, platform, handle, tags, description, color,
                overlays, status, created_at, updated_at
            ) VALUES ('chan_tt', 'TikTok', 'tiktok', 'demo', '[]', '', 'rose',
                      '[]', 'ACTIVE', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO tiktok_accounts (
                open_id, connection_id, username, nickname, avatar_url, scopes,
                privacy_options, access_token_ref, refresh_token_ref, token_expires_at,
                can_publish, last_synced_at, updated_at
            ) VALUES ('oid_1', 'tiktok_default', 'demo', 'Demo', '', '["video.publish"]',
                      '["PUBLIC_TO_EVERYONE"]', 'tiktok.oid_1.access', 'tiktok.oid_1.refresh',
                      ?, 1, ?, ?)
            """,
            ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), now, now),
        )
        conn.execute(
            """
            INSERT INTO channel_destinations (
                channel_id, provider, destination_id, auto_publish, created_at, updated_at
            ) VALUES ('chan_tt', 'tiktok', 'oid_1', ?, ?, ?)
            """,
            (auto_publish, now, now),
        )
        conn.execute(
            """
            INSERT INTO channel_videos (
                id, channel_id, job_id, title, caption, tags, publish_status,
                video_path, notes, created_at, updated_at
            ) VALUES ('cvid_tt', 'chan_tt', 'job_tt', 'Title', 'Caption', ?, ?, ?, '', ?, ?)
            """,
            (json.dumps(["fyp", "vietsub"]), publish_status, str(video_path), now, now),
        )
        conn.commit()


def test_tiktok_enqueue_is_idempotent(tmp_path):
    db_path = str(tmp_path / "jobs.sqlite")
    video_path = tmp_path / "job.tiktok.mp4"
    video_path.write_bytes(b"video")
    init_db(db_path)
    _seed_tiktok(db_path, video_path)
    first = enqueue_tiktok_video(db_path, "cvid_tt", require_auto_publish=True)
    second = enqueue_tiktok_video(db_path, "cvid_tt", require_auto_publish=True)
    assert first and first == second
    with get_db_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM distribution_jobs").fetchall()
    assert len(rows) == 1
    assert rows[0]["provider"] == "tiktok"
    assert rows[0]["destination_id"] == "oid_1"
    assert "#fyp" in rows[0]["caption"]


def test_tiktok_auto_enqueue_requires_ready(tmp_path):
    db_path = str(tmp_path / "draft.sqlite")
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    init_db(db_path)
    _seed_tiktok(db_path, video_path, publish_status="DRAFT")
    assert enqueue_tiktok_video(db_path, "cvid_tt", require_auto_publish=True) is None
    assert enqueue_tiktok_video(db_path, "cvid_tt", require_auto_publish=False)


def test_tiktok_worker_upload_then_publish(tmp_path, monkeypatch):
    from app.services import tiktok_distribution as distribution

    db_path = str(tmp_path / "worker.sqlite")
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"video-bytes")
    init_db(db_path)
    _seed_tiktok(db_path, video_path)
    distribution_id = enqueue_tiktok_video(db_path, "cvid_tt", require_auto_publish=True)
    calls = []

    class FakeClient:
        def __init__(self, timeout=30.0):
            calls.append(("init", timeout))

        def close(self):
            calls.append(("close",))

        def refresh_token(self, **_kwargs):
            return {}

        def creator_info(self, token):
            calls.append(("creator", token))
            return {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]}

        def init_direct_post(self, token, **kwargs):
            calls.append(("init_post", kwargs.get("privacy_level"), kwargs.get("title")))
            return {"publish_id": "v_pub_1", "upload_url": "https://upload.invalid"}

        def upload_file(self, upload_url, source_path, *, chunk_size, total_size):
            calls.append(("upload", upload_url, source_path, chunk_size, total_size))

        def fetch_status(self, token, publish_id):
            calls.append(("status", publish_id))
            return {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": "999"}

    monkeypatch.setattr(distribution, "TikTokClient", FakeClient)
    monkeypatch.setattr(distribution, "get_secret", lambda _ref: "tok")
    monkeypatch.setattr(distribution, "_validate_tiktok_file", lambda _path: None)

    worker = distribution.TikTokDistributionWorker(db_path)
    with get_db_connection(db_path) as conn:
        row = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    worker._process_sync(row)
    with get_db_connection(db_path) as conn:
        processing = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    assert processing["status"] == "PROCESSING"
    assert processing["upload_video_id"] == "v_pub_1"

    worker._process_sync(processing)
    with get_db_connection(db_path) as conn:
        published = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
        video = dict(conn.execute("SELECT * FROM channel_videos WHERE id = 'cvid_tt'").fetchone())
    assert published["status"] == "PUBLISHED"
    assert video["publish_status"] == "PUBLISHED"
    assert [c[0] for c in calls].count("init_post") == 1
    assert [c[0] for c in calls].count("upload") == 1
