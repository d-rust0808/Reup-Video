"""TikTok Login Kit + Content Posting API (Direct Post, FILE_UPLOAD)."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
API_BASE = "https://open.tiktokapis.com"
REQUIRED_SCOPES = ("user.info.basic", "video.publish")
TITLE_MAX = 2200
WHOLE_UPLOAD_MAX = 64 * 1024 * 1024
MIN_CHUNK = 5 * 1024 * 1024
DEFAULT_CHUNK = 10 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024
MAX_LAST_CHUNK = 128 * 1024 * 1024


class TikTokAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 0, code: str = "", retryable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = str(code or "")
        self.retryable = retryable


def plan_chunks(video_size: int) -> Tuple[int, int]:
    size = int(video_size or 0)
    if size <= 0:
        raise TikTokAPIError("Video TikTok trống")
    if size <= WHOLE_UPLOAD_MAX:
        return size, 1
    chunk = DEFAULT_CHUNK
    count = size // chunk
    if count < 1:
        return size, 1
    last = size - (count - 1) * chunk
    if last > MAX_LAST_CHUNK:
        chunk = MAX_CHUNK
        count = math.ceil(size / chunk)
        last = size - (count - 1) * chunk
    if last < MIN_CHUNK and count > 1:
        count -= 1
    return chunk, max(1, count)


def pick_privacy(options: Iterable[str]) -> str:
    available = [str(item).strip() for item in (options or []) if str(item).strip()]
    for preferred in ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"):
        if preferred in available:
            return preferred
    return available[0] if available else "SELF_ONLY"


def parse_auth_code(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if "code=" in text:
        parsed = urlparse(text)
        values = parse_qs(parsed.query or "")
        if not values.get("code") and parsed.fragment:
            values = parse_qs(parsed.fragment)
        code = (values.get("code") or [""])[0]
        return str(code).strip()
    return text


def authorize_url(*, client_key: str, redirect_uri: str, state: str = "reup") -> str:
    params = {
        "client_key": str(client_key or "").strip(),
        "response_type": "code",
        "scope": ",".join(REQUIRED_SCOPES),
        "redirect_uri": str(redirect_uri or "").strip(),
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def clip_title(text: str) -> str:
    value = str(text or "").strip()
    if len(value) <= TITLE_MAX:
        return value
    return value[: TITLE_MAX - 1].rstrip() + "…"


class TikTokClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=20.0, read=timeout, write=timeout),
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _bearer(token: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}

    def _raise(self, response: httpx.Response, payload: Any) -> None:
        error = payload.get("error") if isinstance(payload, dict) else {}
        if not isinstance(error, dict):
            error = {}
        code = str(error.get("code") or error.get("error") or "")
        message = str(
            error.get("message")
            or error.get("error_description")
            or payload.get("error_description")
            or f"TikTok API HTTP {response.status_code}"
        )
        status = int(response.status_code or 0)
        retryable = status == 429 or status >= 500 or code in {
            "rate_limit_exceeded",
            "internal_error",
            "internal_server_error",
        }
        raise TikTokAPIError(message, status_code=status, code=code, retryable=retryable)

    def _json(self, response: httpx.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        error = payload.get("error")
        ok = False
        if isinstance(error, dict):
            ok = str(error.get("code") or "") in {"", "ok"}
        elif not error and response.is_success:
            ok = True
        if response.is_success and ok:
            return payload
        if response.is_success and "access_token" in payload:
            return payload
        self._raise(response, payload)
        return payload

    def exchange_code(
        self,
        *,
        client_key: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> Dict[str, Any]:
        response = self.client.post(
            TOKEN_URL,
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
        )
        return self._json(response)

    def refresh_token(self, *, client_key: str, client_secret: str, refresh_token: str) -> Dict[str, Any]:
        response = self.client.post(
            TOKEN_URL,
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"},
        )
        return self._json(response)

    def user_info(self, access_token: str) -> Dict[str, Any]:
        response = self.client.get(
            f"{API_BASE}/v2/user/info/",
            params={"fields": "open_id,union_id,avatar_url,display_name,username"},
            headers=self._bearer(access_token),
        )
        payload = self._json(response)
        data = payload.get("data") or {}
        user = data.get("user") if isinstance(data, dict) else {}
        return user if isinstance(user, dict) else {}

    def creator_info(self, access_token: str) -> Dict[str, Any]:
        response = self.client.post(
            f"{API_BASE}/v2/post/publish/creator_info/query/",
            headers=self._bearer(access_token),
            json={},
        )
        payload = self._json(response)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return data

    def init_direct_post(
        self,
        access_token: str,
        *,
        title: str,
        privacy_level: str,
        video_size: int,
        chunk_size: int,
        total_chunk_count: int,
        disable_comment: bool = False,
        disable_duet: bool = False,
        disable_stitch: bool = False,
        is_aigc: bool = False,
    ) -> Dict[str, Any]:
        post_info: Dict[str, Any] = {
            "title": clip_title(title),
            "privacy_level": privacy_level,
            "disable_comment": bool(disable_comment),
            "disable_duet": bool(disable_duet),
            "disable_stitch": bool(disable_stitch),
            "video_cover_timestamp_ms": 1000,
        }
        if is_aigc:
            post_info["is_aigc"] = True
        body = {
            "post_info": post_info,
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": int(video_size),
                "chunk_size": int(chunk_size),
                "total_chunk_count": int(total_chunk_count),
            },
        }
        response = self.client.post(
            f"{API_BASE}/v2/post/publish/video/init/",
            headers=self._bearer(access_token),
            json=body,
        )
        payload = self._json(response)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if not data.get("publish_id") or not data.get("upload_url"):
            raise TikTokAPIError("TikTok không trả upload_url")
        return data

    def upload_file(self, upload_url: str, source_path: str, *, chunk_size: int, total_size: int) -> None:
        remaining = int(total_size)
        offset = 0
        index = 0
        with open(source_path, "rb") as handle:
            while remaining > 0:
                index += 1
                is_last = remaining <= chunk_size or offset + chunk_size >= total_size
                take = remaining if is_last else chunk_size
                blob = handle.read(take)
                if not blob:
                    break
                first = offset
                last = offset + len(blob) - 1
                response = self.client.put(
                    upload_url,
                    content=blob,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(blob)),
                        "Content-Range": f"bytes {first}-{last}/{total_size}",
                    },
                    timeout=httpx.Timeout(180.0, connect=20.0, read=180.0, write=180.0),
                )
                if response.status_code not in {200, 201, 206}:
                    try:
                        payload = response.json()
                    except Exception:
                        payload = {}
                    self._raise(response, payload)
                offset += len(blob)
                remaining -= len(blob)

    def fetch_status(self, access_token: str, publish_id: str) -> Dict[str, Any]:
        response = self.client.post(
            f"{API_BASE}/v2/post/publish/status/fetch/",
            headers=self._bearer(access_token),
            json={"publish_id": publish_id},
        )
        payload = self._json(response)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return data
