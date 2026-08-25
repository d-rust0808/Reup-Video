"""
Async Batch Queue Manager & Persistent SQLite Task Processing System.
======================================================================
Target Path: app/services/queue_manager.py
"""

import os
import json
import math
import uuid
import shutil
import asyncio
import sqlite3
import logging
import threading
from datetime import datetime, timezone

from typing import Optional, List, Dict, Any, Callable, Union
from concurrent.futures import ProcessPoolExecutor

from app.models.job import JobAborted, JobStatus, WatermarkConfig, ReupConfig
from app.core.database import get_db_connection, init_db, DEFAULT_DB_PATH
from app.services.reup_service import process_reup_video

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_coro_sync(coro):
    """Executes a coroutine synchronously, safely handling existing running event loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(coro)).result()
    else:
        return asyncio.run(coro)


def _can_resume_completed_stage2(job: Dict[str, Any], stage2_path: str) -> bool:
    """Reuse an inpaint result only when the prior attempt logged its completion."""
    if not stage2_path or not os.path.isfile(stage2_path) or os.path.getsize(stage2_path) < 5000:
        return False
    if not os.path.isfile(stage2_path + ".complete"):
        return False
    logs = job.get("logs") or []
    if isinstance(logs, str):
        try:
            logs = json.loads(logs)
        except Exception:
            return False
    return any(
        isinstance(item, dict)
        and str(item.get("level") or "").upper() == "SUCCESS"
        and "Đã inpaint chữ/watermark" in str(item.get("message") or "")
        for item in logs
    )


def _retry_progress_for_job(job: Optional[Dict[str, Any]]) -> float:
    """Keep UI progress at 65% when a retry can reuse completed inpainting."""
    if not job:
        return 0.05
    output_path = job.get("output_path") or job.get("output_file_path")
    job_id = job.get("job_id")
    if not output_path or not job_id:
        return 0.05
    stage2_path = os.path.join(
        os.path.dirname(os.path.abspath(output_path)),
        f"{job_id}_stage2.mp4",
    )
    return 0.65 if _can_resume_completed_stage2(job, stage2_path) else 0.05


def _fast_auto_cover_filters(video_path: str) -> List[str]:
    """Detect stable overlay regions from five samples instead of every frame."""
    from app.services.subtitle_detector import persistent_text_cover_filters

    return persistent_text_cover_filters(video_path, max_boxes=4, min_hits=3)


class BatchQueueManager:
    """
    Asynchronous task queue manager backed by SQLite persistence (jobs.sqlite)
    and ProcessPoolExecutor for heavy video/audio anti-copyright transformations.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH, max_concurrent_jobs: int = 2):
        self.db_path = db_path
        self.max_concurrent_jobs = max_concurrent_jobs
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.executor = ProcessPoolExecutor(max_workers=max_concurrent_jobs)
        self._workers: List[asyncio.Task] = []
        self._callbacks: List[Callable] = []
        self._shutdown = threading.Event()
        
        # Initialize SQLite database schema
        init_db(self.db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """Returns thread-safe connection to SQLite database."""
        return get_db_connection(self.db_path)

    def register_callback(self, callback: Callable) -> None:
        """Registers a progress callback function (WebSocket subscriber or logger)."""
        self._callbacks.append(callback)

    def _notify_callbacks(self, job_dict: Dict[str, Any]) -> None:
        """Invokes registered callbacks safely."""
        for cb in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(cb(job_dict))
                    except RuntimeError:
                        pass
                else:
                    cb(job_dict)
            except Exception as e:
                logger.warning(f"Error in queue callback: {e}")

    def ensure_workers(self) -> None:
        """Ensures that worker pool tasks are actively running in the current event loop."""
        self._workers = [w for w in self._workers if not w.done()]
        try:
            loop = asyncio.get_running_loop()
            while len(self._workers) < self.max_concurrent_jobs:
                worker = loop.create_task(self._worker_loop())
                self._workers.append(worker)
        except RuntimeError:
            pass

    async def start(self) -> None:
        """Starts worker pool and recovers pending/interrupted jobs from SQLite."""
        await self.recover_jobs()
        self.ensure_workers()

    async def stop(self) -> None:
        """Signals in-flight pipeline threads to unwind, then tears down the worker pool."""
        self._shutdown.set()
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            gathered = asyncio.gather(*self._workers, return_exceptions=True)
            try:
                await asyncio.wait_for(gathered, timeout=20)
            except asyncio.TimeoutError:
                logger.warning("Worker tasks did not unwind within 20s; forcing teardown")
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _abort_requested(self, job_id: str) -> bool:
        if self._shutdown.is_set():
            return True
        job = self.get_job(job_id)
        return not job or (job.get("status") or "").upper() == "CANCELLED"

    def _claim_pending_job(self, job_id: str) -> bool:
        """Atomically reserves a queued job so duplicate queue entries cannot run it twice."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'STARTING', updated_at = ?
                WHERE job_id = ? AND UPPER(status) IN ('PENDING', 'QUEUED')
                """,
                (_utc_now_iso(), job_id),
            )
            conn.commit()
            return cursor.rowcount == 1

    def _stage_progress_callback(self, job_id: str, stage: str) -> Callable[[float], None]:
        """Progress sink that doubles as the cancellation checkpoint for worker threads.

        The inpaint loops call this every 15 frames; raising here is the only way to
        unwind a CPU-bound stage, since cancelling the awaiting asyncio task does not
        interrupt the thread running it.
        """
        def _cb(p: float) -> None:
            if self._abort_requested(job_id):
                raise JobAborted(job_id)
            self.update_job_progress(job_id, p, stage=stage)

        return _cb

    def _tts_progress_callback(self, job_id: str) -> Callable[[int, int], None]:
        """Expose long-form TTS progress instead of leaving the UI frozen at 85%."""
        last_logged_bucket = -1

        def _cb(completed: int, total: int) -> None:
            nonlocal last_logged_bucket
            if self._abort_requested(job_id):
                raise JobAborted(job_id)
            safe_total = max(1, int(total or 0))
            safe_completed = max(0, min(safe_total, int(completed or 0)))
            ratio = safe_completed / safe_total
            progress = 0.85 + (0.07 * ratio)
            bucket = min(10, int(ratio * 10))
            if safe_completed > 0 and bucket > last_logged_bucket:
                last_logged_bucket = bucket
                self.append_job_log(
                    job_id,
                    f"🎙️ TTS đã xử lý {safe_completed}/{safe_total} đoạn ({ratio:.0%})",
                    level="INFO",
                    stage="REUP_TRANSFORM",
                    progress=progress,
                )
            else:
                self.update_job_progress(job_id, progress, stage="REUP_TRANSFORM")

        return _cb

    async def recover_jobs(self) -> None:
        """Recovers interrupted jobs from database on startup/restart."""
        job_ids = []
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT job_id FROM jobs WHERE status IN ('PENDING', 'QUEUED', 'STARTING', 'DOWNLOADING', 'WATERMARK_REMOVAL', 'REUP_TRANSFORM', 'PROCESSING')"
            )
            rows = cursor.fetchall()
            for row in rows:
                job_ids.append(row["job_id"])

            if job_ids:
                now_iso = _utc_now_iso()
                for jid in job_ids:
                    conn.execute(
                        "UPDATE jobs SET status = 'PENDING', updated_at = ? WHERE job_id = ?",
                        (now_iso, jid)
                    )
                conn.commit()

        for jid in job_ids:
            await self.queue.put(jid)

    async def add_job(
        self,
        source_url: str,
        watermark_cfg: Optional[Union[WatermarkConfig, dict]] = None,
        reup_cfg: Optional[Union[ReupConfig, dict]] = None,
        platform: str = "auto"
    ) -> str:
        """Adds a new job asynchronously to the processing queue."""
        if isinstance(watermark_cfg, WatermarkConfig):
            wm_cfg = watermark_cfg
        elif isinstance(watermark_cfg, dict):
            wm_cfg = WatermarkConfig(**watermark_cfg)
        else:
            wm_cfg = WatermarkConfig()

        if isinstance(reup_cfg, ReupConfig):
            r_cfg = reup_cfg
        elif isinstance(reup_cfg, dict):
            r_cfg = ReupConfig(**reup_cfg)
        else:
            r_cfg = ReupConfig()

        job_id = f"job-{uuid.uuid4().hex[:8]}"
        now = _utc_now_iso()

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO jobs (
                    job_id, source_url, platform, status, progress_percent,
                    watermark_config, reup_config, created_at, updated_at
                ) VALUES (?, ?, ?, 'PENDING', 0.0, ?, ?, ?, ?)""",
                (
                    job_id, source_url, platform,
                    wm_cfg.model_dump_json(),
                    r_cfg.model_dump_json(),
                    now, now
                )
            )
            conn.commit()

        self.ensure_workers()
        await self.queue.put(job_id)
        return job_id


    # -----------------------------------------------------------------------
    # Test Suite & Synchronous Compatibility Methods (tests/test_reup.py)
    # -----------------------------------------------------------------------

    def enqueue_job(self, input_path: str, output_path: str, params: Optional[dict] = None) -> str:
        """Synchronous helper method for enqueuing jobs during test runs."""
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        now = _utc_now_iso()
        reup_dict = params if isinstance(params, dict) else {}
        wm_cfg = WatermarkConfig()

        # Parse reup config parameters
        valid_reup_keys = set(ReupConfig.model_fields.keys())
        clean_params = {k: v for k, v in reup_dict.items() if k in valid_reup_keys or k == "speed_ratio"}
        r_cfg = ReupConfig(**clean_params)

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO jobs (
                    job_id, source_url, platform, status, progress_percent,
                    input_file_path, output_file_path, watermark_config, reup_config,
                    created_at, updated_at
                ) VALUES (?, ?, 'auto', 'PENDING', 0.0, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id, input_path, input_path, output_path,
                    wm_cfg.model_dump_json(),
                    r_cfg.model_dump_json(),
                    now, now
                )
            )
            conn.commit()
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Fetches job record by ID and converts to dictionary format."""
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            if not row:
                return None
            
            d = dict(row)
            inp_path = d.get("input_file_path") or d.get("source_url") or ""
            out_path = d.get("output_file_path") or ""
            prog_val = d.get("progress_percent")
            if prog_val is None:
                prog_val = d.get("progress")
            if prog_val is None:
                prog_val = 0.0
            try:
                raw_prog = float(prog_val)
            except (ValueError, TypeError):
                raw_prog = 0.0
            prog_ratio = raw_prog / 100.0 if raw_prog > 1.0 else raw_prog
            prog_pct = raw_prog if raw_prog > 1.0 else raw_prog * 100.0
            raw_status = d.get("status")
            if raw_status is None:
                status_upper = "PENDING"
            else:
                status_upper = str(raw_status).strip().upper() or "PENDING"

            params_dict = {}
            raw_reup = d.get("reup_config")
            if isinstance(raw_reup, dict):
                params_dict = raw_reup
            elif isinstance(raw_reup, str) and raw_reup.strip():
                try:
                    parsed = json.loads(raw_reup)
                    if isinstance(parsed, dict):
                        params_dict = parsed
                except Exception:
                    params_dict = {}

            raw_logs = d.get("logs")
            logs_list = []
            if isinstance(raw_logs, list):
                logs_list = raw_logs
            elif isinstance(raw_logs, str) and raw_logs.strip():
                try:
                    parsed_logs = json.loads(raw_logs)
                    if isinstance(parsed_logs, list):
                        logs_list = parsed_logs
                except Exception:
                    logs_list = []

            raw_msg = d.get("message") or ""

            return {
                "job_id": d["job_id"],
                "source_url": d.get("source_url", ""),
                "platform": d.get("platform", "auto"),
                "status": status_upper,
                "progress": prog_ratio,
                "progress_percent": prog_pct,
                "stage": status_upper,
                "input_path": inp_path,
                "output_path": out_path,
                "input_file_path": inp_path,
                "output_file_path": out_path,
                "error_message": d.get("error_message"),
                "error": d.get("error_message"),
                "message": str(raw_msg),
                "log": str(raw_msg),
                "logs": logs_list,
                "params": params_dict,
                "watermark_config": d.get("watermark_config"),
                "reup_config": d.get("reup_config"),
                "created_at": d.get("created_at"),
                "updated_at": d.get("updated_at"),
            }

    def append_job_log(
        self,
        job_id: str,
        message: str,
        level: str = "INFO",
        stage: Optional[str] = None,
        progress: Optional[Union[float, int, str]] = None
    ) -> None:
        """Appends a new structured log entry to the job and triggers real-time broadcast."""
        now_time = datetime.now().strftime("%H:%M:%S")
        log_entry = {
            "timestamp": now_time,
            "level": str(level).upper(),
            "stage": stage or "",
            "message": str(message)
        }

        with self._get_conn() as conn:
            cursor = conn.execute("SELECT logs FROM jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            if not row:
                return

            logs_list = []
            try:
                if row["logs"]:
                    parsed = json.loads(row["logs"])
                    if isinstance(parsed, list):
                        logs_list = parsed
            except Exception:
                logs_list = []

            logs_list.append(log_entry)
            if len(logs_list) > 500:
                logs_list = logs_list[-500:]

            updates = ["logs = ?", "message = ?", "updated_at = ?"]
            vals: List[Any] = [json.dumps(logs_list, ensure_ascii=False), str(message), _utc_now_iso()]

            if stage:
                updates.append("status = ?")
                vals.append(str(stage).strip().upper())

            if progress is not None:
                try:
                    prog_float = float(progress)
                    if not (math.isnan(prog_float) or math.isinf(prog_float)):
                        pct = prog_float * 100.0 if prog_float <= 1.0 else prog_float
                        pct = max(0.0, min(100.0, pct))
                        # Progress shown over WebSocket and progress loaded after a
                        # page refresh must come from the same monotonic value.
                        updates.append("progress_percent = MAX(COALESCE(progress_percent, 0), ?)")
                        vals.append(pct)
                except Exception:
                    pass

            vals.append(job_id)
            conn.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE job_id = ?", vals)
            conn.commit()

        updated_job = self.get_job(job_id)
        if updated_job:
            self._notify_callbacks(updated_job)

    def update_job_status(
        self,
        job_id: str,
        status: str,
        progress: Optional[Union[float, int, str]] = None,
        error_message: Optional[str] = None,
        message: Optional[str] = None
    ) -> None:
        """Updates job status, progress percentage, and error state in SQLite."""
        now = _utc_now_iso()
        if status is None:
            status_upper = "PENDING"
        else:
            status_upper = str(status).strip().upper() or "PENDING"

        updates = ["status = ?", "updated_at = ?"]
        vals: List[Any] = [status_upper, now]

        if progress is not None:
            try:
                prog_float = float(progress)
                if math.isnan(prog_float) or math.isinf(prog_float):
                    prog_float = 0.0
                pct = prog_float * 100.0 if prog_float <= 1.0 else prog_float
                pct = max(0.0, min(100.0, pct))
                if status_upper == "PENDING":
                    # An explicit retry starts a new attempt and may reset progress.
                    updates.append("progress_percent = ?")
                else:
                    updates.append("progress_percent = MAX(COALESCE(progress_percent, 0), ?)")
                vals.append(pct)
            except (ValueError, TypeError):
                logger.warning(f"Invalid progress value '{progress}' for job {job_id}, skipping progress update")

        if error_message is not None or status_upper == "PENDING":
            updates.append("error_message = ?")
            vals.append(error_message)

        if message is not None:
            updates.append("message = ?")
            vals.append(str(message))

        vals.append(job_id)

        with self._get_conn() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE job_id = ?", vals)
            conn.commit()

        updated_job = self.get_job(job_id)
        if updated_job:
            self._notify_callbacks(updated_job)

    def update_job_progress(
        self,
        job_id: str,
        progress: Union[float, int, str],
        stage: Optional[str] = None
    ) -> None:
        """Lightweight method to update progress percentage and broadcast to WebSocket."""
        self.update_job_status(job_id, status=stage or "WATERMARK_REMOVAL", progress=progress)


    def _row_to_dict(self, row) -> Dict[str, Any]:
        """Converts an SQLite row to a clean job dictionary."""
        d = dict(row)
        inp_path = d.get("input_file_path") or d.get("source_url") or ""
        out_path = d.get("output_file_path") or ""
        prog_val = d.get("progress_percent")
        if prog_val is None:
            prog_val = d.get("progress")
        if prog_val is None:
            prog_val = 0.0
        try:
            raw_prog = float(prog_val)
        except (ValueError, TypeError):
            raw_prog = 0.0
        prog_ratio = raw_prog / 100.0 if raw_prog > 1.0 else raw_prog
        prog_pct = raw_prog if raw_prog > 1.0 else raw_prog * 100.0
        raw_status = d.get("status")
        if raw_status is None:
            status_upper = "PENDING"
        else:
            status_upper = str(raw_status).strip().upper() or "PENDING"

        params_dict = {}
        raw_reup = d.get("reup_config")
        if isinstance(raw_reup, dict):
            params_dict = raw_reup
        elif isinstance(raw_reup, str) and raw_reup.strip():
            try:
                parsed = json.loads(raw_reup)
                if isinstance(parsed, dict):
                    params_dict = parsed
            except Exception:
                params_dict = {}

        raw_logs = d.get("logs")
        logs_list = []
        if isinstance(raw_logs, list):
            logs_list = raw_logs
        elif isinstance(raw_logs, str) and raw_logs.strip():
            try:
                parsed_logs = json.loads(raw_logs)
                if isinstance(parsed_logs, list):
                    logs_list = parsed_logs
            except Exception:
                logs_list = []

        raw_msg = d.get("message") or ""

        return {
            "job_id": d["job_id"],
            "source_url": d.get("source_url", ""),
            "platform": d.get("platform", "auto"),
            "status": status_upper,
            "progress": prog_ratio,
            "progress_percent": prog_pct,
            "stage": status_upper,
            "input_path": inp_path,
            "output_path": out_path,
            "input_file_path": inp_path,
            "output_file_path": out_path,
            "error_message": d.get("error_message"),
            "error": d.get("error_message"),
            "message": str(raw_msg),
            "log": str(raw_msg),
            "logs": logs_list,
            "watermark_config": d.get("watermark_config"),
            "reup_config": d.get("reup_config"),
            "params": params_dict,
            "created_at": d.get("created_at"),
            "updated_at": d.get("updated_at"),
        }

    def list_jobs(self, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists jobs with optional status filter in a single optimized query."""
        with self._get_conn() as conn:
            if status_filter:
                cursor = conn.execute("SELECT * FROM jobs WHERE UPPER(status) = ? ORDER BY created_at ASC", (status_filter.upper(),))
            else:
                cursor = conn.execute("SELECT * FROM jobs ORDER BY created_at ASC")
            
            rows = cursor.fetchall()
            return [self._row_to_dict(r) for r in rows]

    def list_jobs_paginated(
        self, status_filter: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> tuple:
        """Lists jobs with optional status filter and offset/limit pagination in a single optimized query."""
        with self._get_conn() as conn:
            if status_filter:
                sf = status_filter.upper()
                count_cursor = conn.execute("SELECT COUNT(*) FROM jobs WHERE UPPER(status) = ?", (sf,))
                total = count_cursor.fetchone()[0]
                cursor = conn.execute(
                    "SELECT * FROM jobs WHERE UPPER(status) = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (sf, limit, offset)
                )
            else:
                count_cursor = conn.execute("SELECT COUNT(*) FROM jobs")
                total = count_cursor.fetchone()[0]
                cursor = conn.execute(
                    "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset)
                )
            
            rows = cursor.fetchall()
            return [self._row_to_dict(r) for r in rows], total


    def cancel_job(self, job_id: str) -> bool:
        """Cancels a pending or active job and cleans up partial output files."""
        job = self.get_job(job_id)
        if not job or job["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return False

        self.update_job_status(job_id, "CANCELLED")
        out_path = job.get("output_path") or job.get("output_file_path")
        if out_path and os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError as e:
                logger.warning(f"Could not remove partial output file {out_path}: {e}")
        return True

    async def retry_job(self, job_id: str) -> bool:
        """Retries a failed or cancelled job by resetting status to PENDING and re-enqueueing."""
        job = self.get_job(job_id)
        if not job or job["status"] not in ("FAILED", "CANCELLED"):
            return False

        input_path = job.get("input_file_path") or job.get("input_path") or job.get("source_url")
        if not input_path or not os.path.exists(input_path):
            self.update_job_status(
                job_id,
                "FAILED",
                error_message=f"Không thể chạy lại: video nguồn không còn tồn tại ({input_path or 'không xác định'})",
            )
            return False

        self.update_job_status(job_id, "PENDING", progress=0.0, error_message=None)
        await self.queue.put(job_id)
        return True

    async def retry_failed_jobs(self) -> int:
        jobs = self.list_jobs()
        n = 0
        for job in jobs:
            if (job.get("status") or "").upper() in ("FAILED", "CANCELLED"):
                if await self.retry_job(job["job_id"]):
                    n += 1
        return n

    def find_active_by_input(self, input_path: str) -> Optional[Dict[str, Any]]:
        if not input_path:
            return None
        base = os.path.basename(input_path)
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM jobs
                WHERE UPPER(status) NOT IN ('COMPLETED','FAILED','CANCELLED')
                  AND (input_file_path = ? OR input_file_path LIKE ?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (input_path, f"%{base}"),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def fail_stuck_jobs(self, max_minutes: int = 25) -> int:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_minutes)
        n = 0
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT job_id, updated_at FROM jobs
                WHERE UPPER(status) NOT IN ('COMPLETED','FAILED','CANCELLED','PENDING','QUEUED')
                """
            ).fetchall()
        for row in rows:
            raw = row["updated_at"] or ""
            try:
                ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if ts < cutoff:
                self.update_job_status(
                    row["job_id"], "FAILED", progress=0.0,
                    error_message="Job treo quá lâu — đã tự hủy",
                    message="Timeout watchdog",
                )
                n += 1
        return n

    def delete_job(self, job_id: str) -> bool:
        """Deletes job output file from disk and deletes job row from SQLite database."""
        job = self.get_job(job_id)
        if not job:
            return False

        out_path = job.get("output_path") or job.get("output_file_path")
        if out_path:
            out_dir = os.path.dirname(os.path.abspath(out_path))
            master_stem = os.path.splitext(os.path.basename(out_path))[0]
            # Platform exports use <job_id>.<platform>.mp4 and otherwise reappear
            # when /outputs scans the directory after a page reload.
            for name in os.listdir(out_dir) if os.path.isdir(out_dir) else []:
                is_video = name == f"{master_stem}.mp4" or (name.startswith(f"{master_stem}.") and name.endswith(".mp4"))
                is_subtitle = name == f"{master_stem}.vi.srt"
                if is_video or is_subtitle:
                    try:
                        os.remove(os.path.join(out_dir, name))
                    except OSError as e:
                        logger.warning(f"Could not delete output file {name}: {e}")

        with self._get_conn() as conn:
            conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
            conn.commit()
        return True

    def delete_jobs_batch(self, job_ids: List[str]) -> int:
        """Deletes multiple jobs and their associated output files."""
        deleted_count = 0
        for jid in job_ids:
            if self.delete_job(jid):
                deleted_count += 1
        return deleted_count

    def clear_jobs(self, status_filter: Optional[str] = None) -> int:
        """
        Clears jobs by status (e.g. 'COMPLETED', 'FAILED', 'CANCELLED') or all terminal jobs if None.
        """
        with self._get_conn() as conn:
            if status_filter:
                cursor = conn.execute(
                    "SELECT job_id, output_file_path FROM jobs WHERE UPPER(status) = UPPER(?)",
                    (status_filter,)
                )
            else:
                cursor = conn.execute(
                    "SELECT job_id, output_file_path FROM jobs WHERE UPPER(status) IN ('COMPLETED', 'FAILED', 'CANCELLED')"
                )
            rows = cursor.fetchall()

            job_ids_to_del = []
            for r in rows:
                jid = r["job_id"]
                job_ids_to_del.append(jid)
                out_p = r["output_file_path"]
                if out_p:
                    for target in (out_p, os.path.splitext(out_p)[0] + ".vi.srt"):
                        if os.path.exists(target):
                            try:
                                os.remove(target)
                            except OSError as e:
                                logger.warning(f"Could not delete output file {target}: {e}")

            if job_ids_to_del:
                placeholders = ",".join("?" * len(job_ids_to_del))
                conn.execute(f"DELETE FROM jobs WHERE job_id IN ({placeholders})", job_ids_to_del)
                conn.commit()
            return len(job_ids_to_del)

    def _run_pipeline_stages(self, job_id: str) -> Dict[str, Any]:
        """
        Executes full 4-stage job processing pipeline synchronously:
          Stage 1 (DOWNLOADING): Source video acquisition and input path validation.
          Stage 2 (WATERMARK_REMOVAL): Subtitle & watermark detection & inpainting via WatermarkService.
          Stage 3 (REUP_TRANSFORM): Audio-video transformation, vocal muting, TTS synthesis via ReupService.
          Stage 4 (COMPLETED): Persistence, final output verification, and status updates.
        """
        from app.config import settings
        from app.services.watermark_service import WatermarkService
        from app.services.reup_service import ReupService

        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")
        if job["status"] == "CANCELLED":
            return job

        try:
            # ------------------------------------------------------------------
            # Stage 1: DOWNLOADING (Input Validation & Acquisition)
            # ------------------------------------------------------------------
            input_file = job.get("input_file_path") or job.get("input_path") or job.get("source_url") or ""
            source_url = job.get("source_url") or ""

            self.append_job_log(
                job_id,
                f"🚀 Khởi chạy tác vụ [{job_id}] | Chuẩn bị nạp tài nguyên...",
                level="INFO",
                stage="DOWNLOADING",
                progress=0.05
            )

            current_video_path: Optional[str] = None
            if input_file and os.path.exists(input_file):
                current_video_path = input_file
                self.append_job_log(
                    job_id,
                    f"📁 Đã nhận file video cục bộ: {os.path.basename(current_video_path)} ({os.path.getsize(current_video_path):,} bytes)",
                    level="INFO",
                    stage="DOWNLOADING",
                    progress=0.15
                )
            elif source_url and os.path.exists(source_url):
                current_video_path = source_url
                self.append_job_log(
                    job_id,
                    f"📁 Đã nhận video từ đường dẫn nguồn: {os.path.basename(current_video_path)}",
                    level="INFO",
                    stage="DOWNLOADING",
                    progress=0.15
                )
            elif source_url and (source_url.startswith("http://") or source_url.startswith("https://")):
                self.append_job_log(
                    job_id,
                    f"🌐 Đang bóc tách và tải video từ URL: {source_url}...",
                    level="INFO",
                    stage="DOWNLOADING",
                    progress=0.10
                )
                try:
                    from app.scraper.downloader import VideoDownloader
                    from app.scraper.manager import ScraperManager
                    dl_dir = settings.RAW_INPUT_DIR if hasattr(settings, "RAW_INPUT_DIR") else "data/input/raw"
                    downloader = VideoDownloader(output_dir=dl_dir)
                    manager = ScraperManager(output_dir=dl_dir)
                    try:
                        meta = _run_coro_sync(manager.extract(source_url))
                        dl_res = _run_coro_sync(downloader.download(meta))
                    except Exception as meta_err:
                        logger.warning(f"Platform extraction warning for {source_url}: {meta_err}. Falling back to direct download.")
                        dl_res = _run_coro_sync(downloader.download(source_url))
                    if isinstance(dl_res, str) and os.path.exists(dl_res):
                        current_video_path = dl_res
                    elif isinstance(dl_res, dict):
                        fp = dl_res.get("file_path")
                        if isinstance(fp, str) and os.path.exists(fp):
                            current_video_path = fp
                    elif hasattr(dl_res, "file_path"):
                        fp = getattr(dl_res, "file_path")
                        if isinstance(fp, str) and os.path.exists(fp):
                            current_video_path = fp
                    else:
                        raise FileNotFoundError(f"Failed to download source video from URL: {source_url}")

                    if not current_video_path or not os.path.exists(current_video_path):
                        raise FileNotFoundError(f"Source video download failed for {source_url}")

                    self.append_job_log(
                        job_id,
                        f"✅ Tải video thành công: {os.path.basename(current_video_path)} ({os.path.getsize(current_video_path):,} bytes)",
                        level="SUCCESS",
                        stage="DOWNLOADING",
                        progress=0.25
                    )

                except Exception as e:
                    logger.error(f"Stage 1 video download error for {job_id}: {e}")
                    raise FileNotFoundError(f"Source video download failed for {source_url}: {e}")
            else:
                raise FileNotFoundError(f"Source video file or URL not found: {input_file or source_url}")

            if not current_video_path or not os.path.exists(current_video_path):
                raise FileNotFoundError(f"Input video file path does not exist: {current_video_path}")

            # Validate input video file integrity — never silently replace a real clip
            if os.path.getsize(current_video_path) < 5000:
                raise ValueError(
                    f"Input video file '{current_video_path}' is corrupted or truncated "
                    f"({os.path.getsize(current_video_path)} bytes)."
                )

            # ------------------------------------------------------------------
            # Stage 2: WATERMARK_REMOVAL
            # ------------------------------------------------------------------
            raw_wm = job.get("watermark_config")
            wm_config: WatermarkConfig
            if isinstance(raw_wm, str):
                try:
                    wm_config = WatermarkConfig.model_validate_json(raw_wm)
                except Exception:
                    wm_config = WatermarkConfig()
            elif isinstance(raw_wm, WatermarkConfig):
                wm_config = raw_wm
            elif isinstance(raw_wm, dict):
                wm_config = WatermarkConfig(**raw_wm)
            else:
                wm_config = WatermarkConfig()

            target_out_path = job.get("output_path") or job.get("output_file_path")
            if not target_out_path:
                out_dir = settings.OUTPUT_DIR if hasattr(settings, "OUTPUT_DIR") else "data/output"
                target_out_path = os.path.join(out_dir, f"{job_id}.mp4")

            stage2_out_path = os.path.join(
                os.path.dirname(os.path.abspath(target_out_path)),
                f"{job_id}_stage2.mp4"
            )

            resume_stage2 = _can_resume_completed_stage2(job, stage2_out_path)
            # Partial files without a completion log are unsafe to reuse.
            if not resume_stage2:
                for stale_path in (stage2_out_path, stage2_out_path + ".complete"):
                    if os.path.exists(stale_path):
                        try:
                            os.remove(stale_path)
                        except Exception:
                            pass

            algo_name = wm_config.algorithm or "auto"
            self.append_job_log(
                job_id,
                f"🧹 [Giai đoạn 2] Bắt đầu khử Logo & Phụ Đề (Thuật toán: {algo_name.upper()}, Bán kính: {wm_config.radius}px)...",
                level="INFO",
                stage="WATERMARK_REMOVAL",
                progress=0.35
            )

            raw_reup = job.get("reup_config")
            reup_config: ReupConfig
            if isinstance(raw_reup, str):
                try:
                    reup_config = ReupConfig.model_validate_json(raw_reup)
                except Exception:
                    reup_config = ReupConfig()
            elif isinstance(raw_reup, ReupConfig):
                reup_config = raw_reup
            elif isinstance(raw_reup, dict):
                reup_config = ReupConfig(**raw_reup)
            else:
                reup_config = ReupConfig()

            params = job.get("params") or {}
            if isinstance(params, dict) and params:
                valid_keys = set(ReupConfig.model_fields.keys())
                for k, v in params.items():
                    if k in valid_keys or k == "speed_ratio":
                        target_field = "speed_factor" if k == "speed_ratio" else k
                        if hasattr(reup_config, target_field):
                            setattr(reup_config, target_field, v)

            original_base = os.path.splitext(
                job.get("input_file_path") or job.get("input_path") or job.get("source_url") or ""
            )[0]
            if original_base:
                sidecar_srt = original_base + ".vi.srt"
                sidecar_tts = original_base + ".vi.mp3"
                if not getattr(reup_config, "srt_path", None) and os.path.exists(sidecar_srt):
                    reup_config.srt_path = sidecar_srt
                if not getattr(reup_config, "tts_audio_path", None) and os.path.exists(sidecar_tts):
                    reup_config.tts_audio_path = sidecar_tts
                    reup_config.enable_tts = True

            stage2_res_path = None
            fold_algo = (algo_name or "auto").lower()
            fold_skip = fold_algo in ("crop", "none", "off", "disabled")
            if fold_skip:
                if fold_algo == "crop":
                    reup_config.subtitle_bottom_crop = max(
                        float(getattr(reup_config, "subtitle_bottom_crop", 0.0) or 0.0),
                        0.06,
                    )
                    try:
                        from app.services.subtitle_detector import persistent_text_cover_filters
                        covers = persistent_text_cover_filters(current_video_path)
                        if covers:
                            reup_config.text_cover_vf = ",".join(covers)
                            self.append_job_log(
                                job_id,
                                f"🧽 Che {len(covers)} vùng chữ (delogo)",
                                level="INFO",
                                stage="WATERMARK_REMOVAL",
                            )
                    except Exception as e:
                        logger.warning(f"mid-text cover detect failed: {e}")
                self.append_job_log(
                    job_id,
                    "⚡ Bỏ qua inpaint — chỉ crop/delogo",
                    level="INFO",
                    stage="WATERMARK_REMOVAL",
                    progress=0.60,
                )
            elif fold_algo == "auto":
                if resume_stage2:
                    stage2_res_path = stage2_out_path
                    current_video_path = stage2_out_path
                    self.append_job_log(
                        job_id,
                        "♻️ Dùng lại kết quả khử chữ/logo đã hoàn tất từ lần chạy trước",
                        level="SUCCESS",
                        stage="WATERMARK_REMOVAL",
                        progress=0.65,
                    )
                else:
                    covers = _fast_auto_cover_filters(current_video_path)
                    if covers:
                        reup_config.text_cover_vf = ",".join(covers)
                        self.append_job_log(
                            job_id,
                            f"⚡ Phát hiện {len(covers)} vùng chữ/logo ổn định; xử lý cùng một lần render",
                            level="SUCCESS",
                            stage="WATERMARK_REMOVAL",
                            progress=0.65,
                        )
                    else:
                        self.append_job_log(
                            job_id,
                            "✨ Không thấy vùng chữ/logo ổn định — bỏ qua inpaint từng frame",
                            level="SUCCESS",
                            stage="WATERMARK_REMOVAL",
                            progress=0.65,
                        )
            else:
                from app.services.subtitle_detector import video_has_overlay_text
                dirty = True
                try:
                    dirty = video_has_overlay_text(current_video_path)
                except Exception as e:
                    logger.warning(f"text pre-scan failed: {e}")
                if not dirty:
                    self.append_job_log(
                        job_id,
                        "✨ Clip sạch — bỏ qua inpaint, giữ nguyên hình",
                        level="SUCCESS",
                        stage="WATERMARK_REMOVAL",
                        progress=0.62,
                    )
                elif resume_stage2:
                    stage2_res_path = stage2_out_path
                    current_video_path = stage2_out_path
                    self.append_job_log(
                        job_id,
                        "♻️ Dùng lại kết quả khử chữ/logo đã hoàn tất từ lần chạy trước",
                        level="SUCCESS",
                        stage="WATERMARK_REMOVAL",
                        progress=0.65,
                    )
                else:
                    inpaint_label = (
                        "Telea nhanh + OCR thưa"
                        if fold_algo == "auto"
                        else "hybrid Telea + LaMa"
                    )
                    self.append_job_log(
                        job_id,
                        f"🧹 Đang khử chữ/logo: {inpaint_label} (không cắt đáy hình)…",
                        level="INFO",
                        stage="WATERMARK_REMOVAL",
                        progress=0.50
                    )

                    from app.services.watermark_service import remove_watermark_and_subtitles
                    stage2_res_path = remove_watermark_and_subtitles(
                        video_path=current_video_path,
                        config=wm_config,
                        output_path=stage2_out_path,
                        progress_callback=self._stage_progress_callback(job_id, "WATERMARK_REMOVAL")
                    )

                    if stage2_res_path and os.path.exists(stage2_res_path):
                        current_video_path = stage2_res_path
                        with open(stage2_out_path + ".complete", "w", encoding="ascii") as marker:
                            marker.write("ok\n")
                        self.append_job_log(
                            job_id,
                            f"✨ Đã inpaint chữ/watermark -> {os.path.basename(stage2_res_path)}",
                            level="SUCCESS",
                            stage="WATERMARK_REMOVAL",
                            progress=0.65
                        )
                    if fold_algo in ("all", "all_in_one"):
                        try:
                            from app.services.subtitle_detector import persistent_text_cover_filters
                            covers = persistent_text_cover_filters(current_video_path)
                            if covers:
                                reup_config.text_cover_vf = ",".join(covers)
                                self.append_job_log(
                                    job_id,
                                    f"🧽 Phủ nốt {len(covers)} vệt chữ còn sót (delogo)",
                                    level="INFO",
                                    stage="WATERMARK_REMOVAL",
                                )
                        except Exception as e:
                            logger.warning(f"residual text cover failed: {e}")

            # ------------------------------------------------------------------
            # Stage 3: REUP_TRANSFORM
            # ------------------------------------------------------------------

            hflip_val = getattr(reup_config, "hflip", True)
            speed_val = getattr(reup_config, "speed_factor", 1.0)
            md5_val = getattr(reup_config, "modify_md5", True)
            crop_val = getattr(reup_config, "crop_percent", 0.0)
            grain_val = getattr(reup_config, "film_grain", 0.0)
            pitch_val = getattr(reup_config, "pitch_shift", True)
            mute_val = getattr(reup_config, "enable_vocal_mute", False)
            tts_val = getattr(reup_config, "enable_tts", False)
            burn_val = getattr(reup_config, "burn_subtitles", True)
            subtitle_mode = getattr(reup_config, "subtitle_mode", "soft") if burn_val else "off"

            self.append_job_log(
                job_id,
                (
                    f"🎬 [Giai đoạn 3] Reup FX: hflip={hflip_val}, speed={speed_val}x, "
                    f"pitch={pitch_val}, crop={crop_val:.2%}, grain={grain_val}, "
                    f"mute={mute_val}, tts={tts_val}, subtitles={subtitle_mode}, md5={md5_val}"
                ),
                level="INFO",
                stage="REUP_TRANSFORM",
                progress=0.75
            )

            if getattr(reup_config, "enable_tts", False) or getattr(reup_config, "burn_subtitles", True):
                lipsync_note = " | lip-sync rate+rubberband" if getattr(reup_config, "enable_lipsync", True) else ""
                self.append_job_log(
                    job_id,
                    f"📝 Nhận lời thoại → dịch tiếng Việt → lồng tiếng khớp timeline gốc (giữ BGM){lipsync_note}",
                    level="INFO",
                    stage="REUP_TRANSFORM",
                    progress=0.72
                )

            if getattr(reup_config, "enable_tts", False):
                self.append_job_log(
                    job_id,
                    f"🎙️ Đang tổng hợp thuyết minh Tiếng Việt (Giọng: {getattr(reup_config, 'tts_voice', 'vieneu:Trúc Ly')})...",
                    level="INFO",
                    stage="REUP_TRANSFORM",
                    progress=0.85
                )

            # Defensive purge of any stale output file before final render
            if os.path.exists(target_out_path):
                try:
                    os.remove(target_out_path)
                except Exception:
                    pass

            final_video_path = ReupService.process_reup_pipeline(
                video_path=current_video_path,
                config=reup_config,
                output_path=target_out_path,
                tts_progress_callback=self._tts_progress_callback(job_id),
            )


            # Cleanup intermediate stage 2 file if separate
            if stage2_res_path and stage2_res_path != target_out_path and stage2_res_path != final_video_path and os.path.exists(stage2_res_path):
                try:
                    os.remove(stage2_res_path)
                except OSError:
                    pass
                try:
                    os.remove(stage2_out_path + ".complete")
                except OSError:
                    pass

            self.append_job_log(
                job_id,
                f"🎞️ Render video biến đổi hoàn tất -> {os.path.basename(final_video_path)}",
                level="SUCCESS",
                stage="REUP_TRANSFORM",
                progress=0.95
            )

            # ------------------------------------------------------------------
            # Stage 4: COMPLETED
            # ------------------------------------------------------------------
            now = _utc_now_iso()
            self.append_job_log(
                job_id,
                "🎉 Quy trình xử lý hoàn tất 100%! Video đã sẵn sàng tải xuống hoặc xem trực tiếp.",
                level="SUCCESS",
                stage="COMPLETED",
                progress=1.0
            )

            with self._get_conn() as conn:
                conn.execute(
                    "UPDATE jobs SET output_file_path = ?, status = 'COMPLETED', progress_percent = 100.0, updated_at = ? WHERE job_id = ?",
                    (final_video_path, now, job_id)
                )
                conn.commit()

            # Multi-platform export (TikTok / Shorts / Reels / YouTube)
            variants = []
            try:
                from app.services.platform_export import export_for_platforms, normalize_platforms
                wanted = normalize_platforms(
                    getattr(reup_config, "target_platforms", None)
                    or params.get("target_platforms")
                    or []
                )
                if wanted:
                    variants = export_for_platforms(final_video_path, job_id, wanted)
                    if variants:
                        labels = ", ".join(f"{v['label']} {v['ratio']}" for v in variants)
                        self.append_job_log(
                            job_id,
                            f"🌍 Xuất đa nền tảng: {labels}",
                            level="SUCCESS",
                            stage="COMPLETED",
                        )
            except Exception as e:
                logger.warning(f"Platform export skipped for {job_id}: {e}")

            # Auto-assign to distribution channel if configured
            chan_id = getattr(reup_config, "channel_id", None) or params.get("channel_id")
            assign_paths = [final_video_path] + [v["path"] for v in variants if v.get("path")]
            unique_paths = []
            seen_p = set()
            for pth in assign_paths:
                if pth and pth not in seen_p:
                    seen_p.add(pth)
                    unique_paths.append(pth)

            def _assign(channel_id: str, video_path: str, suffix: str = "") -> str:
                cv_id = f"cvid_{uuid.uuid4().hex[:8]}"
                raw_tags = getattr(reup_config, "post_tags", None) or params.get("post_tags") or []
                tags_json = json.dumps(raw_tags if isinstance(raw_tags, list) else [], ensure_ascii=False)
                p_title = getattr(reup_config, "post_title", None) or params.get("post_title") or f"Video Reup #{job_id[-6:]}"
                if suffix:
                    p_title = f"{p_title} · {suffix}"
                p_caption = getattr(reup_config, "post_caption", None) or params.get("post_caption") or ""
                p_status = (getattr(reup_config, "publish_status", None) or params.get("publish_status") or "READY").upper()
                with self._get_conn() as conn:
                    conn.execute("""
                        INSERT INTO channel_videos (
                            id, channel_id, job_id, title, caption, tags,
                            publish_status, video_path, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        cv_id, channel_id, job_id, p_title, p_caption,
                        tags_json, p_status, video_path, now, now
                    ))
                    conn.commit()
                try:
                    from app.services.facebook_distribution import enqueue_channel_video

                    enqueue_channel_video(
                        self.db_path,
                        cv_id,
                        require_auto_publish=True,
                    )
                except Exception as e:
                    logger.warning(f"Facebook auto-publish enqueue skipped for {cv_id}: {e}")
                return cv_id

            if chan_id:
                try:
                    selected_path = unique_paths[0] if unique_paths else final_video_path
                    with self._get_conn() as conn:
                        selected_channel = conn.execute(
                            "SELECT platform FROM channels WHERE channel_id = ?",
                            (chan_id,),
                        ).fetchone()
                    selected_platform = (
                        str(selected_channel["platform"] or "").lower()
                        if selected_channel
                        else ""
                    )
                    platform_alias = {
                        "youtube": "youtube_shorts",
                        "fb": "facebook",
                    }
                    selected_platform = platform_alias.get(selected_platform, selected_platform)
                    selected_variant = next(
                        (v for v in variants if v.get("platform") == selected_platform and v.get("path")),
                        None,
                    )
                    if selected_variant:
                        selected_path = selected_variant["path"]
                    _assign(chan_id, selected_path)
                    self.append_job_log(
                        job_id,
                        "📢 Đã tự động phân bổ video vào kênh thành công!",
                        level="SUCCESS",
                        stage="COMPLETED"
                    )
                except Exception as e:
                    logger.warning(f"Failed to auto-assign video {job_id} to channel {chan_id}: {e}")

            # Also drop variants onto channels whose platform matches
            if variants:
                try:
                    with self._get_conn() as conn:
                        rows = conn.execute(
                            """
                            SELECT c.channel_id, c.platform
                            FROM channels c
                            LEFT JOIN channel_destinations cd
                              ON cd.channel_id = c.channel_id AND cd.provider = 'facebook'
                            WHERE UPPER(c.status) = 'ACTIVE'
                              AND (LOWER(c.platform) != 'facebook' OR COALESCE(cd.auto_publish, 0) = 1)
                            """
                        ).fetchall()
                    plat_to_chans = {}
                    for row in rows:
                        plat = (row["platform"] or "").lower()
                        plat_to_chans.setdefault(plat, []).append(row["channel_id"])
                    if not plat_to_chans.get("youtube_shorts"):
                        plat_to_chans["youtube_shorts"] = list(plat_to_chans.get("youtube") or [])
                    assigned_extra = 0
                    for v in variants:
                        for cid in plat_to_chans.get(v["platform"], []):
                            if cid == chan_id:
                                continue
                            _assign(cid, v["path"], v.get("label") or v["platform"])
                            assigned_extra += 1
                    if assigned_extra:
                        self.append_job_log(
                            job_id,
                            f"📢 Đã gán {assigned_extra} bản xuất vào kênh cùng nền tảng.",
                            level="SUCCESS",
                            stage="COMPLETED",
                        )
                except Exception as e:
                    logger.warning(f"Variant channel assign skipped: {e}")

            updated_job = self.get_job(job_id)
            if updated_job:
                self._notify_callbacks(updated_job)
            return updated_job or {}


        except Exception as e:
            logger.error(f"Pipeline failure for job {job_id}: {e}")
            self.append_job_log(job_id, f"❌ Lỗi tiến trình: {e}", level="ERROR", stage="FAILED", progress=0.0)
            self.update_job_status(job_id, "FAILED", progress=0.0, error_message=str(e), message=f"Lỗi: {e}")
            raise

    def process_job(self, job_id: str) -> Dict[str, Any]:
        """Synchronously processes a job; auto-retries once on failure."""
        try:
            return self._run_pipeline_stages(job_id)
        except Exception as e:
            if "CANCEL" in str(e).upper():
                raise
            if isinstance(e, FileNotFoundError):
                raise
            self.append_job_log(
                job_id,
                f"🔁 Lỗi lần 1 ({e}). Tự chạy lại 1 lần…",
                level="WARN",
                stage="DOWNLOADING",
            )
            self.update_job_status(
                job_id,
                "PENDING",
                progress=_retry_progress_for_job(self.get_job(job_id)),
                error_message=None,
            )
            return self._run_pipeline_stages(job_id)

    def process_next_pending(self) -> Optional[Dict[str, Any]]:
        """Picks up and processes the next pending job in queue."""
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT job_id FROM jobs WHERE status = 'PENDING' ORDER BY created_at ASC LIMIT 1")
            row = cursor.fetchone()
            if not row:
                return None
            job_id = row["job_id"]
        return self.process_job(job_id)

    async def _worker_loop(self) -> None:
        """Background worker task loop."""
        while True:
            try:
                self.fail_stuck_jobs(25)
            except Exception:
                pass
            job_id = await self.queue.get()
            if self._shutdown.is_set():
                self.queue.task_done()
                break
            try:
                await self._process_job_pipeline(job_id)
            except asyncio.CancelledError:
                break
            except JobAborted:
                logger.info(f"Job {job_id} aborted (cancel or shutdown)")
                if self._shutdown.is_set():
                    break
            except Exception as e:
                logger.error(f"Worker pipeline error for job {job_id}: {e}")
                self.update_job_status(job_id, "FAILED", error_message=str(e))
            finally:
                self.queue.task_done()

    async def _process_job_pipeline(self, job_id: str) -> None:
        """Executes full 4-stage job pipeline asynchronously in thread executor."""
        if not self._claim_pending_job(job_id):
            logger.info(f"Skipping job {job_id}: it is missing, cancelled, or already claimed")
            return

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._run_pipeline_stages, job_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Worker pipeline async execution error for job {job_id}: {e}")
            if isinstance(e, FileNotFoundError):
                return
            try:
                self.append_job_log(job_id, f"🔁 Tự chạy lại 1 lần sau lỗi: {e}", level="WARN")
                self.update_job_status(
                    job_id,
                    "PENDING",
                    progress=_retry_progress_for_job(self.get_job(job_id)),
                    error_message=None,
                )
                await loop.run_in_executor(None, self._run_pipeline_stages, job_id)
            except Exception as e2:
                logger.error(f"Retry also failed for {job_id}: {e2}")
