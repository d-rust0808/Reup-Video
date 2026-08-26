"""Source-channel catalog: YouTube / Douyin / Kuaishou / XHS video checklists."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from app.config import settings
from app.core.database import get_db_connection
from app.scraper.channel import video_page_url

logger = logging.getLogger(__name__)

PLAYABLE_BYTES = 80_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tags_json(tags: Any) -> str:
    if isinstance(tags, str):
        parts = [t.strip().lstrip("#") for t in re.split(r"[,;#]+", tags) if t.strip()]
        return json.dumps(parts, ensure_ascii=False)
    if isinstance(tags, (list, tuple)):
        parts = [str(t).strip().lstrip("#") for t in tags if str(t).strip()]
        return json.dumps(parts, ensure_ascii=False)
    return "[]"


def _tags_list(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(t) for t in raw if str(t).strip()]
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    if isinstance(data, list):
        return [str(t) for t in data if str(t).strip()]
    return []


def disk_file_for(video_id: str) -> str:
    return os.path.join(settings.RAW_INPUT_DIR, f"{video_id}.mp4")


def is_downloaded(video_id: str) -> bool:
    path = disk_file_for(video_id)
    try:
        return os.path.isfile(path) and os.path.getsize(path) >= PLAYABLE_BYTES
    except OSError:
        return False


def native_ids_from_path(path: str) -> Set[str]:
    """Pull source video ids out of a file path or URL."""
    text = (path or "").strip()
    if not text:
        return set()
    found: Set[str] = set()
    stem = os.path.splitext(os.path.basename(text.split("?")[0]))[0]
    if stem:
        found.add(stem)
    for m in re.finditer(r"(?:^|[/_?=.-])([A-Za-z0-9_-]{11})(?=$|[/_?&.-])", text):
        found.add(m.group(1))
    return {item for item in found if item}


def published_native_ids(db_path: str) -> Set[str]:
    """Native source ids whose reup job actually landed on a Facebook Page."""
    found: Set[str] = set()
    with get_db_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT j.input_file_path AS path, j.source_url AS source_url
            FROM jobs j
            JOIN distribution_jobs d ON d.job_id = j.job_id
            WHERE UPPER(d.status) = 'PUBLISHED'
            """
        ).fetchall()
    for row in rows:
        found |= native_ids_from_path(row["path"] or "")
        found |= native_ids_from_path(row["source_url"] or "")
    return found


def sync_posted_from_jobs(db_path: str) -> int:
    """Write posted=1 for catalog rows that already went out as Facebook Reels."""
    ids = published_native_ids(db_path)
    if not ids:
        return 0
    now = _now()
    with get_db_connection(db_path) as conn:
        placeholders = ",".join("?" * len(ids))
        cur = conn.execute(
            f"""
            UPDATE content_videos
            SET posted = 1,
                posted_at = COALESCE(posted_at, ?),
                updated_at = ?
            WHERE posted = 0 AND video_id IN ({placeholders})
            """,
            (now, now, *ids),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def mark_posted_for_input(db_path: str, input_path: str) -> int:
    ids = native_ids_from_path(input_path)
    if not ids:
        return 0
    now = _now()
    with get_db_connection(db_path) as conn:
        placeholders = ",".join("?" * len(ids))
        cur = conn.execute(
            f"""
            UPDATE content_videos
            SET posted = 1,
                posted_at = COALESCE(posted_at, ?),
                updated_at = ?
            WHERE posted = 0 AND video_id IN ({placeholders})
            """,
            (now, now, *ids),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def _find_existing(conn, *, platform: str, handle: str, url: str) -> Optional[str]:
    platform = (platform or "").lower().strip()
    handle = (handle or "").strip()
    url = (url or "").strip()
    if handle:
        row = conn.execute(
            "SELECT channel_id FROM content_channels WHERE platform = ? AND handle = ? LIMIT 1",
            (platform, handle),
        ).fetchone()
        if row:
            return row["channel_id"]
    if url:
        row = conn.execute(
            "SELECT channel_id FROM content_channels WHERE platform = ? AND url = ? LIMIT 1",
            (platform, url),
        ).fetchone()
        if row:
            return row["channel_id"]
    return None


def upsert_source_catalog(
    db_path: str,
    *,
    profile: Optional[Dict[str, Any]] = None,
    platform: str = "",
    url: str = "",
    video_ids: Optional[Iterable[str]] = None,
    catalog: Optional[Iterable[Dict[str, Any]]] = None,
    tags: Any = None,
) -> str:
    """Create/update a source channel and merge its video catalog. Returns channel_id."""
    profile = profile or {}
    platform = (platform or profile.get("platform") or "").lower().strip()
    handle = str(
        profile.get("unique_id")
        or profile.get("sec_user_id")
        or profile.get("uid")
        or ""
    ).strip()
    url = str(url or profile.get("url") or "").strip()
    name = str(profile.get("nickname") or profile.get("name") or handle or url or "Kênh nguồn").strip()
    avatar = str(profile.get("avatar") or "").strip()
    notes = str(profile.get("signature") or profile.get("notes") or "").strip()
    now = _now()

    entries: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for row in catalog or []:
        vid = str((row or {}).get("video_id") or "").strip()
        if not vid or vid in seen:
            continue
        seen.add(vid)
        entries.append({
            "video_id": vid,
            "title": str((row or {}).get("title") or ""),
            "url": str((row or {}).get("url") or ""),
        })
    for vid in video_ids or []:
        text = str(vid or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        entries.append({"video_id": text, "title": "", "url": ""})

    with get_db_connection(db_path) as conn:
        channel_id = _find_existing(conn, platform=platform, handle=handle, url=url)
        if channel_id:
            conn.execute(
                """
                UPDATE content_channels
                SET name = CASE WHEN ? != '' THEN ? ELSE name END,
                    url = CASE WHEN ? != '' THEN ? ELSE url END,
                    handle = CASE WHEN ? != '' THEN ? ELSE handle END,
                    avatar = CASE WHEN ? != '' THEN ? ELSE avatar END,
                    notes = CASE WHEN ? != '' THEN ? ELSE notes END,
                    updated_at = ?
                WHERE channel_id = ?
                """,
                (
                    name, name, url, url, handle, handle, avatar, avatar,
                    notes, notes, now, channel_id,
                ),
            )
            if tags is not None:
                conn.execute(
                    "UPDATE content_channels SET tags = ? WHERE channel_id = ?",
                    (_tags_json(tags), channel_id),
                )
        else:
            channel_id = f"src_{uuid.uuid4().hex[:10]}"
            conn.execute(
                """
                INSERT INTO content_channels (
                    channel_id, name, platform, url, handle, tags, notes, avatar,
                    video_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_id, name[:120], platform, url[:500], handle[:160],
                    _tags_json(tags if tags is not None else []),
                    notes[:500], avatar[:500], len(entries), now, now,
                ),
            )

        for item in entries:
            vid = item["video_id"]
            title = item["title"][:300]
            item_url = item["url"][:500]
            if not item_url:
                item_url = video_page_url(vid, platform)[:500]
            row_id = f"srcv_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO content_videos (
                    id, channel_id, video_id, title, url, duration, posted, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)
                ON CONFLICT(channel_id, video_id) DO UPDATE SET
                    title = CASE WHEN excluded.title != '' THEN excluded.title ELSE content_videos.title END,
                    url = CASE WHEN excluded.url != '' THEN excluded.url ELSE content_videos.url END,
                    updated_at = excluded.updated_at
                """,
                (row_id, channel_id, vid, title, item_url, now, now),
            )

        count = conn.execute(
            "SELECT COUNT(*) AS n FROM content_videos WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()["n"]
        conn.execute(
            "UPDATE content_channels SET video_count = ?, updated_at = ? WHERE channel_id = ?",
            (int(count or 0), now, channel_id),
        )
        conn.commit()
    return channel_id


def _decorate_video(row: Dict[str, Any], published: Set[str]) -> Dict[str, Any]:
    d = dict(row)
    native = str(d.get("video_id") or "")
    downloaded = is_downloaded(native)
    auto_posted = native in published
    posted = bool(int(d.get("posted") or 0)) or auto_posted
    d["downloaded"] = downloaded
    d["posted"] = posted
    d["posted_auto"] = auto_posted
    d["file_path"] = os.path.abspath(disk_file_for(native)) if downloaded else ""
    d["stream_url"] = f"/api/v1/videos/stream/{native}" if downloaded else ""
    return d


def _status_match(item: Dict[str, Any], status: str) -> bool:
    posted = bool(item.get("posted"))
    downloaded = bool(item.get("downloaded"))
    if status in ("all", "", None):
        return True
    if status == "posted":
        return posted
    if status in ("unposted", "chua_dang"):
        return not posted
    if status in ("downloaded", "da_tai"):
        return downloaded
    if status in ("missing", "chua_tai"):
        return not downloaded
    return True


def stats_from_videos(items: List[Dict[str, Any]]) -> Dict[str, int]:
    posted = sum(1 for item in items if item.get("posted"))
    downloaded = sum(1 for item in items if item.get("downloaded"))
    total = len(items)
    return {
        "video_count": total,
        "posted_count": posted,
        "unposted_count": max(0, total - posted),
        "downloaded_count": downloaded,
    }


def load_channel_videos(db_path: str, channel_id: str) -> List[Dict[str, Any]]:
    sync_posted_from_jobs(db_path)
    published = published_native_ids(db_path)
    with get_db_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM content_videos
            WHERE channel_id = ?
            ORDER BY created_at DESC, title COLLATE NOCASE
            """,
            (channel_id,),
        ).fetchall()
    return [_decorate_video(dict(row), published) for row in rows]


def list_channels(db_path: str) -> List[Dict[str, Any]]:
    sync_posted_from_jobs(db_path)
    with get_db_connection(db_path) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM content_channels ORDER BY updated_at DESC"
        ).fetchall()]
    out = []
    for raw in rows:
        d = dict(raw)
        d["tags"] = _tags_list(d.get("tags"))
        d.update(stats_from_videos(load_channel_videos(db_path, d["channel_id"])))
        out.append(d)
    return out


def get_channel(db_path: str, channel_id: str) -> Optional[Dict[str, Any]]:
    with get_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM content_channels WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tags"] = _tags_list(d.get("tags"))
        return d


def update_channel(
    db_path: str,
    channel_id: str,
    *,
    name: Optional[str] = None,
    tags: Any = None,
    url: Optional[str] = None,
    notes: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    fields: List[str] = []
    values: List[Any] = []
    if name is not None:
        fields.append("name = ?")
        values.append(str(name)[:120])
    if tags is not None:
        fields.append("tags = ?")
        values.append(_tags_json(tags))
    if url is not None:
        fields.append("url = ?")
        values.append(str(url)[:500])
    if notes is not None:
        fields.append("notes = ?")
        values.append(str(notes)[:500])
    if not fields:
        return get_channel(db_path, channel_id)
    fields.append("updated_at = ?")
    values.append(_now())
    values.append(channel_id)
    with get_db_connection(db_path) as conn:
        cur = conn.execute(
            f"UPDATE content_channels SET {', '.join(fields)} WHERE channel_id = ?",
            values,
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return get_channel(db_path, channel_id)


def delete_channel(db_path: str, channel_id: str) -> bool:
    with get_db_connection(db_path) as conn:
        conn.execute("DELETE FROM content_videos WHERE channel_id = ?", (channel_id,))
        cur = conn.execute("DELETE FROM content_channels WHERE channel_id = ?", (channel_id,))
        conn.commit()
        return cur.rowcount > 0


def list_videos(
    db_path: str,
    channel_id: str,
    *,
    status: str = "all",
) -> List[Dict[str, Any]]:
    items = load_channel_videos(db_path, channel_id)
    return [item for item in items if _status_match(item, status)]


def channel_inventory(
    db_path: str,
    channel_id: str,
    *,
    status: str = "all",
) -> Dict[str, Any]:
    items = load_channel_videos(db_path, channel_id)
    stats = stats_from_videos(items)
    return {
        "videos": [item for item in items if _status_match(item, status)],
        **stats,
    }


def set_posted(db_path: str, video_pk: str, posted: bool) -> Optional[Dict[str, Any]]:
    now = _now()
    with get_db_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM content_videos WHERE id = ?",
            (video_pk,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            """
            UPDATE content_videos
            SET posted = ?, posted_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (1 if posted else 0, now if posted else None, now, video_pk),
        )
        conn.commit()
        data = dict(row)
    data["posted"] = bool(posted)
    data["posted_at"] = now if posted else None
    data["downloaded"] = is_downloaded(data["video_id"])
    return data


def pending_download_ids(db_path: str, channel_id: str, video_ids: Optional[List[str]] = None) -> List[str]:
    with get_db_connection(db_path) as conn:
        if video_ids:
            rows = conn.execute(
                f"""
                SELECT video_id FROM content_videos
                WHERE channel_id = ?
                  AND video_id IN ({",".join("?" * len(video_ids))})
                """,
                (channel_id, *video_ids),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT video_id FROM content_videos WHERE channel_id = ?",
                (channel_id,),
            ).fetchall()
    return [r["video_id"] for r in rows if not is_downloaded(r["video_id"])]



