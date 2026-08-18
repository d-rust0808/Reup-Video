"""
Backend Pytest Suite for Reup-Video System.
============================================
Tests health endpoints, sample media seeding, FFmpeg error handling,
STT fallback, and SQLite DB CRUD operations.

Target Path: tests/test_backend.py
"""

import os
import pytest
from fastapi.testclient import TestClient

from app.main import app, _seed_sample_media
from app.config import settings
from app.core.database import init_db, get_db_connection
from app.services.queue_manager import BatchQueueManager
from app.services.reup_service import process_reup_video
from app.services.pyvideotrans_service import PyVideoTransService

client = TestClient(app)


def test_health_check_endpoints():
    """Verify system health check endpoints return 200 OK."""
    res_root = client.get("/health")
    assert res_root.status_code == 200
    assert res_root.json()["status"] == "ok"

    res_v1 = client.get("/api/v1/health")
    assert res_v1.status_code == 200
    assert res_v1.json()["status"] == "ok"


def test_sample_media_seeding():
    """Verify sample media seeding creates genuine playable MP4 files > 10KB."""
    settings.ensure_directories()
    _seed_sample_media()

    seed_ids = ["douyin_123", "kuaishou_456", "xiaohongshu_789"]
    for sid in seed_ids:
        fpath = os.path.join(settings.RAW_INPUT_DIR, f"{sid}.mp4")
        assert os.path.exists(fpath), f"Sample media file missing: {fpath}"
        file_size = os.path.getsize(fpath)
        assert file_size > 10240, f"Sample media file {fpath} is too small: {file_size} bytes"

        with open(fpath, "rb") as f:
            header = f.read(100)
            assert b"END_OF_MP4_SAMPLE" not in header, f"Sample file {fpath} is still mock corrupt bytes"


def test_ffmpeg_error_handling(tmp_path):
    """Verify FFmpeg failure in process_reup_video raises RuntimeError instead of silent copy."""
    nonexistent_path = str(tmp_path / "nonexistent_input.mp4")
    out_path = str(tmp_path / "output.mp4")

    # Non-existent input should raise FileNotFoundError
    with pytest.raises(FileNotFoundError):
        process_reup_video(input_path=nonexistent_path, output_path=out_path)

    # Corrupt or invalid input file should raise RuntimeError upon FFmpeg execution
    corrupt_input = str(tmp_path / "corrupt.mp4")
    with open(corrupt_input, "wb") as f:
        f.write(b"NOT_A_VALID_VIDEO_HEADER_CONTENT_BYTES")

    with pytest.raises(RuntimeError) as exc_info:
        process_reup_video(input_path=corrupt_input, output_path=out_path)

    assert "FFmpeg execution failed" in str(exc_info.value) or "FFmpeg" in str(exc_info.value)


def test_stt_fallback_stub(tmp_path):
    """Verify speech_to_text creates a stub SRT fallback file when whisper is missing/fails."""
    dummy_input = str(tmp_path / "sample_input.mp4")
    with open(dummy_input, "wb") as f:
        f.write(b"dummy video content for stt test")

    service = PyVideoTransService()
    res = service.speech_to_text(dummy_input, output_dir=str(tmp_path))

    assert "srt_path" in res
    srt_path = res["srt_path"]
    assert os.path.exists(srt_path)
    assert srt_path.endswith(".srt")

    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()
        assert "[Sample Subtitle]" in content or "00:00:00" in content


def test_database_and_queue_crud(tmp_path):
    """Verify SQLite database initialization and BatchQueueManager CRUD operations."""
    db_file = str(tmp_path / "test_jobs.sqlite")
    init_db(db_file)

    qm = BatchQueueManager(db_path=db_file)
    input_file = os.path.join(settings.RAW_INPUT_DIR, "douyin_123.mp4")
    output_file = str(tmp_path / "test_out.mp4")

    job_id = qm.enqueue_job(input_file, output_file)
    assert job_id.startswith("job-")

    job = qm.get_job(job_id)
    assert job is not None
    assert job["job_id"] == job_id
    assert job["status"] == "PENDING"
    assert job["input_path"] == input_file

    qm.update_job_status(job_id, "PROCESSING", progress=50)
    job_updated = qm.get_job(job_id)
    assert job_updated["status"] == "PROCESSING"
    assert job_updated["progress_percent"] == 50.0

    qm.update_job_status(job_id, "COMPLETED", progress=100)
    job_completed = qm.get_job(job_id)
    assert job_completed["status"] == "COMPLETED"
    assert job_completed["progress_percent"] == 100.0

