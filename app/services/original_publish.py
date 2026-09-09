"""Ingest a user-made video (no reup) and publish it to Fanpage groups."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.config import settings
from app.core.database import get_db_connection
from app.services.facebook_copyright import find_ffprobe_binary, media_duration_seconds
from app.services.job_publish import parse_hashtags, publish_job_to_groups
from app.services.reup_service import find_ffmpeg_binary

logger = logging.getLogger(__name__)

_VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


class OriginalPublishError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _probe_video(path: str) -> Dict[str, Any]:
    probe = find_ffprobe_binary()
    info = {"width": 0, "height": 0, "codec": "", "duration": media_duration_seconds(path)}
    if not probe:
        return info
    result = subprocess.run(
        [
            probe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height",
            "-of", "json", path,
        ],
        capture_output=True, text=True, check=False,
    )
    try:
        stream = (json.loads(result.stdout or "{}").get("streams") or [{}])[0]
        info["width"] = int(stream.get("width") or 0)
        info["height"] = int(stream.get("height") or 0)
        info["codec"] = str(stream.get("codec_name") or "").lower()
    except Exception:
        pass
    return info


def is_facebook_reel_ready(path: str) -> bool:
    if not path or not os.path.isfile(path) or os.path.getsize(path) < 1000:
        return False
    info = _probe_video(path)
    width, height = info["width"], info["height"]
    if not width or not height or height <= width:
        return False
    ratio = width / height
    if not 0.5 <= ratio <= 0.8:
        return False
    if info["codec"] and info["codec"] != "h264":
        return False
    duration = float(info["duration"] or 0)
    if duration and not (3.0 <= duration <= 91.0):
        return False
    return True


def prepare_facebook_reel(source_path: str, dest_path: str) -> str:
    """Copy or transcode to a Facebook Reel-shaped H.264 9:16 file (max 90s)."""
    if is_facebook_reel_ready(source_path):
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".", exist_ok=True)
        if os.path.abspath(source_path) != os.path.abspath(dest_path):
            shutil.copy2(source_path, dest_path)
        return dest_path

    ffmpeg = find_ffmpeg_binary()
    if not ffmpeg:
        raise OriginalPublishError("Không tìm thấy ffmpeg để chuẩn hoá video đăng Reel")
    duration = media_duration_seconds(source_path)
    if duration and duration < 3:
        raise OriginalPublishError("Video ngắn hơn 3 giây — Facebook Reel không nhận")
    length = min(90.0, duration) if duration else 90.0
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".", exist_ok=True)
    vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30"
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-t", f"{length:.2f}", "-i", source_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "128k",
        "-movflags", "+faststart",
        dest_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not os.path.isfile(dest_path) or os.path.getsize(dest_path) < 1000:
        silent = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-t", f"{length:.2f}", "-i", source_path,
            "-f", "lavfi", "-t", f"{length:.2f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            "-movflags", "+faststart",
            dest_path,
        ]
        result = subprocess.run(silent, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not os.path.isfile(dest_path) or os.path.getsize(dest_path) < 1000:
        detail = (result.stderr or result.stdout or "").strip()[:400]
        raise OriginalPublishError(f"Không chuẩn hoá được video Reel: {detail or 'ffmpeg failed'}")
    return dest_path


def _validate_source(path: str) -> str:
    text = os.path.abspath(os.path.expanduser(str(path or "").strip()))
    if not text or not os.path.isfile(text):
        raise OriginalPublishError(f"Không tìm thấy file video: {path}")
    ext = os.path.splitext(text)[1].lower()
    if ext not in _VIDEO_EXT:
        raise OriginalPublishError(f"Định dạng không hỗ trợ: {ext or 'unknown'}")
    if os.path.getsize(text) < 5000:
        raise OriginalPublishError("File video trống hoặc hỏng")
    return text


def _insert_completed_job(
    db_path: str,
    *,
    job_id: str,
    source_path: str,
    output_path: str,
    title: str,
    caption: str,
    hashtags: List[str],
    intent: str,
    affiliate_link: str,
    affiliate_product: str,
) -> None:
    now = _utc_now()
    cfg = {
        "original_publish": True,
        "post_title": title,
        "post_caption": caption,
        "post_intent": intent,
        "hashtags": hashtags,
        "affiliate_link": affiliate_link,
        "affiliate_product": affiliate_product,
    }
    with get_db_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, source_url, platform, status, progress_percent,
                input_file_path, output_file_path, watermark_config, reup_config,
                created_at, updated_at, message, logs
            ) VALUES (?, ?, 'original', 'COMPLETED', 100, ?, ?, '{}', ?, ?, ?, ?, '[]')
            """,
            (
                job_id,
                source_path,
                source_path,
                output_path,
                json.dumps(cfg, ensure_ascii=False),
                now,
                now,
                "Video tự làm — xếp đăng nhóm, không reup",
            ),
        )
        conn.commit()


def publish_original_videos(
    db_path: str,
    source_paths: Iterable[str],
    *,
    group_ids: Optional[List[str]] = None,
    channel_ids: Optional[List[str]] = None,
    title: str = "",
    caption: str = "",
    hashtags: Optional[Any] = None,
    post_intent: str = "",
    affiliate_link: str = "",
    affiliate_product: str = "",
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    paths = [_validate_source(p) for p in (source_paths or [])]
    if not paths:
        raise OriginalPublishError("Chọn ít nhất một video để đăng")
    groups = [str(g).strip() for g in (group_ids or []) if str(g).strip()]
    extra = [str(c).strip() for c in (channel_ids or []) if str(c).strip()]
    if not groups and not extra:
        raise OriginalPublishError("Chọn ít nhất một nhóm Fanpage để đăng")

    out_dir = output_dir or getattr(settings, "OUTPUT_DIR", "data/outputs")
    os.makedirs(out_dir, exist_ok=True)
    tags = parse_hashtags(hashtags)
    title = str(title or "").strip()
    caption = str(caption or "").strip()
    intent = str(post_intent or "").strip()
    published: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    for source in paths:
        job_id = f"job_orig_{uuid.uuid4().hex[:8]}"
        label = title or os.path.splitext(os.path.basename(source))[0]
        try:
            fb_path = os.path.join(out_dir, f"{job_id}.facebook.mp4")
            master = os.path.join(out_dir, f"{job_id}.mp4")
            prepared = prepare_facebook_reel(source, fb_path)
            if os.path.abspath(prepared) != os.path.abspath(master):
                shutil.copy2(prepared, master)
            _insert_completed_job(
                db_path,
                job_id=job_id,
                source_path=source,
                output_path=master,
                title=label,
                caption=caption,
                hashtags=tags,
                intent=intent,
                affiliate_link=affiliate_link,
                affiliate_product=affiliate_product,
            )
            result = publish_job_to_groups(
                db_path,
                job_id,
                group_ids=groups,
                channel_ids=extra,
                output_dir=out_dir,
                affiliate_link=affiliate_link,
                affiliate_product=affiliate_product,
                title=label,
                caption=caption,
                hashtags=tags,
                intent=intent,
            )
            published.append({
                "job_id": job_id,
                "file": os.path.basename(source),
                "assigned": result.get("assigned") or 0,
                "queued": result.get("queued") or 0,
                "message": result.get("message") or "",
            })
        except Exception as exc:
            logger.warning("Original publish failed for %s: %s", source, exc)
            errors.append({"file": os.path.basename(source), "error": str(exc)[:300]})

    if not published and errors:
        raise OriginalPublishError(errors[0]["error"])

    assigned = sum(int(item.get("assigned") or 0) for item in published)
    queued = sum(int(item.get("queued") or 0) for item in published)
    return {
        "ok": True,
        "videos": len(published),
        "assigned": assigned,
        "queued": queued,
        "jobs": published,
        "errors": errors,
        "message": (
            f"Đã xếp đăng {len(published)} video lên {assigned} Fanpage"
            + (f" ({len(errors)} file lỗi)" if errors else "")
            + ". Không reup — check bản quyền Facebook trên nháp trước khi lên page."
        ),
    }
