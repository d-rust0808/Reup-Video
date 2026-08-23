"""
REST API Router for Output Video Listing, Single Download, Batch ZIP Streaming, and Deletion.
=============================================================================================
Target Path: app/api/outputs.py
"""

import os
import io
import json
import zipfile
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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


@router.get("/outputs")
async def list_outputs(request: Request):
    """
    Lists all completed output video files available for preview or download.
    """
    qm = _get_queue_manager(request)
    completed_jobs = qm.list_jobs(status_filter="COMPLETED")

    outputs = []
    seen = set()
    for j in completed_jobs:
        out_p = j.get("output_file_path") or j.get("output_path")
        if out_p and os.path.exists(out_p):
            seen.add(os.path.abspath(out_p))
            raw_cfg = j.get("reup_config") or {}
            if isinstance(raw_cfg, str):
                try:
                    raw_cfg = json.loads(raw_cfg)
                except Exception:
                    raw_cfg = {}
            outputs.append({
                "job_id": j["job_id"],
                "output_path": out_p,
                "output_file_path": out_p,
                "filename": os.path.basename(out_p),
                "file_size": os.path.getsize(out_p),
                "created_at": j.get("created_at"),
                "title": (raw_cfg or {}).get("post_title"),
                "caption": (raw_cfg or {}).get("post_caption"),
                "platform": j.get("platform"),
            })

    from app.services.platform_export import PRESETS
    out_dir = getattr(settings, "OUTPUT_DIR", "data/outputs")
    if os.path.isdir(out_dir):
        for fname in sorted(os.listdir(out_dir)):
            if not fname.endswith(".mp4"):
                continue
            path = os.path.join(out_dir, fname)
            if not os.path.isfile(path):
                continue
            abs_p = os.path.abspath(path)
            if abs_p in seen:
                continue
            stem = fname[:-4]
            plat = None
            parent = stem
            bits = stem.split(".")
            if len(bits) >= 2 and bits[-1] in PRESETS:
                plat = bits[-1]
                parent = ".".join(bits[:-1])
            outputs.append({
                "job_id": stem,
                "parent_job_id": parent,
                "platform": plat,
                "output_path": path,
                "output_file_path": path,
                "filename": fname,
                "file_size": os.path.getsize(path),
                "created_at": None,
            })
            seen.add(abs_p)

    return {"outputs": outputs}


@router.api_route("/outputs/download/{job_id}", methods=["GET", "HEAD"])
async def download_output(job_id: str, request: Request):
    """
    Downloads a specific processed output video file as an attachment.
    """
    qm = _get_queue_manager(request)
    job = qm.get_job(job_id)
    out_p = None
    if job:
        out_p = job.get("output_file_path") or job.get("output_path")
    if not out_p or not os.path.exists(out_p):
        from app.api.stream import _resolve_media_file_path
        out_p = _resolve_media_file_path(job_id)
    if not out_p or not os.path.exists(out_p):
        raise HTTPException(status_code=404, detail="Output video file missing from disk")

    file_size = os.path.getsize(out_p)
    with open(out_p, "rb") as f:
        data = f.read()

    return Response(
        content=data,
        status_code=200,
        headers={
            "Content-Type": "video/mp4",
            "Content-Disposition": f'attachment; filename="{job_id}.mp4"',
            "Content-Length": str(len(data)),
        },
    )


@router.get("/outputs/download-batch")
async def download_batch_outputs(
    request: Request,
    job_ids: Optional[str] = Query(None, description="Comma-separated list of job IDs")
):
    """
    Streams a ZIP archive containing multiple completed output videos.
    """
    qm = _get_queue_manager(request)

    target_ids = []
    if job_ids:
        target_ids = [jid.strip() for jid in job_ids.split(",") if jid.strip()]
    else:
        completed = qm.list_jobs(status_filter="COMPLETED")
        target_ids = [j["job_id"] for j in completed]

    if not target_ids:
        raise HTTPException(status_code=404, detail="No completed jobs found for batch ZIP creation")

    zip_buffer = io.BytesIO()
    valid_files_count = 0

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for jid in target_ids:
            job = qm.get_job(jid)
            if job:
                out_p = job.get("output_file_path") or job.get("output_path")
                if out_p and os.path.exists(out_p):
                    zf.write(out_p, arcname=f"{jid}.mp4")
                    valid_files_count += 1

    if valid_files_count == 0:
        raise HTTPException(status_code=404, detail="No completed jobs found for batch ZIP creation")

    zip_bytes = zip_buffer.getvalue()

    def iter_bytes(data: bytes, chunk_size: int = 65536):
        buf = io.BytesIO(data)
        while True:
            chunk = buf.read(chunk_size)
            if not chunk:
                break
            yield chunk

    return StreamingResponse(
        iter_bytes(zip_bytes),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="reup_batch_outputs.zip"',
            "Content-Length": str(len(zip_bytes)),
        },
    )


@router.delete("/outputs/{job_id}")
async def delete_output(job_id: str, request: Request):
    """
    Deletes output video file from disk and purges job record from database.
    """
    qm = _get_queue_manager(request)
    job = qm.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job ID not found")

    deleted = qm.delete_job(job_id)
    if not deleted:
        raise HTTPException(status_code=400, detail="Failed to delete output file and job record")

    return {
        "job_id": job_id,
        "deleted": True,
        "message": "Output file and job record deleted successfully"
    }


class BatchDeleteOutputsRequest(BaseModel):
    job_ids: List[str]


@router.delete("/outputs")
async def clear_all_outputs(request: Request):
    """
    Deletes all completed output video files from disk and removes completed job records from DB.
    """
    qm = _get_queue_manager(request)
    completed = qm.list_jobs(status_filter="COMPLETED")
    deleted_count = 0
    for j in completed:
        if qm.delete_job(j["job_id"]):
            deleted_count += 1

    return {
        "deleted_count": deleted_count,
        "message": f"Successfully deleted {deleted_count} completed output files"
    }


@router.post("/outputs/delete-batch")
async def delete_batch_outputs(req: BatchDeleteOutputsRequest, request: Request):
    """
    Deletes a list of output video files from disk and purges their job records from DB.
    """
    qm = _get_queue_manager(request)
    deleted_count = 0
    for jid in req.job_ids:
        if qm.delete_job(jid):
            deleted_count += 1

    return {
        "deleted_count": deleted_count,
        "message": f"Successfully deleted {deleted_count} output files"
    }
