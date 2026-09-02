"""Free disk after a reup finishes: drop source clips, TTS scratch, and completed outputs."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Set

from app.config import settings
from app.services.content_catalog import invalidate_download_cache, native_ids_from_path
from app.services.sample_media import SAMPLE_IDS

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {
    "PENDING",
    "QUEUED",
    "DOWNLOAD",
    "DOWNLOADING",
    "WATERMARK_REMOVAL",
    "REUP_TRANSFORM",
    "PROCESSING",
}

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _canonical_stem(stem: str) -> str:
    numeric = re.search(r"(?:^|_)(\d{8,})", stem)
    if numeric:
        return numeric.group(1)
    youtube = re.match(r"youtube_([A-Za-z0-9_-]{11})_", stem)
    if youtube:
        return youtube.group(1)
    return stem.split(".")[0]


def library_name_matches(name: str, video_id: str) -> bool:
    """True when a raw-library filename belongs to this source id (mp4, json, srt, titled copy)."""
    if not name or not video_id:
        return False
    stem, _ext = os.path.splitext(name)
    if stem == video_id:
        return True
    if stem.startswith(f"{video_id}.") or stem.startswith(f"{video_id}_"):
        return True
    return _canonical_stem(stem) == video_id


def _under_root(path: str, root: str) -> bool:
    if not path or not root:
        return False
    abs_path = os.path.abspath(path)
    abs_root = os.path.abspath(root)
    try:
        return os.path.commonpath([abs_root, abs_path]) == abs_root
    except ValueError:
        return False


def _unlink(path: str) -> int:
    try:
        if not os.path.isfile(path):
            return 0
        size = os.path.getsize(path)
        os.remove(path)
        return size
    except OSError as exc:
        logger.warning("Could not delete %s: %s", path, exc)
        return 0


def remove_library_files(
    video_id: str,
    *,
    raw_dir: Optional[str] = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """Delete a source clip plus json/srt/titled duplicates. Sample ids are refused."""
    safe_id = str(video_id or "").strip()
    removed: List[str] = []
    bytes_freed = 0
    if not safe_id or not _VIDEO_ID_RE.fullmatch(safe_id):
        return {"removed": removed, "bytes_freed": 0, "video_id": safe_id}
    if safe_id in SAMPLE_IDS:
        return {"removed": removed, "bytes_freed": 0, "video_id": safe_id, "skipped": "sample"}

    root = os.path.abspath(raw_dir or settings.RAW_INPUT_DIR)
    if not os.path.isdir(root):
        return {"removed": removed, "bytes_freed": 0, "video_id": safe_id}

    for name in os.listdir(root):
        if not library_name_matches(name, safe_id):
            continue
        path = os.path.abspath(os.path.join(root, name))
        if not _under_root(path, root) or not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
            os.remove(path)
            removed.append(name)
            bytes_freed += size
        except OSError as exc:
            if strict:
                raise
            logger.warning("Could not delete library file %s: %s", path, exc)

    if removed:
        invalidate_download_cache()
    return {"removed": removed, "bytes_freed": bytes_freed, "video_id": safe_id}


def source_ids_from_job(job: Optional[Dict[str, Any]]) -> Set[str]:
    """Native source ids for a job, from the clip filename (not random 11-char path chunks)."""
    if not job:
        return set()
    found: Set[str] = set()
    for key in ("input_file_path", "input_path", "source_url"):
        text = str(job.get(key) or "").strip()
        if not text:
            continue
        if "://" in text:
            found |= native_ids_from_path(text)
            continue
        stem = os.path.splitext(os.path.basename(text.split("?")[0]))[0]
        if stem:
            found.add(stem)
            found.add(_canonical_stem(stem))
    return {item for item in found if item and _VIDEO_ID_RE.fullmatch(item)}


def protected_source_ids(queue_manager) -> Set[str]:
    """Source ids still needed by a job that has not finished."""
    protected: Set[str] = set()
    if queue_manager is None:
        return protected
    try:
        jobs = queue_manager.list_jobs()
    except Exception:
        return protected
    for job in jobs or []:
        status = str(job.get("status") or "").upper()
        if status in ACTIVE_STATUSES:
            protected |= source_ids_from_job(job)
    protected |= set(SAMPLE_IDS)
    return protected


def remaining_output_stems(queue_manager, *, skip_statuses: Optional[Iterable[str]] = None) -> Set[str]:
    """Basenames of outputs that still belong to a live job row."""
    stems: Set[str] = set()
    skip = {str(item).upper() for item in (skip_statuses or []) if item}
    if queue_manager is None:
        return stems
    try:
        jobs = queue_manager.list_jobs()
    except Exception:
        return stems
    for job in jobs or []:
        status = str(job.get("status") or "").upper()
        if skip and status in skip:
            continue
        out_p = job.get("output_file_path") or job.get("output_path") or ""
        if out_p:
            stems.add(os.path.splitext(os.path.basename(out_p))[0])
        job_id = str(job.get("job_id") or "").strip()
        if job_id:
            stems.add(job_id)
    return stems


def remove_tts_for_ids(video_ids: Iterable[str], *, tts_dir: Optional[str] = None) -> Dict[str, Any]:
    wanted = {str(item).strip() for item in video_ids if str(item).strip()}
    removed: List[str] = []
    bytes_freed = 0
    root = os.path.abspath(tts_dir or settings.TTS_OUTPUT_DIR)
    if not wanted or not os.path.isdir(root):
        return {"removed": removed, "bytes_freed": bytes_freed}

    for name in os.listdir(root):
        lower = name.lower()
        if not lower.endswith(".wav"):
            continue
        stem = name[:-4]
        matched = False
        for vid in wanted:
            if stem == vid or stem.startswith(f"{vid}_") or _canonical_stem(stem) == vid:
                matched = True
                break
        if not matched:
            continue
        path = os.path.abspath(os.path.join(root, name))
        if not _under_root(path, root):
            continue
        size = _unlink(path)
        if size or not os.path.exists(path):
            removed.append(name)
            bytes_freed += size
    return {"removed": removed, "bytes_freed": bytes_freed}


def purge_orphan_tts(*, keep_ids: Optional[Set[str]] = None, tts_dir: Optional[str] = None) -> Dict[str, Any]:
    """Delete TTS wavs that are not tied to an in-flight source."""
    keep = {str(item).strip() for item in (keep_ids or set()) if str(item).strip()}
    keep |= set(SAMPLE_IDS)
    removed: List[str] = []
    bytes_freed = 0
    root = os.path.abspath(tts_dir or settings.TTS_OUTPUT_DIR)
    if not os.path.isdir(root):
        return {"removed": removed, "bytes_freed": bytes_freed}

    for name in os.listdir(root):
        lower = name.lower()
        if not lower.endswith(".wav"):
            continue
        stem = name[:-4]
        if any(stem == vid or stem.startswith(f"{vid}_") or _canonical_stem(stem) == vid for vid in keep):
            continue
        path = os.path.abspath(os.path.join(root, name))
        if not _under_root(path, root):
            continue
        size = _unlink(path)
        if size or not os.path.exists(path):
            removed.append(name)
            bytes_freed += size
    return {"removed": removed, "bytes_freed": bytes_freed}


def purge_output_dir(*, keep_stems: Optional[Set[str]] = None, out_dir: Optional[str] = None) -> Dict[str, Any]:
    """Remove completed/orphan renders in OUTPUT_DIR, keeping files for remaining jobs."""
    keep = {str(item).strip() for item in (keep_stems or set()) if str(item).strip()}
    removed: List[str] = []
    bytes_freed = 0
    root = os.path.abspath(out_dir or settings.OUTPUT_DIR)
    if not os.path.isdir(root):
        return {"removed": removed, "bytes_freed": bytes_freed}

    for name in os.listdir(root):
        path = os.path.abspath(os.path.join(root, name))
        if not _under_root(path, root) or not os.path.isfile(path):
            continue
        lower = name.lower()
        if not lower.endswith((".mp4", ".srt", ".wav", ".complete")):
            continue
        stem = os.path.splitext(name)[0]
        parent = stem.split(".")[0]
        if stem in keep or parent in keep:
            continue
        size = _unlink(path)
        if size or not os.path.exists(path):
            removed.append(name)
            bytes_freed += size
    return {"removed": removed, "bytes_freed": bytes_freed}


def release_scratch_after_complete(job: Optional[Dict[str, Any]], queue_manager=None) -> Dict[str, Any]:
    """After a successful reup, drop the source clip and TTS wav. Keep the finished output."""
    ids = source_ids_from_job(job)
    protected = protected_source_ids(queue_manager)
    dropped = ids - protected
    source_res = {"removed": [], "bytes_freed": 0}
    tts_res = {"removed": [], "bytes_freed": 0}
    for vid in sorted(dropped):
        part = remove_library_files(vid)
        source_res["removed"].extend(part.get("removed") or [])
        source_res["bytes_freed"] += int(part.get("bytes_freed") or 0)
    if dropped:
        tts_res = remove_tts_for_ids(dropped)
    bytes_freed = int(source_res["bytes_freed"]) + int(tts_res.get("bytes_freed") or 0)
    if bytes_freed:
        logger.info(
            "Released %.2f MB of source/TTS after job %s",
            bytes_freed / (1024 * 1024),
            (job or {}).get("job_id"),
        )
    return {
        "source_ids": sorted(dropped),
        "sources_removed": source_res["removed"],
        "tts_removed": tts_res.get("removed") or [],
        "bytes_freed": bytes_freed,
    }


def cleanup_completed_reup(queue_manager) -> Dict[str, Any]:
    """
    Delete finished reup outputs, their source clips, and leftover TTS.

    Keeps in-flight jobs (and their sources), cancelled/failed sources (so they
    can be retried), and seeded sample clips.
    """
    completed = queue_manager.list_jobs(status_filter="COMPLETED") if queue_manager else []
    source_ids: Set[str] = set()
    job_ids: List[str] = []
    for job in completed:
        job_ids.append(job.get("job_id"))
        source_ids |= source_ids_from_job(job)

    protected = protected_source_ids(queue_manager)
    keep_stems = remaining_output_stems(queue_manager, skip_statuses=("COMPLETED",))
    outputs = purge_output_dir(keep_stems=keep_stems)

    jobs_deleted = 0
    for job_id in job_ids:
        if not job_id:
            continue
        try:
            if queue_manager.delete_job(job_id):
                jobs_deleted += 1
        except Exception as exc:
            logger.warning("Could not delete completed job %s: %s", job_id, exc)

    sources_removed: List[str] = []
    source_bytes = 0
    for vid in sorted(source_ids - protected):
        part = remove_library_files(vid)
        sources_removed.extend(part.get("removed") or [])
        source_bytes += int(part.get("bytes_freed") or 0)

    tts = purge_orphan_tts(keep_ids=protected)

    bytes_freed = int(outputs.get("bytes_freed") or 0) + source_bytes + int(tts.get("bytes_freed") or 0)
    logger.info(
        "Cleanup completed reup: %s jobs, %s output files, %s source files, %s TTS, %.2f GB",
        jobs_deleted,
        len(outputs.get("removed") or []),
        len(sources_removed),
        len(tts.get("removed") or []),
        bytes_freed / (1024 ** 3),
    )
    return {
        "jobs_deleted": jobs_deleted,
        "outputs_removed": len(outputs.get("removed") or []),
        "sources_removed": len(sources_removed),
        "tts_removed": len(tts.get("removed") or []),
        "bytes_freed": bytes_freed,
        "output_files": outputs.get("removed") or [],
        "source_files": sources_removed,
        "tts_files": tts.get("removed") or [],
        "kept_active_sources": sorted(protected - set(SAMPLE_IDS)),
    }
