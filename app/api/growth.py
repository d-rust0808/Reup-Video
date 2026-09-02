"""Channel growth dashboard: follow / like / comment / view."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.services.channel_growth import (
    growth_detail,
    growth_overview,
    refresh_channel,
    refresh_status,
    start_refresh_all,
)

router = APIRouter(prefix="/growth")


class GrowthRefreshRequest(BaseModel):
    channel_id: Optional[str] = Field(default=None, max_length=80)
    days: int = Field(default=90, ge=7, le=180)


@router.get("/channels")
async def get_growth_overview(days: int = Query(default=30, ge=7, le=180)) -> Dict[str, Any]:
    return await asyncio.to_thread(growth_overview, settings.DB_PATH, days=days)


@router.get("/channels/{channel_id}")
async def get_growth_detail(
    channel_id: str,
    days: int = Query(default=30, ge=7, le=180),
) -> Dict[str, Any]:
    payload = await asyncio.to_thread(growth_detail, settings.DB_PATH, channel_id, days=days)
    if not payload.get("ok"):
        raise HTTPException(status_code=404, detail=payload.get("error") or "Kênh không tồn tại")
    return payload


@router.post("/refresh")
async def post_growth_refresh(req: GrowthRefreshRequest) -> Dict[str, Any]:
    channel_id = str(req.channel_id or "").strip()
    if channel_id:
        result = await asyncio.to_thread(
            refresh_channel,
            settings.DB_PATH,
            channel_id,
            days=req.days,
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "Không kéo được số liệu")
        detail = await asyncio.to_thread(growth_detail, settings.DB_PATH, channel_id, days=min(req.days, 90))
        result["channel"] = detail.get("channel")
        result["posts"] = detail.get("posts") or []
        result["refresh"] = refresh_status()
        return result
    state = start_refresh_all(settings.DB_PATH, days=req.days)
    return {"ok": True, "refresh": state, "message": "Đang kéo số liệu Facebook cho tất cả Fanpage"}
