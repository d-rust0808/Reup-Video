"""Assign a finished reup job to Fanpage groups and enqueue publishing."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.database import get_db_connection
from app.api.channels import expand_group_channel_ids, record_publish_event
from app.services.affiliate_link import persist_affiliate_on_job, prepend_affiliate_caption
from app.services.facebook_distribution import enqueue_channel_video, wake_distribution_worker
from app.services.post_writer import find_job_transcript, write_facebook_posts
from app.services.tiktok_distribution import enqueue_tiktok_video, wake_tiktok_worker

logger = logging.getLogger(__name__)

_PLATFORM_ALIAS = {
    "youtube": "youtube_shorts",
    "yt": "youtube_shorts",
    "fb": "facebook",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _isfile(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0


def parse_hashtags(raw: Any) -> List[str]:
    if isinstance(raw, (list, tuple)):
        parts = [str(item or "") for item in raw]
    else:
        parts = re.split(r"[\s,]+", str(raw or ""))
    out: List[str] = []
    seen = set()
    for part in parts:
        tag = str(part or "").strip().lstrip("#")
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
        if len(out) >= 12:
            break
    return out


def _merge_hashtags(*groups: Any) -> List[str]:
    merged: List[str] = []
    seen = set()
    for group in groups:
        for tag in parse_hashtags(group):
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(tag)
            if len(merged) >= 12:
                return merged
    return merged


def job_media_files(job_id: str, db_path: str, output_dir: str) -> Dict[str, str]:
    files: Dict[str, str] = {}
    with get_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT output_file_path, status FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    if not row:
        raise FileNotFoundError("Không tìm thấy job")
    master = str(row["output_file_path"] or "")
    if not _isfile(master):
        master = os.path.join(output_dir, f"{job_id}.mp4")
    if _isfile(master):
        files["master"] = master
    for plat in ("facebook", "tiktok", "youtube_shorts"):
        path = os.path.join(output_dir, f"{job_id}.{plat}.mp4")
        if _isfile(path):
            files[plat] = path
    if not files:
        raise FileNotFoundError("Job chưa có file thành phẩm để đăng")
    return files


def path_for_platform(files: Dict[str, str], platform: str) -> str:
    plat = _PLATFORM_ALIAS.get(str(platform or "").lower(), str(platform or "").lower())
    return (
        files.get(plat)
        or files.get("master")
        or files.get("facebook")
        or files.get("tiktok")
        or files.get("youtube_shorts")
        or ""
    )


def publish_job_to_groups(
    db_path: str,
    job_id: str,
    *,
    group_ids: Optional[List[str]] = None,
    channel_ids: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
    affiliate_link: str = "",
    affiliate_product: str = "",
    title: str = "",
    caption: str = "",
    hashtags: Optional[Any] = None,
    intent: str = "",
) -> Dict[str, Any]:
    job_id = str(job_id or "").strip()
    if not job_id:
        raise ValueError("Thiếu mã job")
    groups = [str(g).strip() for g in (group_ids or []) if str(g).strip()]
    extra = [str(c).strip() for c in (channel_ids or []) if str(c).strip()]
    if not groups and not extra:
        raise ValueError("Chọn ít nhất một nhóm Fanpage để đăng")

    out_dir = output_dir or getattr(settings, "OUTPUT_DIR", "data/outputs")
    files = job_media_files(job_id, db_path, out_dir)

    with get_db_connection(db_path) as conn:
        job = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    if not job:
        raise FileNotFoundError("Không tìm thấy job")
    status = str(job["status"] or "").upper()
    if status not in {"COMPLETED", "DONE"}:
        raise ValueError("Chỉ đăng được video đã hoàn thành")

    group_origin = expand_group_channel_ids(db_path, groups) if groups else {}
    ordered: List[str] = []
    for cid in list(group_origin.keys()) + extra:
        if cid not in ordered:
            ordered.append(cid)
    if not ordered:
        raise ValueError("Nhóm đã chọn chưa có Fanpage nào")

    with get_db_connection(db_path) as conn:
        names: List[str] = []
        platforms: Dict[str, str] = {}
        valid: List[str] = []
        for cid in ordered:
            row = conn.execute(
                "SELECT channel_id, name, platform FROM channels WHERE channel_id = ? AND UPPER(status) != 'ARCHIVED'",
                (cid,),
            ).fetchone()
            if not row:
                continue
            valid.append(cid)
            names.append(row["name"] or cid)
            platforms[cid] = str(row["platform"] or "").lower()
        already = {
            str(row["channel_id"])
            for row in conn.execute(
                "SELECT channel_id FROM channel_videos WHERE job_id = ?",
                (job_id,),
            ).fetchall()
        }
        try:
            cfg = json.loads(job["reup_config"] or "{}")
        except Exception:
            cfg = {}
    if not valid:
        raise ValueError("Không còn kênh hợp lệ trong nhóm")

    aff_url, aff_product = persist_affiliate_on_job(
        db_path,
        job_id,
        url=affiliate_link,
        product=affiliate_product,
    )
    if aff_url:
        cfg["affiliate_link"] = aff_url
    if aff_product:
        cfg["affiliate_product"] = aff_product

    intent_text = str(intent or cfg.get("post_intent") or "").strip()
    brand = str(title or cfg.get("post_title") or "").strip()
    user_caption = str(caption or cfg.get("post_caption") or "").strip()
    user_tags = _merge_hashtags(hashtags, cfg.get("hashtags") or cfg.get("post_tags"))
    brief = find_job_transcript(job_id, out_dir)
    fresh_ids = [cid for cid in valid if cid not in already]
    skipped = [cid for cid in valid if cid in already]
    posts: List[Dict[str, Any]] = []
    if fresh_ids:
        page_names = [names[valid.index(cid)] for cid in fresh_ids]
        manual_only = bool((brand or user_caption or user_tags) and not intent_text)
        if manual_only:
            body = user_caption or brand
            posts = [
                {"title": brand, "caption": body, "hashtags": user_tags}
                for _ in page_names
            ]
        else:
            posts = write_facebook_posts(
                intent=intent_text,
                brand_title=brand,
                video_brief=brief,
                page_names=page_names,
            )
            if user_tags:
                for copy in posts:
                    copy["hashtags"] = _merge_hashtags(user_tags, copy.get("hashtags"))
            if brand:
                for copy in posts:
                    if not str(copy.get("title") or "").strip():
                        copy["title"] = brand

    now = _utc_now()
    assigned: List[Dict[str, str]] = []
    queued = 0
    for index, cid in enumerate(fresh_ids):
        copy = posts[index] if index < len(posts) else {}
        post_title = str(copy.get("title") or brand or "")
        post_caption = str(copy.get("caption") or user_caption or "")
        plat = str(platforms.get(cid) or "").lower()
        if aff_url and plat in {"facebook", "fb"}:
            post_caption = prepend_affiliate_caption(post_caption, aff_url, aff_product)
        tags = copy.get("hashtags") if isinstance(copy.get("hashtags"), list) else user_tags
        origin = group_origin.get(cid) or {}
        video_path = path_for_platform(files, platforms.get(cid) or "")
        if not _isfile(video_path):
            continue
        cv_id = f"cvid_{uuid.uuid4().hex[:8]}"
        with get_db_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO channel_videos (
                    id, channel_id, job_id, title, caption, tags,
                    publish_status, video_path, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'READY', ?, '', ?, ?)
                """,
                (
                    cv_id, cid, job_id, post_title, post_caption,
                    json.dumps(tags, ensure_ascii=False), video_path, now, now,
                ),
            )
            conn.commit()
        record_publish_event(
            db_path,
            job_id=job_id,
            channel_video_id=cv_id,
            channel_id=cid,
            group_id=origin.get("group_id") or "",
            group_name=origin.get("group_name") or "",
            title=post_title,
            caption=post_caption,
            status="QUEUED",
        )
        fb = enqueue_channel_video(db_path, cv_id, require_auto_publish=False)
        tk = enqueue_tiktok_video(db_path, cv_id, require_auto_publish=False)
        if fb or tk:
            queued += 1
        assigned.append({"channel_id": cid, "name": names[valid.index(cid)], "video_id": cv_id})

    wake_distribution_worker()
    wake_tiktok_worker()

    group_names: List[str] = []
    seen_g = set()
    for meta in group_origin.values():
        name = meta.get("group_name") or ""
        if name and name not in seen_g:
            seen_g.add(name)
            group_names.append(name)
    label = ", ".join(group_names) if group_names else "các Fanpage đã chọn"
    return {
        "ok": True,
        "job_id": job_id,
        "assigned": len(assigned),
        "queued": queued,
        "skipped": len(skipped),
        "group_names": group_names,
        "pages": assigned,
        "title": (posts[0]["title"] if posts else brand),
        "message": (
            f"Đã xếp đăng {len(assigned)} Fanpage nhóm {label}"
            + (f" (bỏ qua {len(skipped)} page đã có bài)" if skipped else "")
            + ". Check bản quyền Facebook trên bản nháp trước khi lên page; trùng thì huỷ cả nhóm."
        ),
    }
