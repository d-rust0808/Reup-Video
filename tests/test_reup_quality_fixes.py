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


def test_concurrent_append_job_log_keeps_every_line(tmp_path):
    import threading
    from app.services.queue_manager import BatchQueueManager

    manager = BatchQueueManager(db_path=str(tmp_path / "jobs.sqlite"), max_concurrent_jobs=1)
    try:
        job_id = manager.enqueue_job("input.mp4", "output.mp4")

        def writer(prefix, count):
            for i in range(count):
                manager.append_job_log(job_id, f"{prefix}-{i}", stage="REUP_TRANSFORM")

        threads = [threading.Thread(target=writer, args=(f"t{n}", 20)) for n in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        logs = manager.get_job(job_id)["logs"]
        assert len(logs) == 100
        messages = {item["message"] for item in logs}
        assert "t0-0" in messages
        assert "t4-19" in messages
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_cancel_running_job_cannot_be_overwritten_by_late_progress(tmp_path):
    from app.services.queue_manager import BatchQueueManager

    manager = BatchQueueManager(db_path=str(tmp_path / "jobs.sqlite"), max_concurrent_jobs=1)
    try:
        job_id = manager.enqueue_job("input.mp4", "output.mp4")
        assert manager._claim_pending_job(job_id) is True
        manager.update_job_status(job_id, "REUP_TRANSFORM", progress=0.76)
        assert manager.cancel_job(job_id) is True
        assert manager.get_job(job_id)["status"] == "CANCELLED"
        assert manager._abort_requested(job_id) is True

        manager.update_job_status(job_id, "COMPLETED", progress=1.0)
        manager.update_job_progress(job_id, 0.99, stage="REUP_TRANSFORM")
        assert manager.get_job(job_id)["status"] == "CANCELLED"
        assert manager.cancel_job(job_id) is False
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_high_core_queue_uses_configured_bounded_thread_pool(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from app.services.queue_manager import BatchQueueManager

    manager = BatchQueueManager(db_path=str(tmp_path / "jobs.sqlite"), max_concurrent_jobs=8)
    try:
        assert isinstance(manager.executor, ThreadPoolExecutor)
        assert manager.max_concurrent_jobs == 8
        assert manager.executor._max_workers == 8
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_nvenc_inpainting_flags_are_encoder_specific(monkeypatch):
    from app.services import opencv_inpainter

    class Result:
        stdout = " V..... h264_nvenc NVIDIA NVENC H.264 encoder"

    monkeypatch.setattr(opencv_inpainter.subprocess, "run", lambda *args, **kwargs: Result())
    assert opencv_inpainter.get_best_h264_encoder("ffmpeg") == "h264_nvenc"
    assert opencv_inpainter.get_h264_encoder_flags("h264_nvenc") == ["-preset", "p4", "-cq", "23"]
    assert "-crf" not in opencv_inpainter.get_h264_encoder_flags("h264_nvenc")


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


def test_auto_inpaint_rejects_scene_sized_false_text_boxes(monkeypatch):
    from app.services import opencv_inpainter

    frame = np.full((720, 1280, 3), 140, dtype=np.uint8)
    monkeypatch.setattr(
        "app.services.subtitle_detector.detect_text_boxes",
        lambda *_args, **_kwargs: [(0, 90, 1280, 610)],
    )
    monkeypatch.setattr("app.services.subtitle_detector.detect_faces", lambda *_args, **_kwargs: [])

    mask = opencv_inpainter.extract_dynamic_subtitle_mask(
        frame,
        tracker=opencv_inpainter.TemporalTextTracker(),
        run_ocr=True,
        roi_fallback=False,
    )

    assert np.count_nonzero(mask) == 0


def test_probe_audio_sample_rate_reads_the_file(tmp_path):
    import wave

    from app.services.audio_service import probe_audio_sample_rate

    wav_path = tmp_path / "rate48.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"\x00\x00" * 480)
    assert probe_audio_sample_rate(str(wav_path)) == 48000
    assert probe_audio_sample_rate(str(tmp_path / "missing.wav")) is None


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
    assert "stereotools" not in keep_af
    assert "volume=0.70" in keep_af
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


def test_demucs_duck_keeps_background_and_mixes_quiet_source_voice(monkeypatch, tmp_path):
    from app.services import audio_service

    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFF" + b"\x00" * 512)
    captured = {}

    def fake_extract(_input_path, output_dir):
        vocal = os.path.join(output_dir, "vocals.wav")
        background = os.path.join(output_dir, "no_vocals.wav")
        with open(vocal, "wb") as f:
            f.write(b"RIFF" + b"\x00" * 512)
        with open(background, "wb") as f:
            f.write(b"RIFF" + b"\x00" * 512)
        return vocal, background

    def fake_mix(background_path, vocal_path, output_path, vocal_volume):
        captured.update({
            "background_path": background_path,
            "vocal_path": vocal_path,
            "vocal_volume": vocal_volume,
        })
        with open(output_path, "wb") as f:
            f.write(b"RIFF" + b"\x00" * 512)
        return True

    monkeypatch.setattr(audio_service, "check_demucs_available", lambda: True)
    monkeypatch.setattr(audio_service, "extract_vocals_demucs", fake_extract)
    monkeypatch.setattr(audio_service, "mix_separated_stems", fake_mix)

    output = tmp_path / "ducked.wav"
    cfg = ReupConfig(
        enable_vocal_mute=True,
        preserve_bgm=True,
        vocal_mute_strategy="demucs_duck",
        original_vocal_volume=0.08,
    )
    result = audio_service.process_vocal_muting(str(source), str(output), config=cfg)

    assert result["method"] == "demucs_duck"
    assert output.exists()
    assert captured["vocal_volume"] == pytest.approx(0.08)
    assert captured["background_path"].endswith("no_vocals.wav")
    assert captured["vocal_path"].endswith("vocals.wav")


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


def test_identity_audio_is_preserved_without_audio_fx():
    cfg = ReupConfig(
        hflip=False,
        crop_percent=0,
        color_adjust=False,
        film_grain=0,
        pitch_shift=False,
        speed_factor=1.0,
    )
    graph, has_audio, _, audio_filter = build_reup_filtergraph(cfg, has_audio=True)

    assert has_audio is True
    assert "[0:a]anull[a_out]" in graph
    assert audio_filter == "anull"


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


def test_submitted_speed_ratio_is_kept():
    from app.api.process import ProcessJobRequest, resolve_submitted_speed

    nested = ProcessJobRequest(reup={"speed_ratio": 1.3, "hflip": False})
    assert resolve_submitted_speed(nested) == pytest.approx(1.3)

    factor = ProcessJobRequest(reup={"speed_factor": 1.42, "speed_ratio": 1.03})
    assert resolve_submitted_speed(factor) == pytest.approx(1.42)

    both = ProcessJobRequest(reup={"speed_ratio": 1.3, "speed_factor": 1.3})
    cfg_speed = resolve_submitted_speed(both)
    assert cfg_speed == pytest.approx(1.3)
    from app.services.reup_service import build_reup_filtergraph
    _, _, vf, af = build_reup_filtergraph(
        ReupConfig(speed_factor=cfg_speed, film_grain=0.0, frame_enabled=False, pitch_shift=False),
        has_audio=True,
    )
    assert "setpts=PTS/1.3000" in vf
    assert "atempo=1.3000" in af
    assert "asetpts=PTS-STARTPTS" in af


def test_custom_speed_factor_lands_in_setpts():
    cfg = ReupConfig(speed_factor=1.3, film_grain=0.0, frame_enabled=False, pitch_shift=False)
    _, _, vf, af = build_reup_filtergraph(cfg, has_audio=True)
    assert "setpts=PTS/1.3000" in vf
    assert "atempo=1.3000" in af
    assert "asetpts=PTS-STARTPTS" in af


def test_pitch_shift_uses_probed_source_sample_rate():
    cfg = ReupConfig(speed_factor=1.03, film_grain=0.0, frame_enabled=False, pitch_shift=True)
    _, _, _, af_48k = build_reup_filtergraph(cfg, has_audio=True, audio_sample_rate=48000)
    assert "asetrate=48000*1.0300,aresample=48000" in af_48k
    assert "asetrate=44100" not in af_48k

    _, _, _, af_44k = build_reup_filtergraph(cfg, has_audio=True, audio_sample_rate=44100)
    assert "asetrate=44100*1.0300,aresample=44100" in af_44k


def test_pitch_shift_without_known_rate_keeps_full_atempo():
    cfg = ReupConfig(speed_factor=1.03, film_grain=0.0, frame_enabled=False, pitch_shift=True)
    _, _, _, af = build_reup_filtergraph(cfg, has_audio=True, audio_sample_rate=0)
    assert "asetrate" not in af
    assert "atempo=1.0300" in af


def test_large_speedup_uses_matching_setpts_and_atempo():
    cfg = ReupConfig(speed_factor=1.5, film_grain=0.0, frame_enabled=False, pitch_shift=True)
    _, _, vf, af = build_reup_filtergraph(cfg, has_audio=True, audio_sample_rate=48000)
    assert "setpts=PTS/1.5000" in vf
    assert "atempo=1.5000" in af
    assert "asetrate" not in af
    assert "asetpts=PTS-STARTPTS" in af
    assert vf.index("setpts=PTS/1.5000") < vf.index("fps=30")


def test_speed_encode_uses_libx264_not_videotoolbox():
    from app.services.reup_service import browser_safe_encode_args, find_ffmpeg_binary

    ffmpeg = find_ffmpeg_binary()
    if not ffmpeg:
        pytest.skip("ffmpeg required")
    args = browser_safe_encode_args(ffmpeg, force_software=True)
    assert args[1] == "libx264"
    assert "videotoolbox" not in " ".join(args)


def test_ensure_av_lock_rejects_ignored_setpts(tmp_path):
    from app.services.audio_service import ensure_av_lock

    assert ensure_av_lock("/tmp/nope-av-lock.mp4") is False
    fake = tmp_path / "empty.mp4"
    fake.write_bytes(b"")
    assert ensure_av_lock(str(fake), source_dur=10.0, speed=1.5) is False


def test_ffmpeg_1_5x_keeps_audio_and_video_the_same_length(tmp_path):
    import shutil
    import subprocess
    from app.services.audio_service import ensure_av_lock, probe_media_duration_sec
    from app.services.reup_service import find_ffmpeg_binary

    ffmpeg = find_ffmpeg_binary()
    if not ffmpeg:
        pytest.skip("ffmpeg required")
    src = tmp_path / "src.mp4"
    make = subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:s=160x120:r=25:d=2",
            "-f", "lavfi", "-i", "sine=f=440:d=2",
            "-shortest",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "2",
            str(src),
        ],
        capture_output=True, text=True, check=False,
    )
    if make.returncode != 0 or not src.is_file():
        pytest.skip(f"could not mint test media: {(make.stderr or '')[-200:]}")
    cfg = ReupConfig(
        speed_factor=1.5, hflip=False, film_grain=0, crop_percent=0,
        pitch_shift=False, modify_md5=False,
    )
    fc, has_a, vf, af = build_reup_filtergraph(cfg, has_audio=True, frame_size=(160, 120))
    assert has_a
    out = tmp_path / "out.mp4"
    enc = subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src), "-filter_complex", fc,
            "-map", "[v_out]", "-map", "[a_out]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "2", "-shortest", "-t", "1.40",
            str(out),
        ],
        capture_output=True, text=True, check=False,
    )
    assert enc.returncode == 0, enc.stderr[-400:]
    assert out.is_file()
    assert ensure_av_lock(str(out), source_dur=2.0, speed=1.5)
    video_dur = probe_media_duration_sec(str(out), "v:0")
    audio_dur = probe_media_duration_sec(str(out), "a:0")
    assert abs(video_dur - audio_dur) < 0.12
    assert 1.15 < video_dur < 1.55


def test_place_consecutive_tts_does_not_pile_behind_picture():
    from app.services.tts_service import place_consecutive_tts_clips

    clips = [
        {"segment": {"start_time": 1.0, "end_time": 1.4}, "final_dur": 2.4},
        {"segment": {"start_time": 1.5, "end_time": 2.0}, "final_dur": 2.4},
        {"segment": {"start_time": 2.2, "end_time": 2.8}, "final_dur": 2.4},
    ]
    placed = place_consecutive_tts_clips(clips)
    assert placed[0]["segment"]["start_time"] == pytest.approx(1.0)
    assert placed[1]["segment"]["start_time"] == pytest.approx(1.5)
    assert placed[2]["segment"]["start_time"] == pytest.approx(2.2)


def test_restretch_audio_skips_missing_file():
    from app.services.audio_service import restretch_audio_to_video

    assert restretch_audio_to_video("/tmp/definitely-missing-av-lock.mp4") is False


def test_timed_subtitle_overlay_is_composited_before_setpts():
    from app.services.subtitle_overlay import inject_timed_overlay_before_speed

    fc = (
        "[0:v]hflip,scale=trunc(iw/2)*2:trunc(ih/2)*2,setpts=PTS/1.3000,fps=30[v_out];"
        "[0:a]atempo=1.3000[a_out]"
    )
    out = inject_timed_overlay_before_speed(fc, 2)
    assert out.index("overlay=") < out.index("setpts=PTS/1.3000")
    assert "[2:v]format=rgba[v_sub_track]" in out
    assert out.endswith("[a_out]")
    assert "atempo=1.3000" in out


def test_bgm_swap_keeps_speed_and_overlay_chains():
    from app.services.reup_service import drop_audio_chains

    fc = (
        "[0:v]scale=2:2,setpts=PTS/1.3000,fps=30[v_out];"
        "[0:a]atempo=1.3000[a_out];"
        "[v_out][2:v]overlay=0:H-h[v_out]"
    )
    video = drop_audio_chains(fc)
    assert "setpts=PTS/1.3000" in video
    assert "overlay=0:H-h" in video
    assert "atempo" not in video
    assert "[a_out]" not in video


def test_hardsub_filter_uses_original_timestamps_before_setpts(tmp_path):
    srt = tmp_path / "vi.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n\n", encoding="utf-8")
    cfg = ReupConfig(speed_factor=1.03, film_grain=0.0, frame_enabled=False)
    _, _, vf, _ = build_reup_filtergraph(
        cfg, has_audio=False, burn_srt_path=str(srt), frame_size=(1920, 1080),
    )
    assert "subtitles=" in vf
    assert "original_size=1920x1080" in vf
    assert "BorderStyle=3" in vf
    assert "Alignment=2" in vf
    assert "drawbox=" in vf
    assert vf.index("drawbox=") < vf.index("subtitles=")
    assert vf.index("subtitles=") < vf.index("setpts=")


def test_hardsub_graph_uses_configured_vietsub_plate(tmp_path):
    srt = tmp_path / "vi.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n\n", encoding="utf-8")
    cfg = ReupConfig(
        caption_cover="white_solid",
        subtitle_y=0.72,
        subtitle_box_w=0.90,
        subtitle_box_h=0.10,
        film_grain=0,
        hflip=False,
        crop_percent=0,
        speed_factor=1.0,
    )
    _, _, vf, _ = build_reup_filtergraph(
        cfg, has_audio=False, burn_srt_path=str(srt), frame_size=(1080, 1920),
    )
    assert "w=972" in vf
    assert "h=192" in vf
    assert "white@1" in vf
    assert "BorderStyle=1" in vf
    assert "Alignment=8" in vf
    assert vf.index("drawbox=") < vf.index("subtitles=")


def test_toggleable_vietsub_is_output_timed_and_embedded(tmp_path):
    import json
    import shutil
    import subprocess

    from app.services.reup_service import mux_toggleable_subtitle, prepare_output_subtitle

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg is required for soft subtitle mux verification")

    video = tmp_path / "softsub.mp4"
    subprocess.run(
        [
            ffmpeg, "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=320x180:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(video),
        ],
        check=True,
    )
    source_srt = tmp_path / "source.srt"
    source_srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nXin chào Nha Trang\n",
        encoding="utf-8",
    )

    sidecar = prepare_output_subtitle(str(source_srt), str(video), speed_factor=2.0)
    assert sidecar
    with open(sidecar, encoding="utf-8") as subtitle_file:
        assert "00:00:00,000 --> 00:00:00,500" in subtitle_file.read()
    assert mux_toggleable_subtitle(str(video), sidecar, str(video)) is True

    probe = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-show_entries", "stream=codec_type,codec_name:stream_tags=language,title:stream_disposition=default",
            "-of", "json", str(video),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    assert any(s["codec_type"] == "video" and s["codec_name"] == "h264" for s in streams)
    assert any(s["codec_type"] == "audio" and s["codec_name"] == "aac" for s in streams)
    subtitle = next(s for s in streams if s["codec_type"] == "subtitle")
    assert subtitle["codec_name"] == "mov_text"
    assert subtitle["tags"]["language"] == "vie"


def test_subtitle_mode_can_be_soft_hard_or_off():
    assert ReupConfig(subtitle_mode="soft").subtitle_mode == "soft"
    assert ReupConfig(subtitle_mode="hard").subtitle_mode == "hard"
    disabled = ReupConfig(burn_subtitles=False, subtitle_mode="soft")
    assert disabled.subtitle_mode == "off"
    assert disabled.burn_subtitles is False


def test_pipeline_with_subtitles_off_does_not_reference_missing_srt(monkeypatch, tmp_path):
    from app.services import reup_service

    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    source.write_bytes(b"video")
    captured = {}

    def fake_process_reup_video(**kwargs):
        captured.update(kwargs)
        return {"output_path": kwargs["output_path"]}

    monkeypatch.setattr(reup_service, "process_reup_video", fake_process_reup_video)

    result = reup_service.ReupService.process_reup_pipeline(
        str(source),
        ReupConfig(burn_subtitles=False, subtitle_mode="off", enable_tts=False),
        str(output),
    )

    assert result == str(output)
    assert captured["srt_override"] is None
    assert captured["speech_intervals"] == []


def test_require_complete_reup_blocks_missing_vietsub_and_tts(tmp_path):
    from app.services.reup_service import (
        abort_incomplete_reup,
        refuse_incomplete_output,
        require_complete_reup,
    )

    require_complete_reup(
        ReupConfig(burn_subtitles=False, subtitle_mode="off", enable_tts=False)
    )

    with pytest.raises(RuntimeError, match="Vietsub"):
        require_complete_reup(
            ReupConfig(subtitle_mode="hard", enable_tts=False),
            translated_srt=None,
        )

    srt = tmp_path / "vi.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="lồng tiếng"):
        require_complete_reup(
            ReupConfig(subtitle_mode="hard", enable_tts=True),
            translated_srt=str(srt),
            synced_tts_audio=None,
        )

    tts = tmp_path / "voice.wav"
    tts.write_bytes(b"\x00" * 4096)
    require_complete_reup(
        ReupConfig(subtitle_mode="hard", enable_tts=True),
        translated_srt=str(srt),
        synced_tts_audio=str(tts),
    )

    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not-a-finished-video")
    with pytest.raises(RuntimeError, match="Vietsub in cứng"):
        refuse_incomplete_output(
            ReupConfig(subtitle_mode="hard", enable_tts=False),
            output_path=str(broken),
            burned_sub=False,
            had_srt=True,
        )
    assert not broken.exists()

    leftover = tmp_path / "desync.mp4"
    leftover.write_bytes(b"leftover")
    with pytest.raises(RuntimeError, match="không xuất file dở"):
        abort_incomplete_reup(str(leftover), "Reup thiếu lồng tiếng — không xuất file dở.")
    assert not leftover.exists()


def test_empty_stt_never_turns_post_title_into_full_video_subtitle(monkeypatch, tmp_path):
    from app.services import pyvideotrans_service, reup_service, tts_service

    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    source.write_bytes(b"video")
    captured = {}

    monkeypatch.setattr(tts_service, "get_audio_duration", lambda _path: 29.0)
    monkeypatch.setattr(
        pyvideotrans_service.PyVideoTransService,
        "speech_to_text",
        lambda *_args, **_kwargs: {"status": "empty", "srt_path": None},
    )

    def fail_translation(*_args, **_kwargs):
        raise AssertionError("An empty STT result must not translate the post title")

    monkeypatch.setattr(
        pyvideotrans_service.PyVideoTransService,
        "translate_subtitles",
        fail_translation,
    )

    def fake_process_reup_video(**kwargs):
        captured.update(kwargs)
        return {"output_path": kwargs["output_path"]}

    monkeypatch.setattr(reup_service, "process_reup_video", fake_process_reup_video)

    with pytest.raises(RuntimeError, match="Vietsub"):
        reup_service.ReupService.process_reup_pipeline(
            str(source),
            ReupConfig(
                burn_subtitles=True,
                subtitle_mode="hard",
                enable_tts=False,
                post_title="Tây Tạng Mê Tho — tiêu đề bài đăng",
            ),
            str(output),
        )

    assert captured == {}
    assert not output.exists()
    assert not (tmp_path / "source.title.srt").exists()


def test_tts_aligned_srt_drives_burned_subtitles(monkeypatch, tmp_path):
    from app.services import reup_service, tts_service

    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    srt = tmp_path / "source.vi.srt"
    aligned = tmp_path / "source.vi.aligned.srt"
    source.write_bytes(b"video")
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:10,000\nKhó khăn lắm mới gây dựng được danh tiếng.\n",
        encoding="utf-8",
    )
    aligned.write_text(
        "1\n00:00:00,000 --> 00:00:03,200\nKhó khăn lắm mới gây dựng được danh tiếng.\n",
        encoding="utf-8",
    )
    captured = {}

    monkeypatch.setattr(tts_service, "get_audio_duration", lambda _path: 10.0)

    async def fake_tts(*, output_audio_path, **_kwargs):
        os.makedirs(os.path.dirname(os.path.abspath(output_audio_path)) or ".", exist_ok=True)
        with open(output_audio_path, "wb") as handle:
            handle.write(b"\x00" * 4096)
        return {
            "status": "completed",
            "output_audio_path": output_audio_path,
            "aligned_srt_path": str(aligned),
        }

    monkeypatch.setattr(tts_service.tts_service, "synthesize_synchronized_tts", fake_tts)

    def fake_process_reup_video(**kwargs):
        captured.update(kwargs)
        return {"output_path": kwargs["output_path"]}

    monkeypatch.setattr(reup_service, "process_reup_video", fake_process_reup_video)

    result = reup_service.ReupService.process_reup_pipeline(
        str(source),
        ReupConfig(
            enable_tts=True,
            enable_vocal_mute=True,
            vocal_mute_strategy="demucs_duck",
            preserve_bgm=True,
            srt_path=str(srt),
            subtitle_mode="hard",
        ),
        str(output),
    )

    assert result == str(output)
    assert captured["srt_override"] == str(aligned)
    assert captured["tts_audio_override"]
    assert os.path.getsize(captured["tts_audio_override"]) > 2048
    intervals = captured["speech_intervals"]
    assert intervals
    assert intervals[0][1] == pytest.approx(3.2)


def test_failed_tts_fails_job_instead_of_exporting_chinese_only(monkeypatch, tmp_path):
    from app.services import reup_service, tts_service

    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    srt = tmp_path / "source.vi.srt"
    source.write_bytes(b"video")
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nXin chào\n",
        encoding="utf-8",
    )
    captured = {}

    monkeypatch.setattr(tts_service, "get_audio_duration", lambda _path: 1.0)

    async def fail_tts(**_kwargs):
        raise RuntimeError("tts unavailable")

    monkeypatch.setattr(tts_service.tts_service, "synthesize_synchronized_tts", fail_tts)

    def fake_process_reup_video(**kwargs):
        captured.update(kwargs)
        return {"output_path": kwargs["output_path"]}

    monkeypatch.setattr(reup_service, "process_reup_video", fake_process_reup_video)

    with pytest.raises(RuntimeError, match="lồng tiếng"):
        reup_service.ReupService.process_reup_pipeline(
            str(source),
            ReupConfig(
                enable_tts=True,
                enable_vocal_mute=True,
                vocal_mute_strategy="demucs_duck",
                preserve_bgm=True,
                srt_path=str(srt),
                subtitle_mode="hard",
            ),
            str(output),
        )

    assert captured == {}
    assert not output.exists()


def test_translation_failure_surfaces_provider_warning(monkeypatch, tmp_path):
    from app.services import pyvideotrans_service, reup_service, tts_service

    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    srt = tmp_path / "source.srt"
    source.write_bytes(b"video")
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(tts_service, "get_audio_duration", lambda _path: 8.0)
    monkeypatch.setattr(
        pyvideotrans_service.PyVideoTransService,
        "speech_to_text",
        lambda *_args, **_kwargs: {"status": "success", "srt_path": str(srt)},
    )
    monkeypatch.setattr(
        pyvideotrans_service.PyVideoTransService,
        "translate_subtitles",
        lambda *_args, **_kwargs: {
            "status": "failed",
            "srt_path": None,
            "warning": "DeepSeek API trả HTTP 402: tài khoản hoặc API key không còn quota/thanh toán.",
        },
    )

    captured = {}

    def fake_process_reup_video(**kwargs):
        captured.update(kwargs)
        return {"output_path": kwargs["output_path"]}

    monkeypatch.setattr(reup_service, "process_reup_video", fake_process_reup_video)

    with pytest.raises(RuntimeError, match="Vietsub"):
        reup_service.ReupService.process_reup_pipeline(
            str(source),
            ReupConfig(enable_tts=True, subtitle_mode="hard", source_lang="zh", target_lang="vi"),
            str(output),
        )

    assert captured == {}
    assert not output.exists()


def test_retry_reuses_only_completed_stage2(tmp_path):
    from app.services.queue_manager import _can_resume_completed_stage2, _retry_progress_for_job

    stage2 = tmp_path / "job_stage2.mp4"
    stage2.write_bytes(b"x" * 6000)

    completed = {
        "job_id": "job",
        "output_file_path": str(tmp_path / "job.mp4"),
        "logs": [{
            "level": "SUCCESS",
            "message": "✨ Đã inpaint chữ/watermark -> job_stage2.mp4",
        }]
    }
    assert _can_resume_completed_stage2(completed, str(stage2)) is False
    (tmp_path / "job_stage2.mp4.complete").write_text("ok\n", encoding="ascii")
    assert _can_resume_completed_stage2(completed, str(stage2)) is True
    assert _retry_progress_for_job(completed) == pytest.approx(0.65)
    assert _can_resume_completed_stage2({"logs": []}, str(stage2)) is False


def test_crop_and_off_modes_never_attach_delogo():
    from app.services.queue_manager import _apply_skip_inpaint, _text_cover_allowed

    assert _text_cover_allowed("auto") is True
    assert _text_cover_allowed("all") is True
    assert _text_cover_allowed("crop") is False
    assert _text_cover_allowed("none") is False
    assert _text_cover_allowed("off") is False

    crop_cfg = ReupConfig(
        text_cover_vf="delogo=x=10:y=20:w=400:h=200:show=0",
        subtitle_bottom_crop=0.0,
    )
    crop_msg = _apply_skip_inpaint("crop", crop_cfg)
    assert crop_cfg.text_cover_vf == ""
    assert crop_cfg.subtitle_bottom_crop >= 0.06
    assert "delogo" in crop_msg.lower()
    assert "không delogo" in crop_msg.lower()

    off_cfg = ReupConfig(text_cover_vf="delogo=x=10:y=20:w=400:h=200:show=0")
    off_msg = _apply_skip_inpaint("none", off_cfg)
    assert off_cfg.text_cover_vf == ""
    assert "tắt" in off_msg.lower()


def test_delogo_rejects_scaffolding_keeps_bottom_caption():
    from app.services.subtitle_detector import _is_delogo_candidate

    w, h = 540, 960
    # Upper building / scaffolding smear box from job_997f353f
    assert _is_delogo_candidate(20, 40, 500, 180, w, h) is False
    # Bottom Chinese hardsub
    assert _is_delogo_candidate(40, 780, 400, 70, w, h) is True
    # Small top-left watermark
    assert _is_delogo_candidate(12, 16, 90, 36, w, h) is True


def test_fast_auto_cover_samples_only_stable_regions(monkeypatch):
    from app.services.queue_manager import _fast_auto_cover_filters

    captured = {}

    def fake_detector(path, max_boxes, min_hits):
        captured.update(path=path, max_boxes=max_boxes, min_hits=min_hits)
        return ["delogo=x=10:y=20:w=100:h=30:show=0"]

    monkeypatch.setattr(
        "app.services.subtitle_detector.persistent_text_cover_filters",
        fake_detector,
    )

    filters = _fast_auto_cover_filters("source.mp4")

    assert filters == ["delogo=x=10:y=20:w=100:h=30:show=0"]
    assert captured == {"path": "source.mp4", "max_boxes": 4, "min_hits": 2}


def test_persistent_box_clusters_count_distinct_frames_and_use_median_size():
    from app.services.subtitle_detector import _cluster_persistent_boxes

    # Two neighboring detections from one frame must not impersonate persistence.
    same_frame = [
        (0, 10, 10, 300, 80),
        (0, 40, 12, 180, 40),
    ]
    assert _cluster_persistent_boxes(same_frame, 1024, 576, min_hits=2) == []

    boxes = [
        (0, 35, 26, 106, 27),
        (1, 34, 25, 108, 28),
        (2, 11, 0, 190, 54),  # One oversized detector result.
    ]
    clustered = _cluster_persistent_boxes(boxes, 1024, 576, min_hits=3)

    assert clustered == [(34, 25, 108, 28, 3)]


def test_same_line_fragments_merge_into_top_banner():
    from app.services.subtitle_detector import _merge_same_line_boxes

    fragments = [
        (22, 19, 56, 28),
        (51, 18, 93, 29),
        (162, 20, 27, 27),
        (211, 20, 28, 27),
        (258, 18, 26, 29),
        (470, 78, 183, 141),
    ]
    merged = _merge_same_line_boxes(fragments, 1024, 576)
    top = [box for box in merged if box[1] < 40]
    assert len(top) == 1
    x, y, bw, bh = top[0]
    assert x <= 22
    assert bw >= 250
    assert bh <= 40


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
    from app.services import tts_service as tts_module
    from app.services.tts_service import get_audio_duration, parse_srt_segments, tts_service

    srt = tmp_path / "voice.srt"
    first = "Mình bảo sao ngửi thấy mùi khai."
    second = "Thối chết đi được, cái chân nhỏ đó phải không?"
    srt.write_text(
        f"1\n00:00:00,000 --> 00:00:00,400\n{first}\n\n"
        f"2\n00:00:12,000 --> 00:00:12,400\n{second}\n",
        encoding="utf-8",
    )

    async def fake_generate_speech(*, output_path, **_kwargs):
        with wave.open(output_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * int(16000 * 0.8))
        return output_path

    real_scale_audio_speed = tts_module.scale_audio_speed_ffmpeg
    applied_speeds = []

    def track_scale_audio_speed(input_audio_path, output_audio_path, speed_factor, sample_rate=44100):
        applied_speeds.append(speed_factor)
        return real_scale_audio_speed(input_audio_path, output_audio_path, speed_factor, sample_rate)

    monkeypatch.setattr(tts_service, "generate_speech", fake_generate_speech)
    monkeypatch.setattr(tts_module, "scale_audio_speed_ffmpeg", track_scale_audio_speed)
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
    # Timeline 2x only shifts placement. VieNeu must not also rubberband 2x
    # or the voice runs ahead of the sped picture.
    assert all(abs(s - 2.0) > 0.2 for s in applied_speeds)
    assert all(abs(float(clip["speed_factor"]) - 2.0) > 0.2 for clip in result["clips"])
    assert get_audio_duration(str(output)) >= 0.7


@pytest.mark.anyio
async def test_vieneu_does_not_double_speed_against_timeline(monkeypatch, tmp_path):
    from app.services import tts_service as tts_module
    from app.services.tts_service import tts_service

    srt = tmp_path / "voice.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:04,000\nXin chào các bạn\n\n"
        "2\n00:00:05,000 --> 00:00:09,000\nHôm nay xem nguyên lý\n",
        encoding="utf-8",
    )

    async def fake_generate_speech(*, output_path, **_kwargs):
        with wave.open(output_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x40" * int(16000 * 1.2))
        return output_path

    applied = []

    def track_scale(input_audio_path, output_audio_path, speed_factor, sample_rate=44100):
        applied.append(speed_factor)
        return tts_module.scale_audio_speed_ffmpeg(
            input_audio_path, output_audio_path, speed_factor, sample_rate
        )

    monkeypatch.setattr(tts_service, "generate_speech", fake_generate_speech)
    monkeypatch.setattr(tts_module, "scale_audio_speed_ffmpeg", track_scale)
    result = await tts_service.synthesize_synchronized_tts(
        srt_path=str(srt),
        output_audio_path=str(tmp_path / "voice.wav"),
        voice="vieneu:Ngọc Huyền",
        engine="vieneu",
        enable_lipsync=True,
        timeline_speed=1.3,
    )
    assert result["segment_count"] == 2
    assert applied == []
    assert [round(float(c["speed_factor"]), 2) for c in result["clips"]] == [1.0, 1.0]


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


def test_tts_mix_preserves_source_gain():
    from app.services.reup_service import (
        build_tts_bgm_mix_filter,
        build_reup_filtergraph,
        should_use_demucs_for_dubbing,
    )
    from app.services.audio_service import build_timed_speech_ducking_filter, build_vocal_mute_ffmpeg_filter
    fc = build_tts_bgm_mix_filter()
    assert "sidechaincompress" in fc
    assert "asplit=2[voice_duck][voice_mix]" in fc
    assert "[bed][voice_duck]sidechaincompress=" in fc
    assert "volume=0.95[bed]" in fc
    assert "volume=2.20" in fc
    assert "threshold=0.08:ratio=4" in fc
    assert "alimiter" not in fc
    assert "[2:a]" not in fc
    assert "source_voice" not in fc
    assert "stereotools" not in fc
    assert "weights=1.00 1.15" in fc
    overlay_fc = build_tts_bgm_mix_filter(0.08, 1.22)
    assert overlay_fc == fc
    import inspect
    from app.services.reup_service import process_reup_video
    process_src = inspect.getsource(process_reup_video)
    assert "vocal_volume=cfg.original_vocal_volume" in process_src
    assert "Đang phủ giọng Việt riêng" in process_src
    assert "giữ audio gốc" not in process_src
    assert "refuse_incomplete_output" in process_src
    mute = build_vocal_mute_ffmpeg_filter(preserve_bgm=True)
    assert "stereotools" not in mute
    assert mute == "volume=0.70"
    cfg = ReupConfig(enable_vocal_mute=True, film_grain=0, pitch_shift=False, speed_factor=1.0)
    graph, has_a, vf, af = build_reup_filtergraph(cfg, has_audio=True)
    assert has_a is True
    assert "stereotools" not in graph
    assert "volume=0.70" in graph
    assert graph.count("[0:a]") == 1
    timed = build_timed_speech_ducking_filter([(1.0, 2.0)])
    assert "between(t,0.900,2.150)" in timed
    ducked = ReupConfig(
        enable_vocal_mute=True, preserve_bgm=True, film_grain=0,
        pitch_shift=False, speed_factor=1.0, vocal_mute_strategy="auto",
    )
    g2, _, _, af2 = build_reup_filtergraph(
        ducked, has_audio=True, speech_intervals=[(1.0, 2.0)],
    )
    assert "between(t," in af2
    assert "stereotools" not in af2
    assert ",0.10,1.0" in af2
    custom_duck = ReupConfig(
        enable_vocal_mute=True, preserve_bgm=True, film_grain=0,
        pitch_shift=False, speed_factor=1.0, vocal_mute_strategy="auto",
        original_vocal_volume=0.08,
    )
    _, _, _, af_custom = build_reup_filtergraph(
        custom_duck, has_audio=True, speech_intervals=[(1.0, 2.0)],
    )
    assert ",0.08,1.0" in af_custom
    dub_cfg = ReupConfig(enable_tts=True, enable_vocal_mute=True, vocal_mute_strategy="demucs")
    assert should_use_demucs_for_dubbing(dub_cfg, [(1.0, 2.0)]) is True
    assert should_use_demucs_for_dubbing(dub_cfg, []) is True
    duck_cfg = ReupConfig(enable_tts=True, enable_vocal_mute=True, vocal_mute_strategy="demucs_duck")
    assert should_use_demucs_for_dubbing(duck_cfg, [(1.0, 2.0)]) is True
    auto_cfg = ReupConfig(enable_tts=True, enable_vocal_mute=True, vocal_mute_strategy="auto")
    assert should_use_demucs_for_dubbing(auto_cfg, [(1.0, 2.0)]) is False
    mute_all_cfg = ReupConfig(enable_vocal_mute=True, preserve_bgm=False, vocal_mute_strategy="mute_all")
    assert should_use_demucs_for_dubbing(mute_all_cfg, [(1.0, 2.0)]) is False


def test_long_speech_duck_uses_sendcmd_not_giant_volume_expr(tmp_path):
    from app.services.audio_service import build_timed_speech_ducking_filter
    from app.services.reup_service import (
        build_reup_filtergraph,
        find_cached_tts_audio,
        find_cached_vietsub_srt,
    )

    many = [(float(i), float(i) + 0.8) for i in range(0, 400, 2)]
    duck_path = str(tmp_path / "duck.txt")
    timed = build_timed_speech_ducking_filter(many, duck_volume=0.22, command_path=duck_path)
    assert "asendcmd=" in timed
    assert "between(t," not in timed
    assert os.path.isfile(duck_path)
    text = open(duck_path, encoding="ascii").read()
    assert "volume volume 0.22" in text
    cfg = ReupConfig(
        enable_vocal_mute=True, preserve_bgm=True, film_grain=0,
        pitch_shift=False, speed_factor=1.0, vocal_mute_strategy="auto",
    )
    _, _, _, af = build_reup_filtergraph(
        cfg, has_audio=True, speech_intervals=many, duck_command_path=duck_path,
    )
    assert "asendcmd=" in af

    wav = tmp_path / "clip_synced_tts.wav"
    wav.write_bytes(b"\x00" * 4096)
    srt = tmp_path / "clip_vi.aligned.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n", encoding="utf-8")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    assert find_cached_tts_audio(str(video), str(tmp_path)).endswith("clip_synced_tts.wav")
    assert find_cached_vietsub_srt(str(video)).endswith("clip_vi.aligned.srt")


def test_vietnamese_overlay_keeps_tts_in_the_mix(tmp_path):
    import shutil
    import subprocess

    from app.services.reup_service import build_tts_bgm_mix_filter, find_ffmpeg_binary

    ffmpeg = find_ffmpeg_binary() or shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required to verify the TTS overlay graph")

    bed = tmp_path / "bed.wav"
    voice = tmp_path / "voice.wav"
    mixed = tmp_path / "mixed.wav"
    for path, freq in ((bed, 120), (voice, 1200)):
        subprocess.run(
            [
                ffmpeg, "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100:duration=1",
                "-ac", "2", str(path),
            ],
            check=True,
        )
    result = subprocess.run(
        [
            ffmpeg, "-y", "-loglevel", "error",
            "-i", str(bed), "-i", str(voice),
            "-filter_complex", build_tts_bgm_mix_filter(),
            "-map", "[aout]", str(mixed),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert mixed.exists() and mixed.stat().st_size > 1000


def test_vietsub_style_auto_and_recap_map_to_dub():
    from app.services.xai_media_service import resolve_vietsub_style, compact_vi_cue
    assert resolve_vietsub_style("auto", 60) == "dub"
    assert resolve_vietsub_style("auto", 200) == "dub"
    assert resolve_vietsub_style("auto", 900) == "dub"
    assert resolve_vietsub_style("recap", 900) == "dub"
    assert resolve_vietsub_style("funny", 900) == "funny"
    assert resolve_vietsub_style("goc", 60) == "dub"
    assert resolve_vietsub_style("kechuyen", 90) == "narrator"
    assert resolve_vietsub_style("vuinhon", 900) == "funny"
    short = compact_vi_cue("Mau đưa khô cá cho tôi")
    assert "\n" not in short
    wrapped = compact_vi_cue("Đây là một câu vietsub rất dài lê thê sẽ đè hết phần hình mèo đang chạy")
    assert "\n" in wrapped or len(wrapped) <= 42


def test_mid_text_cover_appended_to_filtergraph():
    cfg = ReupConfig(
        text_cover_vf="drawbox=x=10:y=10:w=80:h=20:t=fill:color=black@0.7",
        film_grain=0,
    )
    _, _, vf, _ = build_reup_filtergraph(cfg, has_audio=False)
    assert "drawbox=" in vf
    assert "delogo=" not in vf


def test_delogo_nodes_are_stripped_from_filtergraph():
    cfg = ReupConfig(
        text_cover_vf="delogo=x=10:y=10:w=80:h=20:show=0,drawbox=x=0:y=0:w=10:h=10:t=fill:color=black@1",
        film_grain=0,
    )
    _, _, vf, _ = build_reup_filtergraph(cfg, has_audio=False)
    assert "delogo=" not in vf
    assert "drawbox=" in vf


def test_vietsub_plate_and_cover_can_turn_off():
    from app.services.caption_cover import (
        caption_cover_drawbox,
        clamp_color_cover_height,
        clamp_subtitle_box_h,
        subtitle_force_style,
        subtitle_plate_drawbox,
    )

    assert clamp_color_cover_height(0) == 0.0
    assert clamp_subtitle_box_h(0) == 0.0
    assert caption_cover_drawbox("white_solid", 0) == ""
    assert subtitle_plate_drawbox("white_solid", 0.88, 0) == ""
    style = subtitle_force_style("white_solid", subtitle_box_h=0)
    assert "BorderStyle=1" in style
    assert "BackColour=&HFF000000" in style
    cfg = ReupConfig(
        caption_cover="white_solid",
        subtitle_bottom_crop=0.0,
        subtitle_box_h=0.0,
        crop_percent=0,
        film_grain=0,
        hflip=False,
    )
    _, _, vf, _ = build_reup_filtergraph(cfg, has_audio=False)
    assert "drawbox=" not in vf


def test_vietsub_plate_covers_hardsub_band():
    from app.services.caption_cover import subtitle_plate_drawbox

    bar = subtitle_plate_drawbox(
        "white_solid", 0.90, 0.10, subtitle_y=0.72, video_w=1080, video_h=1920,
    )
    assert "w=972" in bar
    assert "h=192" in bar
    assert "white@1" in bar
    y = int(round(1920 * 0.72 - 192 / 2))
    assert f"y={y}" in bar
    expr = subtitle_plate_drawbox("black_solid", 0.88, 0.08, subtitle_y=0.5)
    assert "iw*0.8800" in expr
    assert "ih*0.0800" in expr


def test_white_cover_uses_black_vietsub():
    from app.services.caption_cover import cover_cue_rgba, subtitle_force_style

    text, box, _stroke = cover_cue_rgba("white_solid")
    assert text[0] < 40 and text[1] < 40 and text[2] < 40
    assert box[0] > 200 and box[1] > 200 and box[2] > 200
    style = subtitle_force_style("white_solid")
    assert "PrimaryColour=&H00000000" in style
    assert "OutlineColour=&H00FFFFFF" in style
    assert "BorderStyle=1" in style
    dark_text, dark_box, _ = cover_cue_rgba("black_solid")
    assert dark_text[0] > 200
    assert dark_box[0] < 40


def test_color_cover_stays_pinned_to_bottom():
    from app.services.caption_cover import caption_cover_drawbox, caption_layout

    px = caption_cover_drawbox(
        "black_solid", 0.22, cover_pad=0.08, video_w=1080, video_h=1920,
    )
    assert "h=422" in px
    assert "y=1498" in px
    landscape = caption_cover_drawbox(
        "black_solid", 0.22, cover_pad=0.08, video_w=1920, video_h=1080,
    )
    assert "h=238" in landscape
    flush = caption_cover_drawbox("black_solid", 0.22, cover_pad=0.0)
    lifted = caption_cover_drawbox("black_solid", 0.22, cover_pad=0.12)
    assert flush == lifted
    assert "y=ih*0.7800" in flush
    assert "h=ih*0.2200" in flush
    flush_m, _ = caption_layout(1080, 1920, "black_solid", 0.22, cover_pad=0.0)
    lift_m, _ = caption_layout(1080, 1920, "black_solid", 0.22, cover_pad=0.08)
    assert flush_m == lift_m


def test_caption_cover_paints_bar_instead_of_cropping():
    from app.services.caption_cover import caption_cover_drawbox, subtitle_force_style

    cfg = ReupConfig(
        caption_cover="white_solid",
        subtitle_bottom_crop=0.20,
        crop_percent=0,
        film_grain=0,
        hflip=False,
    )
    _, _, vf, _ = build_reup_filtergraph(cfg, has_audio=False)
    assert "drawbox=" in vf
    assert "t=fill" in vf
    assert "white@1" in vf
    assert "crop=iw:trunc(ih*(1-0.20" not in vf
    style = subtitle_force_style("white_solid")
    assert "PrimaryColour=&H00000000" in style
    assert "OutlineColour=&H00FFFFFF" in style
    assert "BorderStyle=1" in style
    black = subtitle_force_style("black_solid")
    assert "PrimaryColour=&H00FFFFFF" in black
    assert "OutlineColour=&H00000000" in black

    off = ReupConfig(caption_cover="off", subtitle_bottom_crop=0.20, crop_percent=0, film_grain=0, hflip=False)
    _, _, vf_off, _ = build_reup_filtergraph(off, has_audio=False)
    assert "crop=iw:trunc(ih*(1-0.2000" in vf_off
    assert caption_cover_drawbox("off", 0.2) == ""
    assert "black@0.62" in caption_cover_drawbox("black_soft", 0.18)
    mid = caption_cover_drawbox("black_solid", 0.20, cover_y=0.5)
    assert "y=ih*0.8000" in mid
    assert "h=ih*0.2000" in mid
    lifted = caption_cover_drawbox("black_solid", 0.22, cover_pad=0.12)
    assert "h=ih*0.2200" in lifted
    assert caption_cover_drawbox("black_solid", 0.22, cover_pad=0.0) == lifted


def test_resolve_caption_cover_image_from_studio_url(tmp_path, monkeypatch):
    from app.config import settings as app_settings
    from app.services.caption_cover import resolve_caption_cover_image

    studio = tmp_path / "studio"
    studio.mkdir()
    banner = studio / "abc123.png"
    banner.write_bytes(b"img")
    monkeypatch.setattr(app_settings, "CHANNELS_DIR", str(tmp_path), raising=False)
    found = resolve_caption_cover_image("", "/api/v1/studio/overlay/abc123.png")
    assert os.path.isfile(found)
    assert found.endswith("abc123.png")


def test_filtered_size_image_cover_crops_hardsub_strip(monkeypatch):
    from app.services import reup_service

    monkeypatch.setattr(reup_service, "_probe_video_size", lambda _p: (720, 1280))
    cfg = ReupConfig(
        caption_cover="image",
        caption_cover_image="/tmp/x.png",
        subtitle_bottom_crop=0.30,
        crop_percent=0.02,
        force_bottom_crop=False,
        film_grain=0,
        hflip=False,
    )
    w, h = reup_service._filtered_video_size("in.mp4", cfg)
    assert w == 690
    assert h == 860


def _force_style_margin_v(style: str) -> int:
    import re
    match = re.search(r"MarginV=(\d+)", style)
    assert match, style
    return int(match.group(1))


def test_subtitle_style_on_image_cover_is_white():
    from app.services.caption_cover import subtitle_force_style

    style = subtitle_force_style("image", 0.30, video_w=1080, video_h=1920)
    assert "PrimaryColour=&H00FFFFFF" in style
    assert "BorderStyle=3" in style
    assert "Alignment=2" in style
    # 9:16 logo banner: sit just above the 30% plate (576px + ~2% pad).
    margin = _force_style_margin_v(style)
    assert 590 <= margin <= 640


def test_subtitle_y_uses_top_alignment():
    from app.services.caption_cover import subtitle_force_style

    style = subtitle_force_style("off", video_w=1920, video_h=1080, subtitle_y=0.5)
    assert "Alignment=8" in style
    margin = _force_style_margin_v(style)
    assert 480 <= margin <= 580
    auto = subtitle_force_style("off", video_w=1920, video_h=1080, subtitle_y=0)
    assert "Alignment=2" in auto


def test_subtitle_style_pins_bottom_on_landscape():
    from app.services.caption_cover import caption_layout, subtitle_force_style

    style = subtitle_force_style("image", 0.30, video_w=1920, video_h=1080)
    assert "Alignment=2" in style
    # 16:9 must not lift 18–30% of height — that parks the cue under the title.
    margin = _force_style_margin_v(style)
    assert 16 <= margin <= 40

    off = subtitle_force_style("off", video_w=1920, video_h=1080)
    assert _force_style_margin_v(off) <= 40
    assert "Alignment=2" in off

    pad, _font = caption_layout(1920, 1080, "image", 0.30)
    assert pad == margin


def test_image_cover_crops_bottom_hardsubs():
    cfg = ReupConfig(
        caption_cover="image",
        caption_cover_image="/tmp/does-not-need-to-exist-for-vf.png",
        subtitle_bottom_crop=0.24,
        crop_percent=0,
        film_grain=0,
        hflip=False,
        force_bottom_crop=False,
    )
    _, _, vf, _ = build_reup_filtergraph(cfg, has_audio=False)
    assert "crop=iw:trunc(ih*(1-0.2400" in vf
    assert "drawbox=" not in vf


def test_banner_overlay_fills_bottom_band(tmp_path):
    from app.services.overlay_service import append_overlay_filter, normalize_overlays

    img = tmp_path / "banner.jpg"
    img.write_bytes(b"\xff" * 64)
    items = normalize_overlays([{
        "image_path": str(img),
        "kind": "banner",
        "band_h": 0.22,
    }])
    assert items and items[0]["kind"] == "banner"
    fc, paths = append_overlay_filter("[0:v]null[v_out]", items, first_overlay_index=1, main_size=(1080, 1920))
    assert paths == [str(img.resolve())]
    assert "overlay=0:H-h" in fc
    assert "crop=1080:422" in fc or "crop=1080:" in fc


def test_crop_mode_cuts_bottom_even_if_cover_selected():
    cfg = ReupConfig(
        caption_cover="white_solid",
        force_bottom_crop=True,
        subtitle_bottom_crop=0.30,
        crop_percent=0,
        film_grain=0,
        hflip=False,
    )
    _, _, vf, _ = build_reup_filtergraph(cfg, has_audio=False)
    assert "crop=iw:trunc(ih*(1-0.3000" in vf
    assert "drawbox=" not in vf


def test_default_vietnamese_engine_uses_local_vieneu_presets():
    from app.services.tts_service import DEFAULT_VOICES
    assert ReupConfig().tts_voice == "vieneu:Trúc Ly"
    assert ReupConfig().tts_engine == "vieneu"
    assert DEFAULT_VOICES["vi"]["female"] == "vieneu:Trúc Ly"
    assert DEFAULT_VOICES["vi"]["male"] == "vieneu:Phạm Tuyên"

    migrated = ReupConfig(tts_voice="vi-VN-HoaiMy-Fast", tts_engine="edge-tts", target_lang="vi")
    assert migrated.tts_voice == "vieneu:Trúc Ly"
    assert migrated.tts_engine == "vieneu"


@pytest.mark.anyio
async def test_supported_vietnamese_voices_are_real_vieneu_presets():
    from app.api.process import get_supported_voices

    result = await get_supported_voices()
    vi_voices = [voice for voice in result["voices"] if voice["lang"] == "vi"]

    assert len(vi_voices) == 20
    assert result["default_voice"] == "vieneu:Trúc Ly"
    assert all(voice["id"].startswith("vieneu:") for voice in vi_voices)
    assert not any("HoaiMy" in voice["id"] or "NamMinh" in voice["id"] for voice in vi_voices)


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
    assert calls["init"]["mode"] == "v3turbo"
    assert calls["init"]["backend"] == "onnx"
    assert calls["infer"] == ("Xin chào Việt Nam", "Trúc Ly")
    assert output.stat().st_size > 256


@pytest.mark.anyio
async def test_edge_vietnamese_voice_is_not_pitch_shifted_by_a_fake_preset(tmp_path, monkeypatch):
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
        "Đây là giọng nữ tiêu chuẩn.",
        lang="vi",
        voice="vi-VN-HoaiMyNeural",
        output_path=str(tmp_path / "female.mp3"),
    )
    await provider.generate(
        "Đây là giọng nam tiêu chuẩn.",
        lang="vi",
        voice="vi-VN-NamMinhNeural",
        output_path=str(tmp_path / "male.mp3"),
    )

    assert calls[0]["voice"] == "vi-VN-HoaiMyNeural"
    assert calls[0]["rate"] == "+0%"
    assert calls[0]["pitch"] == "+0Hz"
    assert calls[1]["voice"] == "vi-VN-NamMinhNeural"
    assert calls[1]["rate"] == "+0%"
    assert calls[1]["pitch"] == "+0Hz"


@pytest.mark.anyio
async def test_vieneu_voice_overrides_stale_engine(tmp_path, monkeypatch):
    from app.services.tts_service import tts_service

    calls = {}

    class FakeProvider:
        async def generate(self, **kwargs):
            calls.update(kwargs)
            output_path = kwargs["output_path"]
            with open(output_path, "wb") as f:
                f.write(b"RIFF" + b"\x00" * 300)
            return output_path

    monkeypatch.setattr("app.services.tts_service.get_tts_provider", lambda _engine: FakeProvider())
    output = str(tmp_path / "review.wav")
    result = await tts_service.generate_speech(
        text="Giọng review tiếng Việt.",
        lang="vi",
        voice="vieneu:Trúc Ly",
        engine="edge-tts",
        output_path=output,
    )

    assert result == output
    assert calls["voice"] == "vieneu:Trúc Ly"


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
    assert calls["voice"] == "vieneu:Trúc Ly"
    assert calls["engine"] == "vieneu"
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

    from PIL import Image
    banded = render_srt_to_overlays(
        str(srt), 1080, 1920, str(tmp_path / "ovl_band"),
        cover_band=0.30, cover_kind="image",
    )
    assert banded
    bbox = Image.open(banded[0]["png"]).getbbox()
    assert bbox is not None
    band_top = int(1920 * 0.70)
    assert bbox[3] <= band_top + 8
    assert bbox[1] < band_top - 8

    landscape = render_srt_to_overlays(
        str(srt), 1920, 1080, str(tmp_path / "ovl_wide"),
        cover_band=0.30, cover_kind="image",
    )
    assert landscape
    wide_bbox = Image.open(landscape[0]["png"]).getbbox()
    assert wide_bbox is not None
    # Pin to the frame bottom — do not sit under a centered title card.
    assert wide_bbox[3] >= int(1080 * 0.90)
    assert wide_bbox[1] >= int(1080 * 0.72)


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
            "text": f"Câu số {index + 1}.",
        }
        for index in range(60)
    ]

    grouped = group_long_form_tts_segments(segments)

    assert 20 <= len(grouped) <= 60
    combined = " ".join(segment["text"] for segment in grouped)
    assert "Câu số 1" in combined
    assert "Câu số 60" in combined


def test_tts_groups_jump_cuts_but_keeps_real_pauses():
    from app.services.tts_service import group_long_form_tts_segments

    nearby_a = "Line A"
    nearby_b = "Line B"
    later = "Line C"
    segments = [
        {"index": 1, "start_time": 0.0, "end_time": 1.8, "duration": 1.8, "text": nearby_a},
        {"index": 2, "start_time": 1.92, "end_time": 3.4, "duration": 1.48, "text": nearby_b},
        {"index": 3, "start_time": 8.5, "end_time": 10.0, "duration": 1.5, "text": later},
    ]
    grouped = group_long_form_tts_segments(segments)
    assert len(grouped) == 3
    assert grouped[0]["text"] == nearby_a
    assert grouped[1]["text"] == nearby_b
    assert grouped[2]["text"] == later


def test_tts_does_not_merge_ten_second_shots():
    from app.services.tts_service import group_long_form_tts_segments

    first = "Đây là câu dài khớp đúng cảnh một."
    second = "Đây là câu dài khớp đúng cảnh hai."
    grouped = group_long_form_tts_segments([
        {"index": 1, "start_time": 0.0, "end_time": 7.57, "duration": 7.57, "text": first},
        {"index": 2, "start_time": 7.57, "end_time": 15.15, "duration": 7.58, "text": second},
        {"index": 3, "start_time": 15.45, "end_time": 17.0, "duration": 1.55, "text": "Cảnh sau"},
    ])
    assert len(grouped) == 3
    assert grouped[0]["end_time"] == 7.57
    assert grouped[1]["start_time"] == 7.57
    assert grouped[2]["text"] == "Cảnh sau"


def test_tts_keeps_a_jump_cut_as_its_own_take():
    from app.services.tts_service import group_long_form_tts_segments

    grouped = group_long_form_tts_segments([
        {"index": 1, "start_time": 0.0, "end_time": 1.8, "duration": 1.8, "text": "Câu trên hình một."},
        {"index": 2, "start_time": 2.1, "end_time": 3.6, "duration": 1.5, "text": "Câu trên hình hai."},
    ])
    assert len(grouped) == 2
    assert grouped[1]["start_time"] == 2.1


def test_tts_finishes_unfinished_vietnamese_sentence():
    from app.services.tts_service import group_long_form_tts_segments

    first = "Tôi cứ nghĩ xe năng lượng mới chỉ sạc điện hoặc đổ xăng, nhưng dạo này bạn có thấy từ"
    second = '"methanol" bỗng nhiên gây sốt, CCTV liên tục đưa tin.'
    grouped = group_long_form_tts_segments([
        {"index": 1, "start_time": 0.0, "end_time": 3.38, "duration": 3.38, "text": first},
        {"index": 2, "start_time": 3.38, "end_time": 6.82, "duration": 3.44, "text": second},
        {"index": 3, "start_time": 7.40, "end_time": 9.10, "duration": 1.70, "text": "Câu mới sau khi chấm."},
    ])
    assert len(grouped) == 2
    assert "methanol" in grouped[0]["text"]
    assert grouped[0]["end_time"] == 6.82
    assert grouped[1]["text"] == "Câu mới sau khi chấm."


def test_tts_soft_joins_period_so_last_sentence_does_not_hold():
    from app.services.tts_service import group_long_form_tts_segments, join_spoken_cue_text

    joined = join_spoken_cue_text("Câu cuối của đoạn này.", "Câu đầu của đoạn kia.", 0.18)
    assert joined == "Câu cuối của đoạn này Câu đầu của đoạn kia."
    assert ". C" not in joined

    grouped = group_long_form_tts_segments([
        {"index": 1, "start_time": 0.0, "end_time": 2.0, "duration": 2.0, "text": "Câu cuối của đoạn này."},
        {"index": 2, "start_time": 2.2, "end_time": 4.0, "duration": 1.8, "text": "Câu đầu của đoạn kia."},
    ])
    assert len(grouped) == 2
    assert grouped[0]["text"] == "Câu cuối của đoạn này."
    assert grouped[1]["text"] == "Câu đầu của đoạn kia."


def test_tts_does_not_glue_separate_paragraphs():
    from app.services.tts_service import group_long_form_tts_segments

    grouped = group_long_form_tts_segments([
        {"index": 1, "start_time": 0.0, "end_time": 2.0, "duration": 2.0, "text": "Hết đoạn một."},
        {"index": 2, "start_time": 3.6, "end_time": 5.2, "duration": 1.6, "text": "Sang đoạn hai."},
    ])
    assert len(grouped) == 2
    assert grouped[0]["text"] == "Hết đoạn một."
    assert grouped[1]["text"] == "Sang đoạn hai."


def test_tts_does_not_steal_next_shot_word():
    from app.services.tts_service import group_long_form_tts_segments

    grouped = group_long_form_tts_segments([
        {
            "index": 1,
            "start_time": 0.0,
            "end_time": 1.65,
            "duration": 1.65,
            "text": "Mua kẹo hồ lô",
        },
        {
            "index": 2,
            "start_time": 1.80,
            "end_time": 3.10,
            "duration": 1.30,
            "text": "ngọt lắm",
        },
    ])
    assert len(grouped) == 2
    assert grouped[0]["text"] == "Mua kẹo hồ lô"
    assert grouped[1]["text"] == "ngọt lắm"
    assert "ngọt" not in grouped[0]["text"]
    assert grouped[1]["start_time"] == pytest.approx(1.80)


def test_tts_keeps_a_new_image_as_its_own_take():
    from app.services.tts_service import group_long_form_tts_segments

    grouped = group_long_form_tts_segments([
        {"index": 1, "start_time": 0.0, "end_time": 2.4, "duration": 2.4, "text": "Câu của hình một."},
        {"index": 2, "start_time": 3.2, "end_time": 5.0, "duration": 1.8, "text": "Câu của hình hai."},
    ])
    assert len(grouped) == 2
    assert grouped[1]["start_time"] == pytest.approx(3.2)


def test_place_tts_clips_keeps_paragraph_pause():
    from app.services.tts_service import place_consecutive_tts_clips

    clips = [
        {"final_dur": 1.0, "segment": {"start_time": 0.0, "end_time": 1.0, "duration": 1.0, "text": "A"}},
        {"final_dur": 1.0, "segment": {"start_time": 3.0, "end_time": 4.0, "duration": 1.0, "text": "B"}},
    ]
    placed = place_consecutive_tts_clips(clips)
    assert placed[0]["segment"]["end_time"] == pytest.approx(1.0)
    assert placed[1]["segment"]["start_time"] == pytest.approx(3.0)


def test_place_tts_clips_stays_tight_on_jump_cuts():
    from app.services.tts_service import place_consecutive_tts_clips

    clips = [
        {"final_dur": 1.2, "segment": {"start_time": 0.0, "end_time": 1.0, "duration": 1.0, "text": "A"}},
        {"final_dur": 0.8, "segment": {"start_time": 1.15, "end_time": 2.0, "duration": 0.85, "text": "B"}},
    ]
    placed = place_consecutive_tts_clips(clips)
    assert placed[1]["segment"]["start_time"] == pytest.approx(1.22)
    assert placed[1]["segment"]["start_time"] >= placed[0]["segment"]["end_time"]


def test_trim_tts_silence_drops_leading_and_trailing_pad(tmp_path):
    from app.services.tts_service import get_audio_duration, trim_tts_silence

    src = tmp_path / "padded.wav"
    out = tmp_path / "trim.wav"
    rate = 16000
    spoken = b"\x00\x40" * int(rate * 0.25)
    pad = b"\x00\x00" * int(rate * 0.4)
    with wave.open(str(src), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pad + spoken + pad)
    assert trim_tts_silence(str(src), str(out)) is True
    duration = get_audio_duration(str(out))
    assert 0.22 <= duration <= 0.40


def test_limit_clip_to_duration_stops_before_next_shot(tmp_path):
    from app.services.tts_service import get_audio_duration, limit_clip_to_duration

    src = tmp_path / "long.wav"
    out = tmp_path / "cut.wav"
    rate = 16000
    with wave.open(str(src), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x40" * int(rate * 1.2))
    assert limit_clip_to_duration(str(src), str(out), 0.5) is True
    duration = get_audio_duration(str(out))
    assert 0.45 <= duration <= 0.55


@pytest.mark.anyio
async def test_vieneu_reads_micro_gaps_as_one_take(monkeypatch, tmp_path):
    from app.services.tts_service import parse_srt_segments, tts_service

    first = "Line A"
    second = "Line B"
    srt = tmp_path / "voice.srt"
    srt.write_text(
        f"1\n00:00:00,000 --> 00:00:01,800\n{first}\n\n"
        f"2\n00:00:01,920 --> 00:00:03,400\n{second}\n",
        encoding="utf-8",
    )
    spoken = []

    async def fake_generate_speech(*, text, output_path, **_kwargs):
        spoken.append(text)
        with wave.open(output_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * int(16000 * 1.2))
        return output_path

    monkeypatch.setattr(tts_service, "generate_speech", fake_generate_speech)
    output = tmp_path / "voice.wav"
    result = await tts_service.synthesize_synchronized_tts(
        srt_path=str(srt),
        output_audio_path=str(output),
        voice="vieneu:Trúc Ly",
        engine="vieneu",
        enable_lipsync=False,
    )

    assert spoken == [f"{first} {second}"]
    assert result["segment_count"] == 1
    aligned = parse_srt_segments(result["aligned_srt_path"])
    assert len(aligned) == 1
    assert second in aligned[0]["text"]


@pytest.mark.anyio
async def test_vieneu_keeps_jump_cut_on_its_own_take(monkeypatch, tmp_path):
    from app.services.tts_service import parse_srt_segments, tts_service

    first = "Câu hình một."
    second = "Câu hình hai."
    srt = tmp_path / "voice.srt"
    srt.write_text(
        f"1\n00:00:00,000 --> 00:00:01,800\n{first}\n\n"
        f"2\n00:00:02,100 --> 00:00:03,600\n{second}\n",
        encoding="utf-8",
    )
    spoken = []

    async def fake_generate_speech(*, text, output_path, **_kwargs):
        spoken.append(text)
        with wave.open(output_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * int(16000 * 0.6))
        return output_path

    monkeypatch.setattr(tts_service, "generate_speech", fake_generate_speech)
    output = tmp_path / "voice.wav"
    result = await tts_service.synthesize_synchronized_tts(
        srt_path=str(srt),
        output_audio_path=str(output),
        voice="vieneu:Trúc Ly",
        engine="vieneu",
        enable_lipsync=False,
    )

    assert spoken == [first, second]
    assert result["segment_count"] == 2
    aligned = parse_srt_segments(result["aligned_srt_path"])
    assert [item["text"] for item in aligned] == [first, second]
    assert aligned[1]["start_time"] >= 2.0


@pytest.mark.anyio
async def test_vieneu_does_not_chop_sentence_at_next_shot(monkeypatch, tmp_path):
    from app.services import tts_service as tts_module
    from app.services.tts_service import parse_srt_segments, tts_service

    srt = tmp_path / "voice.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nXin chào các bạn hôm nay xem nguyên lý.\n\n"
        "2\n00:00:02,400 --> 00:00:04,000\nCâu sau bắt đầu.\n",
        encoding="utf-8",
    )

    async def fake_generate_speech(*, text, output_path, **_kwargs):
        seconds = 3.2 if "nguyên lý" in (text or "") else 0.6
        with wave.open(output_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x40" * int(16000 * seconds))
        return output_path

    def keep_natural_duration(src, dest, speed_factor, sample_rate=44100):
        del speed_factor, sample_rate
        import shutil
        shutil.copy(src, dest)
        return True

    monkeypatch.setattr(tts_service, "generate_speech", fake_generate_speech)
    monkeypatch.setattr(tts_module, "scale_audio_speed_ffmpeg", keep_natural_duration)
    output = tmp_path / "voice.wav"
    result = await tts_service.synthesize_synchronized_tts(
        srt_path=str(srt),
        output_audio_path=str(output),
        voice="vieneu:Trúc Ly",
        engine="vieneu",
        enable_lipsync=False,
    )

    assert result["segment_count"] == 2
    first_clip = sorted(result["clips"], key=lambda item: float(item["segment"]["start_time"]))[0]
    # Next sentence starts at 2.4s. Chopping would cut ~2.4s; keep the whole take.
    assert first_clip["final_dur"] >= 3.0
    aligned = parse_srt_segments(result["aligned_srt_path"])
    assert "nguyên lý" in aligned[0]["text"]
    assert aligned[0]["end_time"] >= 3.0


@pytest.mark.anyio
async def test_tts_progress_callback_advances_after_each_batch(monkeypatch, tmp_path):
    from app.services.tts_service import TTSService

    srt = tmp_path / "many.srt"
    cues = []
    for index in range(7):
        cues.append(
            f"{index + 1}\n00:00:{index:02d},000 --> 00:00:{index + 1:02d},000\nCâu {index + 1}\n"
        )
    srt.write_text("\n".join(cues), encoding="utf-8")
    output = tmp_path / "voice.wav"
    progress = []
    service = TTSService(output_dir=str(tmp_path))

    async def fake_generate(**kwargs):
        path = kwargs["output_path"]
        with wave.open(path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(b"\x00\x00" * int(16000 * 0.2))
        return path

    monkeypatch.setattr(service, "generate_speech", fake_generate)
    monkeypatch.setattr(service, "_assemble_synchronized_audio", lambda *_args: True)

    result = await service.synthesize_synchronized_tts(
        str(srt),
        str(output),
        voice="vieneu:Trúc Ly",
        engine="vieneu",
        enable_lipsync=False,
        progress_callback=lambda completed, total: progress.append((completed, total)),
    )

    assert result["segment_count"] >= 1
    assert progress[0] == (0, result["segment_count"])
    assert progress[-1][0] == result["segment_count"]


def test_inpaint_progress_advances_immediately_after_pipeline_50_percent():
    import inspect
    from app.services.opencv_inpainter import _inpaint_frame_region, inpaint_video_opencv

    src = inspect.getsource(inpaint_video_opencv)
    assert "0.50 + 0.15 * (frames_processed / max(1, total_frames_est))" in src
    assert "0.35 + 0.30 * (frames_processed / max(1, total_frames_est))" not in src
    assert "frames_processed % 12" in inspect.getsource(_inpaint_frame_region)


def test_auto_watermark_mode_uses_fast_telea(monkeypatch, tmp_path):
    from app.services import watermark_service

    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    source.write_bytes(b"video")
    captured = {}

    class FakeInpainter:
        def __init__(self, radius, method):
            captured["radius"] = radius
            captured["method"] = method

        def inpaint_video(self, input_path, output_path, roi, progress_callback=None):
            captured["roi"] = roi
            return output_path

    monkeypatch.setattr(watermark_service, "OpenCVInpainter", FakeInpainter)
    monkeypatch.setattr("app.services.tts_service.get_audio_duration", lambda _path: 10.0)

    result = watermark_service.remove_watermark(
        str(source), str(output), method="auto", radius=5
    )

    assert result == str(output)
    assert captured["method"] == "telea"


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
    assert DEFAULT_VOICES["vi"]["child"] == "vieneu:Đoan Trang"
    assert DEFAULT_VOICES["vi"]["narrator"] == "vieneu:Thái Sơn"
    assert DEFAULT_VOICES["en"]["male"] == "en-US-GuyNeural"
    assert DEFAULT_VOICES["en"]["elder_male"] == "en-US-RyanNeural"


def test_heartbeat_emits_keep_alive_while_step_runs():
    import time
    from app.services.activity import heartbeat

    notes = []
    with heartbeat(notes.append, "Whisper đang nhận dạng lời thoại", interval=0.05):
        time.sleep(0.18)
    assert any("vẫn đang chạy" in note for note in notes)
    assert any("Whisper" in note for note in notes)


def test_pipeline_reports_live_stt_and_translate_status(monkeypatch, tmp_path):
    from app.services import pyvideotrans_service, reup_service, tts_service

    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    srt = tmp_path / "source.srt"
    vi = tmp_path / "source_vi.srt"
    source.write_bytes(b"video")
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    vi.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n", encoding="utf-8")
    messages = []

    monkeypatch.setattr(tts_service, "get_audio_duration", lambda _path: 8.0)

    def fake_stt(_self, *_args, **kwargs):
        on_status = kwargs.get("on_status")
        if on_status:
            on_status("🎧 Đang nạp model Whisper 'base'...")
        return {
            "status": "success",
            "srt_path": str(srt),
            "cue_count": 1,
            "model": "base",
            "detected_language": "zh",
        }

    def fake_translate(_self, *_args, **kwargs):
        on_status = kwargs.get("on_status")
        if on_status:
            on_status("🌐 Đang gửi 1 câu sang DeepSeek (deepseek-v4-flash)...")
        return {"status": "success", "srt_path": str(vi), "provider": "deepseek"}

    monkeypatch.setattr(pyvideotrans_service.PyVideoTransService, "speech_to_text", fake_stt)
    monkeypatch.setattr(pyvideotrans_service.PyVideoTransService, "translate_subtitles", fake_translate)
    monkeypatch.setattr(
        reup_service,
        "process_reup_video",
        lambda **kwargs: {"output_path": kwargs["output_path"]},
    )

    result = reup_service.ReupService.process_reup_pipeline(
        str(source),
        ReupConfig(
            enable_tts=False,
            subtitle_mode="hard",
            burn_subtitles=True,
            source_lang="zh",
            target_lang="vi",
        ),
        str(output),
        stage_progress_callback=lambda _progress, message: messages.append(message),
    )

    assert result == str(output)
    blob = "\n".join(messages)
    assert "Whisper" in blob
    assert "1 câu" in blob
    assert "DeepSeek" in blob
    assert "render" in blob.lower()


def test_tts_and_inpaint_callbacks_write_activity_logs(tmp_path):
    from app.services.queue_manager import BatchQueueManager

    manager = BatchQueueManager(db_path=str(tmp_path / "jobs.sqlite"), max_concurrent_jobs=1)
    try:
        job_id = manager.enqueue_job("input.mp4", "output.mp4")
        tts_cb = manager._tts_progress_callback(job_id)
        tts_cb(0, 10)
        tts_cb(4, 10)
        inpaint_cb = manager._stage_progress_callback(job_id, "WATERMARK_REMOVAL")
        inpaint_cb(0.515)
        inpaint_cb(0.575)
        messages = [entry["message"] for entry in manager.get_job(job_id)["logs"]]
        assert any("Bắt đầu TTS" in message for message in messages)
        assert any("4/10" in message for message in messages)
        assert any("10%" in message for message in messages)
        assert any("50%" in message for message in messages)
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)
