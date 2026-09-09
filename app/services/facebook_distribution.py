"""Durable Facebook Reels outbox and background publisher."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core.database import get_db_connection
from app.services.affiliate_link import (
    comment_body,
    load_affiliate_from_job,
    prepend_affiliate_caption,
)
from app.services.facebook_client import FacebookAPIError, FacebookClient
from app.services.facebook_copyright import (
    COPYRIGHT_WAIT_SECONDS,
    CopyrightVerdict,
    evaluate_copyright,
)
from app.services.secret_store import get_secret


logger = logging.getLogger(__name__)
PROVIDER = "facebook"

_active_worker: Optional["FacebookDistributionWorker"] = None


def absolute_facebook_permalink(
    permalink: str = "",
    *,
    video_id: str = "",
    page_id: str = "",
) -> str:
    """Turn Graph's relative /reel/ID into a clickable facebook.com URL."""
    text = str(permalink or "").strip()
    if text.startswith(("http://", "https://")):
        return text
    if text.startswith("facebook.com/") or text.startswith("www.facebook.com/"):
        return f"https://{text.lstrip('/')}"
    if text.startswith("/"):
        return f"https://www.facebook.com{text}"
    vid = str(video_id or "").strip()
    if vid:
        return f"https://www.facebook.com/reel/{vid}"
    pid = str(page_id or "").strip()
    if pid and text:
        return f"https://www.facebook.com/{pid}/videos/{text}"
    return text


def wake_distribution_worker() -> None:
    worker = _active_worker
    if worker is not None:
        worker.wake()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime] = None) -> str:
    return (value or _now()).isoformat()


def _caption(
    row: Dict[str, Any],
    *,
    affiliate_url: str = "",
    affiliate_product: str = "",
) -> str:
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
    body = "\n\n".join(part for part in (caption, hashtags) if part)
    return prepend_affiliate_caption(body, affiliate_url, affiliate_product)


def _validate_reel_file(source_path: str) -> None:
    if not os.path.isfile(source_path) or os.path.getsize(source_path) <= 0:
        raise FacebookAPIError("Video file is missing or empty")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height",
            "-of",
            "json",
            source_path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise FacebookAPIError("ffprobe cannot read the Reel video")
    try:
        stream = (json.loads(result.stdout or "{}").get("streams") or [])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        codec = str(stream.get("codec_name") or "").lower()
    except Exception as error:
        raise FacebookAPIError("Invalid video metadata") from error
    if codec != "h264":
        raise FacebookAPIError(f"Facebook Reel requires H.264 output, received {codec or 'unknown'}")
    ratio = (width / height) if height else 0
    if not width or not height or height <= width or not 0.5 <= ratio <= 0.8:
        raise FacebookAPIError(f"Facebook Reel must be portrait 9:16-compatible, received {width}x{height}")


def enqueue_channel_video(
    db_path: str,
    channel_video_id: str,
    *,
    require_auto_publish: bool = False,
    reset_failed: bool = False,
) -> Optional[str]:
    """Create one idempotent publish job for the video's bound Facebook Page."""
    with get_db_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT cv.*, c.platform, cd.destination_id, cd.auto_publish,
                   fp.can_publish
            FROM channel_videos cv
            JOIN channels c ON c.channel_id = cv.channel_id
            JOIN channel_destinations cd
              ON cd.channel_id = cv.channel_id AND cd.provider = 'facebook'
            JOIN facebook_pages fp ON fp.page_id = cd.destination_id
            WHERE cv.id = ? AND LOWER(c.platform) = 'facebook'
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

        aff_url, aff_product = load_affiliate_from_job(db_path, str(data.get("job_id") or ""))
        existing = conn.execute(
            """
            SELECT id, status FROM distribution_jobs
            WHERE channel_video_id = ? AND provider = ? AND destination_id = ?
            """,
            (channel_video_id, PROVIDER, data["destination_id"]),
        ).fetchone()
        now = _iso()
        if existing:
            existing_status = str(existing["status"]).upper()
            if reset_failed and existing_status in {"FAILED", "COPYRIGHT_BLOCKED"}:
                if existing_status == "COPYRIGHT_BLOCKED":
                    conn.execute(
                        """
                        UPDATE distribution_jobs
                        SET status = 'PENDING', attempts = 0, next_attempt_at = ?,
                            lease_until = NULL, last_error = '',
                            upload_phase = '', upload_video_id = '', upload_url = '',
                            remote_media_id = '', remote_post_id = '', updated_at = ?
                        WHERE id = ?
                        """,
                        (now, now, existing["id"]),
                    )
                else:
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
                    _caption(data, affiliate_url=aff_url, affiliate_product=aff_product),
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
    wake_distribution_worker()
    return distribution_id


class FacebookDistributionWorker:
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
            self._task = asyncio.create_task(self._run(), name="facebook-distribution-worker")

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
                SELECT * FROM distribution_jobs d
                WHERE d.provider = 'facebook'
                  AND d.status IN ('PENDING','FAILED','STARTING','UPLOADING','FINISHING','PROCESSING')
                  AND d.attempts < d.max_attempts
                  AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= ?)
                  AND (d.lease_until IS NULL OR d.lease_until < ?)
                  AND (
                    d.job_id IS NULL OR d.job_id = ''
                    OR NOT EXISTS (
                      SELECT 1 FROM distribution_jobs s
                      WHERE s.provider = 'facebook'
                        AND s.job_id = d.job_id
                        AND s.id != d.id
                        AND s.status IN ('STARTING','UPLOADING','FINISHING','PROCESSING')
                    )
                  )
                ORDER BY d.created_at ASC
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

    def _connection(self, page_id: str) -> Dict[str, Any]:
        with get_db_connection(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT fp.page_token_ref, fc.graph_version, fc.id AS connection_id
                FROM facebook_pages fp
                JOIN facebook_connections fc ON fc.id = fp.connection_id
                WHERE fp.page_id = ? AND fp.can_publish = 1 AND fc.status = 'CONNECTED'
                """,
                (page_id,),
            ).fetchone()
        if not row:
            raise FacebookAPIError("Facebook Page is not connected or cannot publish")
        data = dict(row)
        page_token = get_secret(data["page_token_ref"])
        if not page_token:
            raise FacebookAPIError("Facebook Page token is missing from the credential store")
        data["page_token"] = page_token
        return data

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

    @staticmethod
    def _is_published(payload: Dict[str, Any]) -> bool:
        status = payload.get("status") or {}
        if not isinstance(status, dict):
            return False
        video_status = str(status.get("video_status") or "").lower()
        publishing = status.get("publishing_phase") or {}
        publishing_status = str(
            publishing.get("status") if isinstance(publishing, dict) else ""
        ).lower()
        return video_status in {"ready", "published"} or publishing_status in {"complete", "completed"}

    @staticmethod
    def _copyright_since(row: Dict[str, Any]) -> Optional[datetime]:
        raw = str(row.get("upload_url") or "")
        if not raw.startswith("copyright_since:"):
            return None
        try:
            started = datetime.fromisoformat(raw.split(":", 1)[1])
        except Exception:
            return None
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return started

    def _copyright_wait_expired(self, row: Dict[str, Any]) -> bool:
        started = self._copyright_since(row)
        if started is None:
            return False
        return (_now() - started).total_seconds() >= COPYRIGHT_WAIT_SECONDS

    def _job_has_published_sibling(self, row: Dict[str, Any]) -> bool:
        job_id = str(row.get("job_id") or "").strip()
        if not job_id:
            return False
        with get_db_connection(self.db_path) as conn:
            hit = conn.execute(
                """
                SELECT 1 FROM distribution_jobs
                WHERE provider = 'facebook' AND job_id = ? AND status = 'PUBLISHED'
                LIMIT 1
                """,
                (job_id,),
            ).fetchone()
        return bool(hit)

    def _caption_for(self, row: Dict[str, Any]) -> str:
        aff_url, aff_product = load_affiliate_from_job(
            self.db_path, str(row.get("job_id") or "")
        )
        return prepend_affiliate_caption(
            str(row.get("caption") or ""), aff_url, aff_product
        )

    def _block_job_copyright(
        self,
        row: Dict[str, Any],
        summary: str,
        client: Optional[FacebookClient] = None,
        page_token: str = "",
    ) -> None:
        video_id = str(row.get("upload_video_id") or row.get("remote_media_id") or "").strip()
        if client and page_token and video_id:
            try:
                client.delete_object(video_id, page_token)
            except Exception:
                logger.warning(
                    "Could not delete copyright-blocked Facebook draft %s",
                    video_id,
                    exc_info=True,
                )
        now = _iso()
        message = str(summary or "Facebook Rights Manager chặn bản quyền").strip()[:2000]
        job_id = str(row.get("job_id") or "").strip()
        with get_db_connection(self.db_path) as conn:
            if job_id:
                targets = conn.execute(
                    """
                    SELECT id, channel_video_id FROM distribution_jobs
                    WHERE provider = 'facebook' AND job_id = ?
                      AND status NOT IN ('PUBLISHED','DELETED','CANCELLED','COPYRIGHT_BLOCKED')
                    """,
                    (job_id,),
                ).fetchall()
            else:
                targets = conn.execute(
                    "SELECT id, channel_video_id FROM distribution_jobs WHERE id = ?",
                    (row["id"],),
                ).fetchall()
            for item in targets:
                conn.execute(
                    """
                    UPDATE distribution_jobs
                    SET status = 'COPYRIGHT_BLOCKED', upload_phase = 'BLOCKED',
                        lease_until = NULL, next_attempt_at = NULL,
                        last_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (message, now, item["id"]),
                )
                conn.execute(
                    """
                    UPDATE channel_videos
                    SET publish_status = 'COPYRIGHT_BLOCKED', notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (message, now, item["channel_video_id"]),
                )
                conn.execute(
                    """
                    UPDATE publish_log
                    SET status = 'COPYRIGHT_BLOCKED', notes = ?, updated_at = ?
                    WHERE channel_video_id = ?
                    """,
                    (message, now, item["channel_video_id"]),
                )
            conn.commit()

    def _wait_copyright(self, row: Dict[str, Any], verdict: CopyrightVerdict) -> None:
        self._update(
            row["id"],
            lease_until=None,
            next_attempt_at=_iso(_now() + timedelta(seconds=20)),
            last_error=verdict.summary or "Facebook đang quét bản quyền (nháp, chưa lên page)",
        )

    def _finish_as(
        self,
        row: Dict[str, Any],
        client: FacebookClient,
        page_token: str,
        video_id: str,
        video_state: str,
        *,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        description = self._caption_for(row)
        finished = client.finish_reel(
            row["destination_id"],
            page_token,
            video_id,
            description,
            video_state=video_state,
        )
        remote_post_id = str(finished.get("post_id") or finished.get("id") or row.get("remote_post_id") or "")
        fields: Dict[str, Any] = {
            "status": "PROCESSING",
            "upload_phase": "DRAFT" if video_state == "DRAFT" else "FINISHED",
            "lease_until": None,
            "next_attempt_at": _iso(_now() + timedelta(seconds=15 if video_state == "DRAFT" else 10)),
            "remote_media_id": video_id,
            "remote_post_id": remote_post_id,
            "last_error": (
                "Facebook đang quét bản quyền (nháp, chưa lên page)"
                if video_state == "DRAFT"
                else ""
            ),
        }
        if video_state == "DRAFT":
            fields["upload_url"] = f"copyright_since:{_iso()}"
        if extra:
            fields.update(extra)
        self._update(row["id"], **fields)
        return finished

    def _handle_copyright_then_publish(
        self,
        row: Dict[str, Any],
        client: FacebookClient,
        page_token: str,
        video_id: str,
        payload: Dict[str, Any],
    ) -> None:
        if self._job_has_published_sibling(row):
            self._finish_as(row, client, page_token, video_id, "PUBLISHED")
            return
        verdict = evaluate_copyright(payload)
        if verdict.blocked:
            self._block_job_copyright(row, verdict.summary, client, page_token)
            return
        if verdict.state == "clear":
            self._finish_as(row, client, page_token, video_id, "PUBLISHED")
            return
        if self._copyright_wait_expired(row):
            self._block_job_copyright(
                row,
                verdict.summary or "Không xác nhận được bản quyền — không đăng để tránh strike",
                client,
                page_token,
            )
            return
        self._wait_copyright(row, verdict)

    def _mark_published(self, row: Dict[str, Any], payload: Dict[str, Any]) -> None:
        now = _iso()
        remote_media_id = str(payload.get("id") or row.get("upload_video_id") or "")
        permalink = absolute_facebook_permalink(
            str(payload.get("permalink_url") or row.get("permalink") or ""),
            video_id=remote_media_id,
            page_id=str(row.get("destination_id") or ""),
        )
        with get_db_connection(self.db_path) as conn:
            conn.execute(
                """
                UPDATE distribution_jobs
                SET status = 'PUBLISHED', upload_phase = 'PUBLISHED', lease_until = NULL,
                    remote_media_id = ?, permalink = ?, last_error = '',
                    published_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (remote_media_id, permalink, now, now, row["id"]),
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

    def _comment_affiliate(
        self,
        row: Dict[str, Any],
        client: FacebookClient,
        page_token: str,
        payload: Dict[str, Any],
    ) -> str:
        """ok = done, skip = nothing to post, retry = wait and try again."""
        if str(row.get("affiliate_comment_id") or "").strip():
            return "ok"
        url, product = load_affiliate_from_job(self.db_path, str(row.get("job_id") or ""))
        message = comment_body(url, product)
        if not message:
            return "skip"
        video_id = str(
            payload.get("id") or row.get("remote_media_id") or row.get("upload_video_id") or ""
        )
        post_id = str(row.get("remote_post_id") or payload.get("post_id") or "")
        try:
            result = client.comment_on_reel(
                page_token,
                video_id=video_id,
                post_id=post_id,
                page_id=str(row.get("destination_id") or ""),
                message=message,
                attachment_url=url,
                pin=True,
            )
            comment_id = str(result.get("id") or "")
            if not comment_id:
                return "retry"
            self._update(row["id"], affiliate_comment_id=comment_id)
            row["affiliate_comment_id"] = comment_id
            return "ok"
        except FacebookAPIError as exc:
            logger.warning(
                "Facebook affiliate comment skipped for %s: %s",
                row.get("id"),
                exc,
            )
            if exc.retryable:
                return "retry"
            return "skip"
        except Exception as exc:
            logger.warning(
                "Facebook affiliate comment skipped for %s: %s",
                row.get("id"),
                exc,
            )
            return "skip"

    def _process_sync(self, row: Dict[str, Any]) -> None:
        connection = self._connection(row["destination_id"])
        page_token = connection["page_token"]
        client = FacebookClient(connection["graph_version"], timeout=60.0)
        try:
            source_path = str(row.get("source_path") or "")
            _validate_reel_file(source_path)

            video_id = str(row.get("upload_video_id") or "")
            upload_url = str(row.get("upload_url") or "")
            phase = str(row.get("upload_phase") or "").upper()
            status = str(row.get("status") or "").upper()

            if video_id and (status == "PROCESSING" or phase in {"DRAFT", "FINISHED"}):
                payload = client.get_video_status(video_id, page_token)
                if phase != "FINISHED":
                    self._handle_copyright_then_publish(
                        row, client, page_token, video_id, payload
                    )
                    return
                if self._is_published(payload):
                    outcome = self._comment_affiliate(row, client, page_token, payload)
                    if outcome == "retry":
                        self._update(
                            row["id"],
                            lease_until=None,
                            next_attempt_at=_iso(_now() + timedelta(seconds=20)),
                        )
                        return
                    self._mark_published(row, payload)
                    return
                status_payload = payload.get("status") or {}
                video_status = str(
                    status_payload.get("video_status")
                    if isinstance(status_payload, dict)
                    else ""
                ).lower()
                if video_status in {"error", "failed"}:
                    raise FacebookAPIError("Facebook failed to process the uploaded Reel")
                self._update(
                    row["id"],
                    lease_until=None,
                    next_attempt_at=_iso(_now() + timedelta(seconds=20)),
                )
                return

            attempts = int(row.get("attempts") or 0) + 1
            row["attempts"] = attempts
            self._update(row["id"], attempts=attempts)

            if not video_id or not upload_url or str(upload_url).startswith("copyright_since:"):
                self._update(row["id"], status="STARTING")
                started = client.start_reel(row["destination_id"], page_token)
                video_id = started["video_id"]
                upload_url = started["upload_url"]
                phase = "STARTED"
                self._update(
                    row["id"],
                    status="UPLOADING",
                    upload_phase=phase,
                    upload_video_id=video_id,
                    upload_url=upload_url,
                )

            if phase != "UPLOADED":
                uploaded = client.upload_reel_binary(upload_url, page_token, source_path) or {}
                phase = "UPLOADED"
                self._update(row["id"], status="FINISHING", upload_phase=phase)
                if isinstance(uploaded, dict):
                    early = evaluate_copyright(uploaded)
                    if early.blocked:
                        self._block_job_copyright(row, early.summary, client, page_token)
                        return

            if self._job_has_published_sibling(row):
                self._finish_as(row, client, page_token, video_id, "PUBLISHED")
            else:
                self._finish_as(row, client, page_token, video_id, "DRAFT")
        finally:
            client.close()

    def _record_error(self, row: Dict[str, Any], error: Exception) -> None:
        attempts = max(1, int(row.get("attempts") or 0))
        retryable = not isinstance(error, FacebookAPIError) or error.retryable
        token_expired = isinstance(error, FacebookAPIError) and error.code == 190
        delay = min(900, 15 * (2 ** min(attempts, 6)))
        status = "FAILED"
        next_attempt = _iso(_now() + timedelta(seconds=delay)) if retryable else None
        self._update(
            row["id"],
            status=status,
            lease_until=None,
            next_attempt_at=next_attempt,
            last_error=str(error)[:2000],
            attempts=attempts if retryable else int(row.get("max_attempts") or 5),
        )
        if token_expired:
            with get_db_connection(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE facebook_connections
                    SET status = 'EXPIRED', last_error = ?, updated_at = ?
                    WHERE id = (
                        SELECT connection_id FROM facebook_pages WHERE page_id = ?
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
                    logger.warning("Facebook distribution %s failed: %s", row["id"], error)
                    await asyncio.to_thread(self._record_error, row, error)
                continue

            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass
