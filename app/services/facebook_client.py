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

    def list_pages(self, user_token: str) -> List[Dict[str, Any]]:
        url: Optional[str] = f"{self.base_url}/me/accounts"
        params: Optional[Dict[str, str]] = {
            "fields": "id,name,access_token,category,tasks,picture{url}",
            "limit": "100",
        }
        pages: List[Dict[str, Any]] = []
        while url:
            response = self.client.get(url, params=params, headers=self._auth(user_token))
            payload = self._decode(response)
            data = payload.get("data") or []
            pages.extend(item for item in data if isinstance(item, dict))
            next_url = ((payload.get("paging") or {}).get("next") or "").strip()
            url = next_url or None
            params = None
        return pages

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


def token_metadata(debug_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_id": str(debug_data.get("user_id") or ""),
        "scopes": list(debug_data.get("scopes") or []),
        "expires_at": _iso_from_timestamp(debug_data.get("expires_at")),
        "is_valid": bool(debug_data.get("is_valid")),
        "app_id": str(debug_data.get("app_id") or ""),
        "raw": json.dumps(debug_data, ensure_ascii=True),
    }
