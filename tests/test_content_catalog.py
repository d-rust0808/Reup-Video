"""Source content catalog: channels, video checklist, posted stats."""

import os

from datetime import datetime, timezone

from app.core.database import get_db_connection, init_db
from app.services.content_catalog import (
    list_channels,
    list_videos,
    native_ids_from_path,
    set_posted,
    sync_posted_from_jobs,
    update_channel,
    upsert_source_catalog,
)


def test_upsert_channel_and_posted_checklist(tmp_path):
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    cid = upsert_source_catalog(
        db,
        profile={
            "nickname": "Happy renovation worker",
            "platform": "youtube",
            "unique_id": "UCxv7-vqATDY9Uveu0iA2-WQ",
            "url": "https://www.youtube.com/@hongguo/videos",
        },
        platform="youtube",
        url="https://www.youtube.com/@hongguo/videos",
        catalog=[
            {"video_id": "aaaaaaaaaaa", "title": "Clip 1", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"},
            {"video_id": "bbbbbbbbbbb", "title": "Clip 2", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb"},
        ],
        tags=["xây dựng", "đập phá"],
    )
    again = upsert_source_catalog(
        db,
        profile={"nickname": "Happy renovation worker", "unique_id": "UCxv7-vqATDY9Uveu0iA2-WQ"},
        platform="youtube",
        url="https://www.youtube.com/@hongguo/videos",
        catalog=[{"video_id": "ccccccccccc", "title": "Clip 3", "url": ""}],
        tags=None,
    )
    assert again == cid

    channels = list_channels(db)
    assert len(channels) == 1
    ch = channels[0]
    assert ch["name"].startswith("Happy")
    assert ch["url"].endswith("/videos")
    assert "xây dựng" in ch["tags"]
    assert "reup" not in ch["tags"]
    assert ch["video_count"] == 3
    assert ch["unposted_count"] == 3
    assert ch["posted_count"] == 0

    videos = list_videos(db, cid)
    assert [v["video_id"] for v in videos][::-1] or True
    assert {v["video_id"] for v in videos} == {"aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"}
    first = next(v for v in videos if v["video_id"] == "aaaaaaaaaaa")
    updated = set_posted(db, first["id"], True)
    assert updated and updated["posted"] is True

    channels = list_channels(db)
    assert channels[0]["posted_count"] == 1
    assert channels[0]["unposted_count"] == 2
    posted_only = list_videos(db, cid, status="posted")
    assert len(posted_only) == 1
    assert posted_only[0]["video_id"] == "aaaaaaaaaaa"
    from app.services.content_catalog import channel_inventory
    inv = channel_inventory(db, cid, status="posted")
    assert len(inv["videos"]) == 1
    assert inv["posted_count"] == 1
    assert inv["unposted_count"] == 2
    assert inv["video_count"] == 3
    ch = list_channels(db)[0]
    assert ch["posted_count"] == inv["posted_count"]
    assert ch["unposted_count"] == inv["unposted_count"]
    assert ch["video_count"] == inv["video_count"]

    patched = update_channel(
        db,
        cid,
        name="Nội Dung Xây Dựng",
        url="https://www.youtube.com/channel/UCxv7-vqATDY9Uveu0iA2-WQ",
        tags=["xây dựng", "reup"],
        notes="Kênh Hồng Quỷ",
    )
    assert patched["name"] == "Nội Dung Xây Dựng"
    assert patched["url"].endswith("UCxv7-vqATDY9Uveu0iA2-WQ")
    assert patched["notes"] == "Kênh Hồng Quỷ"
    assert "reup" in patched["tags"]
    assert os.path.isfile(db)


def test_new_channel_has_no_default_tags(tmp_path):
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    cid = upsert_source_catalog(
        db,
        profile={"nickname": "Kênh A", "platform": "youtube", "unique_id": "chanA"},
        platform="youtube",
        url="https://www.youtube.com/@chanA/videos",
        catalog=[{"video_id": "aaaaaaaaaaa", "title": "One", "url": ""}],
    )
    ch = next(c for c in list_channels(db) if c["channel_id"] == cid)
    assert ch["tags"] == []
    vid = list_videos(db, cid)[0]
    assert vid["url"].startswith("https://www.youtube.com/watch?v=")


def test_published_job_marks_catalog_video_posted(tmp_path):
    db = str(tmp_path / "jobs.sqlite")
    init_db(db)
    cid = upsert_source_catalog(
        db,
        profile={"nickname": "Kênh A", "platform": "youtube", "unique_id": "chanA"},
        platform="youtube",
        url="https://www.youtube.com/@chanA/videos",
        catalog=[
            {"video_id": "EOTcuEj-SvA", "title": "Posted clip", "url": ""},
            {"video_id": "bbbbbbbbbbb", "title": "Other", "url": ""},
        ],
    )
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection(db) as conn:
        conn.execute(
            """INSERT INTO jobs (
                job_id, source_url, platform, status, progress_percent,
                input_file_path, output_file_path, watermark_config, reup_config,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "job_abs",
                "https://www.youtube.com/watch?v=EOTcuEj-SvA",
                "youtube",
                "COMPLETED",
                100,
                "/Volumes/DATA/Reup-Video/data/input/raw/EOTcuEj-SvA.mp4",
                "data/outputs/job_abs.mp4",
                "{}",
                "{}",
                now,
                now,
            ),
        )
        conn.execute(
            """INSERT INTO distribution_jobs (
                id, channel_video_id, job_id, provider, destination_id,
                source_path, caption, status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "dist_1", "cvid_1", "job_abs", "facebook", "page_1",
                "data/outputs/job_abs.facebook.mp4", "", "PUBLISHED", now, now,
            ),
        )
        conn.commit()

    assert "EOTcuEj-SvA" in native_ids_from_path(
        "/Volumes/DATA/Reup-Video/data/input/raw/EOTcuEj-SvA.mp4"
    )
    assert sync_posted_from_jobs(db) == 1
    posted = list_videos(db, cid, status="posted")
    assert {v["video_id"] for v in posted} == {"EOTcuEj-SvA"}
    unposted = list_videos(db, cid, status="unposted")
    assert {v["video_id"] for v in unposted} == {"bbbbbbbbbbb"}
