"""Regression tests for reup FX, Chinese text wipe, vietsub, and lip-sync."""

import os
import sys
import types
import wave

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


def test_persisted_job_progress_never_moves_backwards(tmp_path):
    from app.services.queue_manager import BatchQueueManager

    manager = BatchQueueManager(db_path=str(tmp_path / "jobs.sqlite"), max_concurrent_jobs=1)
    try:
        job_id = manager.enqueue_job("input.mp4", "output.mp4")

        manager.append_job_log(job_id, "halfway", stage="WATERMARK_REMOVAL", progress=0.50)
        manager.append_job_log(job_id, "late lower update", stage="WATERMARK_REMOVAL", progress=0.30)
        assert manager.get_job(job_id)["progress_percent"] == 50.0

        manager.update_job_status(job_id, "REUP_TRANSFORM", progress=0.72)
        manager.update_job_progress(job_id, 0.60, stage="REUP_TRANSFORM")
        assert manager.get_job(job_id)["progress_percent"] == 72.0

        # Retry is the only intentional reset boundary.
        manager.update_job_status(job_id, "PENDING", progress=0.05)
        assert manager.get_job(job_id)["progress_percent"] == 5.0
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_job_claim_is_atomic_and_deleted_job_requests_abort(tmp_path):
    from app.services.queue_manager import BatchQueueManager

    manager = BatchQueueManager(db_path=str(tmp_path / "jobs.sqlite"), max_concurrent_jobs=1)
    try:
        job_id = manager.enqueue_job("input.mp4", "output.mp4")
        assert manager._claim_pending_job(job_id) is True
        assert manager._claim_pending_job(job_id) is False

        assert manager.delete_job(job_id) is True
        assert manager._abort_requested(job_id) is True
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_backend_instance_lock_is_exclusive_and_recoverable(tmp_path):
    from app.core.instance_lock import BackendInstanceLock

    db_path = str(tmp_path / "jobs.sqlite")
    first = BackendInstanceLock(db_path)
    second = BackendInstanceLock(db_path)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
        first.release()
        assert second.acquire() is True
    finally:
        first.release()
        second.release()


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
    assert cfg.enable_vocal_mute is False
    fc, has_a, vf, af = build_reup_filtergraph(cfg, has_audio=True)
    assert "hflip" in vf
    assert "noise=" in vf
    assert "eq=" in vf
    assert "scale=trunc(iw/2)*2" in vf
    assert "crop=" in vf


def test_clean_audio_modes_keep_or_remove_source_background():
    keep_cfg = ReupConfig(
        enable_vocal_mute=True,
        preserve_bgm=True,
        vocal_mute_strategy="ffmpeg_filter",
        pitch_shift=False,
        speed_factor=1.0,
    )
    _, keep_audio, _, keep_af = build_reup_filtergraph(keep_cfg, has_audio=True)
    assert keep_audio is True
    assert "stereotools" in keep_af
    assert keep_af != "volume=0"

    mute_cfg = ReupConfig(
        enable_vocal_mute=True,
        preserve_bgm=False,
        vocal_mute_strategy="mute_all",
        pitch_shift=False,
        speed_factor=1.0,
    )
    _, mute_audio, _, mute_af = build_reup_filtergraph(mute_cfg, has_audio=True)
    assert mute_audio is True
    assert mute_af == "volume=0"


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


def test_lipsync_preserves_script_and_rates():
    from app.services.lipsync_service import (
        compact_for_duration,
        edge_rate_tag,
        estimate_tts_duration,
        lipsync_prepare_segment,
        regroup_words_to_cues,
    )
    long = "Vui lòng thì giơ hai tay lên nào các bạn ạ"
    short = compact_for_duration(long, 0.8)
    assert len(short) <= len(long)
    assert "thì" not in short.split()
    rate = edge_rate_tag(2.0, 1.2)
    assert rate.startswith("+")
    assert estimate_tts_duration("xin chào") >= 0.28
    exact = "Mình bảo sao ngửi thấy mùi khai."
    assert lipsync_prepare_segment(exact, 1.2)["text"] == exact
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


@pytest.mark.anyio
async def test_synchronized_tts_uses_exact_srt_text_without_cutting(monkeypatch, tmp_path):
    from app.services.tts_service import get_audio_duration, parse_srt_segments, tts_service

    srt = tmp_path / "voice.srt"
    first = "Mình bảo sao ngửi thấy mùi khai."
    second = "Thối chết đi được, cái chân nhỏ đó phải không?"
    srt.write_text(
        f"1\n00:00:00,000 --> 00:00:00,400\n{first}\n\n"
        f"2\n00:00:00,500 --> 00:00:00,900\n{second}\n",
        encoding="utf-8",
    )

    async def fake_generate_speech(*, output_path, **_kwargs):
        with wave.open(output_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * int(16000 * 0.8))
        return output_path

    monkeypatch.setattr(tts_service, "generate_speech", fake_generate_speech)
    output = tmp_path / "voice.wav"
    result = await tts_service.synthesize_synchronized_tts(
        srt_path=str(srt),
        output_audio_path=str(output),
        voice="vieneu:Trúc Ly",
        engine="vieneu",
        total_duration=1.0,
        enable_lipsync=True,
        timeline_speed=2.0,
    )

    aligned = parse_srt_segments(result["aligned_srt_path"])
    assert [seg["text"] for seg in aligned] == [first, second]
    assert aligned[0]["duration"] > 0.4
    assert aligned[1]["start_time"] >= aligned[0]["end_time"]
    assert get_audio_duration(str(output)) > 0.9


@pytest.mark.anyio
async def test_synchronized_tts_skips_punctuation_only_cues(monkeypatch, tmp_path):
    from app.services.tts_service import parse_srt_segments, tts_service

    srt = tmp_path / "voice.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:00,220\n?\n\n"
        "2\n00:00:00,300 --> 00:00:01,000\nXin chào\n",
        encoding="utf-8",
    )
    spoken_texts = []

    async def fake_generate_speech(*, text, output_path, **_kwargs):
        spoken_texts.append(text)
        with wave.open(output_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * int(16000 * 0.5))
        return output_path

    monkeypatch.setattr(tts_service, "generate_speech", fake_generate_speech)
    output = tmp_path / "voice.wav"
    result = await tts_service.synthesize_synchronized_tts(
        srt_path=str(srt),
        output_audio_path=str(output),
        enable_lipsync=False,
    )

    assert spoken_texts == ["Xin chào"]
    aligned_texts = [
        seg["text"]
        for seg in parse_srt_segments(result["aligned_srt_path"])
    ]
    assert aligned_texts == ["Xin chào"]


def test_tts_mix_ducks_only_during_speech():
    from app.services.reup_service import (
        build_tts_bgm_mix_filter,
        build_reup_filtergraph,
        should_use_demucs_for_dubbing,
    )
    from app.services.audio_service import build_timed_speech_ducking_filter, build_vocal_mute_ffmpeg_filter
    fc = build_tts_bgm_mix_filter()
    assert "sidechaincompress" in fc
    assert "stereotools" not in fc
    assert "volume=0.22" not in fc
    assert "[0:a]lowpass=f=180" not in fc
    assert "alimiter" in fc or "dynaudnorm" in fc
    mute = build_vocal_mute_ffmpeg_filter(preserve_bgm=True)
    assert "stereotools=" in mute
    assert "asplit=" in mute
    assert "lowpass=" in mute
    cfg = ReupConfig(enable_vocal_mute=True, film_grain=0, pitch_shift=False, speed_factor=1.0)
    graph, has_a, vf, af = build_reup_filtergraph(cfg, has_audio=True)
    assert has_a is True
    assert "asplit=2" in graph
    assert graph.count("[0:a]") == 1
    timed = build_timed_speech_ducking_filter([(1.0, 2.0)])
    assert "between(t,0.900,2.150)" in timed
    assert ",0.12,1.0" in timed
    dub_cfg = ReupConfig(enable_tts=True, enable_vocal_mute=True, vocal_mute_strategy="demucs")
    assert should_use_demucs_for_dubbing(dub_cfg, [(1.0, 2.0)]) is True
    assert should_use_demucs_for_dubbing(dub_cfg, []) is True
    auto_cfg = ReupConfig(enable_tts=True, enable_vocal_mute=True, vocal_mute_strategy="auto")
    assert should_use_demucs_for_dubbing(auto_cfg, [(1.0, 2.0)]) is False
    mute_all_cfg = ReupConfig(enable_vocal_mute=True, preserve_bgm=False, vocal_mute_strategy="mute_all")
    assert should_use_demucs_for_dubbing(mute_all_cfg, [(1.0, 2.0)]) is False


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


def test_default_vietnamese_engine_is_native_edge_tts():
    from app.services.tts_service import DEFAULT_VOICES
    assert ReupConfig().tts_voice == "vi-VN-HoaiMy-Fast"
    assert ReupConfig().tts_engine == "edge-tts"
    assert DEFAULT_VOICES["vi"]["female"] == "vi-VN-HoaiMy-Fast"
    assert DEFAULT_VOICES["vi"]["male"] == "vi-VN-NamMinh-Fast"

    migrated = ReupConfig(tts_voice="vieneu:Trúc Ly", tts_engine="vieneu", target_lang="vi")
    assert migrated.tts_voice == "vi-VN-HoaiMy-Fast"
    assert migrated.tts_engine == "edge-tts"


@pytest.mark.anyio
async def test_vieneu_provider_uses_local_preset_voice(tmp_path, monkeypatch):
    from app.modules.tts.providers import VieNeuTTSProvider

    calls = {}

    class FakeVieneu:
        def __init__(self, **kwargs):
            calls["init"] = kwargs

        def infer(self, text, voice):
            calls["infer"] = (text, voice)
            return np.array([0.0, 0.1], dtype=np.float32)

        def save(self, _audio, output_path):
            with open(output_path, "wb") as f:
                f.write(b"RIFF" + b"\x00" * 300)

    monkeypatch.setitem(sys.modules, "vieneu", types.SimpleNamespace(Vieneu=FakeVieneu))
    VieNeuTTSProvider._model = None
    output = tmp_path / "voice.wav"
    result = await VieNeuTTSProvider().generate(
        "Xin chào Việt Nam",
        voice="vieneu:Trúc Ly",
        output_path=str(output),
    )

    assert result == str(output)
    assert calls["init"] == {"mode": "v3turbo", "backend": "onnx"}
    assert calls["infer"] == ("Xin chào Việt Nam", "Trúc Ly")
    assert output.stat().st_size > 256


@pytest.mark.anyio
async def test_native_vietnamese_review_presets_map_to_edge_voices(tmp_path, monkeypatch):
    from app.modules.tts.providers import EdgeTTSProvider

    calls = []

    class FakeCommunicate:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def save(self, output_path):
            with open(output_path, "wb") as f:
                f.write(b"ID3" + b"\x00" * 300)

    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=FakeCommunicate))
    provider = EdgeTTSProvider()

    await provider.generate(
        "Đây là giọng review nhanh.",
        lang="vi",
        voice="vi-VN-HoaiMy-Fast",
        output_path=str(tmp_path / "female.mp3"),
    )
    await provider.generate(
        "Đây là giọng recap trầm.",
        lang="vi",
        voice="vi-VN-NamMinh-Deep",
        output_path=str(tmp_path / "male.mp3"),
    )

    assert calls[0]["voice"] == "vi-VN-HoaiMyNeural"
    assert calls[0]["rate"] == "+10%"
    assert calls[1]["voice"] == "vi-VN-NamMinhNeural"
    assert calls[1]["rate"] == "-6%"
    assert calls[1]["pitch"] == "-3Hz"


@pytest.mark.anyio
async def test_native_vietnamese_voice_overrides_stale_engine(tmp_path, monkeypatch):
    from app.services.tts_service import tts_service

    calls = {}

    async def fake_edge(text, voice, output_path, **kwargs):
        calls.update({"text": text, "voice": voice, "output_path": output_path, **kwargs})
        with open(output_path, "wb") as f:
            f.write(b"ID3" + b"\x00" * 300)
        return output_path

    monkeypatch.setattr(tts_service, "generate_speech_edge_tts", fake_edge)
    output = str(tmp_path / "review.mp3")
    result = await tts_service.generate_speech(
        text="Giọng review tiếng Việt.",
        lang="vi",
        voice="vi-VN-HoaiMy-Fast",
        engine="vieneu",
        output_path=output,
    )

    assert result == output
    assert calls["voice"] == "vi-VN-HoaiMy-Fast"


@pytest.mark.anyio
async def test_voice_preview_uses_selected_language_and_engine(tmp_path, monkeypatch):
    from app.api.process import VoicePreviewRequest, preview_voice
    from app.config import settings
    from app.services.tts_service import tts_service

    calls = {}

    async def fake_generate_speech(**kwargs):
        calls.update(kwargs)
        with open(kwargs["output_path"], "wb") as f:
            f.write(b"RIFF" + b"\x00" * 300)
        return kwargs["output_path"]

    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(tts_service, "generate_speech", fake_generate_speech)
    response = await preview_voice(
        VoicePreviewRequest(voice="vieneu:Trúc Ly", lang="vi", engine="vieneu")
    )

    assert calls["lang"] == "vi"
    assert calls["voice"] == "vi-VN-HoaiMy-Fast"
    assert calls["engine"] == "edge-tts"
    assert os.path.exists(response.path)


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


def test_srt_renders_to_single_timed_apng(tmp_path):
    import os
    from app.services.subtitle_overlay import (
        append_timed_subtitle_filter,
        find_overlay_font,
        render_srt_to_apng,
    )
    if not find_overlay_font():
        pytest.skip("no unicode font available")
    srt = tmp_path / "vi.srt"
    srt.write_text(
        "1\n00:00:00,200 --> 00:00:01,200\nXin chào\n\n"
        "2\n00:00:01,500 --> 00:00:02,500\nĂn cơm chưa\n",
        encoding="utf-8",
    )

    track = render_srt_to_apng(str(srt), 320, 180, str(tmp_path / "subs.png"))

    assert track and os.path.getsize(track) > 0
    fc = append_timed_subtitle_filter("[0:v]null[v_out]", 1)
    assert fc.count("overlay=") == 1
    assert "[1:v]format=rgba" in fc
    assert "repeatlast=0" in fc


def test_long_form_tts_groups_nearby_cues_without_cutting_text():
    from app.services.tts_service import group_long_form_tts_segments

    segments = [
        {
            "index": index + 1,
            "start_time": index * 1.1,
            "end_time": index * 1.1 + 1.0,
            "duration": 1.0,
            "text": f"Câu số {index + 1}",
        }
        for index in range(60)
    ]

    grouped = group_long_form_tts_segments(segments)

    assert len(grouped) < 20
    combined = " ".join(segment["text"] for segment in grouped)
    assert "Câu số 1" in combined
    assert "Câu số 60" in combined


def test_inpaint_progress_advances_immediately_after_pipeline_50_percent():
    import inspect
    from app.services.opencv_inpainter import inpaint_video_opencv

    src = inspect.getsource(inpaint_video_opencv)
    assert "0.50 + 0.15 * (frames_processed / max(1, total_frames_est))" in src
    assert "0.35 + 0.30 * (frames_processed / max(1, total_frames_est))" not in src


def test_watermark_removal_fallback_for_long_videos(monkeypatch, tmp_path):
    from app.services.watermark_service import remove_watermark
    import app.services.watermark_service as wm_service

    # Mock get_audio_duration to return 350 seconds
    monkeypatch.setattr("app.services.tts_service.get_audio_duration", lambda path: 350.0)

    # Mock inpaint_video_ffmpeg to verify it gets called
    called_with_filter = None
    def mock_inpaint_ffmpeg(input_path, output_path, roi, filter_type, radius):
        nonlocal called_with_filter
        called_with_filter = filter_type
        return output_path

    monkeypatch.setattr(wm_service, "inpaint_video_ffmpeg", mock_inpaint_ffmpeg)

    # Create dummy input file
    dummy_input = tmp_path / "dummy.mp4"
    dummy_input.write_text("dummy content")
    dummy_output = tmp_path / "dummy_out.mp4"

    res = remove_watermark(str(dummy_input), str(dummy_output), method="auto")
    assert called_with_filter == "delogo"


def test_smart_voice_mapping():
    from app.services.tts_service import DEFAULT_VOICES
    assert DEFAULT_VOICES["vi"]["child"] == "vi-VN-HoaiMyNeural"
    assert DEFAULT_VOICES["vi"]["narrator"] == "vi-VN-NamMinh-Deep"
    assert DEFAULT_VOICES["en"]["male"] == "en-US-GuyNeural"
    assert DEFAULT_VOICES["en"]["elder_male"] == "en-US-RyanNeural"
