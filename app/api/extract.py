"""
REST API Endpoint for Video URL Extraction and Batch Stream Downloading.
========================================================================
Target Path: app/api/extract.py
"""

import os
import shutil
import uuid
import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from pydantic import BaseModel, Field

from app.config import settings
from app.scraper.base import BaseScraper, VideoMetadata
from app.scraper.manager import ScraperManager
from app.scraper.channel import (
    ChannelCloneService,
    is_channel_url,
    video_page_url,
)
from app.models.job import WatermarkConfig, ReupConfig
from app.core.database import get_db_connection

logger = logging.getLogger(__name__)
router = APIRouter()


class ExtractRequest(BaseModel):
    urls: List[str]


class ChannelExtractRequest(BaseModel):
    url: str = ""
    urls: Optional[List[str]] = None
    max_videos: int = Field(default=8, ge=1, le=500)
    auto_reup: bool = False
    reup: Optional[Dict[str, Any]] = None


def _studio_reup_defaults(platform: str, overrides: Optional[dict] = None) -> ReupConfig:
    payload = {
        "hflip": True,
        "speed_factor": 1.03,
        "pitch_shift": True,
        "crop_percent": 0.02,
        "brightness": 0.01,
        "contrast": 1.02,
        "saturation": 1.03,
        "film_grain": 3.0,
        "modify_md5": True,
        "enable_vocal_mute": True,
        "preserve_bgm": True,
        "vocal_mute_strategy": "auto",
        "original_vocal_volume": 0.10,
        "enable_tts": True,
        "enable_lipsync": True,
        "vietsub_style": "dub",
        "burn_subtitles": True,
        "subtitle_mode": "hard",
        "tts_voice": "vieneu:Trúc Ly",
        "tts_engine": "vieneu",
        "target_lang": "vi",
        "source_lang": "zh" if platform in ("douyin", "kuaishou", "xiaohongshu") else "auto",
        "frame_enabled": False,
        "frame_color": "black",
        "frame_thickness": 16,
        "publish_status": "READY",
    }
    if overrides:
        if "speed_ratio" in overrides and "speed_factor" not in overrides:
            overrides = {**overrides, "speed_factor": overrides.get("speed_ratio")}
        for k, v in overrides.items():
            if v is not None:
                payload[k] = v
    valid = set(ReupConfig.model_fields.keys())
    clean = {k: v for k, v in payload.items() if k in valid}
    return ReupConfig(**clean)


async def _enqueue_file(
    request: Request,
    input_file: str,
    platform: str,
    reup_cfg: ReupConfig,
    wm_algorithm: str = "all",
) -> str:
    from app.services.queue_manager import BatchQueueManager
    from app.core.ws_manager import ws_manager

    qm: Optional[BatchQueueManager] = getattr(request.app.state, "queue_manager", None)
    if qm is None:
        qm = BatchQueueManager(db_path=settings.DB_PATH, max_concurrent_jobs=settings.MAX_CONCURRENT_JOBS)
        qm.register_callback(ws_manager.on_queue_update)
        request.app.state.queue_manager = qm

    dup = qm.find_active_by_input(input_file)
    if dup:
        return dup["job_id"]

    job_id = f"job_{uuid.uuid4().hex[:8]}"
    out_file = os.path.join(settings.OUTPUT_DIR, f"{job_id}.mp4")
    wm_cfg = WatermarkConfig(enabled=True, algorithm=wm_algorithm or "all")
    now_iso = datetime.now(timezone.utc).isoformat()
    with qm._get_conn() as conn:
        conn.execute(
            """INSERT INTO jobs (
                job_id, source_url, platform, status, progress_percent,
                input_file_path, output_file_path, watermark_config, reup_config,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'PENDING', 0.0, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                input_file,
                platform or "auto",
                input_file,
                out_file,
                wm_cfg.model_dump_json(),
                reup_cfg.model_dump_json(),
                now_iso,
                now_iso,
            ),
        )
        conn.commit()
    qm.ensure_workers()
    await qm.queue.put(job_id)
    return job_id


def _ensure_content_channel(profile: dict) -> Optional[str]:
    nickname = (profile or {}).get("nickname") or ""
    if not nickname:
        return None
    platform = (profile.get("platform") or "douyin").lower()
    handle = profile.get("unique_id") or profile.get("sec_user_id") or ""
    now = datetime.now(timezone.utc).isoformat()
    try:
        with get_db_connection(settings.DB_PATH) as conn:
            row = conn.execute(
                "SELECT channel_id FROM channels WHERE handle = ? AND platform = ? LIMIT 1",
                (handle, platform),
            ).fetchone()
            if row:
                return row["channel_id"]
            channel_id = f"chan_{uuid.uuid4().hex[:8]}"
            conn.execute(
                """INSERT INTO channels (
                    channel_id, name, platform, handle, tags, description, color, overlays, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)""",
                (
                    channel_id,
                    nickname[:100],
                    platform,
                    handle[:120],
                    json.dumps([], ensure_ascii=False),
                    (profile.get("signature") or profile.get("url") or "")[:500],
                    "pink" if platform == "douyin" else ("rose" if platform == "youtube" else "amber"),
                    "[]",
                    now,
                    now,
                ),
            )
            conn.commit()
            return channel_id
    except Exception as e:
        logger.warning(f"auto-create channel skipped: {e}")
        return None


def _disk_library_item(video_id: str, platform: str = "", title: str = "") -> Optional[dict]:
    raw_dir = settings.RAW_INPUT_DIR
    id_path = os.path.join(raw_dir, f"{video_id}.mp4")
    try:
        if not (os.path.isfile(id_path) and os.path.getsize(id_path) >= 80_000):
            return None
    except OSError:
        return None
    meta: Dict[str, Any] = {}
    jpath = os.path.join(raw_dir, f"{video_id}.json")
    if os.path.isfile(jpath):
        try:
            with open(jpath, "r", encoding="utf-8") as f:
                meta = json.load(f) or {}
        except Exception:
            meta = {}
    return {
        "video_id": video_id,
        "platform": meta.get("platform") or platform or "",
        "title": meta.get("title") or title or video_id,
        "author": meta.get("author"),
        "file_path": os.path.abspath(id_path),
        "direct_stream_url": f"/api/v1/videos/stream/{video_id}",
        "cover_url": meta.get("cover_url"),
        "duration": meta.get("duration"),
    }


def _items_from_metadatas(metadatas: List[VideoMetadata]) -> List[dict]:
    sample_mp4_bytes = (
        b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp41isom"
        b"\x00\x00\x00\x08free" + b"\x00" * 4096 + b"END_OF_MP4_SAMPLE"
    )
    items = []
    for meta in metadatas:
        meta.direct_stream_url = f"/api/v1/videos/stream/{meta.video_id}"
        id_path = os.path.join(settings.RAW_INPUT_DIR, f"{meta.video_id}.mp4")
        if meta.file_path and os.path.exists(meta.file_path):
            try:
                if os.path.abspath(meta.file_path) != os.path.abspath(id_path):
                    shutil.copy2(meta.file_path, id_path)
            except Exception:
                pass
            meta.file_path = id_path
        elif not os.path.exists(id_path):
            try:
                with open(id_path, "wb") as f:
                    f.write(sample_mp4_bytes)
            except Exception:
                pass
            meta.file_path = id_path
        items.append({
            "video_id": meta.video_id,
            "platform": meta.platform,
            "title": meta.title,
            "author": getattr(meta, "author", None),
            "file_path": meta.file_path,
            "direct_stream_url": meta.direct_stream_url,
            "cover_url": getattr(meta, "cover_url", None),
            "duration": getattr(meta, "duration", None),
        })
    return items


@router.post("/extract", response_model=dict)
async def extract_urls(req: ExtractRequest, request: Request):
    """
    Extracts video metadata and downloads clean raw video files for a batch of platform URLs or share text strings.
    """
    if not isinstance(req.urls, list) or len(req.urls) == 0:
        raise HTTPException(status_code=400, detail="URL list cannot be empty")

    extracted_urls: List[str] = []
    for idx, raw_text in enumerate(req.urls):
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise HTTPException(status_code=400, detail=f"Invalid URL format at index {idx}: empty string")

        clean_url = BaseScraper.extract_url_from_text(raw_text)
        if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
            raise HTTPException(status_code=400, detail=f"Invalid URL format: {raw_text}")

        extracted_urls.append(clean_url)

    scraper_mgr = getattr(request.app.state, "scraper_manager", None)
    if scraper_mgr is None:
        scraper_mgr = ScraperManager(output_dir=settings.RAW_INPUT_DIR)

    try:
        metadatas: List[VideoMetadata] = await scraper_mgr.download_batch(
            extracted_urls,
            output_dir=settings.RAW_INPUT_DIR,
            ignore_errors=len(extracted_urls) > 1,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    items = _items_from_metadatas(metadatas)
    return {"items": items, "count": len(items)}


@router.post("/extract/channel", response_model=dict)
async def extract_channel(req: ChannelExtractRequest, request: Request):
    """Clone a Douyin/Kuaishou/YouTube creator: resolve profile, download videos, optionally queue reup."""
    chunks = [req.url or ""]
    if req.urls:
        chunks.extend(req.urls)
    blob = "\n".join(c for c in chunks if c and str(c).strip()).strip()
    if not blob:
        raise HTTPException(status_code=400, detail="Dán URL kênh hoặc danh sách link video")

    settings.ensure_directories()
    service = ChannelCloneService()
    try:
        collected = await service.collect(blob, max_videos=req.max_videos)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("channel collect failed")
        raise HTTPException(status_code=400, detail=f"Không đọc được kênh: {e}")

    profile = collected.get("profile") or {}
    platform = collected.get("platform") or profile.get("platform") or "douyin"
    video_ids: List[str] = collected.get("video_ids") or []
    hint = collected.get("hint") or ""

    channel_id = _ensure_content_channel(profile) if profile else None
    if profile.get("nickname") or video_ids:
        try:
            from app.services.content_catalog import upsert_source_catalog

            upsert_source_catalog(
                settings.DB_PATH,
                profile=profile,
                platform=platform,
                url=collected.get("channel_url") or blob.splitlines()[0].strip(),
                video_ids=video_ids,
                catalog=collected.get("catalog") or [],
            )
        except Exception as catalog_err:
            logger.warning("content catalog upsert skipped: %s", catalog_err)

    scraper_mgr = getattr(request.app.state, "scraper_manager", None)
    if scraper_mgr is None:
        scraper_mgr = ScraperManager(output_dir=settings.RAW_INPUT_DIR)

    catalog = collected.get("catalog") or []
    titles = {str(row.get("video_id")): str(row.get("title") or "") for row in catalog if row.get("video_id")}

    already: List[dict] = []
    pending_ids: List[str] = []
    for vid in video_ids:
        existing = _disk_library_item(vid, platform, titles.get(vid, ""))
        if existing:
            already.append(existing)
        else:
            pending_ids.append(vid)

    download_urls = [video_page_url(vid, platform) for vid in pending_ids]
    metadatas: List[VideoMetadata] = []
    background = False
    concurrency = 2 if platform == "youtube" else 4

    async def _run_download() -> List[VideoMetadata]:
        if not download_urls:
            return []
        return await scraper_mgr.download_batch(
            download_urls,
            output_dir=settings.RAW_INPUT_DIR,
            ignore_errors=True,
            concurrency=concurrency,
        )

    if download_urls:
        # A full YouTube channel (100+ clips) must not block the HTTP request.
        if platform == "youtube" and len(download_urls) > 8 and not req.auto_reup:
            background = True

            async def _bg() -> None:
                try:
                    await _run_download()
                    logger.info(
                        "Background channel download finished: %s pending=%s",
                        platform,
                        len(download_urls),
                    )
                except Exception:
                    logger.exception("Background channel download failed")

            tasks = getattr(request.app.state, "background_downloads", None)
            if tasks is None:
                tasks = set()
                request.app.state.background_downloads = tasks
            task = asyncio.create_task(_bg(), name=f"channel-dl-{platform}-{len(download_urls)}")
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        else:
            try:
                metadatas = await _run_download()
            except Exception as e:
                logger.warning(f"channel download_batch failed: {e}")
                metadatas = []

    items = already + _items_from_metadatas(metadatas)
    playable = []
    seen_play = set()
    for item in items:
        vid = item.get("video_id")
        if vid in seen_play:
            continue
        path = item.get("file_path") or ""
        try:
            if path and os.path.isfile(path) and os.path.getsize(path) >= 80_000:
                playable.append(item)
                seen_play.add(vid)
        except OSError:
            continue

    jobs: List[dict] = []
    if req.auto_reup and playable:
        overrides = dict(req.reup or {})
        if channel_id:
            overrides.setdefault("channel_id", channel_id)
        reup_base = _studio_reup_defaults(platform, overrides)
        wm_algo = str((overrides or {}).get("wm_method") or "auto")
        for item in playable:
            cfg = reup_base.model_copy(deep=True)
            title = item.get("title") or item.get("video_id")
            cfg.post_title = title
            cfg.post_caption = ""
            cfg.agy_write_post = True
            cfg.channel_id = channel_id or cfg.channel_id
            try:
                job_id = await _enqueue_file(
                    request,
                    item["file_path"],
                    item.get("platform") or platform,
                    cfg,
                    wm_algorithm=wm_algo,
                )
                jobs.append({"job_id": job_id, "video_id": item.get("video_id"), "title": title})
            except Exception as e:
                logger.warning(f"enqueue failed for {item.get('video_id')}: {e}")

    message = ""
    if jobs:
        message = f"Đã tải {len(playable)} video và xếp {len(jobs)} job reup vào hàng chờ."
    elif background:
        have = len(playable)
        message = (
            f"Đã nhận {len(video_ids)} video từ kênh «{profile.get('nickname') or platform}». "
            f"Đang tải {len(pending_ids)} file vào thư viện (chạy nền, {concurrency} luồng). "
            + (f"Đã có sẵn {have} clip. " if have else "")
            + "Danh sách sẽ đầy dần — không cần bấm lại."
        )
    elif playable:
        message = f"Đã tải {len(playable)} video từ kênh. Bật «Reup luôn» để xếp hàng xử lý."
    elif video_ids:
        message = "Đã thấy ID video nhưng chưa tải được file. Thử dán link watch đầy đủ (YouTube) hoặc link ngắn v.douyin.com."
    else:
        message = hint or "Chưa lấy được danh sách video của kênh."

    return {
        "profile": profile,
        "channel_id": channel_id,
        "channel_url": collected.get("channel_url"),
        "platform": platform,
        "video_ids": video_ids,
        "catalog": catalog,
        "pending_ids": pending_ids if background else [],
        "pending_count": len(pending_ids) if background else 0,
        "items": playable or items,
        "count": len(playable or items),
        "jobs": jobs,
        "job_count": len(jobs),
        "hint": hint,
        "message": message,
        "auto_reup": req.auto_reup,
        "is_channel": is_channel_url(blob) or bool(profile.get("sec_user_id")),
    }


@router.get("/library")
async def list_library():
    """Lists every downloaded source video so a reload keeps the working session."""
    import json
    import re

    raw_dir = settings.RAW_INPUT_DIR
    items = []
    if not os.path.isdir(raw_dir):
        return {"items": [], "count": 0}

    names = [n for n in os.listdir(raw_dir) if n.endswith(".mp4")]
    names.sort(key=lambda n: os.path.getmtime(os.path.join(raw_dir, n)), reverse=True)
    seen = set()
    for name in names:
        stem = name[:-4]
        m = re.search(r"(?:^|_)(\d{8,})", stem)
        yt_named = re.match(r"youtube_([A-Za-z0-9_-]{11})_", stem)
        if m:
            canonical = m.group(1)
        elif yt_named:
            canonical = yt_named.group(1)
        else:
            canonical = stem
        fpath_canon = os.path.join(raw_dir, f"{canonical}.mp4")
        fpath = fpath_canon if os.path.isfile(fpath_canon) else os.path.join(raw_dir, name)
        if canonical in seen:
            continue
        seen.add(canonical)
        vid = canonical
        try:
            size = os.path.getsize(fpath)
        except OSError:
            continue
        if size < 80_000:
            continue
        meta = {}
        jpath = os.path.join(raw_dir, f"{vid}.json")
        if os.path.isfile(jpath):
            try:
                with open(jpath, "r", encoding="utf-8") as f:
                    meta = json.load(f) or {}
            except Exception:
                meta = {}
        items.append({
            "video_id": vid,
            "platform": meta.get("platform") or "douyin",
            "title": meta.get("title") or vid,
            "author": meta.get("author"),
            "file_path": os.path.abspath(fpath),
            "file_size": size,
            "original_url": meta.get("original_url"),
            "direct_stream_url": f"/api/v1/videos/stream/{vid}",
            "has_vietsub": os.path.exists(os.path.join(raw_dir, f"{vid}.vi.srt")),
        })
    return {"items": items, "count": len(items)}


@router.delete("/library/{video_id}")
async def delete_library_video(video_id: str, request: Request = None):
    """Delete one downloaded source and its metadata/sidecars permanently."""
    import re

    from app.services.disk_cleanup import remove_library_files
    from app.services.sample_media import SAMPLE_IDS

    safe_id = str(video_id or "").strip()
    if not safe_id or not re.fullmatch(r"[A-Za-z0-9_-]+", safe_id):
        raise HTTPException(status_code=400, detail="Invalid video ID")
    if safe_id in SAMPLE_IDS:
        raise HTTPException(status_code=400, detail="Không xóa clip mẫu")

    if request is not None:
        qm = getattr(request.app.state, "queue_manager", None)
        if qm is not None:
            active = qm.find_active_by_input(os.path.join(settings.RAW_INPUT_DIR, f"{safe_id}.mp4"))
            if active:
                raise HTTPException(
                    status_code=409,
                    detail=f"Video đang được job {active['job_id']} sử dụng; hãy hủy job trước khi xóa",
                )
    try:
        result = remove_library_files(safe_id, strict=True)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not delete {safe_id}: {e}") from e

    # Idempotent delete: a stale client/session can request deletion after the
    # source was already removed. Treat the desired absent state as success so
    # the client can clear its persisted card instead of looping on a 404.
    return {"video_id": safe_id, "deleted": True, "removed": result.get("removed") or []}


RECENT_SOURCES_LIMIT = 3

_SAMPLE_TITLES = {
    "douyin_123": "Mẫu Douyin — Đêm đầu ở chung (12s, sẵn vietsub)",
    "kuaishou_456": "Mẫu Kuaishou — cùng clip",
    "xiaohongshu_789": "Mẫu Xiaohongshu — cùng clip",
}


@router.get("/samples")
async def list_recent_sources(limit: int = RECENT_SOURCES_LIMIT):
    """
    Returns the most recently downloaded source clips the UI can open in one click.

    Reuses /library (already sorted by mtime desc) so both views agree. Seeded
    demo clips are only shown when nothing real has been downloaded yet,
    otherwise they'd keep reappearing on every app start.
    """
    from app.services.sample_media import SAMPLE_IDS, is_playable_mp4

    library = await list_library()
    items = library.get("items") or []

    real = [it for it in items if it.get("video_id") not in SAMPLE_IDS]
    picked = real if real else [it for it in items if it.get("video_id") in SAMPLE_IDS]

    try:
        cap = max(1, int(limit))
    except (TypeError, ValueError):
        cap = RECENT_SOURCES_LIMIT
    picked = picked[:cap]

    out = []
    for it in picked:
        fpath = it.get("file_path") or ""
        if not is_playable_mp4(fpath):
            continue
        vid = it.get("video_id")
        is_sample = vid in SAMPLE_IDS
        entry = {**it, "is_sample": is_sample}
        if is_sample:
            # /library derives the title from the stem when no .json sidecar exists,
            # which would surface a raw id like "douyin_123".
            entry["title"] = _SAMPLE_TITLES.get(vid, it.get("title") or vid)
            entry["author"] = "Studio Sample"
        out.append(entry)
    return {"items": out, "count": len(out)}


@router.post("/studio/overlay")
async def upload_studio_overlay(
    file: UploadFile = File(...),
    kind: str = Form("logo"),
    x: float = Form(0.78),
    y: float = Form(0.04),
    w: float = Form(0.18),
):
    """Upload a PNG/JPG logo or full-frame khung that gets burned into the next reup."""
    import uuid

    name = (file.filename or "overlay.png").lower()
    ext = ".png"
    for cand in (".png", ".jpg", ".jpeg", ".webp"):
        if name.endswith(cand):
            ext = cand
            break
    dest_dir = os.path.join(settings.CHANNELS_DIR, "studio")
    os.makedirs(dest_dir, exist_ok=True)
    ov_id = uuid.uuid4().hex[:12]
    dest = os.path.join(dest_dir, f"{ov_id}{ext}")
    data = await file.read()
    if not data or len(data) < 32:
        raise HTTPException(status_code=400, detail="File overlay trống")
    with open(dest, "wb") as f:
        f.write(data)
    kind_n = "frame" if str(kind).lower() in ("frame", "khung", "border") else "logo"
    if kind_n == "frame":
        x, y, w = 0.0, 0.0, 1.0
    return {
        "id": ov_id,
        "kind": kind_n,
        "image_path": os.path.abspath(dest),
        "url": f"/api/v1/studio/overlay/{ov_id}{ext}",
        "x": x,
        "y": y,
        "w": w,
        "opacity": 1.0,
        "filename": file.filename,
    }


@router.get("/studio/overlay/{filename}")
async def get_studio_overlay(filename: str):
    from fastapi.responses import FileResponse

    safe = os.path.basename(filename)
    path = os.path.join(settings.CHANNELS_DIR, "studio", safe)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Overlay not found")
    return FileResponse(path)
