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

        def finish_reel(self, page_id, token, video_id, description, video_state="PUBLISHED"):
            calls.append(("finish", video_state, page_id, token, video_id, description))
            return {"success": True}

        def get_video_status(self, video_id, token):
            calls.append(("status", video_id, token))
            return {
                "id": video_id,
                "permalink_url": "https://facebook.invalid/reel/1",
                "status": {"video_status": "ready"},
                "copyright_check_information": {
                    "status": {"status": "complete", "matches_found": False},
                },
            }

    monkeypatch.setattr(distribution, "FacebookClient", FakeFacebookClient)
    monkeypatch.setattr(distribution, "get_secret", lambda _ref: "page-token")
    monkeypatch.setattr(distribution, "_validate_reel_file", lambda _path: None)

    worker = distribution.FacebookDistributionWorker(db_path)
    with get_db_connection(db_path) as conn:
        row = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    worker._process_sync(row)

    with get_db_connection(db_path) as conn:
        draft = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    assert draft["status"] == "PROCESSING"
    assert draft["upload_phase"] == "DRAFT"
    assert draft["upload_video_id"] == "remote_video_1"
    assert draft["attempts"] == 1
    assert str(draft["upload_url"]).startswith("copyright_since:")

    worker._process_sync(draft)
    with get_db_connection(db_path) as conn:
        processing = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    assert processing["upload_phase"] == "FINISHED"

    worker._process_sync(processing)
    with get_db_connection(db_path) as conn:
        published = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
        channel_video = dict(conn.execute("SELECT * FROM channel_videos WHERE id = 'cvid_1'").fetchone())
    assert published["status"] == "PUBLISHED"
    assert published["permalink"] == "https://facebook.invalid/reel/1"
    assert channel_video["publish_status"] == "PUBLISHED"
    assert [call[0] for call in calls].count("start") == 1
    assert [call[0] for call in calls].count("upload") == 1
    assert [call[0] for call in calls].count("finish") == 2
    assert [call[1] for call in calls if call[0] == "finish"] == ["DRAFT", "PUBLISHED"]


def test_facebook_worker_prepends_affiliate_and_comments(tmp_path, monkeypatch):
    from app.services import facebook_distribution as distribution

    db_path = str(tmp_path / "aff.sqlite")
    video_path = tmp_path / "reel.mp4"
    video_path.write_bytes(b"video")
    init_db(db_path)
    _seed_binding(db_path, video_path)
    now = _now()
    with get_db_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO jobs (job_id, source_url, platform, status, progress_percent,
                output_file_path, watermark_config, reup_config, created_at, updated_at, message, logs)
               VALUES ('job_1', '', 'youtube', 'COMPLETED', 100, '', '{}', ?, ?, ?, '', '[]')""",
            (
                json.dumps({
                    "affiliate_link": "https://shopee.vn/giay-ve-sinh",
                    "affiliate_product": "giấy vệ sinh",
                }, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()

    distribution_id = enqueue_channel_video(db_path, "cvid_1", require_auto_publish=True)
    with get_db_connection(db_path) as conn:
        queued = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    assert queued["caption"].startswith("https://shopee.vn/giay-ve-sinh")
    assert "ủng hộ shop qua link: https://shopee.vn/giay-ve-sinh" in queued["caption"]

    comments = []

    class FakeFacebookClient:
        def __init__(self, graph_version, timeout=30.0):
            pass

        def close(self):
            pass

        def start_reel(self, page_id, token):
            return {"video_id": "remote_video_1", "upload_url": "https://upload.invalid"}

        def upload_reel_binary(self, upload_url, token, source_path):
            return None

        def finish_reel(self, page_id, token, video_id, description, video_state="PUBLISHED"):
            comments.append(("finish", video_state, description))
            return {"success": True, "post_id": "page_1_remote_video_1"}

        def get_video_status(self, video_id, token):
            return {
                "id": video_id,
                "permalink_url": "https://facebook.invalid/reel/1",
                "status": {"video_status": "ready"},
                "copyright_check_information": {
                    "status": {"status": "complete", "matches_found": False},
                },
            }

        def comment_on_reel(self, page_token, **kwargs):
            comments.append(("comment", kwargs))
            return {"id": "cmt_99"}

    monkeypatch.setattr(distribution, "FacebookClient", FakeFacebookClient)
    monkeypatch.setattr(distribution, "get_secret", lambda _ref: "page-token")
    monkeypatch.setattr(distribution, "_validate_reel_file", lambda _path: None)

    worker = distribution.FacebookDistributionWorker(db_path)
    with get_db_connection(db_path) as conn:
        row = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    worker._process_sync(row)
    with get_db_connection(db_path) as conn:
        draft = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    worker._process_sync(draft)
    with get_db_connection(db_path) as conn:
        processing = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    worker._process_sync(processing)
    with get_db_connection(db_path) as conn:
        published = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())

    assert published["status"] == "PUBLISHED"
    assert published["affiliate_comment_id"] == "cmt_99"
    finish_desc = next(item[2] for item in comments if item[0] == "finish")
    assert finish_desc.startswith("https://shopee.vn/giay-ve-sinh")
    comment_kwargs = next(item[1] for item in comments if item[0] == "comment")
    assert comment_kwargs["message"].startswith("https://shopee.vn/giay-ve-sinh")
    assert "ủng hộ kênh qua: https://shopee.vn/giay-ve-sinh" in comment_kwargs["message"]
    assert comment_kwargs["pin"] is True
    assert [item[1] for item in comments if item[0] == "finish"] == ["DRAFT", "PUBLISHED"]


def _seed_second_page(db_path, video_path):
    now = _now()
    with get_db_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO channels (
                channel_id, name, platform, handle, tags, description, color,
                overlays, status, created_at, updated_at
            ) VALUES ('chan_fb_2', 'Facebook Two', 'facebook', '', '[]', '', 'blue',
                      '[]', 'ACTIVE', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO facebook_pages (
                page_id, connection_id, name, category, tasks, picture_url,
                page_token_ref, can_publish, last_synced_at, updated_at
            ) VALUES ('page_2', 'facebook_default', 'Page Two', '', '["CREATE_CONTENT"]', '',
                      'facebook.page.page_2.token', 1, ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO channel_destinations (
                channel_id, provider, destination_id, auto_publish, created_at, updated_at
            ) VALUES ('chan_fb_2', 'facebook', 'page_2', 1, ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO channel_videos (
                id, channel_id, job_id, title, caption, tags, publish_status,
                video_path, notes, created_at, updated_at
            ) VALUES ('cvid_2', 'chan_fb_2', 'job_1', 'Title 2', 'Caption 2', '[]', 'READY', ?, '', ?, ?)
            """,
            (str(video_path), now, now),
        )
        conn.commit()


def test_copyright_match_blocks_whole_job_and_deletes_draft(tmp_path, monkeypatch):
    from app.services import facebook_distribution as distribution

    db_path = str(tmp_path / "blocked.sqlite")
    video_path = tmp_path / "reel.mp4"
    video_path.write_bytes(b"video")
    init_db(db_path)
    _seed_binding(db_path, video_path)
    _seed_second_page(db_path, video_path)
    first = enqueue_channel_video(db_path, "cvid_1", require_auto_publish=True)
    second = enqueue_channel_video(db_path, "cvid_2", require_auto_publish=True)
    assert first and second and first != second

    calls = []

    class FakeFacebookClient:
        def __init__(self, graph_version, timeout=30.0):
            pass

        def close(self):
            pass

        def start_reel(self, page_id, token):
            calls.append(("start", page_id))
            return {"video_id": f"vid_{page_id}", "upload_url": "https://upload.invalid"}

        def upload_reel_binary(self, upload_url, token, source_path):
            calls.append(("upload", source_path))

        def finish_reel(self, page_id, token, video_id, description, video_state="PUBLISHED"):
            calls.append(("finish", video_state, page_id))
            return {"success": True}

        def get_video_status(self, video_id, token):
            return {
                "id": video_id,
                "status": {"video_status": "ready"},
                "copyright_check_information": {
                    "status": {"status": "complete", "matches_found": True},
                    "copyright_matches": [
                        {
                            "content_title": "Phim cung đình",
                            "owner_copyright_policy": {
                                "name": "Studio",
                                "actions": [{"action": "BLOCK"}],
                            },
                            "matched_segments": [{"segment_type": "VIDEO", "duration_in_seconds": 30}],
                        }
                    ],
                },
            }

        def delete_object(self, object_id, token):
            calls.append(("delete", object_id))
            return {"success": True}

    monkeypatch.setattr(distribution, "FacebookClient", FakeFacebookClient)
    monkeypatch.setattr(distribution, "get_secret", lambda _ref: "page-token")
    monkeypatch.setattr(distribution, "_validate_reel_file", lambda _path: None)

    worker = distribution.FacebookDistributionWorker(db_path)
    with get_db_connection(db_path) as conn:
        row = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (first,)).fetchone())
    worker._process_sync(row)
    claimed = worker._claim_next()
    assert claimed is None
    with get_db_connection(db_path) as conn:
        draft = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (first,)).fetchone())
        sibling = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (second,)).fetchone())
    assert draft["upload_phase"] == "DRAFT"
    assert sibling["status"] == "PENDING"

    worker._process_sync(draft)
    with get_db_connection(db_path) as conn:
        rows = {
            item["id"]: dict(item)
            for item in conn.execute("SELECT * FROM distribution_jobs").fetchall()
        }
        videos = {
            item["id"]: dict(item)
            for item in conn.execute("SELECT * FROM channel_videos").fetchall()
        }
    assert rows[first]["status"] == "COPYRIGHT_BLOCKED"
    assert rows[second]["status"] == "COPYRIGHT_BLOCKED"
    assert "Phim cung đình" in rows[first]["last_error"]
    assert videos["cvid_1"]["publish_status"] == "COPYRIGHT_BLOCKED"
    assert videos["cvid_2"]["publish_status"] == "COPYRIGHT_BLOCKED"
    assert [call[0] for call in calls].count("start") == 1
    assert [call[1] for call in calls if call[0] == "finish"] == ["DRAFT"]
    assert ("delete", "vid_page_1") in calls

    reset = enqueue_channel_video(db_path, "cvid_1", require_auto_publish=False, reset_failed=True)
    assert reset == first
    with get_db_connection(db_path) as conn:
        restarted = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (first,)).fetchone())
    assert restarted["status"] == "PENDING"
    assert restarted["upload_video_id"] == ""
    assert restarted["upload_phase"] == ""


def test_unknown_copyright_result_does_not_publish(tmp_path, monkeypatch):
    from app.services import facebook_distribution as distribution

    db_path = str(tmp_path / "unknown.sqlite")
    video_path = tmp_path / "reel.mp4"
    video_path.write_bytes(b"video")
    init_db(db_path)
    _seed_binding(db_path, video_path)
    distribution_id = enqueue_channel_video(db_path, "cvid_1", require_auto_publish=True)
    finishes = []

    class FakeFacebookClient:
        def __init__(self, graph_version, timeout=30.0):
            pass

        def close(self):
            pass

        def start_reel(self, page_id, token):
            return {"video_id": "remote_video_1", "upload_url": "https://upload.invalid"}

        def upload_reel_binary(self, upload_url, token, source_path):
            return {"success": True}

        def finish_reel(self, page_id, token, video_id, description, video_state="PUBLISHED"):
            finishes.append(video_state)
            return {"success": True}

        def get_video_status(self, video_id, token):
            return {"id": video_id, "status": {"video_status": "ready"}}

        def delete_object(self, object_id, token):
            return {"success": True}

    monkeypatch.setattr(distribution, "FacebookClient", FakeFacebookClient)
    monkeypatch.setattr(distribution, "get_secret", lambda _ref: "page-token")
    monkeypatch.setattr(distribution, "_validate_reel_file", lambda _path: None)

    worker = distribution.FacebookDistributionWorker(db_path)
    with get_db_connection(db_path) as conn:
        row = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    worker._process_sync(row)
    with get_db_connection(db_path) as conn:
        draft = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    worker._process_sync(draft)
    with get_db_connection(db_path) as conn:
        still = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    assert still["status"] == "PROCESSING"
    assert still["upload_phase"] == "DRAFT"
    assert finishes == ["DRAFT"]


def test_copyright_timeout_blocks_instead_of_publishing(tmp_path, monkeypatch):
    from datetime import timedelta
    from app.services import facebook_distribution as distribution

    db_path = str(tmp_path / "timeout.sqlite")
    video_path = tmp_path / "reel.mp4"
    video_path.write_bytes(b"video")
    init_db(db_path)
    _seed_binding(db_path, video_path)
    distribution_id = enqueue_channel_video(db_path, "cvid_1", require_auto_publish=True)

    class FakeFacebookClient:
        def __init__(self, graph_version, timeout=30.0):
            pass

        def close(self):
            pass

        def start_reel(self, page_id, token):
            return {"video_id": "remote_video_1", "upload_url": "https://upload.invalid"}

        def upload_reel_binary(self, upload_url, token, source_path):
            return {"success": True}

        def finish_reel(self, page_id, token, video_id, description, video_state="PUBLISHED"):
            return {"success": True}

        def get_video_status(self, video_id, token):
            return {"id": video_id, "status": {"video_status": "ready"}}

        def delete_object(self, object_id, token):
            return {"success": True}

    monkeypatch.setattr(distribution, "FacebookClient", FakeFacebookClient)
    monkeypatch.setattr(distribution, "get_secret", lambda _ref: "page-token")
    monkeypatch.setattr(distribution, "_validate_reel_file", lambda _path: None)

    worker = distribution.FacebookDistributionWorker(db_path)
    with get_db_connection(db_path) as conn:
        row = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    worker._process_sync(row)
    stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with get_db_connection(db_path) as conn:
        conn.execute(
            "UPDATE distribution_jobs SET upload_url = ? WHERE id = ?",
            (f"copyright_since:{stale}", distribution_id),
        )
        conn.commit()
        draft = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    worker._process_sync(draft)
    with get_db_connection(db_path) as conn:
        blocked = dict(conn.execute("SELECT * FROM distribution_jobs WHERE id = ?", (distribution_id,)).fetchone())
    assert blocked["status"] == "COPYRIGHT_BLOCKED"
    assert "không đăng" in (blocked["last_error"] or "").lower() or "bản quyền" in (blocked["last_error"] or "").lower()


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
