"""Small synchronous client for Facebook Page discovery and Reels upload."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx


class FacebookAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        code: int = 0,
        subcode: int = 0,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.subcode = subcode
        self.retryable = retryable


def _iso_from_timestamp(value: Any) -> Optional[str]:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


class FacebookClient:
    def __init__(self, graph_version: str = "v24.0", timeout: float = 30.0) -> None:
        version = str(graph_version or "v24.0").strip()
        if not version.startswith("v"):
            version = f"v{version}"
        self.graph_version = version
        self.base_url = f"https://graph.facebook.com/{version}"
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=15.0, read=timeout, write=timeout),
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _auth(token: str, *, oauth: bool = False) -> Dict[str, str]:
        scheme = "OAuth" if oauth else "Bearer"
        return {"Authorization": f"{scheme} {token}"}

    @staticmethod
    def _decode(response: httpx.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except Exception:
            payload = {}
        if response.is_success and not payload.get("error"):
            return payload

        error = payload.get("error") if isinstance(payload, dict) else {}
        error = error if isinstance(error, dict) else {}
        status = int(response.status_code or 0)
        code = int(error.get("code") or 0)
        subcode = int(error.get("error_subcode") or 0)
        message = str(error.get("message") or f"Facebook API returned HTTP {status}")
        retryable = status == 429 or status >= 500 or code in {1, 2, 4, 17, 32, 341, 613}
        raise FacebookAPIError(
            message,
            status_code=status,
            code=code,
            subcode=subcode,
            retryable=retryable,
        )

    def validate_user_token(self, user_token: str) -> Dict[str, Any]:
        response = self.client.get(
            f"{self.base_url}/me",
            params={"fields": "id,name"},
            headers=self._auth(user_token),
        )
        return self._decode(response)

    def debug_user_token(self, user_token: str, app_id: str, app_secret: str) -> Dict[str, Any]:
        response = self.client.get(
            f"{self.base_url}/debug_token",
            params={"input_token": user_token},
            headers=self._auth(f"{app_id}|{app_secret}"),
        )
        return self._decode(response).get("data") or {}

    def exchange_long_lived_token(
        self,
        user_token: str,
        app_id: str,
        app_secret: str,
    ) -> Dict[str, Any]:
        response = self.client.post(
            f"{self.base_url}/oauth/access_token",
            data={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": user_token,
            },
        )
        return self._decode(response)

    _PAGE_FIELDS = (
        "id,name,access_token,category,tasks,username,link,"
        "fan_count,followers_count,about,"
        "picture.type(large){url,width,height}"
    )

    def _paged(
        self,
        url: str,
        user_token: str,
        params: Optional[Dict[str, str]] = None,
        *,
        max_items: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        next_url: Optional[str] = url
        next_params = params
        while next_url:
            response = self.client.get(next_url, params=next_params, headers=self._auth(user_token))
            payload = self._decode(response)
            data = payload.get("data") or []
            items.extend(item for item in data if isinstance(item, dict))
            if max_items and len(items) >= max_items:
                return items[:max_items]
            next_url = ((payload.get("paging") or {}).get("next") or "").strip() or None
            next_params = None
        return items

    def list_pages(self, user_token: str) -> List[Dict[str, Any]]:
        """All Fanpages this user can see: /me/accounts plus Business Manager pages."""
        params = {"fields": self._PAGE_FIELDS, "limit": "100"}
        by_id: Dict[str, Dict[str, Any]] = {}

        def _merge(rows: List[Dict[str, Any]]) -> None:
            for item in rows:
                pid = str(item.get("id") or "").strip()
                if not pid:
                    continue
                current = by_id.get(pid) or {}
                merged = {**current, **item}
                if current.get("access_token") and not item.get("access_token"):
                    merged["access_token"] = current["access_token"]
                by_id[pid] = merged

        _merge(self._paged(f"{self.base_url}/me/accounts", user_token, params))
        try:
            businesses = self._paged(
                f"{self.base_url}/me/businesses",
                user_token,
                {"fields": "id,name", "limit": "100"},
            )
        except FacebookAPIError:
            businesses = []
        for business in businesses:
            bid = str(business.get("id") or "").strip()
            if not bid:
                continue
            for edge in ("owned_pages", "client_pages", "assigned_pages"):
                try:
                    _merge(self._paged(
                        f"{self.base_url}/{bid}/{edge}",
                        user_token,
                        {"fields": self._PAGE_FIELDS, "limit": "100"},
                    ))
                except FacebookAPIError:
                    continue

        for pid, page in list(by_id.items()):
            if str(page.get("access_token") or "").strip():
                continue
            try:
                response = self.client.get(
                    f"{self.base_url}/{pid}",
                    params={"fields": self._PAGE_FIELDS},
                    headers=self._auth(user_token),
                )
                extra = self._decode(response)
                if isinstance(extra, dict):
                    _merge([extra])
            except FacebookAPIError:
                continue
        return list(by_id.values())

    def fetch_page(self, page_id: str, user_token: str) -> Dict[str, Any]:
        """Load one Page with the user token (page access token included when granted)."""
        pid = str(page_id or "").strip()
        if not pid:
            raise FacebookAPIError("Thiếu Page ID")
        response = self.client.get(
            f"{self.base_url}/{pid}",
            params={"fields": self._PAGE_FIELDS},
            headers=self._auth(user_token),
        )
        payload = self._decode(response)
        if not isinstance(payload, dict) or not payload.get("id"):
            raise FacebookAPIError("Facebook không trả về Fanpage")
        return payload

    def get_page_profile(self, page_id: str, page_token: str) -> Dict[str, Any]:
        response = self.client.get(
            f"{self.base_url}/{page_id}",
            params={
                "fields": (
                    "name,category,username,link,about,"
                    "fan_count,followers_count,picture.type(large){url,width,height}"
                )
            },
            headers=self._auth(page_token),
        )
        return self._decode(response)

    def download_page_picture(self, page_id: str, page_token: str) -> bytes:
        response = self.client.get(
            f"{self.base_url}/{page_id}/picture",
            params={"type": "large"},
            headers=self._auth(page_token),
        )
        if not response.is_success or not response.content:
            raise FacebookAPIError("Không tải được ảnh Fanpage")
        content_type = str(response.headers.get("content-type") or "")
        if "image" not in content_type and response.content[:3] != b"\xff\xd8\xff":
            raise FacebookAPIError("Facebook không trả về ảnh đại diện")
        return response.content

    def start_reel(self, page_id: str, page_token: str) -> Dict[str, str]:
        response = self.client.post(
            f"{self.base_url}/{page_id}/video_reels",
            data={"upload_phase": "start"},
            headers=self._auth(page_token),
        )
        payload = self._decode(response)
        video_id = str(payload.get("video_id") or "")
        upload_url = str(payload.get("upload_url") or "")
        if not video_id or not upload_url:
            raise FacebookAPIError("Facebook did not return video_id/upload_url")
        return {"video_id": video_id, "upload_url": upload_url}

    def upload_reel_binary(self, upload_url: str, page_token: str, source_path: str) -> None:
        file_size = os.path.getsize(source_path)
        with open(source_path, "rb") as video:
            response = self.client.post(
                upload_url,
                content=video,
                headers={
                    **self._auth(page_token, oauth=True),
                    "offset": "0",
                    "file_size": str(file_size),
                    "Content-Type": "application/octet-stream",
                },
                timeout=httpx.Timeout(1800.0, connect=30.0, read=1800.0, write=1800.0),
            )
        self._decode(response)

    def finish_reel(
        self,
        page_id: str,
        page_token: str,
        video_id: str,
        description: str,
    ) -> Dict[str, Any]:
        response = self.client.post(
            f"{self.base_url}/{page_id}/video_reels",
            data={
                "upload_phase": "finish",
                "video_id": video_id,
                "video_state": "PUBLISHED",
                "description": description or "",
            },
            headers=self._auth(page_token),
        )
        return self._decode(response)

    def get_video_status(self, video_id: str, page_token: str) -> Dict[str, Any]:
        response = self.client.get(
            f"{self.base_url}/{video_id}",
            params={"fields": "id,status,permalink_url"},
            headers=self._auth(page_token),
        )
        return self._decode(response)

    def get_page_insights(
        self,
        page_id: str,
        page_token: str,
        metrics: List[str],
        *,
        period: str = "day",
        since: Optional[int] = None,
        until: Optional[int] = None,
    ) -> Dict[str, Any]:
        names = [str(item).strip() for item in metrics if str(item).strip()]
        if not names:
            return {"data": []}
        params: Dict[str, str] = {
            "metric": ",".join(names),
            "period": period,
        }
        if since:
            params["since"] = str(int(since))
        if until:
            params["until"] = str(int(until))
        response = self.client.get(
            f"{self.base_url}/{page_id}/insights",
            params=params,
            headers=self._auth(page_token),
        )
        return self._decode(response)

    def list_published_posts(
        self,
        page_id: str,
        page_token: str,
        *,
        limit: int = 80,
    ) -> List[Dict[str, Any]]:
        fields = (
            "id,created_time,message,story,permalink_url,"
            "shares,likes.summary(true).limit(0),"
            "comments.summary(true).limit(0),"
            "reactions.summary(true).limit(0)"
        )
        params = {"fields": fields, "limit": "25"}
        cap = max(1, min(int(limit or 80), 200))
        try:
            return self._paged(
                f"{self.base_url}/{page_id}/published_posts",
                page_token,
                params,
                max_items=cap,
            )
        except FacebookAPIError:
            return self._paged(
                f"{self.base_url}/{page_id}/posts",
                page_token,
                params,
                max_items=cap,
            )

    def list_page_videos(
        self,
        page_id: str,
        page_token: str,
        *,
        limit: int = 80,
    ) -> List[Dict[str, Any]]:
        fields = (
            "id,created_time,title,description,views,length,permalink_url,"
            "likes.summary(true).limit(0),comments.summary(true).limit(0)"
        )
        cap = max(1, min(int(limit or 80), 200))
        return self._paged(
            f"{self.base_url}/{page_id}/videos",
            page_token,
            {"fields": fields, "limit": "25"},
            max_items=cap,
        )


def granted_page_ids(debug_data: Dict[str, Any]) -> List[str]:
    """Page IDs locked into a granular Facebook Login token (empty = all pages)."""
    found: List[str] = []
    seen = set()
    for item in debug_data.get("granular_scopes") or []:
        if not isinstance(item, dict):
            continue
        for pid in item.get("target_ids") or []:
            text = str(pid or "").strip()
            if text and text not in seen:
                seen.add(text)
                found.append(text)
    return found


def token_metadata(debug_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_id": str(debug_data.get("user_id") or ""),
        "scopes": list(debug_data.get("scopes") or []),
        "expires_at": _iso_from_timestamp(debug_data.get("expires_at")),
        "is_valid": bool(debug_data.get("is_valid")),
        "app_id": str(debug_data.get("app_id") or ""),
        "granted_page_ids": granted_page_ids(debug_data),
        "raw": json.dumps(debug_data, ensure_ascii=True),
    }
