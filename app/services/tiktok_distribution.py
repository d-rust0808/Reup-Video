"""Durable TikTok Direct Post outbox and background publisher."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core.database import get_db_connection
from app.services.secret_store import get_secret, set_secret
from app.services.tiktok_client import (
    TikTokAPIError,
    TikTokClient,
    pick_privacy,
    plan_chunks,
)


logger = logging.getLogger(__name__)
PROVIDER = "tiktok"

_active_worker: Optional["TikTokDistributionWorker"] = None


def wake_tiktok_worker() -> None:
    worker = _active_worker
    if worker is not None:
        worker.wake()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime] = None) -> str:
    return (value or _now()).isoformat()


def _caption(row: Dict[str, Any]) -> str:
    title = str(row.get("title") or "").strip()
    caption = str(row.get("caption") or "").strip()
    try:
        tags = json.loads(row.get("tags") or "[]")
    except Exception:
        tags = []
    hashtags = " ".join(
        f"#{str(tag).strip().lstrip('#').replace(' ', '')}"
        for tag in tags
        if str(tag).strip()
    )
    parts = [part for part in (title, caption, hashtags) if part]
    if title and caption and caption.startswith(title):
        parts = [caption, hashtags] if hashtags else [caption]
    return "\n\n".join(part for part in parts if part)


def _validate_tiktok_file(source_path: str) -> None:
    if not os.path.isfile(source_path) or os.path.getsize(source_path) <= 0:
        raise TikTokAPIError("Video file is missing or empty")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return
    result = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height",
            "-of", "json",
            source_path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise TikTokAPIError("ffprobe cannot read the TikTok video")
    try:
        stream = (json.loads(result.stdout or "{}").get("streams") or [])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        codec = str(stream.get("codec_name") or "").lower()
    except Exception as error:
        raise TikTokAPIError("Invalid TikTok video metadata") from error
    if codec not in {"h264", "hevc", "h265", "vp8", "vp9"}:
        raise TikTokAPIError(f"TikTok requires H.264/H.265, received {codec or 'unknown'}")
    if min(width, height) < 360:
        raise TikTokAPIError(f"TikTok video too small: {width}x{height}")
    if height < width:
        raise TikTokAPIError(f"TikTok Direct Post cần khung dọc 9:16, nhận {width}x{height}")


def enqueue_tiktok_video(
    db_path: str,
    channel_video_id: str,
    *,
    require_auto_publish: bool = False,
    reset_failed: bool = False,
) -> Optional[str]:
    with get_db_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT cv.*, c.platform, cd.destination_id, cd.auto_publish,
                   ta.can_publish
            FROM channel_videos cv
            JOIN channels c ON c.channel_id = cv.channel_id
            JOIN channel_destinations cd
              ON cd.channel_id = cv.channel_id AND cd.provider = 'tiktok'
            JOIN tiktok_accounts ta ON ta.open_id = cd.destination_id
            WHERE cv.id = ? AND LOWER(c.platform) = 'tiktok'
            """,
            (channel_video_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        if require_auto_publish and not int(data.get("auto_publish") or 0):
            return None
        if require_auto_publish and str(data.get("publish_status") or "").upper() != "READY":
            return None
        if not int(data.get("can_publish") or 0):
            return None
        source_path = str(data.get("video_path") or "")
        if not source_path or not os.path.isfile(source_path):
            return None

        existing = conn.execute(
            """
            SELECT id, status FROM distribution_jobs
            WHERE channel_video_id = ? AND provider = ? AND destination_id = ?
            """,
            (channel_video_id, PROVIDER, data["destination_id"]),
        ).fetchone()
        now = _iso()
        if existing:
            if reset_failed and str(existing["status"]).upper() == "FAILED":
                conn.execute(
                    """
                    UPDATE distribution_jobs
                    SET status = 'PENDING', attempts = 0, next_attempt_at = ?,
                        lease_until = NULL, last_error = '', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, existing["id"]),
                )
                conn.commit()
            distribution_id = str(existing["id"])
        else:
            import uuid

            distribution_id = f"dist_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO distribution_jobs (
                    id, channel_video_id, job_id, provider, destination_id,
                    source_path, caption, status, next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)
                """,
                (
                    distribution_id,
                    channel_video_id,
                    data.get("job_id") or "",
                    PROVIDER,
                    data["destination_id"],
                    source_path,
                    _caption(data),
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
    wake_tiktok_worker()
    return distribution_id


class TikTokDistributionWorker:
    def __init__(self, db_path: str, poll_interval: float = 3.0) -> None:
        self.db_path = db_path
        self.poll_interval = poll_interval
        self._task: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()
        self._stopping = False

    async def start(self) -> None:
        global _active_worker
        self._stopping = False
        _active_worker = self
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._run(), name="tiktok-distribution-worker")

    async def stop(self) -> None:
        global _active_worker
        self._stopping = True
        self._wake.set()
        if _active_worker is self:
            _active_worker = None
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    def _claim_next(self) -> Optional[Dict[str, Any]]:
        now = _iso()
        lease_until = _iso(_now() + timedelta(minutes=35))
        with get_db_connection(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM distribution_jobs
                WHERE provider = 'tiktok'
                  AND status IN ('PENDING','FAILED','STARTING','UPLOADING','FINISHING','PROCESSING')
                  AND attempts < max_attempts
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                  AND (lease_until IS NULL OR lease_until < ?)
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if not row:
                conn.commit()
                return None
            updated = conn.execute(
                """
                UPDATE distribution_jobs
                SET lease_until = ?, updated_at = ?
                WHERE id = ? AND (lease_until IS NULL OR lease_until < ?)
                """,
                (lease_until, now, row["id"], now),
            )
            conn.commit()
            return dict(row) if updated.rowcount == 1 else None

    def _update(self, distribution_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = _iso()
        keys = list(fields)
        values = [fields[key] for key in keys]
        with get_db_connection(self.db_path) as conn:
            conn.execute(
                f"UPDATE distribution_jobs SET {', '.join(f'{key} = ?' for key in keys)} WHERE id = ?",
                (*values, distribution_id),
            )
            conn.commit()

    def _account(self, open_id: str) -> Dict[str, Any]:
        with get_db_connection(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT ta.*, tc.client_key, tc.client_secret_ref
                FROM tiktok_accounts ta
                JOIN tiktok_connections tc ON tc.id = ta.connection_id
                WHERE ta.open_id = ? AND ta.can_publish = 1 AND tc.status = 'CONNECTED'
                """,
                (open_id,),
            ).fetchone()
        if not row:
            raise TikTokAPIError("Tài khoản TikTok chưa kết nối hoặc thiếu quyền video.publish")
        data = dict(row)
        token = get_secret(data["access_token_ref"])
        if not token:
            raise TikTokAPIError("Thiếu TikTok access token trong kho bí mật")
        data["access_token"] = token
        data["refresh_token"] = get_secret(data.get("refresh_token_ref") or "") or ""
        data["client_secret"] = get_secret(data.get("client_secret_ref") or "") or ""
        return data

    def _refresh_if_needed(self, account: Dict[str, Any], client: TikTokClient) -> Dict[str, Any]:
        expires = str(account.get("token_expires_at") or "")
        soon = False
        if expires:
            try:
                soon = datetime.fromisoformat(expires) - _now() <= timedelta(hours=2)
            except Exception:
                soon = True
        if not soon and account.get("access_token"):
            return account
        refresh = account.get("refresh_token") or ""
        if not refresh or not account.get("client_key") or not account.get("client_secret"):
            if soon:
                raise TikTokAPIError("TikTok token hết hạn, cần ủy quyền lại", code="access_token_invalid")
            return account
        if not soon:
            return account
        payload = client.refresh_token(
            client_key=account["client_key"],
            client_secret=account["client_secret"],
            refresh_token=refresh,
        )
        access = str(payload.get("access_token") or "")
        if not access:
            raise TikTokAPIError("Không làm mới được TikTok token")
        new_refresh = str(payload.get("refresh_token") or refresh)
        expires_in = int(payload.get("expires_in") or 86400)
        expires_at = _iso(_now() + timedelta(seconds=max(60, expires_in - 60)))
        set_secret(account["access_token_ref"], access)
        if account.get("refresh_token_ref"):
            set_secret(account["refresh_token_ref"], new_refresh)
        with get_db_connection(self.db_path) as conn:
            conn.execute(
                """
                UPDATE tiktok_accounts
                SET token_expires_at = ?, scopes = ?, updated_at = ?
                WHERE open_id = ?
                """,
                (
                    expires_at,
                    json.dumps(str(payload.get("scope") or "").split(","), ensure_ascii=False),
                    _iso(),
                    account["open_id"],
                ),
            )
            conn.commit()
        account["access_token"] = access
        account["refresh_token"] = new_refresh
        account["token_expires_at"] = expires_at
        return account

    @staticmethod
    def _is_published(payload: Dict[str, Any]) -> bool:
        status = str(payload.get("status") or "").upper()
        return status in {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"}

    @staticmethod
    def _is_failed(payload: Dict[str, Any]) -> bool:
        status = str(payload.get("status") or "").upper()
        return status in {"FAILED", "PUBLISH_FAILED", "ERROR"}

    def _mark_published(self, row: Dict[str, Any], payload: Dict[str, Any]) -> None:
        now = _iso()
        publish_id = str(row.get("upload_video_id") or payload.get("publish_id") or "")
        permalink = str(payload.get("publicaly_available_post_id") or payload.get("publicly_available_post_id") or "")
        if permalink and not permalink.startswith("http"):
            permalink = f"https://www.tiktok.com/@/video/{permalink}"
        with get_db_connection(self.db_path) as conn:
            conn.execute(
                """
                UPDATE distribution_jobs
                SET status = 'PUBLISHED', upload_phase = 'PUBLISHED', lease_until = NULL,
                    remote_media_id = ?, permalink = ?, last_error = '',
                    published_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (publish_id, permalink, now, now, row["id"]),
            )
            conn.execute(
                """
                UPDATE channel_videos
                SET publish_status = 'PUBLISHED', published_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, row["channel_video_id"]),
            )
            conn.execute(
                """
                UPDATE publish_log
                SET status = 'PUBLISHED', permalink = ?, updated_at = ?
                WHERE channel_video_id = ?
                """,
                (permalink, now, row["channel_video_id"]),
            )
            conn.commit()
        try:
            from app.services.content_catalog import mark_posted_for_input

            job_id = str(row.get("job_id") or "")
            input_path = ""
            if job_id:
                with get_db_connection(self.db_path) as conn:
                    job = conn.execute(
                        "SELECT input_file_path FROM jobs WHERE job_id = ?",
                        (job_id,),
                    ).fetchone()
                if job:
                    input_path = str(job["input_file_path"] or "")
            mark_posted_for_input(self.db_path, input_path)
        except Exception:
            logger.warning("Could not mark source catalog video as posted", exc_info=True)

    def _process_sync(self, row: Dict[str, Any]) -> None:
        account = self._account(row["destination_id"])
        client = TikTokClient(timeout=60.0)
        try:
            account = self._refresh_if_needed(account, client)
            source_path = str(row.get("source_path") or "")
            _validate_tiktok_file(source_path)
            publish_id = str(row.get("upload_video_id") or "")
            phase = str(row.get("upload_phase") or "").upper()

            if (
                str(row.get("status") or "").upper() == "PROCESSING"
                or phase in {"UPLOADED", "FINISHED"}
            ) and publish_id:
                payload = client.fetch_status(account["access_token"], publish_id)
                if self._is_published(payload):
                    self._mark_published(row, payload)
                elif self._is_failed(payload):
                    fail = payload.get("fail_reason") or payload.get("status")
                    raise TikTokAPIError(f"TikTok từ chối video: {fail}")
                else:
                    self._update(
                        row["id"],
                        lease_until=None,
                        next_attempt_at=_iso(_now() + timedelta(seconds=20)),
                    )
                return

            attempts = int(row.get("attempts") or 0) + 1
            row["attempts"] = attempts
            self._update(row["id"], attempts=attempts)

            if not publish_id:
                self._update(row["id"], status="STARTING")
                creator = client.creator_info(account["access_token"])
                privacy = pick_privacy(creator.get("privacy_level_options") or [])
                size = os.path.getsize(source_path)
                chunk_size, chunk_count = plan_chunks(size)
                started = client.init_direct_post(
                    account["access_token"],
                    title=str(row.get("caption") or ""),
                    privacy_level=privacy,
                    video_size=size,
                    chunk_size=chunk_size,
                    total_chunk_count=chunk_count,
                )
                publish_id = str(started["publish_id"])
                upload_url = str(started["upload_url"])
                self._update(
                    row["id"],
                    status="UPLOADING",
                    upload_phase="STARTED",
                    upload_video_id=publish_id,
                    upload_url=upload_url,
                    remote_post_id=privacy,
                )
                client.upload_file(upload_url, source_path, chunk_size=chunk_size, total_size=size)
                self._update(
                    row["id"],
                    status="PROCESSING",
                    upload_phase="UPLOADED",
                    lease_until=None,
                    next_attempt_at=_iso(_now() + timedelta(seconds=8)),
                    last_error="",
                )
                return

            upload_url = str(row.get("upload_url") or "")
            if phase == "STARTED" and upload_url:
                size = os.path.getsize(source_path)
                chunk_size, _count = plan_chunks(size)
                client.upload_file(upload_url, source_path, chunk_size=chunk_size, total_size=size)
                self._update(
                    row["id"],
                    status="PROCESSING",
                    upload_phase="UPLOADED",
                    lease_until=None,
                    next_attempt_at=_iso(_now() + timedelta(seconds=8)),
                    last_error="",
                )
        finally:
            client.close()

    def _record_error(self, row: Dict[str, Any], error: Exception) -> None:
        attempts = max(1, int(row.get("attempts") or 0))
        retryable = not isinstance(error, TikTokAPIError) or error.retryable
        token_bad = isinstance(error, TikTokAPIError) and error.code in {
            "access_token_invalid",
            "scope_not_authorized",
        }
        delay = min(900, 15 * (2 ** min(attempts, 6)))
        next_attempt = _iso(_now() + timedelta(seconds=delay)) if retryable else None
        self._update(
            row["id"],
            status="FAILED",
            lease_until=None,
            next_attempt_at=next_attempt,
            last_error=str(error)[:2000],
            attempts=attempts if retryable else int(row.get("max_attempts") or 5),
        )
        if token_bad:
            with get_db_connection(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE tiktok_connections
                    SET status = 'EXPIRED', last_error = ?, updated_at = ?
                    WHERE id = (
                        SELECT connection_id FROM tiktok_accounts WHERE open_id = ?
                    )
                    """,
                    (str(error)[:1000], _iso(), row["destination_id"]),
                )
                conn.commit()

    async def _run(self) -> None:
        while not self._stopping:
            row = self._claim_next()
            if row:
                try:
                    await asyncio.to_thread(self._process_sync, row)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    logger.warning("TikTok distribution %s failed: %s", row["id"], error)
                    await asyncio.to_thread(self._record_error, row, error)
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass
