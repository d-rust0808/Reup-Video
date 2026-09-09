"""Scan Fanpages for Shopee cart / affiliate-link signals on recent posts."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from app.core.database import get_db_connection
from app.services.facebook_client import FacebookAPIError, FacebookClient
from app.services.secret_store import get_secret

logger = logging.getLogger(__name__)

_SHOPEE_LINK = re.compile(
    r"(?i)(?:https?://)?(?:[\w.-]*\.)?(?:shopee\.vn|shopee\.com(?:\.\w+)?|shp\.ee|shope\.ee)\b"
)


def looks_like_shopee(text: str) -> bool:
    return bool(_SHOPEE_LINK.search(str(text or "")))


def _post_blob(post: Dict[str, Any]) -> str:
    return " ".join(
        str(post.get(key) or "")
        for key in ("message", "story", "description", "permalink_url")
    )


def classify_page(page: Dict[str, Any], client: FacebookClient, token: str) -> Dict[str, Any]:
    page_id = str(page.get("page_id") or "")
    result = {
        "page_id": page_id,
        "name": page.get("name") or page_id,
        "followers": int(page.get("followers_count") or page.get("fan_count") or 0),
        "can_publish": bool(page.get("can_publish")),
        "status": "unknown",
        "label": "Chưa rõ",
        "detail": "",
        "shopee_posts": 0,
        "checked_posts": 0,
        "sample_link": "",
    }
    if not token:
        result.update(status="no_token", label="Thiếu token", detail="Token không đọc được Page này — tick page khi tạo token mới.")
        return result
    if not page.get("can_publish"):
        result["detail"] = "Page chưa có quyền CREATE_CONTENT."

    try:
        profile = client.get_page_profile(page_id, token) or {}
    except FacebookAPIError as exc:
        result.update(status="cannot_read", label="Không đọc được", detail=str(exc)[:180])
        return result

    followers = int(profile.get("followers_count") or profile.get("fan_count") or result["followers"] or 0)
    result["followers"] = followers
    result["name"] = str(profile.get("name") or result["name"])

    posts: List[Dict[str, Any]] = []
    try:
        posts = client.list_recent_posts(page_id, token, limit=12)
    except FacebookAPIError as exc:
        result["detail"] = str(exc)[:180]

    hits = []
    for post in posts:
        blob = _post_blob(post)
        if looks_like_shopee(blob):
            hits.append(post)
    result["checked_posts"] = len(posts)
    result["shopee_posts"] = len(hits)
    if hits:
        sample = _post_blob(hits[0])
        match = _SHOPEE_LINK.search(sample)
        result["sample_link"] = (match.group(0) if match else "")[:120]
        result.update(
            status="has_cart_signal",
            label="Có dấu hiệu giỏ hàng",
            detail=f"Thấy link Shopee trên {len(hits)}/{len(posts)} bài gần đây. Page này gắn giỏ được.",
        )
        return result

    if followers and followers < 100:
        result.update(
            status="weak_page",
            label="Page yếu / mới",
            detail=f"{followers} follow — Shopee/Facebook hay từ chối liên kết kênh khi page quá mới hoặc quá ít follow.",
        )
        return result
    if not posts:
        result.update(
            status="no_posts",
            label="Chưa đọc được bài",
            detail="Token đọc Page được nhưng chưa lấy được bài đăng gần đây.",
        )
        return result
    result.update(
        status="no_signal",
        label="Chưa thấy giỏ trên bài",
        detail=(
            f"Đã xem {len(posts)} bài gần đây, không có link shopee.vn / shp.ee. "
            "Có thể đã liên kết trên Shopee nhưng chưa dán link sản phẩm vào Reel."
        ),
    )
    return result


def scan_facebook_shop_status(db_path: str, *, max_pages: int = 80) -> Dict[str, Any]:
    with get_db_connection(db_path) as conn:
        connection = conn.execute(
            "SELECT graph_version FROM facebook_connections WHERE id = 'facebook_default'"
        ).fetchone()
        rows = conn.execute(
            """
            SELECT page_id, name, can_publish, page_token_ref, fan_count, followers_count
            FROM facebook_pages
            ORDER BY name COLLATE NOCASE
            """
        ).fetchall()
    if not connection:
        return {"ok": False, "pages": [], "message": "Chưa kết nối Facebook App"}

    graph_version = str(connection["graph_version"] or "v24.0")
    targets = [dict(row) for row in rows][:max_pages]
    results: List[Dict[str, Any]] = []

    def _one(page: Dict[str, Any]) -> Dict[str, Any]:
        token = get_secret(page.get("page_token_ref") or "") or ""
        client = FacebookClient(graph_version, timeout=10.0)
        try:
            return classify_page(page, client, token)
        finally:
            client.close()

    if not targets:
        return {"ok": True, "pages": [], "summary": {}, "message": "Chưa có Fanpage"}

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_one, page) for page in targets]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                logger.warning("shop scan failed: %s", exc)

    results.sort(key=lambda row: (row.get("status") != "has_cart_signal", str(row.get("name") or "").lower()))
    counts: Dict[str, int] = {}
    for row in results:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "ok": True,
        "pages": results,
        "summary": counts,
        "total": len(results),
        "message": (
            f"Có dấu hiệu giỏ hàng: {counts.get('has_cart_signal', 0)}/{len(results)} page. "
            f"Chưa thấy link Shopee: {counts.get('no_signal', 0)}. "
            f"Thiếu token: {counts.get('no_token', 0)}."
        ),
    }
