"""REST API for source-channel content catalogs (YouTube / Douyin / …)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.scraper.channel import ChannelCloneService, video_page_url
from app.scraper.manager import ScraperManager
from app.services.content_catalog import (
    channel_inventory,
    delete_channel,
    get_channel,
    list_channels,
    pending_download_ids,
    set_posted,
    update_channel,
    upsert_source_catalog,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class AddChannelRequest(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    tags: Optional[List[str]] = None
    name: Optional[str] = None
    notes: Optional[str] = None
    max_videos: int = Field(default=500, ge=1, le=500)


class PatchChannelRequest(BaseModel):
    name: Optional[str] = None
    tags: Optional[List[str]] = None
    url: Optional[str] = None
    notes: Optional[str] = None


class PatchVideoRequest(BaseModel):
    posted: bool


class FetchRequest(BaseModel):
    video_ids: Optional[List[str]] = None


def _mgr(request: Request) -> ScraperManager:
    mgr = getattr(request.app.state, "scraper_manager", None)
    if mgr is None:
        mgr = ScraperManager(output_dir=settings.RAW_INPUT_DIR)
        request.app.state.scraper_manager = mgr
    return mgr


async def _collect(url: str, max_videos: int = 500) -> Dict[str, Any]:
    service = ChannelCloneService()
    return await service.collect(url, max_videos=max_videos)


def _spawn_download(request: Request, urls: List[str], platform: str) -> None:
    if not urls:
        return
    mgr = _mgr(request)
    concurrency = 2 if platform == "youtube" else 4

    async def _bg() -> None:
        try:
            await mgr.download_batch(
                urls,
                output_dir=settings.RAW_INPUT_DIR,
                ignore_errors=True,
                concurrency=concurrency,
            )
            logger.info("Content catalog download finished platform=%s n=%s", platform, len(urls))
        except Exception:
            logger.exception("Content catalog download failed")

    tasks = getattr(request.app.state, "background_downloads", None)
    if tasks is None:
        tasks = set()
        request.app.state.background_downloads = tasks
    task = asyncio.create_task(_bg(), name=f"content-dl-{platform}-{len(urls)}")
    tasks.add(task)
    task.add_done_callback(tasks.discard)


@router.get("/content/channels")
async def api_list_channels():
    settings.ensure_directories()
    channels = list_channels(settings.DB_PATH)
    return {"channels": channels, "count": len(channels)}


@router.post("/content/channels")
async def api_add_channel(req: AddChannelRequest):
    url = (req.url or "").strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="Dán link kênh YouTube / Douyin / Kuaishou / Xiaohongshu")
    try:
        collected = await _collect(url, max_videos=req.max_videos)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("content collect failed")
        raise HTTPException(status_code=400, detail=f"Không đọc được kênh: {e}") from e

    profile = dict(collected.get("profile") or {})
    platform = (collected.get("platform") or profile.get("platform") or "").lower()
    if not platform:
        try:
            scraper = ScraperManager().get_scraper_for_url(url)
            platform = scraper.__class__.__name__.replace("Scraper", "").lower()
        except Exception:
            platform = ""
    if req.name:
        profile["nickname"] = req.name.strip()
    if req.notes:
        profile["signature"] = req.notes.strip()
    catalog = collected.get("catalog") or []
    video_ids = collected.get("video_ids") or []
    channel_id = upsert_source_catalog(
        settings.DB_PATH,
        profile=profile,
        platform=platform,
        url=collected.get("channel_url") or url,
        video_ids=video_ids,
        catalog=catalog,
        tags=req.tags,
    )
    channels = [c for c in list_channels(settings.DB_PATH) if c["channel_id"] == channel_id]
    return {
        "channel": channels[0] if channels else get_channel(settings.DB_PATH, channel_id),
        "video_ids": video_ids,
        "count": len(video_ids),
        "message": (
            f"Đã lưu kênh «{profile.get('nickname') or platform}» với {len(video_ids)} video. "
            "Bấm vào kênh để xem checklist đã đăng / chưa đăng."
        ),
    }


@router.patch("/content/channels/{channel_id}")
async def api_patch_channel(channel_id: str, req: PatchChannelRequest):
    updated = update_channel(
        settings.DB_PATH,
        channel_id,
        name=req.name,
        tags=req.tags,
        url=req.url,
        notes=req.notes,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Không thấy kênh nguồn")
    return {"channel": updated}


@router.delete("/content/channels/{channel_id}")
async def api_delete_channel(channel_id: str):
    if not delete_channel(settings.DB_PATH, channel_id):
        raise HTTPException(status_code=404, detail="Không thấy kênh nguồn")
    return {"deleted": True, "channel_id": channel_id}


@router.get("/content/channels/{channel_id}/videos")
async def api_list_videos(
    channel_id: str,
    status: str = Query("all"),
):
    if not get_channel(settings.DB_PATH, channel_id):
        raise HTTPException(status_code=404, detail="Không thấy kênh nguồn")
    inventory = channel_inventory(settings.DB_PATH, channel_id, status=status)
    return {
        "channel_id": channel_id,
        "videos": inventory["videos"],
        "count": len(inventory["videos"]),
        "video_count": inventory["video_count"],
        "posted_count": inventory["posted_count"],
        "unposted_count": inventory["unposted_count"],
        "downloaded_count": inventory["downloaded_count"],
    }


@router.post("/content/channels/{channel_id}/sync")
async def api_sync_channel(channel_id: str):
    channel = get_channel(settings.DB_PATH, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Không thấy kênh nguồn")
    url = (channel.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Kênh chưa có link để đồng bộ")
    try:
        collected = await _collect(url, max_videos=500)
    except Exception as e:
        logger.exception("content sync failed")
        raise HTTPException(status_code=400, detail=f"Không đồng bộ được kênh: {e}") from e
    profile = dict(collected.get("profile") or {})
    # Keep the name the user set in Sửa kênh; scrape nickname is only a fallback.
    if channel.get("name"):
        profile["nickname"] = channel.get("name")
    else:
        profile.setdefault("nickname", channel.get("name"))
    platform = (collected.get("platform") or channel.get("platform") or "").lower()
    upsert_source_catalog(
        settings.DB_PATH,
        profile=profile,
        platform=platform,
        url=collected.get("channel_url") or url,
        video_ids=collected.get("video_ids") or [],
        catalog=collected.get("catalog") or [],
    )
    inventory = channel_inventory(settings.DB_PATH, channel_id, status="all")
    return {
        "channel_id": channel_id,
        "count": inventory["video_count"],
        "video_count": inventory["video_count"],
        "posted_count": inventory["posted_count"],
        "unposted_count": inventory["unposted_count"],
        "downloaded_count": inventory["downloaded_count"],
        "message": f"Đã đồng bộ {inventory['video_count']} video từ kênh.",
    }


@router.post("/content/channels/{channel_id}/fetch")
async def api_fetch_channel(channel_id: str, req: FetchRequest, request: Request):
    channel = get_channel(settings.DB_PATH, channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Không thấy kênh nguồn")
    platform = (channel.get("platform") or "").lower()
    pending = pending_download_ids(settings.DB_PATH, channel_id, req.video_ids)
    urls = [video_page_url(vid, platform) for vid in pending]
    _spawn_download(request, urls, platform)
    return {
        "channel_id": channel_id,
        "pending_ids": pending,
        "pending_count": len(pending),
        "message": (
            f"Đang tải {len(pending)} video vào thư viện (chạy nền)."
            if pending
            else "Mọi video của kênh đã có trong thư viện."
        ),
    }


@router.patch("/content/videos/{video_pk}")
async def api_patch_video(video_pk: str, req: PatchVideoRequest):
    updated = set_posted(settings.DB_PATH, video_pk, req.posted)
    if not updated:
        raise HTTPException(status_code=404, detail="Không thấy video")
    return {"video": updated}
