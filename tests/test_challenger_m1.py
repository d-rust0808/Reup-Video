"""
Empirical Stress Test Suite for Milestone 1 Backend Infrastructure.
===================================================================
Target: SQLite DB Concurrency, Job State Machine, STT Fallbacks, FFmpeg Error Integrity.
"""

import os
import sys
import json
import tempfile
import asyncio
import sqlite3
import threading
import pytest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import get_db_connection, init_db, checkpoint_db
from app.services.queue_manager import BatchQueueManager
from app.services.reup_service import process_reup_video, ReupService
from app.services.pyvideotrans_service import PyVideoTransService
from app.main import _seed_sample_media
from app.config import settings


def test_sqlite_wal_concurrency(tmp_path):
    """Stress test SQLite database connection with concurrent multi-threaded writes & reads."""
    db_file = os.path.join(tmp_path, "concurrency_test.sqlite")
    init_db(db_file)

    errors = []
    success_count = [0]
    lock = threading.Lock()

    def worker_write(worker_id: int):
        qm = BatchQueueManager(db_path=db_file)
        for i in range(20):
            try:
                job_id = qm.enqueue_job(
                    input_path=f"data/input/raw/test_{worker_id}_{i}.mp4",
                    output_path=f"data/output/test_{worker_id}_{i}.mp4"
                )
                qm.update_job_status(job_id, "PROCESSING", progress=i * 5)
                job = qm.get_job(job_id)
                assert job["status"] == "PROCESSING"
                with lock:
                    success_count[0] += 1
            except Exception as e:
                with lock:
                    errors.append((worker_id, i, str(e)))

    threads = []
    # 10 concurrent threads each doing 20 write/read cycles (200 total job ops)
    for w in range(10):
        t = threading.Thread(target=worker_write, args=(w,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    checkpoint_db(db_file)

    qm_final = BatchQueueManager(db_path=db_file)
    all_jobs = qm_final.list_jobs()

    assert len(errors) == 0, f"SQLite concurrency encountered errors: {errors}"
    assert len(all_jobs) == 200, f"Expected 200 jobs in DB, found {len(all_jobs)}"
    assert success_count[0] == 200


def test_queue_job_status_transitions_and_recoveries(tmp_path):
    """Tests full job lifecycle, state transitions under normal & failure conditions, cancellation, and restart recovery."""
    db_file = os.path.join(tmp_path, "lifecycle_test.sqlite")
    qm = BatchQueueManager(db_path=db_file)

    # 1. Normal transition: PENDING -> DOWNLOADING -> WATERMARK_REMOVAL -> REUP_TRANSFORM -> COMPLETED
    sample_input = "data/input/raw/douyin_123.mp4"
    sample_output = os.path.join(tmp_path, "completed_out.mp4")
    job_id_1 = qm.enqueue_job(sample_input, sample_output)

    assert qm.get_job(job_id_1)["status"] == "PENDING"

    # Process job synchronously
    res = qm.process_job(job_id_1)
    assert res["status"] == "COMPLETED"
    assert os.path.exists(sample_output)
    assert os.path.getsize(sample_output) > 10000

    # 2. Failure transition: Missing file -> FAILED with error_message
    missing_input = "data/input/raw/non_existent_file_9999.mp4"
    fail_output = os.path.join(tmp_path, "fail_out.mp4")
    job_id_2 = qm.enqueue_job(missing_input, fail_output)

    with pytest.raises(FileNotFoundError):
        qm.process_job(job_id_2)

    failed_job = qm.get_job(job_id_2)
    assert failed_job["status"] == "FAILED"
    assert "not found" in failed_job["error_message"].lower()

    # 3. Cancellation transition
    job_id_3 = qm.enqueue_job(sample_input, os.path.join(tmp_path, "cancel_out.mp4"))
    assert qm.cancel_job(job_id_3) is True
    cancelled_job = qm.get_job(job_id_3)
    assert cancelled_job["status"] == "CANCELLED"

    # 4. Job Recovery on Startup
    # Manually insert jobs stuck in intermediate states
    with get_db_connection(db_file) as conn:
        conn.execute("INSERT INTO jobs (job_id, status, created_at, updated_at) VALUES ('job-stuck-1', 'WATERMARK_REMOVAL', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO jobs (job_id, status, created_at, updated_at) VALUES ('job-stuck-2', 'REUP_TRANSFORM', '2026-01-01', '2026-01-01')")
        conn.commit()

    asyncio.run(qm.recover_jobs())

    rec_1 = qm.get_job("job-stuck-1")
    rec_2 = qm.get_job("job-stuck-2")
    assert rec_1["status"] == "PENDING"
    assert rec_2["status"] == "PENDING"


def test_full_pipeline_with_seeded_videos(tmp_path):
    """Executes queue manager processing jobs for kuaishou_456.mp4 and xiaohongshu_789.mp4."""
    db_file = os.path.join(tmp_path, "seeded_jobs.sqlite")
    qm = BatchQueueManager(db_path=db_file)

    for sid in ["kuaishou_456", "xiaohongshu_789"]:
        inp_path = f"data/input/raw/{sid}.mp4"
        out_path = os.path.join(tmp_path, f"out_{sid}.mp4")

        assert os.path.exists(inp_path), f"Seeded file {inp_path} does not exist!"

        job_id = qm.enqueue_job(inp_path, out_path)
        job_res = qm.process_job(job_id)

        assert job_res["status"] == "COMPLETED"
        assert os.path.exists(out_path)
        assert os.path.getsize(out_path) > 10000


def test_stt_fallback_stub_generation(tmp_path):
    """Stress test speech-to-text fallback stub generation under missing whisper / invalid CLI scenarios."""
    pyvt = PyVideoTransService()

    input_video = "data/input/raw/kuaishou_456.mp4"
    out_dir = os.path.join(tmp_path, "sub_output")

    res = pyvt.speech_to_text(input_video, output_dir=out_dir)

    assert "srt_path" in res
    srt_path = res["srt_path"]
    assert os.path.exists(srt_path)

    # Verify SRT format
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "1" in content
    assert "-->" in content
    assert "[Sample Subtitle]" in content


def test_corrupt_video_rejection(tmp_path):
    """Verifies that process_reup_video strictly rejects corrupt video files with RuntimeError instead of passing mock data."""
    corrupt_file = os.path.join(tmp_path, "corrupt.mp4")
    with open(corrupt_file, "wb") as f:
        f.write(b"CORRUPT_NOT_A_VALID_MP4_HEADER_BYTES_1234567890")

    out_file = os.path.join(tmp_path, "corrupt_out.mp4")

    with pytest.raises(RuntimeError) as exc_info:
        process_reup_video(corrupt_file, out_file)

    assert "FFmpeg execution failed" in str(exc_info.value)
    if os.path.exists(out_file):
        assert os.path.getsize(out_file) == 0


def test_sample_media_reseeding_on_corruption(tmp_path):
    """Verifies _seed_sample_media replaces corrupt mock sample media files with valid MP4 files."""
    test_seed_dir = os.path.join(tmp_path, "raw_input")
    os.makedirs(test_seed_dir, exist_ok=True)

    # Mock settings.RAW_INPUT_DIR temporarily
    orig_raw_dir = settings.RAW_INPUT_DIR
    settings.RAW_INPUT_DIR = test_seed_dir

    try:
        # Create corrupt mock byte file
        mock_file = os.path.join(test_seed_dir, "douyin_123.mp4")
        with open(mock_file, "wb") as f:
            f.write(b"END_OF_MP4_SAMPLE mock byte marker")

        _seed_sample_media()

        assert os.path.exists(mock_file)
        assert os.path.getsize(mock_file) > 10240
        with open(mock_file, "rb") as f:
            header = f.read(100)
            assert b"END_OF_MP4_SAMPLE" not in header
    finally:
        settings.RAW_INPUT_DIR = orig_raw_dir


def test_db_boundary_and_corrupt_json_handling(tmp_path):
    """Tests queue manager resilience against corrupt JSON configs or NaN/infinity progress values in DB."""
    db_file = os.path.join(tmp_path, "boundary_test.sqlite")
    qm = BatchQueueManager(db_path=db_file)

    job_id = qm.enqueue_job("data/input/raw/douyin_123.mp4", os.path.join(tmp_path, "out.mp4"))

    # Test invalid progress values
    qm.update_job_status(job_id, "PROCESSING", progress=float("nan"))
    j = qm.get_job(job_id)
    assert j["progress"] == 0.0

    qm.update_job_status(job_id, "PROCESSING", progress=float("inf"))
    j = qm.get_job(job_id)
    assert j["progress"] == 0.0

    # Test string progress
    qm.update_job_status(job_id, "PROCESSING", progress="50.0")
    j = qm.get_job(job_id)
    assert j["progress_percent"] == 50.0

    # Test corrupt JSON in DB
    with get_db_connection(db_file) as conn:
        conn.execute("UPDATE jobs SET reup_config = 'INVALID_JSON{{{', watermark_config = 'NOT_JSON' WHERE job_id = ?", (job_id,))
        conn.commit()

    j_corrupt = qm.get_job(job_id)
    assert j_corrupt["params"] == {}
    assert j_corrupt["job_id"] == job_id

