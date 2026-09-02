"""Studio session compacting + multi-platform export helpers + frame PNG."""

import os
import sys
import sqlite3

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.job import ReupConfig
from app.services.platform_export import (
    build_variant_filters,
    canvas_layout,
    fit_vf,
    normalize_platforms,
    PRESETS,
)


def test_process_payload_keeps_canvas_fill():
    from app.api.process import (
        ProcessJobRequest,
        ReupPayload,
        resolve_submitted_canvas_fill,
        resolve_submitted_subtitle_y,
        resolve_submitted_cover_pad,
        resolve_submitted_subtitle_box_w,
        resolve_submitted_subtitle_box_h,
    )

    nested = ProcessJobRequest(reup=ReupPayload(canvas_fill=0.77, caption_cover="image", subtitle_y=0.72))
    assert abs(resolve_submitted_canvas_fill(nested) - 0.77) < 1e-6
    assert abs(resolve_submitted_subtitle_y(nested) - 0.72) < 1e-6
    padded = ProcessJobRequest(reup=ReupPayload(cover_pad=0.12, caption_cover="black_solid"))
    assert abs(resolve_submitted_cover_pad(padded) - 0.12) < 1e-6
    percent = ProcessJobRequest(cover_pad=8)
    assert abs(resolve_submitted_cover_pad(percent) - 0.08) < 1e-6
    flat = ProcessJobRequest(canvas_fill=80)
    assert abs(resolve_submitted_canvas_fill(flat) - 0.8) < 1e-6
    missing = ProcessJobRequest()
    assert resolve_submitted_canvas_fill(missing) == 0.0
    boxed = ProcessJobRequest(reup=ReupPayload(subtitle_box_w=0.9, subtitle_box_h=0.12))
    assert abs(resolve_submitted_subtitle_box_w(boxed) - 0.9) < 1e-6
    assert abs(resolve_submitted_subtitle_box_h(boxed) - 0.12) < 1e-6
    off_plate = ProcessJobRequest(reup=ReupPayload(subtitle_box_h=0))
    assert resolve_submitted_subtitle_box_h(off_plate) == 0.0


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


def test_fit_vf_fill_keeps_contain_and_shifts_pad():
    vf = fit_vf(1080, 1920, fill=1)
    assert "force_original_aspect_ratio=decrease" in vf
    assert "force_original_aspect_ratio=increase" not in vf
    assert "crop=" not in vf
    assert "pad=1080:1920" in vf
    assert "(1-1.0000)" in vf


def test_fit_vf_mid_fill_scales_then_pads():
    vf = fit_vf(1080, 1920, fill=0.4)
    assert "force_original_aspect_ratio=decrease" in vf
    assert "pad=1080:1920" in vf
    assert "0.4000" in vf
    assert "crop=" not in vf


def test_canvas_layout_fill_grows_plate_without_scaling_picture():
    contain = canvas_layout(1920, 1080, 1080, 1920, 0)
    tall = canvas_layout(1920, 1080, 1080, 1920, 1)
    assert abs(tall["fitted_w"] - contain["fitted_w"]) < 0.5
    assert abs(tall["fitted_h"] - contain["fitted_h"]) < 0.5
    assert tall["pad_y"] < 1
    assert tall["plate_h"] > contain["fitted_h"]


def test_canvas_layout_caps_plate_to_logo_aspect():
    contain = canvas_layout(1920, 1080, 1080, 1920, 0, logo_aspect=16 / 9)
    over = canvas_layout(1920, 1080, 1080, 1920, 0.77, logo_aspect=16 / 9)
    full = canvas_layout(1920, 1080, 1080, 1920, 1, logo_aspect=16 / 9)
    rest = 1920 - contain["fitted_h"]
    natural = contain["fitted_w"] / (16 / 9)
    assert abs(over["fitted_h"] - contain["fitted_h"]) < 0.5
    assert over["plate_h"] <= natural + 1
    assert over["plate_h"] < rest * 0.77 - 10
    assert abs(over["plate_h"] - full["plate_h"]) < 0.5
    assert over["pad_y"] > 100
    leftover = rest - over["plate_h"]
    assert abs(over["pad_y"] - leftover / 2) < 1


def test_build_variant_filters_caps_plate_to_banner_aspect(tmp_path):
    from PIL import Image

    banner = tmp_path / "wide.png"
    Image.new("RGB", (1600, 900), (20, 20, 20)).save(banner)
    spec = build_variant_filters(
        1920, 1080, 1080, 1920, fill=0.77,
        plate_banners=[{"image_path": str(banner), "kind": "banner", "band_h": 0.3}],
    )
    assert spec["mode"] == "complex"
    layout = canvas_layout(1920, 1080, 1080, 1920, 0.77, logo_aspect=1600 / 900)
    assert "force_original_aspect_ratio=increase" in spec["filter_complex"]
    assert "crop=" in spec["filter_complex"]
    assert "overlay=0:" in spec["filter_complex"]
    assert layout["plate_h"] < (1920 - layout["fitted_h"]) * 0.77 - 10


def test_build_variant_filters_plate_uses_full_width_cover(tmp_path):
    banner = tmp_path / "logo.png"
    banner.write_bytes(b"\x89PNG\r\n\x1a\n")
    spec = build_variant_filters(
        1920, 1080, 1080, 1920, fill=1,
        plate_banners=[{"image_path": str(banner), "kind": "banner", "band_h": 0.3}],
    )
    assert spec["mode"] == "complex"
    fc = spec["filter_complex"]
    assert "force_original_aspect_ratio=increase" in fc
    assert "crop=" in fc
    assert "overlay=0:" in fc
    landscape = build_variant_filters(
        1920, 1080, 1920, 1080, fill=1,
        plate_banners=[{"image_path": str(banner), "kind": "banner", "band_h": 0.3}],
    )
    assert landscape["mode"] == "complex"
    assert "overlay=0:H-h" in landscape["filter_complex"]


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
    for name in (
        "12345678.mp4",
        "12345678.json",
        "12345678.vi.srt",
        "12345678_vi.aligned.srt",
        "douyin_12345678_title.mp4",
    ):
        (tmp_path / name).write_bytes(b"video")
    (tmp_path / "87654321.mp4").write_bytes(b"keep")

    result = asyncio.run(delete_library_video("12345678"))

    assert result["deleted"] is True
    assert sorted(result["removed"]) == [
        "12345678.json",
        "12345678.mp4",
        "12345678.vi.srt",
        "12345678_vi.aligned.srt",
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
    assert "#viral" in cap
    assert "#vietsub" not in cap.lower()
    empty = build_caption(None, "youtube")
    assert "Video mới" not in empty
    assert "#vietsub" not in empty.lower()
    assert "#youtube" not in empty.lower()
    tagged = build_caption("Mèo vui", "facebook", ["reup", "vietsub", "reels"])
    assert "#reup" not in tagged.lower()
    assert "#vietsub" not in tagged.lower()
    assert "#reels" in tagged

def test_failed_ws_payload_reads_error_message():
    import inspect
    from app.core.ws_manager import ConnectionManager
    src = inspect.getsource(ConnectionManager.on_queue_update)
    assert "error_msg" not in src
    assert "error_message" in src
