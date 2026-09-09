"""
REST API Router for Job Tracking, Status Querying, Cancellation, and Retry.
=============================================================================
Target Path: app/api/jobs.py
"""

import asyncio
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.services.queue_manager import BatchQueueManager

router = APIRouter()


def _get_queue_manager(request: Request) -> BatchQueueManager:
    """Helper retrieving BatchQueueManager instance from app state."""
    qm = getattr(request.app.state, "queue_manager", None)
    if qm is None:
        from app.core.ws_manager import ws_manager
        qm = BatchQueueManager(db_path=settings.DB_PATH, max_concurrent_jobs=settings.MAX_CONCURRENT_JOBS)
        qm.register_callback(ws_manager.on_queue_update)
        request.app.state.queue_manager = qm
    return qm


@router.get("/jobs")
async def list_jobs(
    request: Request,
    status: Optional[str] = Query(None, description="Optional status filter"),
    limit: int = Query(50, ge=1, le=100, description="Page size limit"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """
    Lists processing jobs with optional status filter and offset/limit pagination.
    """
    qm = _get_queue_manager(request)
    jobs, total = await asyncio.to_thread(
        qm.list_jobs_paginated,
        status,
        limit,
        offset,
        False,
    )
    return {
        "jobs": jobs,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/jobs/{job_id}")
async def get_job_details(job_id: str, request: Request):
    """
    Fetches status details and progress metrics for a specific job.
    """
    qm = _get_queue_manager(request)
    job = qm.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job ID {job_id} not found")
    return job


@router.get("/jobs/{job_id}/logs")
async def get_job_logs(job_id: str, request: Request):
    """
    Fetches real-time terminal logs and stage history for a specific job.
    """
    qm = _get_queue_manager(request)
    job = await asyncio.to_thread(qm.get_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job ID {job_id} not found")
    return {
        "job_id": job_id,
        "status": job.get("status", "PENDING"),
        "stage": job.get("stage", "PENDING"),
        "progress": job.get("progress", 0.0),
        "progress_percent": job.get("progress_percent", 0.0),
        "message": job.get("message", ""),
        "logs": job.get("logs", []),
    }


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    """
    Cancels an active or pending job and removes partial rendering artifacts.
    """
    qm = _get_queue_manager(request)
    job = qm.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job ID {job_id} not found")

    cur_status = (job.get("status") or "").upper()
    terminal_messages = {
        "COMPLETED": "Job đã chạy xong, không cần hủy.",
        "FAILED": "Job đã thất bại, không cần hủy.",
        "CANCELLED": "Job đã được hủy trước đó.",
    }
    if cur_status in terminal_messages:
        return {
            "job_id": job_id,
            "status": cur_status,
            "already_finished": True,
            "message": terminal_messages[cur_status],
        }

    cancelled = qm.cancel_job(job_id)
    if not cancelled:
        latest = qm.get_job(job_id) or job
        latest_status = (latest.get("status") or "").upper()
        if latest_status in terminal_messages:
            return {
                "job_id": job_id,
                "status": latest_status,
                "already_finished": True,
                "message": terminal_messages[latest_status],
            }
        raise HTTPException(status_code=400, detail="Không hủy được job. Thử lại sau vài giây.")

    # Broadcast WebSocket notification
    ws_mgr = getattr(request.app.state, "ws_manager", None)
    if ws_mgr:
        await ws_mgr.broadcast({
            "event": "job_progress",
            "job_id": job_id,
            "status": "CANCELLED",
            "stage": "CANCELLED",
            "progress": job.get("progress", 0.0),
            "message": "Job cancelled by user request"
        })

    return {
        "job_id": job_id,
        "status": "CANCELLED",
        "message": "Job cancelled successfully"
    }


class PublishJobRequest(BaseModel):
    group_ids: List[str] = Field(default_factory=list)
    channel_ids: List[str] = Field(default_factory=list)
    affiliate_link: str = ""
    affiliate_product: str = ""
    title: str = ""
    caption: str = ""
    hashtags: List[str] = Field(default_factory=list)
    post_intent: str = ""


class PublishOriginalRequest(BaseModel):
    paths: List[str] = Field(default_factory=list)
    group_ids: List[str] = Field(default_factory=list)
    channel_ids: List[str] = Field(default_factory=list)
    title: str = ""
    caption: str = ""
    hashtags: List[str] = Field(default_factory=list)
    post_intent: str = ""
    affiliate_link: str = ""
    affiliate_product: str = ""


@router.post("/jobs/{job_id}/publish")
async def publish_completed_job(job_id: str, req: PublishJobRequest, request: Request):
    """Assign a finished output to Fanpage groups and enqueue Facebook/TikTok posts."""
    from app.services.job_publish import publish_job_to_groups

    try:
        result = await asyncio.to_thread(
            publish_job_to_groups,
            settings.DB_PATH,
            job_id,
            group_ids=req.group_ids,
            channel_ids=req.channel_ids,
            affiliate_link=req.affiliate_link,
            affiliate_product=req.affiliate_product,
            title=req.title,
            caption=req.caption,
            hashtags=req.hashtags,
            intent=req.post_intent,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    worker = getattr(request.app.state, "facebook_distribution_worker", None)
    if worker:
        worker.wake()
    tiktok_worker = getattr(request.app.state, "tiktok_distribution_worker", None)
    if tiktok_worker:
        tiktok_worker.wake()
    return result


@router.post("/jobs/publish-original")
async def publish_original_job(req: PublishOriginalRequest, request: Request):
    """Publish user-made local videos to Fanpage groups without running reup."""
    from app.services.original_publish import OriginalPublishError, publish_original_videos

    try:
        result = await asyncio.to_thread(
            publish_original_videos,
            settings.DB_PATH,
            req.paths,
            group_ids=req.group_ids,
            channel_ids=req.channel_ids,
            title=req.title,
            caption=req.caption,
            hashtags=req.hashtags,
            post_intent=req.post_intent,
            affiliate_link=req.affiliate_link,
            affiliate_product=req.affiliate_product,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OriginalPublishError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    worker = getattr(request.app.state, "facebook_distribution_worker", None)
    if worker:
        worker.wake()
    tiktok_worker = getattr(request.app.state, "tiktok_distribution_worker", None)
    if tiktok_worker:
        tiktok_worker.wake()
    return result


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str, request: Request):
    """
    Retries a failed or cancelled processing task.
    """
    qm = _get_queue_manager(request)
    job = qm.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job ID {job_id} not found")

    cur_status = job.get("status", "").upper()
    if cur_status not in ("FAILED", "CANCELLED"):
        raise HTTPException(status_code=400, detail="Only FAILED or CANCELLED jobs can be retried")

    success = await qm.retry_job(job_id)
    if not success:
        refreshed = qm.get_job(job_id) or {}
        raise HTTPException(
            status_code=409,
            detail=refreshed.get("error_message") or "Failed to retry job",
        )

    # Broadcast WebSocket notification
    ws_mgr = getattr(request.app.state, "ws_manager", None)
    if ws_mgr:
        await ws_mgr.broadcast({
            "event": "job_progress",
            "job_id": job_id,
            "status": "PENDING",
            "stage": "QUEUED",
            "progress": 0.0,
            "message": "Job re-enqueued for processing"
        })

    return {
        "job_id": job_id,
        "status": "PENDING",
        "message": "Job re-enqueued for processing"
    }


@router.post("/jobs/retry-failed")
async def retry_failed_jobs(request: Request):
    qm = _get_queue_manager(request)
    n = await qm.retry_failed_jobs()
    return {"retried": n, "message": f"Đã xếp lại {n} job lỗi"}


class BatchDeleteJobsRequest(BaseModel):
    job_ids: List[str]


@router.delete("/jobs/{job_id}")
async def delete_single_job(job_id: str, request: Request):
    """
    Deletes a single job record and its output files if present.
    """
    qm = _get_queue_manager(request)
    job = qm.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job ID {job_id} not found")

    cur_status = job.get("status", "").upper()
    if cur_status in ("PROCESSING", "DOWNLOADING", "WATERMARK_REMOVAL", "REUP_TRANSFORM"):
        qm.cancel_job(job_id)

    deleted = qm.delete_job(job_id)
    if not deleted:
        raise HTTPException(status_code=400, detail="Failed to delete job")

    return {
        "job_id": job_id,
        "deleted": True,
        "message": f"Job {job_id} deleted successfully"
    }


@router.delete("/jobs")
async def clear_all_jobs(
    request: Request,
    status: Optional[str] = Query(None, description="Optional status filter: COMPLETED, FAILED, CANCELLED"),
    all: bool = Query(False, description="Clear all terminal state jobs")
):
    """
    Clears jobs by status (e.g. COMPLETED, FAILED, CANCELLED) or clears all terminal state jobs.
    """
    qm = _get_queue_manager(request)
    status_filter = None if (all or not status or status.upper() == "ALL") else status
    deleted_count = qm.clear_jobs(status_filter=status_filter)
    return {
        "deleted_count": deleted_count,
        "message": f"Successfully cleared {deleted_count} jobs"
    }


@router.post("/jobs/delete-batch")
async def delete_jobs_batch(req: BatchDeleteJobsRequest, request: Request):
    """
    Deletes a list of job IDs and their associated output files.
    """
    qm = _get_queue_manager(request)
    deleted_count = qm.delete_jobs_batch(req.job_ids)
    return {
        "deleted_count": deleted_count,
        "message": f"Successfully deleted {deleted_count} jobs"
    }
