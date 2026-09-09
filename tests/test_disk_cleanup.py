"""Cleanup of already-reup'd videos must free outputs + sources + TTS, and never touch in-flight jobs."""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings
from app.services.disk_cleanup import (
    cleanup_completed_reup,
    release_scratch_after_complete,
    remove_library_files,
)
from app.services.queue_manager import BatchQueueManager
from app.services.sample_media import SAMPLE_IDS


def _write(path, size=32):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"v" * size)


def _insert_job(db_path, job_id, status, input_path, output_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO jobs (job_id, status, input_file_path, output_file_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, status, input_path, output_path, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()


def test_cleanup_completed_reup_keeps_pending_and_samples(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    out = tmp_path / "outputs"
    tts = out / "tts"
    raw.mkdir()
    out.mkdir()
    tts.mkdir()

    monkeypatch.setattr(settings, "RAW_INPUT_DIR", str(raw))
    monkeypatch.setattr(settings, "OUTPUT_DIR", str(out))
    monkeypatch.setattr(settings, "TTS_OUTPUT_DIR", str(tts))

    done_src = raw / "EOTcuEj-SvA.mp4"
    done_json = raw / "EOTcuEj-SvA.json"
    done_srt = raw / "EOTcuEj-SvA_vi.aligned.srt"
    pending_src = raw / "0rsu-gnEnQI.mp4"
    cancelled_src = raw / "kdPzaSM9O3M.mp4"
    sample_src = raw / f"{SAMPLE_IDS[0]}.mp4"
    leftover_tts = tts / "EOTcuEj-SvA_synced_tts.wav"
    pending_tts = tts / "0rsu-gnEnQI_synced_tts.wav"

    for path in (done_src, done_json, done_srt, pending_src, cancelled_src, sample_src, leftover_tts, pending_tts):
        _write(str(path), 64)

    master = out / "job_done.mp4"
    facebook = out / "job_done.facebook.mp4"
    tiktok = out / "job_done.tiktok.mp4"
    shorts = out / "job_done.youtube_shorts.mp4"
    stage_srt = out / "job_done_stage2_vi.srt"
    pending_out = out / "job_pending.mp4"
    for path in (master, facebook, tiktok, shorts, stage_srt, pending_out):
        _write(str(path), 128)

    db_path = tmp_path / "jobs.db"
    manager = BatchQueueManager(db_path=str(db_path), max_concurrent_jobs=1)
    _insert_job(db_path, "job_done", "COMPLETED", str(done_src), str(master))
    _insert_job(db_path, "job_pending", "PENDING", str(pending_src), str(pending_out))
    _insert_job(db_path, "job_cancel", "CANCELLED", str(cancelled_src), str(out / "job_cancel.mp4"))

    result = cleanup_completed_reup(manager)

    assert result["jobs_deleted"] == 1
    assert result["bytes_freed"] > 0
    assert manager.get_job("job_done") is None
    assert manager.get_job("job_pending") is not None
    assert manager.get_job("job_cancel") is not None

    assert not master.exists()
    assert not facebook.exists()
    assert not tiktok.exists()
    assert not shorts.exists()
    assert not stage_srt.exists()
    assert pending_out.exists()

    assert not done_src.exists()
    assert not done_json.exists()
    assert not done_srt.exists()
    assert pending_src.exists()
    assert cancelled_src.exists()
    assert sample_src.exists()

    assert not leftover_tts.exists()
    assert pending_tts.exists()


def test_release_scratch_after_complete_deletes_source_and_tts(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    tts = tmp_path / "tts"
    raw.mkdir()
    tts.mkdir()
    monkeypatch.setattr(settings, "RAW_INPUT_DIR", str(raw))
    monkeypatch.setattr(settings, "TTS_OUTPUT_DIR", str(tts))

    src = raw / "NwBWji5aZdk.mp4"
    titled = raw / "youtube_NwBWji5aZdk_clip.mp4"
    wav = tts / "NwBWji5aZdk_synced_tts.wav"
    _write(str(src), 80)
    _write(str(titled), 80)
    _write(str(wav), 80)

    job = {
        "job_id": "job_new",
        "status": "COMPLETED",
        "input_file_path": str(src),
        "source_url": "https://www.youtube.com/watch?v=NwBWji5aZdk",
    }
    result = release_scratch_after_complete(job, queue_manager=None)
    assert result["bytes_freed"] > 0
    assert not src.exists()
    assert not titled.exists()
    assert not wav.exists()


def test_release_scratch_skips_sample_and_active_source(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(settings, "RAW_INPUT_DIR", str(raw))
    monkeypatch.setattr(settings, "TTS_OUTPUT_DIR", str(tmp_path / "tts"))

    sample = raw / f"{SAMPLE_IDS[0]}.mp4"
    active = raw / "keepme12345.mp4"
    _write(str(sample), 40)
    _write(str(active), 40)

    class _QM:
        def list_jobs(self, status_filter=None):
            return [{"status": "PENDING", "input_file_path": str(active), "source_url": str(active)}]

    sample_res = release_scratch_after_complete(
        {"job_id": "j1", "status": "COMPLETED", "input_file_path": str(sample)},
        queue_manager=_QM(),
    )
    active_res = release_scratch_after_complete(
        {"job_id": "j2", "status": "COMPLETED", "input_file_path": str(active)},
        queue_manager=_QM(),
    )
    assert sample.exists()
    assert active.exists()
    assert sample_res["bytes_freed"] == 0
    assert active_res["bytes_freed"] == 0


def test_remove_library_files_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "RAW_INPUT_DIR", str(tmp_path))
    first = remove_library_files("missingid12")
    second = remove_library_files("missingid12")
    assert first["removed"] == []
    assert second["removed"] == []


def test_purge_stale_downloads_cleans_ytdl_and_part_files(tmp_path):
    from app.services.disk_cleanup import purge_stale_downloads

    stale_ytdl_dir = tmp_path / "video.12345.ytdl"
    stale_ytdl_dir.mkdir()
    part_file = stale_ytdl_dir / "video.f251.webm.part"
    part_file.write_bytes(b"data" * 100)

    orphan_part = tmp_path / "direct.part"
    orphan_part.write_bytes(b"temp" * 50)

    normal_file = tmp_path / "valid_video.mp4"
    normal_file.write_bytes(b"keep" * 100)

    res = purge_stale_downloads(str(tmp_path))
    assert "video.12345.ytdl" in res["removed"]
    assert "direct.part" in res["removed"]
    assert not stale_ytdl_dir.exists()
    assert not orphan_part.exists()
    assert normal_file.exists()

