"""Shopee / affiliate CTAs for Facebook Reels: first-line caption + post comment."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Tuple

from app.core.database import get_db_connection

_URL_IN_TEXT = re.compile(
    r"(?i)(?:https?://[^\s<>\"']+|(?:s\.)?(?:shopee\.vn|shopee\.com(?:\.\w+)?|shp\.ee|shope\.ee)/[^\s<>\"']+)"
)
_TRAILING_PUNCT = re.compile(r"[.,);\]>]+$")
_BLOCKED_SCHEMES = ("javascript:", "data:", "file:", "vbscript:")


def normalize_affiliate_url(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith(_BLOCKED_SCHEMES):
        return ""
    match = _URL_IN_TEXT.search(text)
    url = match.group(0) if match else text.split()[0]
    url = _TRAILING_PUNCT.sub("", url.strip("<>\"'"))
    if not url:
        return ""
    if url.lower().startswith(_BLOCKED_SCHEMES):
        return ""
    if not re.match(r"(?i)^https?://", url):
        if re.match(r"(?i)^(?:s\.)?(?:shopee\.vn|shopee\.com|shp\.ee|shope\.ee)/", url):
            url = f"https://{url}"
        else:
            return ""
    return url[:500]


def product_label(raw: str) -> str:
    name = re.sub(r"\s+", " ", str(raw or "").strip())[:80]
    return name or "sản phẩm"


def looks_like_single_product(url: str) -> bool:
    """Facebook affiliate banners need a single-item URL, not a shop/collection."""
    link = normalize_affiliate_url(url)
    if not link:
        return False
    lowered = link.lower()
    if re.search(r"shopee\.[^/]+/(?:shop|mall|search|list|collections?|cart|user)(?:/|$)", lowered):
        return False
    if re.search(r"(?:shp\.ee|shope\.ee|s\.shopee\.vn)/", lowered):
        return True
    if re.search(r"-i\.\d+\.\d+", lowered):
        return True
    if re.search(r"shopee\.[^/]+/product/\d+/\d+", lowered):
        return True
    return False


def caption_lead(url: str, product: str = "") -> str:
    link = normalize_affiliate_url(url)
    if not link:
        return ""
    cta = f"Bạn cần mua {product_label(product)} hãy ủng hộ shop qua link: {link}"
    return f"{link}\n\n{cta}"


def comment_body(url: str, product: str = "") -> str:
    link = normalize_affiliate_url(url)
    if not link:
        return ""
    return (
        f"{link}\n"
        f"Bạn cần mua {product_label(product)} hãy ủng hộ kênh qua: {link}"
    )


def prepend_affiliate_caption(caption: str, url: str, product: str = "") -> str:
    lead = caption_lead(url, product)
    body = str(caption or "").strip()
    if not lead:
        return body
    if not body:
        return lead
    if body.startswith(lead):
        return body
    link = normalize_affiliate_url(url)
    head = body[:700]
    if link and body.startswith(link):
        return body
    if link and link in head and "ủng hộ shop qua link" in head.lower():
        if not body.startswith(link):
            return f"{link}\n\n{body}"
        return body
    return f"{lead}\n\n{body}"


def affiliate_from_mapping(data: Any) -> Tuple[str, str]:
    if data is None:
        return "", ""
    if hasattr(data, "model_dump"):
        try:
            data = data.model_dump()
        except Exception:
            data = {}
    if not isinstance(data, dict):
        return "", ""
    url = normalize_affiliate_url(str(data.get("affiliate_link") or ""))
    product = str(data.get("affiliate_product") or "").strip()
    return url, product


def load_affiliate_from_job(db_path: str, job_id: str) -> Tuple[str, str]:
    job_id = str(job_id or "").strip()
    if not job_id or not db_path:
        return "", ""
    with get_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT reup_config FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    if not row:
        return "", ""
    raw = row["reup_config"] if "reup_config" in row.keys() else ""
    cfg: Dict[str, Any] = {}
    if isinstance(raw, dict):
        cfg = raw
    else:
        try:
            parsed = json.loads(raw or "{}")
            if isinstance(parsed, dict):
                cfg = parsed
        except Exception:
            cfg = {}
    return affiliate_from_mapping(cfg)


def persist_affiliate_on_job(
    db_path: str,
    job_id: str,
    *,
    url: str = "",
    product: str = "",
) -> Tuple[str, str]:
    """Merge a pasted affiliate URL into the stored job config. Empty values keep existing ones."""
    job_id = str(job_id or "").strip()
    incoming_url = normalize_affiliate_url(url)
    incoming_product = str(product or "").strip()[:80]
    if not job_id:
        return incoming_url, incoming_product
    with get_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT reup_config FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return incoming_url, incoming_product
        raw = row["reup_config"] or "{}"
        try:
            cfg = json.loads(raw) if not isinstance(raw, dict) else dict(raw)
        except Exception:
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        if incoming_url:
            cfg["affiliate_link"] = incoming_url
        if incoming_product:
            cfg["affiliate_product"] = incoming_product
        if incoming_url or incoming_product:
            from datetime import datetime, timezone

            conn.execute(
                "UPDATE jobs SET reup_config = ?, updated_at = ? WHERE job_id = ?",
                (
                    json.dumps(cfg, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                    job_id,
                ),
            )
            conn.commit()
    return affiliate_from_mapping(cfg)
