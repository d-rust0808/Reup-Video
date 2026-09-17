"""Facebook Rights Manager checks — source probe before reup, and publish-time gate."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core.database import get_db_connection

logger = logging.getLogger(__name__)


COPYRIGHT_WAIT_SECONDS = 360
FACEBOOK_SESSION_EXPIRED_MESSAGE = (
    "Token Facebook đã hết hạn (đổi mật khẩu hoặc Facebook thu hồi session). "
    "Vào Kênh → Facebook, dán user token mới rồi chạy lại job. "
    "Đây không phải dính bản quyền."
)

@dataclass(frozen=True)
class CopyrightVerdict:
    state: str
    summary: str
    matches_found: bool = False

    @property
    def blocked(self) -> bool:
        return self.state == "blocked"

    @property
    def pending(self) -> bool:
        return self.state == "pending"

    @property
    def auth_expired(self) -> bool:
        return self.state == "auth"


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _copyright_blob(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    info = data.get("copyright_check_information")
    if isinstance(info, dict) and info:
        return info
    status = data.get("copyright_check_status")
    if isinstance(status, dict) and status:
        return status
    return {}


def _status_pair(info: Dict[str, Any]) -> Tuple[str, Any]:
    raw = info.get("status")
    if isinstance(raw, dict):
        return str(raw.get("status") or "").strip().lower(), raw.get("matches_found")
    if isinstance(raw, str):
        return raw.strip().lower(), info.get("matches_found")
    return "", info.get("matches_found")


def _action_names(match: Dict[str, Any]) -> List[str]:
    policy = _as_dict(match.get("owner_copyright_policy") or match.get("copyright_policy"))
    actions = []
    for item in _as_list(policy.get("actions")):
        if isinstance(item, dict):
            name = str(item.get("action") or "").strip().upper()
        else:
            name = str(item or "").strip().upper()
        if name:
            actions.append(name)
    if not actions:
        fallback = str(match.get("action") or policy.get("action") or "").strip().upper()
        if fallback:
            actions.append(fallback)
    return actions


def summarize_matches(matches: List[Any]) -> str:
    parts: List[str] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        title = str(match.get("content_title") or match.get("title") or "nội dung bản quyền").strip()
        policy = _as_dict(match.get("owner_copyright_policy") or match.get("copyright_policy"))
        owner = str(policy.get("name") or match.get("owner") or match.get("author") or "").strip()
        actions = ", ".join(_action_names(match)) or "TRACK"
        segments = _as_list(match.get("matched_segments"))
        kinds = sorted({
            str(seg.get("segment_type") or "").strip().upper()
            for seg in segments
            if isinstance(seg, dict) and str(seg.get("segment_type") or "").strip()
        })
        bit = title
        if owner:
            bit += f" ({owner})"
        bit += f" — {actions}"
        if kinds:
            bit += f" [{'/'.join(kinds)}]"
        parts.append(bit)
        if len(parts) >= 3:
            break
    if not parts:
        return "Facebook Rights Manager phát hiện nội dung trùng bản quyền"
    return "Trùng bản quyền: " + "; ".join(parts)


def evaluate_copyright(payload: Dict[str, Any]) -> CopyrightVerdict:
    """Map Graph copyright_check_information to pending / clear / blocked / unknown."""
    info = _copyright_blob(payload)
    if not info:
        return CopyrightVerdict("unknown", "Facebook chưa trả kết quả check bản quyền")

    status, matches_found = _status_pair(info)
    if status in {"in_progress", "not_started", "processing", "pending"}:
        return CopyrightVerdict("pending", "Facebook đang quét bản quyền (nháp, chưa lên page)")
    if status in {"error", "failed"}:
        return CopyrightVerdict("unknown", "Facebook lỗi khi quét bản quyền")

    matches = [item for item in _as_list(info.get("copyright_matches")) if isinstance(item, dict)]
    found = matches_found
    if found is None:
        found = bool(matches)

    if found is True or matches:
        return CopyrightVerdict("blocked", summarize_matches(matches), matches_found=True)
    if found is False:
        return CopyrightVerdict("clear", "", matches_found=False)
    if status in {"complete", "completed"}:
        return CopyrightVerdict("clear", "", matches_found=False)
    return CopyrightVerdict("unknown", f"Facebook copyright status={status or 'empty'}")


class CopyrightBlockedError(RuntimeError):
    """Source matched Facebook Rights Manager; the reup pipeline must stop."""


class FacebookSessionExpiredError(RuntimeError):
    """Copyright scan could not run because the Facebook session is dead."""


def mark_facebook_connection_expired(
    db_path: str,
    *,
    page_id: str = "",
    error: str = "",
) -> None:
    if not db_path:
        return
    detail = (error or FACEBOOK_SESSION_EXPIRED_MESSAGE)[:1000]
    now = datetime.now(timezone.utc).isoformat()
    with get_db_connection(db_path) as conn:
        if page_id:
            conn.execute(
                """
                UPDATE facebook_connections
                SET status = 'EXPIRED', last_error = ?, updated_at = ?
                WHERE id = (
                    SELECT connection_id FROM facebook_pages WHERE page_id = ?
                )
                """,
                (detail, now, page_id),
            )
        else:
            conn.execute(
                """
                UPDATE facebook_connections
                SET status = 'EXPIRED', last_error = ?, updated_at = ?
                WHERE status = 'CONNECTED'
                """,
                (detail, now),
            )
        conn.commit()


def find_ffprobe_binary() -> Optional[str]:
    from app.services.reup_service import find_ffmpeg_binary

    ffmpeg = find_ffmpeg_binary()
    if ffmpeg:
        sibling = os.path.join(os.path.dirname(ffmpeg), "ffprobe")
        if os.path.isfile(sibling) and os.access(sibling, os.X_OK):
            return sibling
    found = shutil.which("ffprobe")
    return found if found else None


def media_duration_seconds(path: str) -> float:
    probe = find_ffprobe_binary()
    if not probe or not path or not os.path.isfile(path):
        return 0.0
    result = subprocess.run(
        [
            probe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return max(0.0, float((result.stdout or "").strip() or 0))
    except (TypeError, ValueError):
        return 0.0


def build_copyright_probe(source_path: str, dest_path: str, *, max_seconds: float = 40.0) -> str:
    """Cheap 9:16 H.264 sample of the *source* for Rights Manager. No reup FX."""
    from app.services.reup_service import find_ffmpeg_binary

    ffmpeg = find_ffmpeg_binary()
    if not ffmpeg:
        raise RuntimeError("Không tìm thấy ffmpeg để cắt clip check bản quyền")
    if not source_path or not os.path.isfile(source_path):
        raise FileNotFoundError("Thiếu file gốc để check bản quyền")

    duration = media_duration_seconds(source_path)
    window = min(max(4.0, float(max_seconds or 40.0)), 60.0)
    if duration > 0:
        start = 3.0 if duration > 12 else 0.0
        if duration > 80:
            start = min(duration * 0.18, duration - window)
        length = min(window, max(4.0, duration - start))
    else:
        start = 0.0
        length = window

    os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".", exist_ok=True)
    vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30"
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", source_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "128k",
        "-movflags", "+faststart",
        dest_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not os.path.isfile(dest_path) or os.path.getsize(dest_path) < 1000:
        silent = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", source_path,
            "-f", "lavfi", "-t", f"{length:.2f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            "-movflags", "+faststart",
            dest_path,
        ]
        result = subprocess.run(silent, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not os.path.isfile(dest_path) or os.path.getsize(dest_path) < 1000:
        detail = (result.stderr or result.stdout or "").strip()[:400]
        raise RuntimeError(f"Không cắt được clip check bản quyền: {detail or 'ffmpeg failed'}")
    return dest_path


def list_connected_publish_pages(db_path: str) -> List[Dict[str, str]]:
    if not db_path:
        return []
    with get_db_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT fp.page_id, fp.name, fp.page_token_ref,
                   COALESCE(fc.graph_version, 'v24.0') AS graph_version
            FROM facebook_pages fp
            JOIN facebook_connections fc ON fc.id = fp.connection_id
            WHERE fp.can_publish = 1 AND fc.status = 'CONNECTED'
            ORDER BY fp.updated_at DESC
            """
        ).fetchall()
    pages: List[Dict[str, str]] = []
    for row in rows:
        pages.append({
            "page_id": str(row["page_id"] or ""),
            "name": str(row["name"] or ""),
            "page_token_ref": str(row["page_token_ref"] or ""),
            "graph_version": str(row["graph_version"] or "v24.0"),
        })
    return [page for page in pages if page["page_id"] and page["page_token_ref"]]


def _page_index(job_id: str, count: int) -> int:
    if count <= 0:
        return 0
    digits = "".join(ch for ch in str(job_id or "") if ch.isalnum())
    if len(digits) >= 8:
        try:
            return int(digits[-8:], 16) % count
        except ValueError:
            pass
    return 0


def scan_source_on_facebook(
    source_path: str,
    *,
    db_path: str,
    job_id: str = "",
    probe_path: str = "",
    poll_seconds: float = 12.0,
    wait_seconds: float = COPYRIGHT_WAIT_SECONDS,
    log: Optional[Callable[[str], None]] = None,
) -> CopyrightVerdict:
    """Upload a source sample as a Facebook Reel DRAFT, read Rights Manager, delete it."""
    from app.services.facebook_client import FacebookAPIError, FacebookClient
    from app.services.secret_store import get_secret

    pages = list_connected_publish_pages(db_path)
    if not pages:
        return CopyrightVerdict("unknown", "Chưa kết nối Fanpage — bỏ qua check bản quyền trước reup")

    dest = probe_path or os.path.join(
        os.path.dirname(os.path.abspath(source_path)) or ".",
        f"{job_id or 'probe'}.copyright_probe.mp4",
    )
    probe = build_copyright_probe(source_path, dest)
    start_at = _page_index(job_id, len(pages))
    ordered = pages[start_at:] + pages[:start_at]
    last = CopyrightVerdict("unknown", "Facebook chưa trả kết quả check bản quyền")
    tried = 0

    def _log(message: str) -> None:
        if log:
            log(message)

    try:
        for page in ordered:
            if tried >= 3:
                break
            token = get_secret(page["page_token_ref"])
            if not token:
                continue
            tried += 1
            client: Optional[FacebookClient] = None
            video_id = ""
            try:
                client = FacebookClient(page["graph_version"], timeout=60.0)
                started = client.start_reel(page["page_id"], token)
                video_id = str(started.get("video_id") or "")
                upload_url = str(started.get("upload_url") or "")
                if not video_id or not upload_url:
                    raise FacebookAPIError("Facebook không trả video_id để check bản quyền")
                uploaded = client.upload_reel_binary(upload_url, token, probe) or {}
                early = evaluate_copyright(uploaded if isinstance(uploaded, dict) else {})
                if early.blocked:
                    return early
                client.finish_reel(page["page_id"], token, video_id, "", video_state="DRAFT")
                _log(
                    f"🔎 Đang hỏi Facebook Rights Manager trên nháp ({page.get('name') or page['page_id']}) — chưa reup"
                )
                deadline = time.monotonic() + max(30.0, float(wait_seconds or COPYRIGHT_WAIT_SECONDS))
                while time.monotonic() < deadline:
                    payload = client.get_video_status(video_id, token)
                    last = evaluate_copyright(payload)
                    if last.blocked or last.state == "clear":
                        return last
                    time.sleep(max(5.0, float(poll_seconds or 12.0)))
                last = CopyrightVerdict(
                    "unknown",
                    last.summary or "Facebook quét bản quyền quá lâu — không reup để tránh strike",
                )
                return last
            except FacebookAPIError as exc:
                last = CopyrightVerdict("unknown", str(exc)[:300])
                if exc.is_auth_error:
                    mark_facebook_connection_expired(
                        db_path,
                        page_id=page.get("page_id") or "",
                        error=str(exc),
                    )
                    last = CopyrightVerdict("auth", FACEBOOK_SESSION_EXPIRED_MESSAGE)
                    _log(
                        "⚠️ Token Facebook hết hạn — không check được bản quyền. "
                        "Cần kết nối lại Fanpage (không phải dính bản quyền)."
                    )
                    break
                if exc.status_code == 429 or exc.code in {4, 17, 32, 613, 80004}:
                    _log(f"Facebook rate-limit page {page.get('name') or page['page_id']}, thử page khác")
                    continue
                logger.warning("Facebook source copyright scan failed: %s", exc)
                continue
            finally:
                if client is not None:
                    if video_id and token:
                        try:
                            client.delete_object(video_id, token)
                        except Exception:
                            logger.warning("Could not delete copyright probe reel %s", video_id, exc_info=True)
                    try:
                        client.close()
                    except Exception:
                        pass
    finally:
        try:
            if probe and os.path.isfile(probe):
                os.remove(probe)
        except OSError:
            pass
    return last


def assert_source_copyright_clear(
    source_path: str,
    *,
    db_path: str,
    job_id: str = "",
    log: Optional[Callable[[str], None]] = None,
) -> CopyrightVerdict:
    """Fail closed before reup when Facebook is connected. Skip only if no Fanpage."""
    pages = list_connected_publish_pages(db_path)
    if not pages:
        if log:
            log("⚠️ Chưa kết nối Fanpage — bỏ qua check bản quyền trước reup")
        return CopyrightVerdict("unknown", "Chưa kết nối Fanpage — bỏ qua check bản quyền trước reup")

    if log:
        log("🔎 Check bản quyền Facebook trên video gốc (nháp, chưa reup)…")
    verdict = scan_source_on_facebook(
        source_path,
        db_path=db_path,
        job_id=job_id,
        log=log,
    )
    if verdict.state == "clear":
        if log:
            log("✅ Facebook không thấy trùng bản quyền trên clip gốc — chạy reup")
        return verdict
    if verdict.auth_expired:
        raise FacebookSessionExpiredError(
            verdict.summary or FACEBOOK_SESSION_EXPIRED_MESSAGE
        )
    if verdict.blocked:
        raise CopyrightBlockedError(verdict.summary)
    raise CopyrightBlockedError(
        verdict.summary or "Không xác nhận được bản quyền Facebook — dừng reup"
    )
