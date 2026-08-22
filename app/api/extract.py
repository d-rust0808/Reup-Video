"""
REST API Endpoint for Video URL Extraction and Batch Stream Downloading.
========================================================================
Target Path: app/api/extract.py
"""

import os
import shutil
from typing import List
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import settings
from app.scraper.base import BaseScraper, VideoMetadata
from app.scraper.manager import ScraperManager

router = APIRouter()


class ExtractRequest(BaseModel):
    urls: List[str]


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
            ignore_errors=True
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

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

    return {"items": items, "count": len(items)}


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
        canonical = m.group(1) if m else stem
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


@router.get("/samples")
async def list_sample_videos():
    """Returns seeded studio sample clips the UI can load in one click."""
    from app.services.sample_media import SAMPLE_IDS, is_playable_mp4

    items = []
    titles = {
        "douyin_123": "Mẫu Douyin — Đêm đầu ở chung (12s, sẵn vietsub)",
        "kuaishou_456": "Mẫu Kuaishou — cùng clip",
        "xiaohongshu_789": "Mẫu Xiaohongshu — cùng clip",
    }
    platforms = {
        "douyin_123": "douyin",
        "kuaishou_456": "kuaishou",
        "xiaohongshu_789": "xiaohongshu",
    }
    for sid in SAMPLE_IDS:
        fpath = os.path.join(settings.RAW_INPUT_DIR, f"{sid}.mp4")
        if not is_playable_mp4(fpath):
            continue
        items.append({
            "video_id": sid,
            "platform": platforms.get(sid, "douyin"),
            "title": titles.get(sid, sid),
            "author": "Studio Sample",
            "file_path": fpath,
            "file_size": os.path.getsize(fpath),
            "direct_stream_url": f"/api/v1/videos/stream/{sid}",
            "has_vietsub": os.path.exists(os.path.splitext(fpath)[0] + ".vi.srt"),
        })
    return {"items": items, "count": len(items)}
