"""Studio session compacting + multi-platform export helpers + frame PNG."""

import os
import sys
import sqlite3

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.job import ReupConfig
from app.services.platform_export import fit_vf, normalize_platforms, PRESETS


def test_normalize_platforms_aliases():
    assert normalize_platforms(["TikTok", "yt", "fb", "junk"]) == [
        "tiktok",
        "youtube_shorts",
        "facebook",
    ]
    assert normalize_platforms([]) == []
    assert "youtube" in PRESETS


def test_fit_vf_is_contain_pad():
    vf = fit_vf(1080, 1920)
    assert "scale=1080:1920:force_original_aspect_ratio=decrease" in vf
    assert "pad=1080:1920" in vf


def test_reup_config_default_platforms():
    cfg = ReupConfig()
    assert "tiktok" in cfg.target_platforms
    assert "youtube_shorts" in cfg.target_platforms
    assert cfg.frame_enabled is False


def test_delete_library_video_removes_canonical_files_and_sidecars(tmp_path, monkeypatch):
    import asyncio
    from app.api.extract import delete_library_video
    from app.config import settings

    monkeypatch.setattr(settings, "RAW_INPUT_DIR", str(tmp_path))
    for name in ("12345678.mp4", "12345678.json", "12345678.vi.srt", "douyin_12345678_title.mp4"):
        (tmp_path / name).write_bytes(b"video")
    (tmp_path / "87654321.mp4").write_bytes(b"keep")

    result = asyncio.run(delete_library_video("12345678"))

    assert result["deleted"] is True
    assert sorted(result["removed"]) == [
        "12345678.json",
        "12345678.mp4",
        "12345678.vi.srt",
        "douyin_12345678_title.mp4",
    ]
    assert (tmp_path / "87654321.mp4").exists()

    repeated = asyncio.run(delete_library_video("12345678"))
    assert repeated == {"video_id": "12345678", "deleted": True, "removed": []}


def test_delete_job_removes_platform_variants(tmp_path):
    from app.services.queue_manager import BatchQueueManager

    db_path = tmp_path / "jobs.db"
    output = tmp_path / "job_delete.mp4"
    variants = [output, tmp_path / "job_delete.tiktok.mp4", tmp_path / "job_delete.facebook.mp4"]
    for path in variants:
        path.write_bytes(b"video")

    manager = BatchQueueManager(db_path=str(db_path), max_concurrent_jobs=1)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, status, output_file_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("job_delete", "COMPLETED", str(output), "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()

    assert manager.delete_job("job_delete") is True
    assert all(not path.exists() for path in variants)
    assert manager.get_job("job_delete") is None


def test_frame_png_has_alpha_ring():
    from app.services.frame_studio import render_frame_png, PRESETS as FRAMES
    import cv2

    path = render_frame_png("gold", out_name="test_gold.png")
    assert os.path.exists(path)
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    assert img is not None and img.shape[2] == 4
    h, w = img.shape[:2]
    assert img[0, 0, 3] > 200
    assert img[h // 2, w // 2, 3] < 10
    assert any(p["id"] == "cinema" for p in FRAMES)

def test_hybrid_lama_only_every_fifth_frame():
    import inspect
    from app.services.opencv_inpainter import hybrid_inpaint_frame
    src = inspect.getsource(hybrid_inpaint_frame)
    assert "frame_index % 5" in src
    assert "use_lama" in src


def test_video_has_overlay_text_missing_file():
    from app.services.subtitle_detector import video_has_overlay_text
    assert video_has_overlay_text("/no/such/file.mp4") is True


def test_channel_download_does_not_auto_reup_by_default():
    from app.api.extract import ChannelExtractRequest

    request = ChannelExtractRequest(url="https://example.com/channel")
    assert request.auto_reup is False

def test_build_caption_hashtags():
    from app.services.caption import build_caption
    cap = build_caption("Mèo vui", "douyin", ["viral"])
    assert "Mèo vui" in cap
    assert "#douyin" in cap
    assert "#vietsub" in cap
    assert "#viral" in cap
    empty = build_caption(None, "tiktok")
    assert empty.startswith("Video reup")
    assert "#tiktok" in empty

def test_failed_ws_payload_reads_error_message():
    import inspect
    from app.core.ws_manager import ConnectionManager
    src = inspect.getsource(ConnectionManager.on_queue_update)
    assert "error_msg" not in src
    assert "error_message" in src
