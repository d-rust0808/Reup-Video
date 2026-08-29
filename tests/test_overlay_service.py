"""Channel logo/khung overlays persist for the entire video duration."""

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.job import OverlayItem, ReupConfig
from app.services.overlay_service import (
    append_overlay_filter,
    append_plate_banner_filter,
    normalize_overlays,
    overlay_input_args,
    overlays_for_job,
    split_master_and_plate_overlays,
)


def _touch_png(path):
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return str(path)


def test_normalize_skips_missing_file(tmp_path):
    items = normalize_overlays([{"image_path": str(tmp_path / "nope.png"), "kind": "logo"}])
    assert items == []


def test_normalize_logo_clamps_and_preserves_position(tmp_path):
    png = _touch_png(tmp_path / "logo.png")
    items = normalize_overlays(
        [
            {
                "image_path": png,
                "kind": "logo",
                "x": 0.72,
                "y": 0.05,
                "w": 0.18,
                "opacity": 0.8,
            }
        ]
    )
    assert len(items) == 1
    assert items[0]["kind"] == "logo"
    assert abs(items[0]["x"] - 0.72) < 1e-6
    assert abs(items[0]["y"] - 0.05) < 1e-6
    assert abs(items[0]["w"] - 0.18) < 1e-6
    assert abs(items[0]["opacity"] - 0.8) < 1e-6


def test_normalize_khung_alias_fills_frame(tmp_path):
    png = _touch_png(tmp_path / "frame.png")
    items = normalize_overlays(
        [{"image_path": png, "kind": "khung", "x": 0.4, "y": 0.4, "w": 0.2}]
    )
    assert items[0]["kind"] == "frame"
    assert items[0]["x"] == 0.0
    assert items[0]["y"] == 0.0
    assert items[0]["w"] == 1.0


def test_append_overlay_repeats_still_for_full_duration(tmp_path):
    png = _touch_png(tmp_path / "logo.png")
    items = normalize_overlays(
        [{"image_path": png, "kind": "logo", "x": 0.10, "y": 0.08, "w": 0.20, "opacity": 1}]
    )
    fc, paths = append_overlay_filter("[0:v]format=yuv420p[v_out]", items, first_overlay_index=1)
    assert paths and os.path.isfile(paths[0])
    assert "[v_out]" in fc
    assert "eof_action=repeat" in fc
    assert "repeatlast=1" in fc
    assert "W*0.1000" in fc
    assert "H*0.0800" in fc
    assert "scale=216:-1" in fc


def test_append_frame_covers_full_frame(tmp_path):
    png = _touch_png(tmp_path / "khung.png")
    items = normalize_overlays([{"image_path": png, "kind": "frame"}])
    fc, _ = append_overlay_filter("[0:v]null[v_out]", items, 1)
    assert "scale=1080:1920" in fc
    assert "overlay=0:0:format=auto:eof_action=repeat" in fc


def test_append_skips_when_v_out_missing(tmp_path):
    png = _touch_png(tmp_path / "logo.png")
    items = normalize_overlays([{"image_path": png, "kind": "logo"}])
    original = "[0:v]format=yuv420p[vout]"
    fc, paths = append_overlay_filter(original, items, 1)
    assert fc == original
    assert paths == []


def test_reup_config_accepts_overlays(tmp_path):
    png = _touch_png(tmp_path / "logo.png")
    cfg = ReupConfig(
        overlays=[OverlayItem(image_path=png, kind="logo", x=0.78, y=0.04, w=0.18)]
    )
    assert len(cfg.overlays) == 1
    assert cfg.overlays[0].kind == "logo"


def test_overlay_item_keeps_banner_kind(tmp_path):
    png = _touch_png(tmp_path / "banner.png")
    item = OverlayItem(image_path=png, kind="banner", band_h=0.30, x=0, y=0, w=1)
    assert item.kind == "banner"
    assert abs(item.band_h - 0.30) < 1e-6
    assert abs(item.w - 1.0) < 1e-6
    assert abs(item.y - 0.70) < 1e-6
    cfg = ReupConfig(overlays=[item], caption_cover="image", caption_cover_image=png)
    dumped = cfg.model_dump()
    assert dumped["overlays"][0]["kind"] == "banner"
    items = normalize_overlays(cfg.overlays)
    assert items and items[0]["kind"] == "banner"
    assert abs(items[0]["band_h"] - 0.30) < 1e-6


def test_wide_strip_logo_promotes_to_bottom_banner(tmp_path):
    from PIL import Image

    png = tmp_path / "wide.png"
    Image.new("RGB", (800, 200), (255, 180, 80)).save(png)
    items = normalize_overlays(
        [{"image_path": str(png), "kind": "logo", "x": 0, "y": 0, "w": 1, "opacity": 1}]
    )
    assert items and items[0]["kind"] == "banner"
    assert abs(items[0]["w"] - 1.0) < 1e-6
    assert items[0]["y"] >= 0.60
    fc, _ = append_overlay_filter("[0:v]null[v_out]", items, 1, main_size=(690, 1228))
    assert "overlay=0:H-h" in fc
    assert "W*" not in fc


def test_ensure_caption_cover_banner_dedupes_logo(tmp_path):
    from app.services.overlay_service import ensure_caption_cover_banner

    png = _touch_png(tmp_path / "cover.png")
    items = ensure_caption_cover_banner(
        [{"image_path": png, "kind": "logo", "x": 0, "y": 0, "w": 1, "opacity": 1}],
        png,
        0.30,
    )
    assert len(items) == 1
    assert items[0]["kind"] == "banner"
    assert abs(items[0]["band_h"] - 0.30) < 1e-6
    fc, paths = append_overlay_filter("[0:v]null[v_out]", items, 1, main_size=(690, 1228))
    assert len(paths) == 1
    assert "overlay=0:H-h" in fc
    assert "crop=690:" in fc


def test_split_defers_banners_to_plate_when_fill_and_landscape(tmp_path):
    banner = _touch_png(tmp_path / "banner.png")
    logo = _touch_png(tmp_path / "logo.png")
    items = [
        {"image_path": banner, "kind": "banner", "band_h": 0.30},
        {"image_path": logo, "kind": "logo", "x": 0.1, "y": 0.1, "w": 0.2},
    ]
    master, plate = split_master_and_plate_overlays(items, canvas_fill=0.8, src_w=1920, src_h=1080)
    assert [m["kind"] for m in master] == ["logo"]
    assert [p["kind"] for p in plate] == ["banner"]

    kept, empty = split_master_and_plate_overlays(items, canvas_fill=0, src_w=1920, src_h=1080)
    assert empty == []
    assert any(m["kind"] == "banner" for m in kept)

    portrait, no_plate = split_master_and_plate_overlays(items, canvas_fill=1, src_w=1080, src_h=1920)
    assert no_plate == []
    assert any(m["kind"] == "banner" for m in portrait)


def test_overlays_for_job_defers_caption_cover_image(tmp_path):
    png = _touch_png(tmp_path / "cover.png")
    cfg = ReupConfig(
        caption_cover="image",
        caption_cover_image=png,
        subtitle_bottom_crop=0.30,
        canvas_fill=0.7,
        overlays=[],
    )
    master, plate = overlays_for_job(cfg, 1920, 1080)
    assert master == []
    assert len(plate) == 1
    assert plate[0]["kind"] == "banner"

    youtube_only = cfg.model_copy(update={"target_platforms": ["youtube"]})
    kept, empty = overlays_for_job(youtube_only, 1920, 1080)
    assert empty == []
    assert len(kept) == 1
    assert kept[0]["kind"] == "banner"

    no_fill = cfg.model_copy(update={"canvas_fill": 0})
    master0, plate0 = overlays_for_job(no_fill, 1920, 1080)
    assert master0 == []
    assert len(plate0) == 1


def test_plate_banner_covers_full_canvas_width(tmp_path):
    png = _touch_png(tmp_path / "banner.png")
    items = normalize_overlays([{"image_path": png, "kind": "banner", "band_h": 0.30}])
    fc, paths = append_plate_banner_filter(
        "[0:v]null[v_out]", items, 1, 1080, 1920, plate_y=608, plate_h=1312
    )
    assert paths == [str(png)] or paths[0].endswith("banner.png")
    assert "force_original_aspect_ratio=increase" in fc
    assert "crop=1080:1312" in fc
    assert "overlay=0:608" in fc
    assert "(W-w)/2" not in fc


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_ffmpeg_overlay_lasts_whole_clip(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    vid = tmp_path / "in.mp4"
    logo = tmp_path / "logo.png"
    out = tmp_path / "out.mp4"
    subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=320x560:d=1.2",
            "-pix_fmt", "yuv420p", str(vid),
        ],
        check=True,
    )
    subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:s=48x48:d=0.04",
            "-frames:v", "1", str(logo),
        ],
        check=True,
    )
    items = normalize_overlays(
        [{"image_path": str(logo), "kind": "logo", "x": 0.05, "y": 0.05, "w": 0.25}]
    )
    fc, paths = append_overlay_filter("[0:v]format=yuv420p[v_out]", items, 1)
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(vid),
        *overlay_input_args(paths),
        "-filter_complex", fc, "-map", "[v_out]", "-an",
        "-pix_fmt", "yuv420p", str(out),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr[-800:]
    assert out.exists() and out.stat().st_size > 0

    probe = subprocess.run(
        [ffmpeg, "-i", str(out)],
        capture_output=True,
        text=True,
    )
    info = (probe.stderr or "") + (probe.stdout or "")
    assert "Duration: 00:00:01." in info or "Duration: 00:00:02." in info
