"""TikTok app connection, creator binding and Direct Post endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.core.database import get_db_connection
from app.services.secret_store import SecretStoreError, get_secret, set_secret
from app.services.tiktok_client import (
    REQUIRED_SCOPES,
    TikTokAPIError,
    TikTokClient,
    authorize_url,
    parse_auth_code,
    pick_privacy,
)
from app.services.tiktok_distribution import enqueue_tiktok_video, wake_tiktok_worker


router = APIRouter(prefix="/tiktok")
CONNECTION_ID = "tiktok_default"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _api_error(error: Exception) -> HTTPException:
    if isinstance(error, TikTokAPIError):
        return HTTPException(status_code=400, detail=f"TikTok: {error}")
    if isinstance(error, SecretStoreError):
        return HTTPException(status_code=500, detail=str(error))
    return HTTPException(status_code=500, detail=f"Không thể kết nối TikTok: {error}")


class TikTokConnectRequest(BaseModel):
    client_key: str = Field(min_length=4, max_length=200)
    client_secret: str = Field(min_length=4, max_length=500)
    redirect_uri: str = Field(default="", max_length=500)
    auth_code: str = Field(default="", max_length=4000)
    access_token: str = Field(default="", max_length=4000)
    refresh_token: str = Field(default="", max_length=4000)
    auto_publish: bool = True


class TikTokBindRequest(BaseModel):
    open_id: str = Field(min_length=1, max_length=120)
    auto_publish: bool = True


def _scopes_list(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or "")
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            pass
    return [part.strip() for part in text.replace(" ", "").split(",") if part.strip()]


def _materialize_tiktok_channel(conn, account: Dict[str, Any], *, auto_publish: bool) -> str:
    open_id = str(account.get("open_id") or "")
    now = _now()
    existing = conn.execute(
        """
        SELECT channel_id FROM channel_destinations
        WHERE provider = 'tiktok' AND destination_id = ?
        LIMIT 1
        """,
        (open_id,),
    ).fetchone()
    name = str(account.get("nickname") or account.get("username") or f"TikTok {open_id[:6]}")
    handle = str(account.get("username") or "").lstrip("@")
    if existing:
        conn.execute(
            """
            UPDATE channels
            SET name = ?, handle = ?, updated_at = ?
            WHERE channel_id = ?
            """,
            (name, handle, now, existing["channel_id"]),
        )
        conn.execute(
            """
            UPDATE channel_destinations
            SET auto_publish = ?, updated_at = ?
            WHERE channel_id = ? AND provider = 'tiktok'
            """,
            (int(auto_publish), now, existing["channel_id"]),
        )
        return str(existing["channel_id"])
    channel_id = f"chan_tt_{open_id[:16]}"
    conn.execute(
        """
        INSERT OR IGNORE INTO channels (
            channel_id, name, platform, handle, tags, description,
            color, overlays, status, created_at, updated_at
        ) VALUES (?, ?, 'tiktok', ?, ?, ?, 'rose', '[]', 'ACTIVE', ?, ?)
        """,
        (
            channel_id,
            name,
            handle,
            json.dumps(["tiktok"], ensure_ascii=False),
            "Tài khoản TikTok Direct Post",
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO channel_destinations (
            channel_id, provider, destination_id, auto_publish, created_at, updated_at
        ) VALUES (?, 'tiktok', ?, ?, ?, ?)
        ON CONFLICT(channel_id, provider) DO UPDATE SET
            destination_id = excluded.destination_id,
            auto_publish = excluded.auto_publish,
            updated_at = excluded.updated_at
        """,
        (channel_id, open_id, int(auto_publish), now, now),
    )
    return channel_id


def _store_account(
    tokens: Dict[str, Any],
    user: Dict[str, Any],
    creator: Dict[str, Any],
    *,
    connection_id: str,
) -> Dict[str, Any]:
    open_id = str(tokens.get("open_id") or user.get("open_id") or "")
    if not open_id:
        raise TikTokAPIError("TikTok không trả open_id")
    access = str(tokens.get("access_token") or "")
    refresh = str(tokens.get("refresh_token") or "")
    if not access:
        raise TikTokAPIError("Thiếu access_token TikTok")
    access_ref = f"tiktok.account.{open_id}.access"
    refresh_ref = f"tiktok.account.{open_id}.refresh"
    set_secret(access_ref, access)
    if refresh:
        set_secret(refresh_ref, refresh)
    expires_in = int(tokens.get("expires_in") or 86400)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - 60))
    ).isoformat()
    scopes = _scopes_list(tokens.get("scope") or user.get("scope") or "")
    can_publish = int("video.publish" in scopes)
    username = str(creator.get("creator_username") or user.get("username") or "")
    nickname = str(creator.get("creator_nickname") or user.get("display_name") or username)
    avatar = str(creator.get("creator_avatar_url") or user.get("avatar_url") or "")
    privacy = creator.get("privacy_level_options") or []
    now = _now()
    with get_db_connection(settings.DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO tiktok_accounts (
                open_id, connection_id, username, nickname, avatar_url, scopes,
                privacy_options, access_token_ref, refresh_token_ref, token_expires_at,
                can_publish, last_synced_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(open_id) DO UPDATE SET
                connection_id = excluded.connection_id,
                username = excluded.username,
                nickname = excluded.nickname,
                avatar_url = excluded.avatar_url,
                scopes = excluded.scopes,
                privacy_options = excluded.privacy_options,
                access_token_ref = excluded.access_token_ref,
                refresh_token_ref = excluded.refresh_token_ref,
                token_expires_at = excluded.token_expires_at,
                can_publish = excluded.can_publish,
                last_synced_at = excluded.last_synced_at,
                updated_at = excluded.updated_at
            """,
            (
                open_id,
                connection_id,
                username[:80],
                nickname[:120],
                avatar[:500],
                json.dumps(scopes, ensure_ascii=False),
                json.dumps(privacy, ensure_ascii=False),
                access_ref,
                refresh_ref,
                expires_at,
                can_publish,
                now,
                now,
            ),
        )
        conn.commit()
    return {
        "open_id": open_id,
        "username": username,
        "nickname": nickname,
        "avatar_url": avatar,
        "scopes": scopes,
        "privacy_options": privacy,
        "privacy_level": pick_privacy(privacy),
        "can_publish": bool(can_publish),
        "token_expires_at": expires_at,
    }


@router.get("/settings")
async def tiktok_settings():
    with get_db_connection(settings.DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM tiktok_connections WHERE id = ?",
            (CONNECTION_ID,),
        ).fetchone()
        accounts = [dict(item) for item in conn.execute(
            "SELECT open_id, username, nickname, avatar_url, scopes, privacy_options, can_publish, token_expires_at FROM tiktok_accounts"
        ).fetchall()]
    if not row:
        return {
            "connected": False,
            "status": "DISCONNECTED",
            "client_key": "",
            "redirect_uri": "",
            "account_count": 0,
            "accounts": [],
            "authorize_url": "",
            "has_client_secret": False,
        }
    data = dict(row)
    for acc in accounts:
        acc["scopes"] = _scopes_list(acc.get("scopes"))
        try:
            acc["privacy_options"] = json.loads(acc.get("privacy_options") or "[]")
        except Exception:
            acc["privacy_options"] = []
        acc["can_publish"] = bool(acc.get("can_publish"))
        acc.pop("access_token_ref", None)
        acc.pop("refresh_token_ref", None)
    redirect = data.get("redirect_uri") or ""
    key = data.get("client_key") or ""
    return {
        "connected": data["status"] == "CONNECTED" and bool(accounts),
        "status": data["status"],
        "client_key": key,
        "redirect_uri": redirect,
        "account_count": len(accounts),
        "accounts": accounts,
        "last_error": data.get("last_error") or "",
        "has_client_secret": bool(get_secret(data.get("client_secret_ref") or "")),
        "authorize_url": authorize_url(client_key=key, redirect_uri=redirect) if key and redirect else "",
    }


@router.get("/authorize-url")
async def tiktok_authorize_url(client_key: str = "", redirect_uri: str = ""):
    with get_db_connection(settings.DB_PATH) as conn:
        row = conn.execute(
            "SELECT client_key, redirect_uri FROM tiktok_connections WHERE id = ?",
            (CONNECTION_ID,),
        ).fetchone()
    key = (client_key or (row["client_key"] if row else "")).strip()
    uri = (redirect_uri or (row["redirect_uri"] if row else "")).strip()
    if not key or not uri:
        raise HTTPException(status_code=400, detail="Nhập Client Key và Redirect URI đã đăng ký trên TikTok")
    return {"url": authorize_url(client_key=key, redirect_uri=uri)}


@router.put("/settings")
async def connect_tiktok(req: TikTokConnectRequest):
    client_key = req.client_key.strip()
    client_secret = req.client_secret.strip()
    redirect_uri = req.redirect_uri.strip()
    code = parse_auth_code(req.auth_code)
    access = req.access_token.strip()
    refresh = req.refresh_token.strip()
    if not code and not access:
        raise HTTPException(
            status_code=400,
            detail="Dán mã ủy quyền (code) từ TikTok hoặc access token + refresh token",
        )
    client = TikTokClient()
    secret_ref = "tiktok.default.client_secret"
    now = _now()
    try:
        set_secret(secret_ref, client_secret)
        with get_db_connection(settings.DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO tiktok_connections (
                    id, client_key, redirect_uri, client_secret_ref,
                    status, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'CONNECTING', '', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    client_key = excluded.client_key,
                    redirect_uri = excluded.redirect_uri,
                    client_secret_ref = excluded.client_secret_ref,
                    status = 'CONNECTING',
                    last_error = '',
                    updated_at = excluded.updated_at
                """,
                (CONNECTION_ID, client_key, redirect_uri, secret_ref, now, now),
            )
            conn.commit()

        tokens: Dict[str, Any]
        if code:
            if not redirect_uri:
                raise HTTPException(status_code=400, detail="Redirect URI phải trùng với lúc xin code")
            tokens = client.exchange_code(
                client_key=client_key,
                client_secret=client_secret,
                code=code,
                redirect_uri=redirect_uri,
            )
        else:
            tokens = {
                "access_token": access,
                "refresh_token": refresh,
                "expires_in": 86400,
                "scope": ",".join(REQUIRED_SCOPES),
            }
            info = client.user_info(access)
            tokens["open_id"] = info.get("open_id") or ""

        access_token = str(tokens.get("access_token") or access)
        user = client.user_info(access_token)
        if not tokens.get("open_id"):
            tokens["open_id"] = user.get("open_id") or ""
        creator = {}
        try:
            creator = client.creator_info(access_token)
        except TikTokAPIError:
            creator = {}
        account = _store_account(tokens, user, creator, connection_id=CONNECTION_ID)
        with get_db_connection(settings.DB_PATH) as conn:
            channel_id = _materialize_tiktok_channel(conn, account, auto_publish=req.auto_publish)
            conn.execute(
                """
                UPDATE tiktok_connections
                SET status = ?, last_error = '', updated_at = ?
                WHERE id = ?
                """,
                ("CONNECTED" if account["can_publish"] else "LIMITED", now, CONNECTION_ID),
            )
            conn.commit()
        if not account["can_publish"]:
            raise HTTPException(
                status_code=400,
                detail="Token thiếu quyền video.publish. Bật Content Posting API → Direct Post trên TikTok for Developers rồi ủy quyền lại.",
            )
        privacy = account.get("privacy_level") or "SELF_ONLY"
        note = ""
        if privacy == "SELF_ONLY":
            note = " App chưa audit Direct Post nên TikTok chỉ cho đăng riêng tư (SELF_ONLY) đến khi được duyệt."
        return {
            "connected": True,
            "account": account,
            "channel_id": channel_id,
            "message": (
                f"Đã kết nối TikTok @{account.get('username') or account.get('nickname')}."
                f"{note}"
            ),
        }
    except HTTPException:
        raise
    except Exception as error:
        with get_db_connection(settings.DB_PATH) as conn:
            conn.execute(
                "UPDATE tiktok_connections SET status = 'ERROR', last_error = ?, updated_at = ? WHERE id = ?",
                (str(error)[:1000], _now(), CONNECTION_ID),
            )
            conn.commit()
        raise _api_error(error) from error
    finally:
        client.close()


@router.get("/accounts")
async def list_tiktok_accounts():
    with get_db_connection(settings.DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT ta.open_id, ta.username, ta.nickname, ta.avatar_url, ta.can_publish,
                   ta.privacy_options, ta.token_expires_at,
                   COUNT(cd.channel_id) AS bound_channels
            FROM tiktok_accounts ta
            LEFT JOIN channel_destinations cd
              ON cd.provider = 'tiktok' AND cd.destination_id = ta.open_id
            GROUP BY ta.open_id
            ORDER BY ta.nickname COLLATE NOCASE
            """
        ).fetchall()
    accounts = []
    for row in rows:
        data = dict(row)
        data["can_publish"] = bool(data.get("can_publish"))
        try:
            data["privacy_options"] = json.loads(data.get("privacy_options") or "[]")
        except Exception:
            data["privacy_options"] = []
        accounts.append(data)
    return {"accounts": accounts, "total": len(accounts)}


@router.put("/channels/{channel_id}/binding")
async def bind_tiktok_account(channel_id: str, req: TikTokBindRequest):
    now = _now()
    with get_db_connection(settings.DB_PATH) as conn:
        channel = conn.execute(
            "SELECT platform FROM channels WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if not channel:
            raise HTTPException(status_code=404, detail="Kênh không tồn tại")
        if str(channel["platform"] or "").lower() != "tiktok":
            raise HTTPException(status_code=400, detail="Chỉ kênh TikTok mới bind được tài khoản TikTok")
        acc = conn.execute(
            "SELECT nickname, username, can_publish FROM tiktok_accounts WHERE open_id = ?",
            (req.open_id,),
        ).fetchone()
        if not acc:
            raise HTTPException(status_code=404, detail="Chưa kết nối tài khoản TikTok này")
        if not acc["can_publish"]:
            raise HTTPException(status_code=400, detail="Tài khoản thiếu quyền video.publish")
        conn.execute(
            """
            INSERT INTO channel_destinations (
                channel_id, provider, destination_id, auto_publish, created_at, updated_at
            ) VALUES (?, 'tiktok', ?, ?, ?, ?)
            ON CONFLICT(channel_id, provider) DO UPDATE SET
                destination_id = excluded.destination_id,
                auto_publish = excluded.auto_publish,
                updated_at = excluded.updated_at
            """,
            (channel_id, req.open_id, int(req.auto_publish), now, now),
        )
        conn.commit()
    return {
        "channel_id": channel_id,
        "open_id": req.open_id,
        "username": acc["username"] or acc["nickname"],
        "auto_publish": req.auto_publish,
        "message": "Đã liên kết kênh với TikTok",
    }


@router.post("/channel-videos/{video_id}/publish")
async def publish_tiktok_video(video_id: str, request: Request):
    distribution_id = enqueue_tiktok_video(
        settings.DB_PATH,
        video_id,
        require_auto_publish=False,
        reset_failed=True,
    )
    if not distribution_id:
        raise HTTPException(
            status_code=400,
            detail="Video chưa có file hợp lệ hoặc kênh chưa bind TikTok có quyền video.publish",
        )
    worker = getattr(request.app.state, "tiktok_distribution_worker", None)
    if worker:
        worker.wake()
    else:
        wake_tiktok_worker()
    return {
        "distribution_id": distribution_id,
        "message": "Đã xếp hàng đăng TikTok",
    }
