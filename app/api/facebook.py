"""Facebook App connection, Page binding and Reel publishing endpoints."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.core.database import get_db_connection
from app.services.facebook_client import (
    FacebookAPIError,
    FacebookClient,
    token_metadata,
)
from app.services.facebook_distribution import enqueue_channel_video
from app.services.secret_store import SecretStoreError, get_secret, set_secret


router = APIRouter(prefix="/facebook")
CONNECTION_ID = "facebook_default"
REQUIRED_SCOPES = {"pages_show_list", "pages_read_engagement", "pages_manage_posts"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _graph_version(value: str) -> str:
    version = str(value or "v24.0").strip()
    if not version.startswith("v"):
        version = f"v{version}"
    if not re.fullmatch(r"v\d+\.\d+", version):
        raise HTTPException(status_code=422, detail="Graph API version không hợp lệ")
    return version


def _api_error(error: Exception) -> HTTPException:
    if isinstance(error, FacebookAPIError):
        suffix = f" (code {error.code})" if error.code else ""
        return HTTPException(status_code=400, detail=f"Facebook: {error}{suffix}")
    if isinstance(error, SecretStoreError):
        return HTTPException(status_code=500, detail=str(error))
    return HTTPException(status_code=500, detail=f"Không thể kết nối Facebook: {error}")


class FacebookConnectRequest(BaseModel):
    app_id: str = Field(min_length=1, max_length=100)
    app_secret: str = Field(min_length=1, max_length=500)
    user_token: str = Field(min_length=20, max_length=4000)
    graph_version: str = Field(default="v24.0", max_length=20)
    exchange_token: bool = True


class FacebookBindRequest(BaseModel):
    page_id: str = Field(min_length=1, max_length=100)
    auto_publish: bool = True


def _page_payload(page: Dict[str, Any]) -> Dict[str, Any]:
    tasks = [str(item) for item in (page.get("tasks") or [])]
    picture = page.get("picture") or {}
    picture_data = picture.get("data") if isinstance(picture, dict) else {}
    return {
        "page_id": str(page.get("id") or ""),
        "name": str(page.get("name") or ""),
        "category": str(page.get("category") or ""),
        "tasks": tasks,
        "picture_url": str(
            picture_data.get("url") if isinstance(picture_data, dict) else ""
        ),
        "can_publish": "CREATE_CONTENT" in {task.upper() for task in tasks},
        "access_token": str(page.get("access_token") or ""),
    }


def _materialize_page_channels(conn, pages: List[Dict[str, Any]]) -> int:
    """Expose synced Fanpages in the existing channel manager without duplicates."""
    now = _now()
    created = 0
    for page in pages:
        page_id = str(page.get("page_id") or page.get("id") or "")
        if not page_id:
            continue
        existing = conn.execute(
            """
            SELECT channel_id FROM channel_destinations
            WHERE provider = 'facebook' AND destination_id = ?
            LIMIT 1
            """,
            (page_id,),
        ).fetchone()
        if existing:
            continue
        channel_id = f"chan_fb_{page_id}"
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO channels (
                channel_id, name, platform, handle, tags, description,
                color, overlays, status, created_at, updated_at
            ) VALUES (?, ?, 'facebook', ?, ?, ?, 'blue', '[]', 'ACTIVE', ?, ?)
            """,
            (
                channel_id,
                str(page.get("name") or f"Facebook Page {page_id}"),
                page_id,
                json.dumps(["facebook", "reels"], ensure_ascii=False),
                str(page.get("category") or "Facebook Fanpage"),
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO channel_destinations (
                channel_id, provider, destination_id, auto_publish, created_at, updated_at
            ) VALUES (?, 'facebook', ?, 0, ?, ?)
            """,
            (channel_id, page_id, now, now),
        )
        created += int(cursor.rowcount == 1)
    return created


def _store_pages(pages: List[Dict[str, Any]]) -> int:
    now = _now()
    normalized = [_page_payload(page) for page in pages]
    page_ids = [page["page_id"] for page in normalized if page["page_id"]]
    for page in normalized:
        if not page["page_id"] or not page["access_token"]:
            continue
        token_ref = f"facebook.page.{page['page_id']}.token"
        set_secret(token_ref, page["access_token"])
        page["token_ref"] = token_ref

    with get_db_connection(settings.DB_PATH) as conn:
        for page in normalized:
            if not page.get("token_ref"):
                continue
            conn.execute(
                """
                INSERT INTO facebook_pages (
                    page_id, connection_id, name, category, tasks, picture_url,
                    page_token_ref, can_publish, last_synced_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(page_id) DO UPDATE SET
                    connection_id = excluded.connection_id,
                    name = excluded.name,
                    category = excluded.category,
                    tasks = excluded.tasks,
                    picture_url = excluded.picture_url,
                    page_token_ref = excluded.page_token_ref,
                    can_publish = excluded.can_publish,
                    last_synced_at = excluded.last_synced_at,
                    updated_at = excluded.updated_at
                """,
                (
                    page["page_id"],
                    CONNECTION_ID,
                    page["name"],
                    page["category"],
                    json.dumps(page["tasks"], ensure_ascii=False),
                    page["picture_url"],
                    page["token_ref"],
                    int(page["can_publish"]),
                    now,
                    now,
                ),
            )
        _materialize_page_channels(conn, normalized)
        if page_ids:
            placeholders = ",".join("?" for _ in page_ids)
            conn.execute(
                f"DELETE FROM facebook_pages WHERE connection_id = ? AND page_id NOT IN ({placeholders})",
                (CONNECTION_ID, *page_ids),
            )
        else:
            conn.execute("DELETE FROM facebook_pages WHERE connection_id = ?", (CONNECTION_ID,))
        conn.commit()
    return len([page for page in normalized if page.get("token_ref")])


@router.get("/settings")
async def get_facebook_settings():
    with get_db_connection(settings.DB_PATH) as conn:
        connection = conn.execute(
            "SELECT * FROM facebook_connections WHERE id = ?",
            (CONNECTION_ID,),
        ).fetchone()
        page_count = conn.execute(
            "SELECT COUNT(*) FROM facebook_pages WHERE connection_id = ?",
            (CONNECTION_ID,),
        ).fetchone()[0]
    if not connection:
        return {"connected": False, "status": "DISCONNECTED", "page_count": 0}
    data = dict(connection)
    expires_at = data["token_expires_at"]
    token_kind = "UNKNOWN"
    if expires_at:
        try:
            remaining = datetime.fromisoformat(expires_at) - datetime.now(timezone.utc)
            token_kind = "LONG_LIVED" if remaining > timedelta(days=7) else "SHORT_LIVED"
        except ValueError:
            pass
    return {
        "connected": data["status"] == "CONNECTED",
        "status": data["status"],
        "app_id": data["app_id"],
        "graph_version": data["graph_version"],
        "user_id": data["user_id"],
        "user_name": data["user_name"],
        "scopes": json.loads(data["scopes"] or "[]"),
        "token_expires_at": expires_at,
        "token_kind": token_kind,
        "page_count": page_count,
        "last_error": data["last_error"],
        "has_app_secret": bool(get_secret(data["app_secret_ref"])),
        "has_user_token": bool(get_secret(data["user_token_ref"])),
    }


@router.put("/settings")
async def connect_facebook(req: FacebookConnectRequest):
    graph_version = _graph_version(req.graph_version)
    client = FacebookClient(graph_version)
    token = req.user_token.strip()
    try:
        original_debug = client.debug_user_token(
            token,
            req.app_id.strip(),
            req.app_secret.strip(),
        )
        original_metadata = token_metadata(original_debug)
        should_exchange = bool(req.exchange_token and original_metadata["expires_at"])
        if original_metadata["expires_at"]:
            expires_at = datetime.fromisoformat(original_metadata["expires_at"])
            should_exchange = should_exchange and expires_at - datetime.now(timezone.utc) <= timedelta(days=7)

        if should_exchange:
            exchange = client.exchange_long_lived_token(
                token,
                req.app_id.strip(),
                req.app_secret.strip(),
            )
            token = str(exchange.get("access_token") or "")
            if not token:
                raise FacebookAPIError("Không nhận được long-lived token")

        user = client.validate_user_token(token)
        debug = client.debug_user_token(token, req.app_id.strip(), req.app_secret.strip())
        metadata = token_metadata(debug)
        if not metadata["is_valid"]:
            raise FacebookAPIError("User token không hợp lệ")
        missing = sorted(REQUIRED_SCOPES - set(metadata["scopes"]))
        if missing:
            raise FacebookAPIError(f"Token thiếu quyền: {', '.join(missing)}")
        pages = client.list_pages(token)

        app_secret_ref = "facebook.default.app_secret"
        user_token_ref = "facebook.default.user_token"
        set_secret(app_secret_ref, req.app_secret.strip())
        set_secret(user_token_ref, token)
        now = _now()
        with get_db_connection(settings.DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO facebook_connections (
                    id, app_id, graph_version, user_id, user_name, scopes,
                    token_expires_at, app_secret_ref, user_token_ref,
                    status, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'CONNECTED', '', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    app_id = excluded.app_id,
                    graph_version = excluded.graph_version,
                    user_id = excluded.user_id,
                    user_name = excluded.user_name,
                    scopes = excluded.scopes,
                    token_expires_at = excluded.token_expires_at,
                    app_secret_ref = excluded.app_secret_ref,
                    user_token_ref = excluded.user_token_ref,
                    status = 'CONNECTED',
                    last_error = '',
                    updated_at = excluded.updated_at
                """,
                (
                    CONNECTION_ID,
                    req.app_id.strip(),
                    graph_version,
                    str(user.get("id") or metadata["user_id"]),
                    str(user.get("name") or ""),
                    json.dumps(metadata["scopes"], ensure_ascii=False),
                    metadata["expires_at"],
                    app_secret_ref,
                    user_token_ref,
                    now,
                    now,
                ),
            )
            conn.commit()
        page_count = _store_pages(pages)
        return {
            "connected": True,
            "user_name": user.get("name") or "",
            "page_count": page_count,
            "token_exchanged": should_exchange,
            "token_expires_at": metadata["expires_at"],
            "message": f"Đã kết nối Facebook và đồng bộ {page_count} Fanpage",
        }
    except Exception as error:
        raise _api_error(error) from error
    finally:
        client.close()


@router.post("/sync-pages")
async def sync_facebook_pages():
    with get_db_connection(settings.DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM facebook_connections WHERE id = ?",
            (CONNECTION_ID,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Chưa cấu hình Facebook App")
    data = dict(row)
    token = get_secret(data["user_token_ref"])
    if not token:
        raise HTTPException(status_code=400, detail="Không tìm thấy User Token trong Keychain")
    client = FacebookClient(data["graph_version"])
    try:
        client.validate_user_token(token)
        page_count = _store_pages(client.list_pages(token))
        return {"page_count": page_count, "message": f"Đã đồng bộ {page_count} Fanpage"}
    except Exception as error:
        raise _api_error(error) from error
    finally:
        client.close()


@router.get("/pages")
async def list_facebook_pages():
    with get_db_connection(settings.DB_PATH) as conn:
        stored_pages = [dict(row) for row in conn.execute(
            "SELECT page_id, name, category FROM facebook_pages"
        ).fetchall()]
        _materialize_page_channels(conn, stored_pages)
        conn.commit()
        rows = conn.execute(
            """
            SELECT fp.*, COUNT(cd.channel_id) AS bound_channels
            FROM facebook_pages fp
            LEFT JOIN channel_destinations cd
              ON cd.provider = 'facebook' AND cd.destination_id = fp.page_id
            GROUP BY fp.page_id
            ORDER BY fp.can_publish DESC, fp.name COLLATE NOCASE
            """
        ).fetchall()
    pages = []
    for row in rows:
        data = dict(row)
        data["tasks"] = json.loads(data.get("tasks") or "[]")
        data["can_publish"] = bool(data.get("can_publish"))
        data.pop("page_token_ref", None)
        pages.append(data)
    return {"pages": pages, "total": len(pages)}


@router.put("/channels/{channel_id}/binding")
async def bind_facebook_page(channel_id: str, req: FacebookBindRequest):
    now = _now()
    with get_db_connection(settings.DB_PATH) as conn:
        channel = conn.execute(
            "SELECT platform FROM channels WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if not channel:
            raise HTTPException(status_code=404, detail="Kênh không tồn tại")
        if str(channel["platform"] or "").lower() != "facebook":
            raise HTTPException(status_code=400, detail="Chỉ kênh Facebook mới bind được Fanpage")
        page = conn.execute(
            "SELECT name, can_publish FROM facebook_pages WHERE page_id = ?",
            (req.page_id,),
        ).fetchone()
        if not page:
            raise HTTPException(status_code=404, detail="Fanpage chưa được đồng bộ")
        if not page["can_publish"]:
            raise HTTPException(status_code=400, detail="Fanpage không có task CREATE_CONTENT")
        conn.execute(
            """
            INSERT INTO channel_destinations (
                channel_id, provider, destination_id, auto_publish, created_at, updated_at
            ) VALUES (?, 'facebook', ?, ?, ?, ?)
            ON CONFLICT(channel_id, provider) DO UPDATE SET
                destination_id = excluded.destination_id,
                auto_publish = excluded.auto_publish,
                updated_at = excluded.updated_at
            """,
            (channel_id, req.page_id, int(req.auto_publish), now, now),
        )
        conn.commit()
    return {
        "channel_id": channel_id,
        "page_id": req.page_id,
        "page_name": page["name"],
        "auto_publish": req.auto_publish,
        "message": "Đã liên kết kênh với Fanpage",
    }


@router.post("/channel-videos/{video_id}/publish")
async def publish_channel_video(video_id: str, request: Request):
    distribution_id = enqueue_channel_video(
        settings.DB_PATH,
        video_id,
        require_auto_publish=False,
        reset_failed=True,
    )
    if not distribution_id:
        raise HTTPException(
            status_code=400,
            detail="Video chưa có file hợp lệ hoặc kênh chưa bind Fanpage có quyền đăng",
        )
    worker = getattr(request.app.state, "facebook_distribution_worker", None)
    if worker:
        worker.wake()
    return {
        "distribution_id": distribution_id,
        "status": "PENDING",
        "message": "Đã đưa Reel vào hàng đợi đăng Facebook",
    }
