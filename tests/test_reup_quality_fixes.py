"""Regression tests for reup FX, Chinese text wipe, vietsub, and lip-sync."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.job import ReupConfig
from app.services.opencv_inpainter import (
    _inpaint_radius,
    _resolve_scan_region,
    extract_adaptive_text_mask,
)
from app.services.reup_service import build_reup_filtergraph, scale_srt_timestamps
from app.services.subtitle_detector import detect_text_boxes_opencv, HAS_APPLE_VISION
from app.services.watermark_service import inpaint_video_ffmpeg


def test_apple_vision_flag_defined_on_linux():
    assert HAS_APPLE_VISION is False or HAS_APPLE_VISION is True


def test_auto_scan_includes_top_of_frame():
    x1, y1, x2, y2, is_auto = _resolve_scan_region((0, 0, 0, 0), 1080, 1920)
    assert is_auto is True
    assert y1 == 0
    assert x1 == 0
    assert y2 == 1920
    assert x2 == 1080


def test_manual_roi_is_not_auto():
    x1, y1, x2, y2, is_auto = _resolve_scan_region((10, 20, 100, 80), 1080, 1920)
    assert is_auto is False
    assert (x1, y1, x2, y2) == (10, 20, 110, 100)


def test_inpaint_radius_not_capped_at_two():
    assert _inpaint_radius(3) >= 3
    assert _inpaint_radius(5) == 5
    assert _inpaint_radius(1) >= 3


def test_opencv_detects_white_text_blob():
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    frame[:] = (30, 30, 30)
    frame[180:220, 20:300] = (240, 240, 240)
    boxes = detect_text_boxes_opencv(frame, padding=2)
    assert isinstance(boxes, list)
    mask = extract_adaptive_text_mask(frame[180:220, 20:300])
    assert mask.size > 0
    assert int(mask.max()) == 255


def test_reup_defaults_are_visible():
    cfg = ReupConfig()
    assert cfg.hflip is True
    assert cfg.pitch_shift is True
    assert cfg.crop_percent > 0
    assert cfg.film_grain > 0
    assert cfg.enable_vocal_mute is True
    fc, has_a, vf, af = build_reup_filtergraph(cfg, has_audio=True)
    assert "hflip" in vf
    assert "noise=" in vf
    assert "eq=" in vf
    assert "scale=trunc(iw/2)*2" in vf
    assert "crop=" in vf


def test_crop_percent_slider_units():
    cfg = ReupConfig(crop_percent=2.0)
    assert 0.01 <= cfg.crop_percent <= 0.05
    cfg2 = ReupConfig(crop_percent=0.02)
    assert abs(cfg2.crop_percent - 0.02) < 1e-6


def test_identity_fx_still_has_grain_and_scale():
    cfg = ReupConfig(
        hflip=False,
        crop_percent=0.0,
        brightness=0.0,
        contrast=1.0,
        saturation=1.0,
        film_grain=3.0,
        pitch_shift=False,
        speed_factor=1.0,
        color_adjust=False,
    )
    _, _, vf, _ = build_reup_filtergraph(cfg, has_audio=False)
    assert "noise=" in vf
    assert "scale=trunc(iw/2)*2" in vf


def test_scale_srt_timestamps(tmp_path):
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nXin chào\n\n", encoding="utf-8")
    out = scale_srt_timestamps(str(srt), 2.0, str(tmp_path / "b.srt"))
    text = open(out, encoding="utf-8").read()
    assert "00:00:00,500" in text
    assert "Xin chào" in text


def test_even_crop_filter_string():
    import inspect
    from app.services import watermark_service as ws
    src = inspect.getsource(ws.inpaint_video_ffmpeg)
    assert "trunc(ih*0.82/2)*2" in src
    assert "scale=iw:ih" not in src or "trunc" in src


def test_subtitle_bottom_crop_folded_into_reup():
    cfg = ReupConfig(subtitle_bottom_crop=0.18, crop_percent=0.02)
    _, _, vf, _ = build_reup_filtergraph(cfg, has_audio=False)
    assert "trunc(ih*(1-0.1800)/2)*2" in vf
    assert "crop=iw*(1-2*0.0200)" in vf


def test_hardsub_filter_uses_original_timestamps_before_setpts(tmp_path):
    srt = tmp_path / "vi.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n\n", encoding="utf-8")
    cfg = ReupConfig(speed_factor=1.03, film_grain=0.0)
    _, _, vf, _ = build_reup_filtergraph(cfg, has_audio=False, burn_srt_path=str(srt))
    assert "subtitles=" in vf
    assert "drawbox=" in vf
    assert vf.index("drawbox=") < vf.index("subtitles=")
    assert vf.index("subtitles=") < vf.index("setpts=")


def test_lipsync_compacts_and_rates():
    from app.services.lipsync_service import (
        compact_for_duration,
        edge_rate_tag,
        estimate_tts_duration,
        regroup_words_to_cues,
    )
    long = "Vui lòng thì giơ hai tay lên nào các bạn ạ"
    short = compact_for_duration(long, 0.8)
    assert len(short) <= len(long)
    assert "thì" not in short.split()
    rate = edge_rate_tag(2.0, 1.2)
    assert rate.startswith("+")
    assert estimate_tts_duration("xin chào") >= 0.28
    words = [
        {"text": "xin", "start": 0.0, "end": 0.2},
        {"text": "chào", "start": 0.21, "end": 0.5},
        {"text": "bạn", "start": 1.2, "end": 1.5},
    ]
    cues = regroup_words_to_cues(words, gap=0.28)
    assert len(cues) == 2
    assert cues[0]["text"].startswith("xin")
    assert abs(cues[1]["start_time"] - 1.2) < 1e-6


def test_lipsync_default_on():
    cfg = ReupConfig()
    assert cfg.enable_lipsync is True


def test_tts_mix_ducks_only_during_speech():
    from app.services.reup_service import build_tts_bgm_mix_filter
    from app.services.audio_service import build_vocal_mute_ffmpeg_filter
    fc = build_tts_bgm_mix_filter()
    assert "sidechaincompress" in fc
    assert "volume=0.22" not in fc
    mute = build_vocal_mute_ffmpeg_filter(preserve_bgm=True)
    assert "volume=0.78" in mute
    assert "volume=0.30" not in mute
