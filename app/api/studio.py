"""Studio UI session — survives preview / browser reloads (single-operator app)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import settings
from app.core.database import get_db_connection
from app.services.frame_studio import FRAME_DIR, PRESETS, overlay_payload, render_frame_png

logger = logging.getLogger(__name__)
router = APIRouter()
SESSION_ID = "default"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/studio/session")
async def get_studio_session() -> Dict[str, Any]:
    with get_db_connection(settings.DB_PATH) as conn:
        row = conn.execute(
            "SELECT payload, updated_at FROM studio_state WHERE id = ?",
            (SESSION_ID,),
        ).fetchone()
    if not row:
        return {"savedAt": 0}
    try:
        data = json.loads(row["payload"] or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("savedAt", 0)
    return data


@router.put("/studio/session")
async def put_studio_session(body: Dict[str, Any]) -> Dict[str, Any]:
    payload = body if isinstance(body, dict) else {}
    payload["savedAt"] = int(payload.get("savedAt") or 0) or int(datetime.now(timezone.utc).timestamp() * 1000)
    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw) > 400_000:
        payload["extractedMediaList"] = (payload.get("extractedMediaList") or [])[:20]
        raw = json.dumps(payload, ensure_ascii=False)
    now = _utc_now()
    with get_db_connection(settings.DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO studio_state (id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
            """,
            (SESSION_ID, raw, now),
        )
        conn.commit()
    return {"ok": True, "savedAt": payload["savedAt"]}


class FrameRenderRequest(BaseModel):
    preset: str = "cinema"
    color: Optional[str] = None
    accent: Optional[str] = None
    thickness: Optional[int] = None


@router.get("/frames/presets")
async def list_frame_presets() -> Dict[str, Any]:
    return {"presets": PRESETS}


@router.post("/frames/render")
async def render_frame(req: FrameRenderRequest) -> Dict[str, Any]:
    try:
        path = render_frame_png(
            preset_id=req.preset or "cinema",
            color=req.color or "",
            accent=req.accent or "",
            thickness=int(req.thickness or 0),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    ov = overlay_payload(path, req.preset or "cinema")
    return {"ok": True, "overlay": ov, "path": path, "url": ov["url"]}


@router.post("/frames/upload")
async def upload_frame(file: UploadFile = File(...)) -> Dict[str, Any]:
    os.makedirs(FRAME_DIR, exist_ok=True)
    ext = os.path.splitext(file.filename or "frame.png")[1].lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(status_code=400, detail="Chỉ nhận PNG/JPG/WebP")
    dest = os.path.join(FRAME_DIR, f"custom{ext}")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="File rỗng")
    with open(dest, "wb") as f:
        f.write(data)
    ov = overlay_payload(dest, "custom")
    return {"ok": True, "overlay": ov, "url": ov["url"]}


@router.get("/frames/file/{filename}")
async def get_frame_file(filename: str):
    name = os.path.basename(filename)
    path = os.path.join(FRAME_DIR, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Không thấy file khung")
    return FileResponse(path, media_type="image/png")
