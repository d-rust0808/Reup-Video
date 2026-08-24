"""BGM library API: extract instrumental from a URL/video and reuse on reups."""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import settings
from app.scraper.base import BaseScraper
from app.scraper.manager import ScraperManager
from app.services.bgm_library import (
    delete_bgm,
    harvest_bgm,
    import_audio_file,
    import_remote_track,
    load_catalog,
    resolve_bgm,
)
from app.services.bgm_providers import available_providers, search_tracks

router = APIRouter()


class ExtractBgmRequest(BaseModel):
    url: Optional[str] = None
    video_path: Optional[str] = None
    video_id: Optional[str] = None
    title: Optional[str] = None


class ImportBgmRequest(BaseModel):
    """One normalised row returned by GET /bgm/search."""

    audio_url: str
    provider: Optional[str] = None
    external_id: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    license: Optional[str] = None
    license_url: Optional[str] = None
    attribution: Optional[str] = None
    page_url: Optional[str] = None


@router.get("/bgm")
async def list_bgm():
    items = load_catalog()
    for it in items:
        it["stream_url"] = f"/api/v1/bgm/{it['id']}/audio"
    return {"items": items, "count": len(items)}


@router.get("/bgm/providers")
async def list_bgm_providers():
    """Which online music catalogs are usable right now."""
    return {"providers": available_providers()}


@router.get("/bgm/search")
async def search_online_bgm(
    q: str = Query(..., min_length=1, description="Từ khóa tìm nhạc"),
    provider: str = Query("openverse", description="openverse | jamendo | all"),
    limit: int = Query(20, ge=1, le=50),
    page: int = Query(1, ge=1),
    instrumental: bool = Query(False, description="Chỉ lấy bản không lời"),
    commercial_only: bool = Query(True, description="Chỉ license cho phép thương mại + chỉnh sửa"),
    min_duration: float = Query(0, ge=0, description="Thời lượng tối thiểu (giây)"),
    max_duration: float = Query(0, ge=0, description="Thời lượng tối đa (giây)"),
):
    try:
        result = search_tracks(
            q,
            provider=provider,
            limit=limit,
            page=page,
            instrumental=instrumental,
            commercial_only=commercial_only,
            min_duration=min_duration,
            max_duration=max_duration,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    known: Dict[str, str] = {}
    for it in load_catalog():
        ext = str(it.get("external_id") or "")
        if ext:
            known[f"{it.get('provider') or ''}:{ext}"] = it["id"]

    items: List[Dict[str, Any]] = result["items"]
    for it in items:
        it["in_library"] = known.get(f"{it['provider']}:{it['external_id']}")
    return result


@router.post("/bgm/import")
async def import_online_bgm(req: ImportBgmRequest):
    """Download a searched track into the local library."""
    try:
        item = import_remote_track(req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Không tải được bản nhạc: {e}")
    item["stream_url"] = f"/api/v1/bgm/{item['id']}/audio"
    return {"item": item}


@router.post("/bgm/extract")
async def extract_bgm(req: ExtractBgmRequest, request: Request):
    video_path = req.video_path
    title = req.title
    source_url = req.url or ""

    if req.video_id and not video_path:
        cand = os.path.join(settings.RAW_INPUT_DIR, f"{req.video_id}.mp4")
        if os.path.isfile(cand):
            video_path = cand

    if req.url and not video_path:
        clean = BaseScraper.extract_url_from_text(req.url.strip())
        if not (clean.startswith("http://") or clean.startswith("https://")):
            raise HTTPException(status_code=400, detail="Link không hợp lệ")
        source_url = clean
        scraper_mgr = getattr(request.app.state, "scraper_manager", None)
        if scraper_mgr is None:
            scraper_mgr = ScraperManager(output_dir=settings.RAW_INPUT_DIR)
        try:
            metas = await scraper_mgr.download_batch(
                [clean], output_dir=settings.RAW_INPUT_DIR, ignore_errors=True
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Không tải được video: {e}")
        if not metas:
            raise HTTPException(status_code=400, detail="Không bóc được video từ link này")
        meta = metas[0]
        video_path = getattr(meta, "file_path", None)
        title = title or getattr(meta, "title", None)
        if not video_path or not os.path.isfile(video_path):
            raise HTTPException(status_code=400, detail="Tải xong nhưng không có file video")

    if not video_path or not os.path.isfile(video_path):
        raise HTTPException(status_code=400, detail="Cần dán link video hoặc chọn clip đã tải")

    try:
        item = harvest_bgm(video_path, title=title, source_url=source_url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    item["stream_url"] = f"/api/v1/bgm/{item['id']}/audio"
    return {"item": item}


@router.post("/bgm/upload")
async def upload_bgm(file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename or "track.mp3")[1] or ".mp3"
    raw = await file.read()
    if not raw or len(raw) < 400:
        raise HTTPException(status_code=400, detail="File nhạc trống")
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(raw)
            tmp = fh.name
        item = import_audio_file(tmp, title=os.path.splitext(file.filename or "Nhạc nền")[0])
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        if tmp and os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    item["stream_url"] = f"/api/v1/bgm/{item['id']}/audio"
    return {"item": item}


@router.get("/bgm/{track_id}/audio")
async def stream_bgm(track_id: str):
    path = resolve_bgm(track_id)
    if not path:
        raise HTTPException(status_code=404, detail="Không có bản nhạc này")
    media = "audio/mpeg" if path.endswith(".mp3") else "audio/mp4"
    return FileResponse(path, media_type=media, filename=os.path.basename(path))


@router.delete("/bgm/{track_id}")
async def remove_bgm(track_id: str):
    if not delete_bgm(track_id):
        raise HTTPException(status_code=404, detail="Không có bản nhạc này")
    return {"deleted": True, "id": track_id}
