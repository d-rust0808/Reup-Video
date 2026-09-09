import json
from datetime import datetime, timezone

from app.core.database import get_db_connection, init_db
from app.services.job_publish import job_media_files, parse_hashtags, path_for_platform, publish_job_to_groups


def _now():
    return datetime.now(timezone.utc).isoformat()


def _seed(db, output_path):
    now = _now()
    with get_db_connection(db) as conn:
        conn.execute(
            """INSERT INTO jobs (job_id, source_url, platform, status, progress_percent,
                output_file_path, watermark_config, reup_config, created_at, updated_at, message, logs)
               VALUES ('job_done', '', 'youtube', 'COMPLETED', 100, ?, '{}', '{}', ?, ?, '', '[]')""",
            (str(output_path), now, now),
        )
        conn.execute(
            """INSERT INTO channels (channel_id, name, platform, handle, tags, description, color,
                overlays, status, created_at, updated_at)
               VALUES ('chan_a', 'Page A', 'facebook', '', '[]', '', 'blue', '[]', 'ACTIVE', ?, ?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO channels (channel_id, name, platform, handle, tags, description, color,
                overlays, status, created_at, updated_at)
               VALUES ('chan_b', 'Page B', 'facebook', '', '[]', '', 'blue', '[]', 'ACTIVE', ?, ?)""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO facebook_connections (id, app_id, graph_version, user_id, user_name, scopes,
                app_secret_ref, user_token_ref, status, last_error, created_at, updated_at)
               VALUES ('facebook_default', 'app', 'v24.0', 'u', 'User', '[]', '', '', 'CONNECTED', '', ?, ?)""",
            (now, now),
        )
        for page_id, chan in (("page_a", "chan_a"), ("page_b", "chan_b")):
            conn.execute(
                """INSERT INTO facebook_pages (page_id, connection_id, name, category, tasks, picture_url,
                    page_token_ref, can_publish, last_synced_at, updated_at)
                   VALUES (?, 'facebook_default', ?, '', '["CREATE_CONTENT"]', '', '', 1, ?, ?)""",
                (page_id, page_id, now, now),
            )
            conn.execute(
                """INSERT INTO channel_destinations (channel_id, provider, destination_id, auto_publish, created_at, updated_at)
                   VALUES (?, 'facebook', ?, 0, ?, ?)""",
                (chan, page_id, now, now),
            )
        conn.execute(
            """INSERT INTO channel_groups (group_id, name, notes, color, created_at, updated_at)
               VALUES ('grp_review', 'Review Phim', '', 'blue', ?, ?)""",
            (now, now),
        )
        conn.execute("INSERT INTO channel_group_members (group_id, channel_id) VALUES ('grp_review', 'chan_a')")
        conn.execute("INSERT INTO channel_group_members (group_id, channel_id) VALUES ('grp_review', 'chan_b')")
        conn.commit()


def test_publish_job_to_group_assigns_pages(tmp_path, monkeypatch):
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    video = tmp_path / "job_done.mp4"
    video.write_bytes(b"video")
    _seed(db, video)

    from app.services import job_publish

    monkeypatch.setattr(
        job_publish,
        "write_facebook_posts",
        lambda **kwargs: [
            {"title": "Phế phi bị đày", "caption": "Tóm tắt clip #reviewphim", "hashtags": ["reviewphim"]},
            {"title": "Cửu hoàng tử", "caption": "Diễn biến căng #cotrang", "hashtags": ["cotrang"]},
        ],
    )
    monkeypatch.setattr(job_publish, "find_job_transcript", lambda *_a, **_k: "Phế phi bị đày vào lãnh cung")
    monkeypatch.setattr(job_publish, "wake_distribution_worker", lambda: None)
    monkeypatch.setattr(job_publish, "wake_tiktok_worker", lambda: None)

    result = publish_job_to_groups(db, "job_done", group_ids=["grp_review"], output_dir=str(tmp_path))
    assert result["assigned"] == 2
    assert result["skipped"] == 0
    assert "Review Phim" in result["message"]
    with get_db_connection(db) as conn:
        videos = conn.execute("SELECT channel_id, title, publish_status FROM channel_videos WHERE job_id='job_done'").fetchall()
        dist = conn.execute("SELECT COUNT(*) FROM distribution_jobs").fetchone()[0]
    assert {row["channel_id"] for row in videos} == {"chan_a", "chan_b"}
    assert all(row["publish_status"] == "READY" for row in videos)
    assert all("vietsub" not in (row["title"] or "").lower() for row in videos)
    assert dist == 2

    with get_db_connection(db) as conn:
        conn.execute(
            "UPDATE jobs SET reup_config = ? WHERE job_id = 'job_done'",
            (json.dumps({
                "affiliate_link": "https://shp.ee/xyz",
                "affiliate_product": "giấy vệ sinh",
            }, ensure_ascii=False),),
        )
        conn.execute("DELETE FROM channel_videos")
        conn.execute("DELETE FROM distribution_jobs")
        conn.commit()

    with_link = publish_job_to_groups(
        db,
        "job_done",
        group_ids=["grp_review"],
        output_dir=str(tmp_path),
        affiliate_link="https://shp.ee/xyz",
        affiliate_product="giấy vệ sinh",
    )
    assert with_link["assigned"] == 2
    with get_db_connection(db) as conn:
        captions = [row["caption"] for row in conn.execute("SELECT caption FROM channel_videos")]
        dist_captions = [row["caption"] for row in conn.execute("SELECT caption FROM distribution_jobs")]
        stored = conn.execute("SELECT reup_config FROM jobs WHERE job_id='job_done'").fetchone()["reup_config"]
    assert all((c or "").startswith("https://shp.ee/xyz") for c in captions)
    assert all("ủng hộ shop qua link: https://shp.ee/xyz" in (c or "") for c in captions)
    assert all("ủng hộ shop qua link: https://shp.ee/xyz" in (c or "") for c in dist_captions)
    assert "shp.ee/xyz" in stored

    again = publish_job_to_groups(db, "job_done", group_ids=["grp_review"], output_dir=str(tmp_path))
    assert again["assigned"] == 0
    assert again["skipped"] == 2


def test_path_for_platform_prefers_variant(tmp_path):
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    master = tmp_path / "job_done.mp4"
    fb = tmp_path / "job_done.facebook.mp4"
    master.write_bytes(b"m")
    fb.write_bytes(b"f")
    now = _now()
    with get_db_connection(db) as conn:
        conn.execute(
            """INSERT INTO jobs (job_id, source_url, platform, status, progress_percent,
                output_file_path, watermark_config, reup_config, created_at, updated_at, message, logs)
               VALUES ('job_done', '', 'youtube', 'COMPLETED', 100, ?, '{}', '{}', ?, ?, '', '[]')""",
            (str(master), now, now),
        )
        conn.commit()
    files = job_media_files("job_done", db, str(tmp_path))
    assert path_for_platform(files, "facebook").endswith("facebook.mp4")
    assert path_for_platform(files, "youtube").endswith("job_done.mp4") or "youtube" in path_for_platform(files, "youtube")


def test_parse_hashtags_splits_and_dedupes():
    assert parse_hashtags("#reviewphim, phimhay reviewphim xuhuong") == [
        "reviewphim",
        "phimhay",
        "xuhuong",
    ]
    assert parse_hashtags(["#A", "a", "B"]) == ["A", "B"]


def test_publish_job_uses_manual_title_and_hashtags(tmp_path, monkeypatch):
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    video = tmp_path / "job_done.mp4"
    video.write_bytes(b"video")
    _seed(db, video)
    from app.services import job_publish

    monkeypatch.setattr(job_publish, "write_facebook_posts", lambda **kwargs: (_ for _ in ()).throw(AssertionError("AI must not run")))
    monkeypatch.setattr(job_publish, "find_job_transcript", lambda *_a, **_k: "")
    monkeypatch.setattr(job_publish, "wake_distribution_worker", lambda: None)
    monkeypatch.setattr(job_publish, "wake_tiktok_worker", lambda: None)

    result = publish_job_to_groups(
        db,
        "job_done",
        group_ids=["grp_review"],
        output_dir=str(tmp_path),
        title="Clip tự làm",
        caption="Mô tả ngắn",
        hashtags=["reviewphim", "xuhuong"],
    )
    assert result["assigned"] == 2
    with get_db_connection(db) as conn:
        rows = conn.execute("SELECT title, caption, tags FROM channel_videos").fetchall()
    assert {row["title"] for row in rows} == {"Clip tự làm"}
    assert all(row["caption"] == "Mô tả ngắn" for row in rows)
    assert all("reviewphim" in (row["tags"] or "") for row in rows)


def test_publish_original_videos_skips_reup(tmp_path, monkeypatch):
    import shutil
    from app.services import original_publish

    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    src = tmp_path / "mine.mp4"
    src.write_bytes(b"video-bytes" * 800)
    _seed(db, tmp_path / "unused.mp4")

    monkeypatch.setattr(
        original_publish,
        "prepare_facebook_reel",
        lambda source, dest: shutil.copy2(source, dest) or dest,
    )
    calls = []

    def _fake_publish(*_args, **kwargs):
        calls.append(kwargs)
        return {"assigned": 2, "queued": 2, "message": "ok"}

    monkeypatch.setattr(original_publish, "publish_job_to_groups", _fake_publish)

    result = original_publish.publish_original_videos(
        db,
        [str(src)],
        group_ids=["grp_review"],
        title="Tự làm",
        hashtags=["phimhay"],
        affiliate_link="https://shp.ee/abc",
        affiliate_product="giấy",
        output_dir=str(tmp_path),
    )
    assert result["videos"] == 1
    assert result["assigned"] == 2
    assert calls and calls[0]["title"] == "Tự làm"
    assert calls[0]["hashtags"] == ["phimhay"]
    with get_db_connection(db) as conn:
        job = conn.execute(
            "SELECT status, platform, reup_config FROM jobs WHERE job_id LIKE 'job_orig_%'"
        ).fetchone()
    assert job["status"] == "COMPLETED"
    assert job["platform"] == "original"
    assert "Tự làm" in job["reup_config"]
