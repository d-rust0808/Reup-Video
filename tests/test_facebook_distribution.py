import json
from datetime import datetime, timezone

from app.core.database import get_db_connection, init_db
from app.services.facebook_distribution import absolute_facebook_permalink, enqueue_channel_video


def test_relative_reel_permalink_becomes_facebook_url():
    assert absolute_facebook_permalink("/reel/4587803151487718/") == (
        "https://www.facebook.com/reel/4587803151487718/"
    )
    assert absolute_facebook_permalink("https://facebook.invalid/reel/1") == (
        "https://facebook.invalid/reel/1"
    )
    assert absolute_facebook_permalink("", video_id="abc") == "https://www.facebook.com/reel/abc"


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


def test_parse_facebook_page_ref():
    from app.api.facebook import parse_facebook_page_ref

    assert parse_facebook_page_ref("106957297783867") == "106957297783867"
    assert parse_facebook_page_ref("https://www.facebook.com/profile.php?id=106957297783867") == "106957297783867"
    assert parse_facebook_page_ref("https://www.facebook.com/phimhayne") == "phimhayne"
    assert parse_facebook_page_ref("facebook.com/pages/foo/123456789012345") == "123456789012345"
    assert parse_facebook_page_ref("") == ""


def test_granted_page_ids_from_granular_scopes():
    from app.services.facebook_client import granted_page_ids, token_metadata

    debug = {
        "is_valid": True,
        "user_id": "u1",
        "scopes": ["pages_show_list"],
        "granular_scopes": [
            {"scope": "pages_show_list", "target_ids": ["111", "222"]},
            {"scope": "pages_manage_posts", "target_ids": ["222", "333"]},
        ],
    }
    assert granted_page_ids(debug) == ["111", "222", "333"]
    assert token_metadata(debug)["granted_page_ids"] == ["111", "222", "333"]


def test_page_payload_can_publish_with_manage_task():
    from app.api.facebook import _page_payload
    payload = _page_payload({
        "id": "9",
        "name": "New Page",
        "tasks": ["MANAGE"],
        "access_token": "tok",
    })
    assert payload["can_publish"] is True
    skipped = _page_payload({"id": "9", "name": "New Page", "tasks": ["ANALYZE"]})
    assert skipped["can_publish"] is False


def test_store_pages_keeps_pages_without_token(tmp_path, monkeypatch):
    from app.api import facebook
    from app.config import settings

    db_path = str(tmp_path / "pages.sqlite")
    init_db(db_path)
    monkeypatch.setattr(settings, "DB_PATH", db_path)
    monkeypatch.setattr(facebook, "set_secret", lambda _ref, _value: None)
    count = facebook._store_pages([
        {"id": "new1", "name": "Page Mới", "tasks": ["MANAGE"]},
    ])
    assert count >= 1
    with get_db_connection(db_path) as conn:
        row = dict(conn.execute("SELECT name FROM facebook_pages WHERE page_id = 'new1'").fetchone())
    assert row["name"] == "Page Mới"


def test_list_pages_merges_business_owned_and_fills_missing_token():
    from app.services.facebook_client import FacebookClient

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200
            self.is_success = True

        def json(self):
            return self._payload

    def fake_get(url, params=None, headers=None):
        if url.endswith("/me/accounts"):
            return FakeResp({"data": [{"id": "1", "name": "Cũ", "access_token": "t1", "tasks": ["CREATE_CONTENT"]}]})
        if url.endswith("/me/businesses"):
            return FakeResp({"data": [{"id": "biz1", "name": "Biz"}]})
        if url.endswith("/owned_pages"):
            return FakeResp({"data": [{"id": "2", "name": "Page mới", "tasks": ["MANAGE"]}]})
        if url.endswith("/client_pages"):
            return FakeResp({"data": []})
        if url.endswith("/assigned_pages"):
            return FakeResp({"data": []})
        if url.endswith("/2"):
            return FakeResp({"id": "2", "name": "Page mới", "access_token": "t2", "tasks": ["MANAGE"]})
        return FakeResp({"data": []})

    client = FacebookClient("v24.0")
    client.client.get = fake_get
    pages = client.list_pages("user-token")
    by_id = {str(p["id"]): p for p in pages}
    assert "1" in by_id
    assert "2" in by_id
    assert by_id["2"]["access_token"] == "t2"


def test_page_payload_keeps_large_picture_and_profile_fields():
    from app.api.facebook import _page_payload, enlarge_facebook_picture

    tiny = "https://scontent.xx.fbcdn.net/v/t.jpg?stp=cp0_dst-jpg_s50x50_tt6&oh=1"
    payload = _page_payload({
        "id": "99",
        "name": "Page A",
        "category": "Blogger",
        "username": "page.a",
        "fan_count": 12500,
        "followers_count": 13000,
        "about": "Kênh review",
        "link": "https://facebook.com/page.a",
        "tasks": ["CREATE_CONTENT"],
        "picture": {"data": {"url": tiny}},
        "access_token": "tok",
    })
    assert "s200x200" in payload["picture_url"]
    assert payload["username"] == "page.a"
    assert payload["fan_count"] == 12500
    assert payload["about"] == "Kênh review"
    assert enlarge_facebook_picture(tiny).find("s50x50") == -1
    from app.api.facebook import page_picture_api_path
    assert page_picture_api_path("1244") == "/api/v1/facebook/pages/1244/picture"
