"""
Empirical Stress Test Suite for Milestone 3 E2E Pipeline & Media Export Verification.
========================================================================================
Target: FFmpeg pipeline re-encoding, MP4 output ffprobe verification, frame decoding,
video streaming (HTTP 200/206/416/404), frame extraction API, concurrent load stress test.

Target Path: tests/test_challenger_m3.py
"""

import os
import sys
import subprocess
import json
import concurrent.futures
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app, _seed_sample_media
from app.config import settings
from app.services.queue_manager import BatchQueueManager
from app.models.job import WatermarkConfig, ReupConfig

client = TestClient(app)


def test_exported_mp4_ffprobe_and_decode_integrity():
    """Verify data/output/douyin_123_reup_final2.mp4 contains valid H.264 video and AAC audio streams with 0 decode errors."""
    _seed_sample_media()
    base_dir = str(settings.BASE_DIR)
    output_mp4 = os.path.join(base_dir, "data", "output", "douyin_123_reup_final2.mp4")
    if not os.path.exists(output_mp4):
        from app.services.reup_service import process_reup_video
        raw_src = os.path.join(settings.RAW_INPUT_DIR, "douyin_123.mp4")
        process_reup_video(raw_src, output_mp4, speed_ratio=1.03, crop_percent=0.015)
    assert os.path.exists(output_mp4), f"Exported MP4 file missing: {output_mp4}"

    # File size threshold (> 100KB = 102,400 bytes)
    file_size = os.path.getsize(output_mp4)
    assert file_size > 102400, f"Exported MP4 size ({file_size} bytes) is below 100KB threshold!"

    # Probe format and streams via ffprobe
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size:stream=codec_name,codec_type",
        "-of", "json", output_mp4
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    probe_data = json.loads(res.stdout)

    streams = probe_data.get("streams", [])
    video_codecs = [s["codec_name"] for s in streams if s.get("codec_type") == "video"]
    audio_codecs = [s["codec_name"] for s in streams if s.get("codec_type") == "audio"]

    assert "h264" in video_codecs, f"Expected h264 video codec, found: {video_codecs}"
    assert "aac" in audio_codecs, f"Expected aac audio codec, found: {audio_codecs}"

    duration = float(probe_data.get("format", {}).get("duration", 0))
    assert 4.0 <= duration <= 6.0, f"Unexpected duration: {duration}s"

    # FFmpeg null decode test (zero errors)
    decode_cmd = ["ffmpeg", "-v", "error", "-i", output_mp4, "-f", "null", "-"]
    decode_res = subprocess.run(decode_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert decode_res.returncode == 0, f"FFmpeg decode failed: {decode_res.stderr}"
    assert decode_res.stderr.strip() == "", f"FFmpeg decode reported errors: {decode_res.stderr}"


def test_video_stream_api_full_and_range_requests():
    """Verify /api/v1/videos/stream/{media_id} for HTTP 200, 206 Partial Content, 416 Range Not Satisfiable, and 404."""
    _seed_sample_media()

    # 1. Full Stream (HTTP 200)
    res_200 = client.get("/api/v1/videos/stream/douyin_123")
    assert res_200.status_code == 200
    assert res_200.headers["content-type"] == "video/mp4"
    assert res_200.headers["accept-ranges"] == "bytes"
    file_size = int(res_200.headers["content-length"])
    assert file_size > 10240

    # 2. Byte Range (HTTP 206) - start and end
    res_206_1 = client.get("/api/v1/videos/stream/douyin_123", headers={"Range": "bytes=0-1023"})
    assert res_206_1.status_code == 206
    assert res_206_1.headers["content-range"] == f"bytes 0-1023/{file_size}"
    assert len(res_206_1.content) == 1024

    # 3. Byte Range (HTTP 206) - start only
    res_206_2 = client.get("/api/v1/videos/stream/douyin_123", headers={"Range": "bytes=1000-"})
    assert res_206_2.status_code == 206
    assert res_206_2.headers["content-range"] == f"bytes 1000-{file_size - 1}/{file_size}"
    assert len(res_206_2.content) == file_size - 1000

    # 4. Byte Range (HTTP 206) - suffix only
    res_206_3 = client.get("/api/v1/videos/stream/douyin_123", headers={"Range": "bytes=-500"})
    assert res_206_3.status_code == 206
    assert res_206_3.headers["content-range"] == f"bytes {file_size - 500}-{file_size - 1}/{file_size}"
    assert len(res_206_3.content) == 500

    # 5. Invalid Range (HTTP 416) - start > end
    res_416_1 = client.get("/api/v1/videos/stream/douyin_123", headers={"Range": "bytes=500-100"})
    assert res_416_1.status_code == 416

    # 6. Invalid Range (HTTP 416) - out of bounds
    res_416_2 = client.get("/api/v1/videos/stream/douyin_123", headers={"Range": f"bytes={file_size + 100}-{file_size + 200}"})
    assert res_416_2.status_code == 416

    # 7. Invalid Range Unit (HTTP 416)
    res_416_3 = client.get("/api/v1/videos/stream/douyin_123", headers={"Range": "items=0-10"})
    assert res_416_3.status_code == 416

    # 8. Non-existent media ID (HTTP 404)
    res_404 = client.get("/api/v1/videos/stream/nonexistent_media_id_9999")
    assert res_404.status_code == 404


def test_video_frame_extraction_api():
    """Verify /api/v1/videos/frame/{media_id} for JPEG/PNG image formats and timestamp extraction."""
    _seed_sample_media()

    # 1. JPEG frame at timestamp 1.0s
    res_jpeg = client.get("/api/v1/videos/frame/douyin_123?timestamp=1.0&format=jpeg")
    assert res_jpeg.status_code == 200
    assert res_jpeg.headers["content-type"] in ("image/jpeg", "image/jpg")
    assert len(res_jpeg.content) > 1000

    # 2. PNG frame at timestamp 2.0s
    res_png = client.get("/api/v1/videos/frame/douyin_123?timestamp=2.0&format=png")
    assert res_png.status_code == 200
    assert res_png.headers["content-type"] == "image/png"
    assert len(res_png.content) > 1000

    # 3. Specific frame_index
    res_idx = client.get("/api/v1/videos/frame/douyin_123?frame_index=10")
    assert res_idx.status_code == 200
    assert len(res_idx.content) > 1000

    # 4. Non-existent media ID (HTTP 404)
    res_404 = client.get("/api/v1/videos/frame/nonexistent_media_id_9999")
    assert res_404.status_code == 404


def test_concurrent_api_requests_stress():
    """Stress-test concurrent requests to stream and frame API endpoints across multiple threads."""
    _seed_sample_media()

    def make_request(req_type: int):
        if req_type == 0:
            r = client.get("/api/v1/videos/stream/douyin_123", headers={"Range": "bytes=0-511"})
            return r.status_code == 206 and len(r.content) == 512
        elif req_type == 1:
            r = client.get("/api/v1/videos/stream/douyin_123")
            return r.status_code == 200
        else:
            r = client.get("/api/v1/videos/frame/douyin_123?timestamp=1.5")
            return r.status_code == 200

    # Execute 30 concurrent requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(make_request, i % 3) for i in range(30)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert all(results), "At least one concurrent API request failed!"


def test_e2e_reup_pipeline_execution_fresh(tmp_path):
    """Executes a fresh E2E reup pipeline run and verifies the resulting MP4 output with ffprobe."""
    _seed_sample_media()
    db_file = os.path.join(tmp_path, "e2e_m3_test.sqlite")
    qm = BatchQueueManager(db_path=db_file)

    input_path = os.path.join(settings.RAW_INPUT_DIR, "douyin_123.mp4")
    output_path = os.path.join(tmp_path, "fresh_e2e_reup_output.mp4")

    wm_cfg = WatermarkConfig(enabled=True, roi_x=5, roi_y=5, roi_width=50, roi_height=30)
    reup_cfg = ReupConfig(enabled=True, hflip=True, speed_factor=1.05, enable_tts=False, modify_md5=True)

    job_id = qm.enqueue_job(input_path, output_path, params=reup_cfg.model_dump())
    res = qm.process_job(job_id)

    assert res["status"] == "COMPLETED"
    assert os.path.exists(output_path)
    assert os.path.getsize(output_path) > 100000

    # ffprobe check on fresh output
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size:stream=codec_name",
        "-of", "default=noprint_wrappers=1", output_path
    ]
    ffres = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    assert "codec_name=h264" in ffres.stdout
    assert "codec_name=aac" in ffres.stdout


def test_video_upload_endpoint():
    """Verify POST /api/v1/videos/upload saves video file and returns metadata."""
    _seed_sample_media()
    sample_file = os.path.join(settings.RAW_INPUT_DIR, "douyin_123.mp4")
    assert os.path.exists(sample_file)

    with open(sample_file, "rb") as f:
        file_bytes = f.read()

    res = client.post(
        "/api/v1/videos/upload",
        files={"file": ("test_upload_video.mp4", file_bytes, "video/mp4")}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["video_id"].startswith("upload_")
    assert os.path.exists(data["file_path"])
    assert data["file_size"] > 10000


def test_auto_subtitle_and_logo_watermark_removal(tmp_path):
    """Verify auto watermark detection and inpainting without explicit manual ROI."""
    _seed_sample_media()
    from app.services.watermark_service import remove_watermark_and_subtitles

    sample_input = os.path.join(settings.RAW_INPUT_DIR, "douyin_123.mp4")
    out_wm = os.path.join(tmp_path, "auto_wm_out.mp4")

    wm_cfg = WatermarkConfig(enabled=True, algorithm="auto", roi_x=0, roi_y=0, roi_width=0, roi_height=0)
    res_path = remove_watermark_and_subtitles(video_path=sample_input, config=wm_cfg, output_path=out_wm)

    assert os.path.exists(res_path)
    assert os.path.getsize(res_path) > 10000


