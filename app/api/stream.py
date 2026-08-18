"""
REST API Endpoint for HTTP 206 Partial Content Byte-Range Video Streaming and Frame Extraction.
==============================================================================================
Target Path: app/api/stream.py
"""

import os
import io
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Header, Query, Request, Response, UploadFile, File
import uuid
import aiofiles

from app.config import settings
from app.core.database import get_db_connection

logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve_media_file_path(media_id: str, db_path: str = settings.DB_PATH) -> Optional[str]:
    """
    Resolves target file path on disk for a given media_id.
    Searches RAW_INPUT_DIR, OUTPUT_DIR, jobs database, and workspace data/.
    """
    if not media_id or not isinstance(media_id, str):
        return None

    clean_id = os.path.basename(media_id.strip())
    if not clean_id or ".." in media_id:
        return None

    # Check direct path if absolute or relative within project
    candidates = [
        os.path.join(settings.RAW_INPUT_DIR, f"{clean_id}.mp4"),
        os.path.join(settings.RAW_INPUT_DIR, clean_id),
        os.path.join(settings.OUTPUT_DIR, f"{clean_id}.mp4"),
        os.path.join(settings.OUTPUT_DIR, clean_id),
        os.path.join(settings.PREVIEW_DIR, f"{clean_id}.mp4"),
        os.path.join(settings.PREVIEW_DIR, clean_id),
    ]

    for candidate in candidates:
        if os.path.exists(candidate) and os.path.isfile(candidate):
            return candidate

    # Search in SQLite database jobs table
    try:
        if os.path.exists(db_path):
            conn = get_db_connection(db_path)
            cursor = conn.execute(
                "SELECT input_file_path, output_file_path FROM jobs WHERE job_id = ? OR source_url LIKE ?",
                (media_id, f"%{media_id}%")
            )
            row = cursor.fetchone()
            if row:
                inp_p = row["input_file_path"]
                out_p = row["output_file_path"]
                if out_p and os.path.exists(out_p):
                    return out_p
                if inp_p and os.path.exists(inp_p):
                    return inp_p
    except Exception as e:
        logger.warning(f"Error querying database for media_id {media_id}: {e}")

    # Fallback search by filename in RAW_INPUT_DIR or OUTPUT_DIR
    for d in [settings.RAW_INPUT_DIR, settings.OUTPUT_DIR]:
        if os.path.exists(d):
            for fname in os.listdir(d):
                if media_id in fname and os.path.isfile(os.path.join(d, fname)):
                    return os.path.join(d, fname)

    return None


def _validate_safe_path(target_path: str) -> str:
    """
    Canonical path traversal validation ensuring file resides within authorized media directories.
    Prevents directory traversal attacks (e.g. ../../etc/passwd).
    """
    abs_target = os.path.abspath(target_path)
    allowed_dirs = [
        os.path.abspath(settings.RAW_INPUT_DIR),
        os.path.abspath(settings.OUTPUT_DIR),
        os.path.abspath(settings.PREVIEW_DIR),
        os.path.abspath(settings.TEMP_DIR),
        os.path.abspath("data"),
        os.path.abspath("/tmp"),
        os.path.abspath("/var/folders"),
    ]
    is_safe = False
    for allowed_dir in allowed_dirs:
        abs_allowed = os.path.abspath(allowed_dir)
        try:
            if os.path.commonpath([abs_target, abs_allowed]) == abs_allowed:
                is_safe = True
                break
        except ValueError:
            continue

    if not is_safe:
        raise HTTPException(status_code=403, detail="Access denied: Invalid path traversal attempt")
    
    return abs_target


@router.api_route("/videos/stream/{media_id}", methods=["GET", "HEAD"])
async def stream_video(
    media_id: str,
    request: Request,
    range: Optional[str] = Header(None)
):
    """
    Serves MP4 video streams with full HTTP 206 Partial Content Byte-Range request and HEAD support.
    """
    raw_path = _resolve_media_file_path(media_id)
    if not raw_path:
        raise HTTPException(status_code=404, detail=f"Media file '{media_id}' not found")

    file_path = _validate_safe_path(raw_path)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Media file on disk not found")

    file_size = os.path.getsize(file_path)

    # HEAD request support for browser media players (Safari, Chrome)
    if request.method == "HEAD":
        return Response(
            content=b"",
            status_code=200,
            headers={
                "Content-Type": "video/mp4",
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
        )

    # Full Content Stream (HTTP 200) if no Range header is supplied
    if not range:
        with open(file_path, "rb") as f:
            content = f.read()
        return Response(
            content=content,
            status_code=200,
            headers={
                "Content-Type": "video/mp4",
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
            },
        )

    # Handle Range Header (HTTP 206 Partial Content)
    try:
        unit, range_str = range.strip().split("=")
        if unit.lower() != "bytes":
            raise HTTPException(status_code=416, detail="Invalid range unit")

        parts = range_str.split("-")
        start_str, end_str = parts[0], parts[1]

        if start_str and end_str:
            start = int(start_str)
            end = int(end_str)
        elif start_str:
            start = int(start_str)
            end = file_size - 1
        elif end_str:
            start = file_size - int(end_str)
            end = file_size - 1
        else:
            raise HTTPException(status_code=416, detail="Malformed range header")

        if start < 0 or end >= file_size or start > end:
            raise HTTPException(status_code=416, detail="Requested range not satisfiable")

    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=416, detail="Invalid Range Header")

    chunk_length = end - start + 1
    with open(file_path, "rb") as f:
        f.seek(start)
        data = f.read(chunk_length)

    return Response(
        content=data,
        status_code=206,
        headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(chunk_length),
            "Accept-Ranges": "bytes",
        },
    )


@router.get("/videos/frame/{media_id}")
async def extract_video_frame(
    media_id: str,
    timestamp: float = Query(0.0, ge=0.0, description="Time position in seconds"),
    frame_index: Optional[int] = Query(None, ge=0, description="Optional target frame index"),
    format: str = Query("jpeg", description="Image format: jpeg or png")
):
    """
    Extracts a single video frame image at a specified timestamp or frame index for the ROI canvas tool.
    """
    raw_path = _resolve_media_file_path(media_id)
    if not raw_path:
        raise HTTPException(status_code=404, detail=f"Media file '{media_id}' not found")

    file_path = _validate_safe_path(raw_path)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Media file on disk not found")

    fmt_clean = format.lower().strip()
    if fmt_clean not in ("jpeg", "jpg", "png"):
        fmt_clean = "jpeg"
    media_type = "image/png" if fmt_clean == "png" else "image/jpeg"

    # Try OpenCV frame extraction first
    try:
        import cv2
        cap = cv2.VideoCapture(file_path)
        if cap.isOpened():
            if frame_index is not None:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            else:
                cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)

            ret, frame = cap.read()
            cap.release()

            if ret and frame is not None:
                ext = ".png" if fmt_clean == "png" else ".jpg"
                success, buffer = cv2.imencode(ext, frame)
                if success:
                    return Response(content=buffer.tobytes(), media_type=media_type, status_code=200)
    except Exception as e:
        logger.warning(f"OpenCV frame capture failed for {file_path}: {e}")

    # Fallback FFmpeg sub-process frame extraction
    try:
        import subprocess
        cmd = [
            "ffmpeg", "-y", "-ss", str(timestamp), "-i", file_path,
            "-vframes", "1", "-f", "image2", "-c:v", "mjpeg" if fmt_clean != "png" else "png", "pipe:1"
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        if proc.returncode == 0 and len(proc.stdout) > 0:
            return Response(content=proc.stdout, media_type=media_type, status_code=200)
    except Exception as e:
        logger.warning(f"FFmpeg frame extraction fallback failed: {e}")

    raise HTTPException(status_code=500, detail="Failed to extract frame from video stream")


@router.post("/videos/upload")
async def upload_video(file: UploadFile = File(...)):
    """
    Uploads a local video file directly for reup processing without needing an external URL.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file selected")

    settings.ensure_directories()
    video_id = f"upload_{uuid.uuid4().hex[:8]}"
    ext = os.path.splitext(file.filename)[1] or ".mp4"
    if ext.lower() not in (".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv"):
        ext = ".mp4"

    save_filename = f"{video_id}{ext}"
    target_path = os.path.join(settings.RAW_INPUT_DIR, save_filename)

    try:
        async with aiofiles.open(target_path, "wb") as out_file:
            while chunk := await file.read(65536):
                await out_file.write(chunk)
    except Exception as e:
        logger.error(f"Failed to save uploaded video: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded video: {e}")

    file_size = os.path.getsize(target_path) if os.path.exists(target_path) else 0

    return {
        "video_id": video_id,
        "filename": file.filename,
        "file_path": target_path,
        "file_size": file_size,
        "direct_stream_url": f"/api/v1/videos/stream/{video_id}",
        "platform": "upload",
        "title": file.filename,
    }

