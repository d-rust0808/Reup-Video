"""Studio UI session — survives preview / browser reloads (single-operator app)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter

from app.config import settings
from app.core.database import get_db_connection

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
