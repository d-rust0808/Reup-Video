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
    cfg = ReupConfig(speed_factor=1.03, film_grain=0.0, frame_enabled=False)
    _, _, vf, _ = build_reup_filtergraph(cfg, has_audio=False, burn_srt_path=str(srt))
    assert "subtitles=" in vf
    assert "BorderStyle=3" in vf
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
    assert cfg.vietsub_style == "dub"


def test_tts_mix_ducks_only_during_speech():
    from app.services.reup_service import build_tts_bgm_mix_filter, build_reup_filtergraph
    from app.services.audio_service import build_vocal_mute_ffmpeg_filter
    fc = build_tts_bgm_mix_filter()
    assert "sidechaincompress" in fc
    assert "volume=0.22" not in fc
    assert "[0:a]lowpass=f=180" not in fc
    assert "dynaudnorm" in fc
    mute = build_vocal_mute_ffmpeg_filter(preserve_bgm=True)
    assert "stereotools=" in mute
    assert "asplit=" in mute
    assert "treble=" in mute
    cfg = ReupConfig(enable_vocal_mute=True, film_grain=0, pitch_shift=False, speed_factor=1.0)
    graph, has_a, vf, af = build_reup_filtergraph(cfg, has_audio=True)
    assert has_a is True
    assert "asplit=2" in graph
    assert graph.count("[0:a]") == 1


def test_vietsub_style_auto_picks_recap_for_long_clips():
    from app.services.xai_media_service import resolve_vietsub_style, compact_vi_cue
    assert resolve_vietsub_style("auto", 60) == "dub"
    assert resolve_vietsub_style("auto", 200) == "narrator"
    assert resolve_vietsub_style("auto", 900) == "recap"
    assert resolve_vietsub_style("funny", 900) == "funny"
    assert resolve_vietsub_style("goc", 60) == "dub"
    assert resolve_vietsub_style("kechuyen", 90) == "narrator"
    assert resolve_vietsub_style("vuinhon", 900) == "funny"
    short = compact_vi_cue("Mau đưa khô cá cho tôi")
    assert "\n" not in short
    wrapped = compact_vi_cue("Đây là một câu vietsub rất dài lê thê sẽ đè hết phần hình mèo đang chạy")
    assert "\n" in wrapped or len(wrapped) <= 42


def test_mid_text_cover_appended_to_filtergraph():
    cfg = ReupConfig(text_cover_vf="delogo=x=10:y=10:w=80:h=20:show=0", film_grain=0)
    _, _, vf, _ = build_reup_filtergraph(cfg, has_audio=False)
    assert "delogo=" in vf


def test_default_voice_is_ava_not_hoaimy():
    from app.services.tts_service import DEFAULT_VOICES
    assert ReupConfig().tts_voice == "en-US-AvaMultilingualNeural"
    assert DEFAULT_VOICES["vi"]["female"] == "en-US-AvaMultilingualNeural"
    assert DEFAULT_VOICES["vi"]["male"] == "en-US-AndrewMultilingualNeural"


def test_cinematic_frame_is_burned():
    cfg = ReupConfig(frame_enabled=True, frame_thickness=16, film_grain=0)
    _, _, vf, _ = build_reup_filtergraph(cfg, has_audio=False)
    assert "drawbox=" in vf
    assert "t=16" in vf


def test_bgm_lyric_stt_is_detected():
    from app.services.reup_service import srt_looks_like_bgm_lyrics, source_clip_title
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".srt")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:02,000\n人生破破烂烂的我活的与往单单的好事哪有护梦\n")
        assert srt_looks_like_bgm_lyrics(path, "好饿好困也好累") is True
        with open(path, "w", encoding="utf-8") as f:
            f.write("1\n00:00:00,000 --> 00:00:02,000\n好饿好困也好累\n")
        assert srt_looks_like_bgm_lyrics(path, "好饿好困也好累") is False
    finally:
        os.remove(path)
    assert "好饿" in source_clip_title(
        "/no/such.mp4",
        ReupConfig(post_title="好饿好困也好累～ #猫咪 #萌宠"),
    )


def test_hybrid_inpaint_kills_white_caption():
    import numpy as np
    from app.services.opencv_inpainter import hybrid_inpaint_frame
    img = np.full((160, 240, 3), 70, dtype=np.uint8)
    img[90:120, 20:220] = 250
    mask = np.zeros((160, 240), dtype=np.uint8)
    mask[90:120, 20:220] = 255
    out = hybrid_inpaint_frame(img, mask, radius=5)
    region = out[96:114, 40:200]
    assert region.mean() < 180


def test_lama_session_optional():
    import numpy as np
    from app.services.lama_inpainter import get_lama_session, lama_inpaint_bgr
    img = np.full((64, 64, 3), 40, dtype=np.uint8)
    mask = np.zeros((64, 64), dtype=np.uint8)
    mask[20:40, 10:54] = 255
    img[20:40, 10:54] = 255
    out = lama_inpaint_bgr(img, mask)
    assert out.shape == img.shape
    if get_lama_session() is not None:
        assert out[28, 32].mean() < 220


def test_uniform_bright_blob_is_masked():
    frame = np.full((40, 200, 3), 240, dtype=np.uint8)
    mask = extract_adaptive_text_mask(frame)
    assert int(mask.max()) == 255


def test_libass_detection_is_boolean():
    from app.services.reup_service import ffmpeg_supports_libass
    assert ffmpeg_supports_libass() in (True, False)


def test_overlay_filter_chains_per_cue():
    from app.services.subtitle_overlay import build_overlay_filter
    overlays = [
        {"png": "/tmp/a.png", "start": 0.0, "end": 1.0},
        {"png": "/tmp/b.png", "start": 1.2, "end": 2.0},
    ]
    fc, inputs = build_overlay_filter(overlays)
    assert inputs == ["-i", "/tmp/a.png", "-i", "/tmp/b.png"]
    assert fc.count("overlay=") == 2
    assert "enable='between(t,0.000,1.000)'" in fc
    assert fc.endswith("[v_out]")
    assert "[1:v]" in fc and "[2:v]" in fc


def test_srt_renders_to_overlay_pngs(tmp_path):
    import os
    from app.services.subtitle_overlay import render_srt_to_overlays, find_overlay_font
    if not find_overlay_font():
        pytest.skip("no unicode font available")
    srt = tmp_path / "vi.srt"
    srt.write_text(
        "1\n00:00:00,200 --> 00:00:01,800\nXin chào các bạn\n\n"
        "2\n00:00:02,000 --> 00:00:03,500\nĂn cơm chưa\n\n",
        encoding="utf-8",
    )
    overlays = render_srt_to_overlays(str(srt), 1080, 1920, str(tmp_path / "ovl"))
    assert len(overlays) == 2
    for ov in overlays:
        assert os.path.exists(ov["png"])
        assert ov["end"] > ov["start"]

