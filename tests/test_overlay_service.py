"""Channel logo/khung overlays persist for the entire video duration."""

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.job import OverlayItem, ReupConfig
from app.services.overlay_service import append_overlay_filter, normalize_overlays, overlay_input_args


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
    assert "nullsink" in fc
    assert "shortest=1" in fc
    assert "W*0.1000" in fc
    assert "H*0.0800" in fc
    assert "iw*0.2000" in fc


def test_append_frame_covers_full_frame(tmp_path):
    png = _touch_png(tmp_path / "khung.png")
    items = normalize_overlays([{"image_path": png, "kind": "frame"}])
    fc, _ = append_overlay_filter("[0:v]null[v_out]", items, 1)
    assert "scale2ref=w=iw:h=ih" in fc
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
