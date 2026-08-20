"""
REST API Router for Channel Management and Video Content Categorization.
========================================================================
Target Path: app/api/channels.py
"""

import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List, Any, Dict
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.config import settings
from app.core.database import get_db_connection

logger = logging.getLogger(__name__)
router = APIRouter()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -----------------------------------------------------------------------------
# Pydantic Request & Response Schemas
# -----------------------------------------------------------------------------

class ChannelCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="Tên kênh")
    platform: str = Field(default="tiktok", description="Nền tảng (tiktok, youtube, facebook, douyin, kuaishou, instagram)")
    handle: Optional[str] = Field(default="", description="ID/Handle kênh (@username)")
    tags: Optional[List[str]] = Field(default_factory=list, description="Danh sách nhãn/tag gắn cho kênh")
    description: Optional[str] = Field(default="", description="Mô tả nội dung kênh")
    color: Optional[str] = Field(default="blue", description="Màu nhận diện kênh (blue, purple, emerald, rose, amber, cyan)")



class ChannelUpdateRequest(BaseModel):
    name: Optional[str] = None
    platform: Optional[str] = None
    handle: Optional[str] = None
    tags: Optional[List[str]] = None
    description: Optional[str] = None
    color: Optional[str] = None
    status: Optional[str] = None  # ACTIVE, ARCHIVED, PAUSED


class AssignVideoRequest(BaseModel):
    job_id: Optional[str] = Field(default="", description="Mã Job ID của video")
    video_path: Optional[str] = Field(default="", description="Đường dẫn file video thành phẩm")
    title: Optional[str] = Field(default="", description="Tiêu đề video chuẩn bị đăng")
    caption: Optional[str] = Field(default="", description="Nội dung bài đăng / Caption / Hashtags")
    tags: Optional[List[str]] = Field(default_factory=list, description="Tags/Hashtags cho video")
    publish_status: Optional[str] = Field(default="DRAFT", description="Trạng thái: DRAFT, READY, SCHEDULED, PUBLISHED")
    scheduled_at: Optional[str] = None
    notes: Optional[str] = Field(default="", description="Ghi chú nội bộ")


class UpdateVideoContentRequest(BaseModel):
    title: Optional[str] = None
    caption: Optional[str] = None
    tags: Optional[List[str]] = None
    publish_status: Optional[str] = None
    scheduled_at: Optional[str] = None
    published_at: Optional[str] = None
    notes: Optional[str] = None


# -----------------------------------------------------------------------------
# Channel Endpoints
# -----------------------------------------------------------------------------

@router.get("/channels")
async def list_channels():
    """
    Lists all content channels with video counts and parsed tags.
    """
    with get_db_connection(settings.DB_PATH) as conn:
        cursor = conn.execute("""
            SELECT c.*, COUNT(cv.id) as video_count,
                   SUM(CASE WHEN cv.publish_status = 'PUBLISHED' THEN 1 ELSE 0 END) as published_count,
                   SUM(CASE WHEN cv.publish_status = 'READY' THEN 1 ELSE 0 END) as ready_count
            FROM channels c
            LEFT JOIN channel_videos cv ON c.channel_id = cv.channel_id
            GROUP BY c.channel_id
            ORDER BY c.created_at DESC
        """)
        rows = cursor.fetchall()
        
        channels = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except Exception:
                d["tags"] = []
            channels.append(d)
            
        return {"channels": channels, "total": len(channels)}


@router.post("/channels", status_code=status.HTTP_201_CREATED)
async def create_channel(req: ChannelCreateRequest):
    """
    Creates a new content management channel.
    """
    channel_id = f"chan_{uuid.uuid4().hex[:8]}"
    now = _utc_now_iso()
    tags_json = json.dumps(req.tags or [], ensure_ascii=False)

    with get_db_connection(settings.DB_PATH) as conn:
        conn.execute("""
            INSERT INTO channels (
                channel_id, name, platform, handle, tags, description, color, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
        """, (
            channel_id, req.name.strip(), req.platform.lower().strip(),
            (req.handle or "").strip(), tags_json, (req.description or "").strip(),
            req.color or "blue", now, now
        ))
        conn.commit()

    return {
        "channel_id": channel_id,
        "name": req.name,
        "platform": req.platform,
        "tags": req.tags,
        "status": "ACTIVE",
        "message": "Kênh mới đã được tạo thành công!"
    }


@router.put("/channels/{channel_id}")
async def update_channel(channel_id: str, req: ChannelUpdateRequest):
    """
    Updates channel details, tags, or status.
    """
    with get_db_connection(settings.DB_PATH) as conn:
        cursor = conn.execute("SELECT * FROM channels WHERE channel_id = ?", (channel_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Kênh không tồn tại")

        updates = []
        vals = []
        if req.name is not None:
            updates.append("name = ?")
            vals.append(req.name.strip())
        if req.platform is not None:
            updates.append("platform = ?")
            vals.append(req.platform.lower().strip())
        if req.handle is not None:
            updates.append("handle = ?")
            vals.append(req.handle.strip())
        if req.tags is not None:
            updates.append("tags = ?")
            vals.append(json.dumps(req.tags, ensure_ascii=False))
        if req.description is not None:
            updates.append("description = ?")
            vals.append(req.description.strip())
        if req.color is not None:
            updates.append("color = ?")
            vals.append(req.color)
        if req.status is not None:
            updates.append("status = ?")
            vals.append(req.status.upper())

        if updates:
            updates.append("updated_at = ?")
            vals.append(_utc_now_iso())
            vals.append(channel_id)
            conn.execute(f"UPDATE channels SET {', '.join(updates)} WHERE channel_id = ?", vals)
            conn.commit()

    return {"channel_id": channel_id, "message": "Cập nhật kênh thành công"}


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str):
    """
    Deletes a channel and all its video assignments.
    """
    with get_db_connection(settings.DB_PATH) as conn:
        conn.execute("DELETE FROM channel_videos WHERE channel_id = ?", (channel_id,))
        conn.execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,))
        conn.commit()

    return {"channel_id": channel_id, "message": "Đã xóa kênh thành công"}


# -----------------------------------------------------------------------------
# Channel Video Content Endpoints
# -----------------------------------------------------------------------------

@router.get("/channels/{channel_id}/videos")
async def list_channel_videos(
    channel_id: str,
    status_filter: Optional[str] = Query(None, description="Lọc trạng thái: DRAFT, READY, SCHEDULED, PUBLISHED"),
    tag: Optional[str] = Query(None, description="Lọc theo tag")
):
    """
    Lists all videos assigned to a specific channel.
    """
    with get_db_connection(settings.DB_PATH) as conn:
        query = "SELECT * FROM channel_videos WHERE channel_id = ?"
        params = [channel_id]

        if status_filter:
            query += " AND UPPER(publish_status) = UPPER(?)"
            params.append(status_filter)

        query += " ORDER BY created_at DESC"
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

        videos = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except Exception:
                d["tags"] = []
            
            if tag and tag not in d["tags"]:
                continue
            videos.append(d)

        return {"channel_id": channel_id, "videos": videos, "total": len(videos)}


@router.post("/channels/{channel_id}/videos", status_code=status.HTTP_201_CREATED)
async def assign_video_to_channel(channel_id: str, req: AssignVideoRequest):
    """
    Assigns a video to a channel with custom title, caption, hashtags, and status.
    """
    video_content_id = f"cvid_{uuid.uuid4().hex[:8]}"
    now = _utc_now_iso()
    tags_json = json.dumps(req.tags or [], ensure_ascii=False)

    with get_db_connection(settings.DB_PATH) as conn:
        # Verify channel exists
        cursor = conn.execute("SELECT name FROM channels WHERE channel_id = ?", (channel_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Kênh không tồn tại")

        conn.execute("""
            INSERT INTO channel_videos (
                id, channel_id, job_id, title, caption, tags,
                publish_status, scheduled_at, video_path, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            video_content_id, channel_id, req.job_id or "",
            (req.title or "").strip(), (req.caption or "").strip(),
            tags_json, (req.publish_status or "DRAFT").upper(),
            req.scheduled_at, req.video_path or "", (req.notes or "").strip(),
            now, now
        ))
        conn.commit()

    return {
        "id": video_content_id,
        "channel_id": channel_id,
        "title": req.title,
        "message": "Đã thêm video vào kênh thành công!"
    }


@router.patch("/channel-videos/{video_id}")
async def update_channel_video(video_id: str, req: UpdateVideoContentRequest):
    """
    Updates video title, caption, tags, publish status, or notes.
    """
    with get_db_connection(settings.DB_PATH) as conn:
        cursor = conn.execute("SELECT * FROM channel_videos WHERE id = ?", (video_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Video không tồn tại trong kênh")

        updates = []
        vals = []
        if req.title is not None:
            updates.append("title = ?")
            vals.append(req.title.strip())
        if req.caption is not None:
            updates.append("caption = ?")
            vals.append(req.caption.strip())
        if req.tags is not None:
            updates.append("tags = ?")
            vals.append(json.dumps(req.tags, ensure_ascii=False))
        if req.publish_status is not None:
            updates.append("publish_status = ?")
            vals.append(req.publish_status.upper())
            if req.publish_status.upper() == "PUBLISHED" and not req.published_at:
                updates.append("published_at = ?")
                vals.append(_utc_now_iso())
        if req.scheduled_at is not None:
            updates.append("scheduled_at = ?")
            vals.append(req.scheduled_at)
        if req.published_at is not None:
            updates.append("published_at = ?")
            vals.append(req.published_at)
        if req.notes is not None:
            updates.append("notes = ?")
            vals.append(req.notes.strip())

        if updates:
            updates.append("updated_at = ?")
            vals.append(_utc_now_iso())
            vals.append(video_id)
            conn.execute(f"UPDATE channel_videos SET {', '.join(updates)} WHERE id = ?", vals)
            conn.commit()

    return {"id": video_id, "message": "Đã cập nhật nội dung video thành công"}


@router.delete("/channel-videos/{video_id}")
async def remove_video_from_channel(video_id: str):
    """
    Removes a video assignment from a channel.
    """
    with get_db_connection(settings.DB_PATH) as conn:
        conn.execute("DELETE FROM channel_videos WHERE id = ?", (video_id,))
        conn.commit()

    return {"id": video_id, "message": "Đã xóa video khỏi kênh"}
