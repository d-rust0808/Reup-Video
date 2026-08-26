"""
YouTube video / playlist / channel scraper via yt-dlp.

Single-watch URLs (including watch?v=&list=) extract one video when noplaylist=True.
Channel / playlist listing is used by Reup Theo Kênh.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from app.scraper.base import BaseScraper, VideoMetadata

logger = logging.getLogger(__name__)

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
PLAYLIST_ID_RE = re.compile(r"[?&]list=([A-Za-z0-9_-]+)", re.I)
FEED_PATH_RE = re.compile(
    r"youtube\.com/(?:@[\w.-]+|channel/UC[\w-]{10,}|c/[\w.-]+|user/[\w.-]+|"
    r"playlist(?:\?|/)|feeds/videos\.xml)",
    re.I,
)
YOUTUBE_URL_RE = re.compile(r"https?://[^\s>\x22']+", re.I)
WATCH_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|embed/|shorts/|live/|v/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})",
    re.I,
)
QUERY_V_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{11})", re.I)


def _host(url: str) -> str:
    try:
        parsed = urlparse(url if "://" in (url or "") else f"https://{url}")
    except Exception:
        return ""
    host = (parsed.netloc or "").split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_youtube_url(url: str) -> bool:
    host = _host(url or "")
    if not host:
        return False
    return (
        host in {"youtube.com", "youtu.be", "youtube-nocookie.com", "m.youtube.com", "music.youtube.com"}
        or host.endswith(".youtube.com")
        or host.endswith(".youtu.be")
    )


def extract_youtube_video_id(url: str) -> Optional[str]:
    text = (url or "").strip()
    if not text:
        return None
    m = WATCH_ID_RE.search(text) or QUERY_V_RE.search(text)
    if m and VIDEO_ID_RE.fullmatch(m.group(1)):
        return m.group(1)
    try:
        parsed = urlparse(text if "://" in text else f"https://{text}")
    except Exception:
        return None
    host = (parsed.netloc or "").split(":")[0].lower()
    path = (parsed.path or "").strip("/")
    if host.endswith("youtu.be"):
        part = path.split("/")[0] if path else ""
        return part if VIDEO_ID_RE.fullmatch(part) else None
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live", "v"}:
        return parts[1] if VIDEO_ID_RE.fullmatch(parts[1]) else None
    qs = parse_qs(parsed.query or "")
    cand = (qs.get("v") or [None])[0]
    return cand if cand and VIDEO_ID_RE.fullmatch(cand) else None


def extract_youtube_playlist_id(url: str) -> Optional[str]:
    m = PLAYLIST_ID_RE.search(url or "")
    return m.group(1) if m else None


def is_youtube_feed_url(url: str) -> bool:
    """Channel, handle, or playlist page (not a lone watch/shorts video)."""
    if not is_youtube_url(url or ""):
        return False
    if FEED_PATH_RE.search(url or ""):
        return True
    if extract_youtube_playlist_id(url) and not extract_youtube_video_id(url):
        return True
    return False


def extract_youtube_urls(text: str) -> List[str]:
    found: List[str] = []
    seen = set()
    for m in YOUTUBE_URL_RE.finditer(text or ""):
        raw = m.group(0).rstrip(").,];")
        if is_youtube_url(raw) and raw not in seen:
            seen.add(raw)
            found.append(raw)
    return found


def watch_url_for_id(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _ffmpeg_location() -> Optional[str]:
    try:
        from app.services.audio_service import find_ffmpeg_binary

        return find_ffmpeg_binary()
    except Exception:
        return shutil.which("ffmpeg")


def _ydl_opts(
    *,
    noplaylist: bool = True,
    skip_download: bool = True,
    extract_flat: bool = False,
    playlistend: Optional[int] = None,
    outtmpl: Optional[str] = None,
) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "noplaylist": noplaylist,
        "skip_download": skip_download,
        "cachedir": False,
        "ignoreerrors": False,
        "overwrites": True,
    }
    if extract_flat:
        opts["extract_flat"] = "in_playlist"
    if playlistend:
        opts["playlistend"] = int(playlistend)
    if outtmpl:
        opts["outtmpl"] = outtmpl
        opts["skip_download"] = False
        opts["merge_output_format"] = "mp4"
        opts["format"] = (
            "b[ext=mp4][protocol!=m3u8][height<=1080]/"
            "bv*[height<=1080]+ba/"
            "b[ext=mp4]/"
            "bv*+ba/b"
        )
    ffmpeg = _ffmpeg_location()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    return opts


def ytdlp_error_message(exc: BaseException) -> str:
    text = str(exc) or exc.__class__.__name__
    low = text.lower()
    if "no module named 'yt_dlp'" in low or "yt_dlp" in low and "import" in low:
        return "Chưa cài yt-dlp. Chạy pip install yt-dlp rồi khởi động lại Studio."
    if "sign in" in low or "not a bot" in low or "confirm you" in low:
        return (
            "YouTube đang chặn tải (xác minh bot). Cập nhật yt-dlp "
            "(pip install -U yt-dlp) rồi thử lại, hoặc bấm «Tải File Từ Máy»."
        )
    if "private" in low or "unavailable" in low or "removed" in low:
        return "Video YouTube không khả dụng (riêng tư, đã xóa, hoặc hạn chế khu vực)."
    if "ffmpeg" in low:
        return "Cần FFmpeg để ghép video/audio YouTube. Kiểm tra ffmpeg đã cài chưa."
    return f"Không tải được video YouTube: {text[:240]}"


def run_yt_dlp_extract(url: str, opts: Dict[str, Any]) -> Dict[str, Any]:
    try:
        import yt_dlp
    except ImportError as e:
        raise RuntimeError(ytdlp_error_message(e)) from e
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=not opts.get("skip_download", True))
    if not info:
        raise RuntimeError("YouTube không trả về thông tin video.")
    return info


def download_youtube_to_file(url: str, dest_mp4: str) -> int:
    """Download a single YouTube watch URL to dest_mp4. Returns file size in bytes."""
    work_dir = f"{dest_mp4}.{uuid.uuid4().hex}.ytdl"
    os.makedirs(work_dir, exist_ok=True)
    outtmpl = os.path.join(work_dir, "video.%(ext)s")
    opts = _ydl_opts(noplaylist=True, skip_download=False, outtmpl=outtmpl)
    try:
        run_yt_dlp_extract(url, opts)
        files = [
            os.path.join(work_dir, name)
            for name in os.listdir(work_dir)
            if os.path.isfile(os.path.join(work_dir, name)) and not name.endswith(".part")
        ]
        if not files:
            raise RuntimeError("yt-dlp không ghi được file video.")
        files.sort(key=lambda p: (os.path.splitext(p)[1].lower() != ".mp4", -os.path.getsize(p)))
        chosen = files[0]
        ext = os.path.splitext(chosen)[1].lower()
        if ext != ".mp4":
            ffmpeg = _ffmpeg_location()
            if not ffmpeg:
                raise RuntimeError("Cần FFmpeg để chuyển video YouTube sang MP4.")
            import subprocess

            remux = os.path.join(work_dir, "video.remux.mp4")
            proc = subprocess.run(
                [ffmpeg, "-y", "-i", chosen, "-c", "copy", "-movflags", "+faststart", remux],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0 or not os.path.isfile(remux):
                proc = subprocess.run(
                    [ffmpeg, "-y", "-i", chosen, "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart", remux],
                    capture_output=True,
                    text=True,
                )
            if proc.returncode != 0 or not os.path.isfile(remux):
                raise RuntimeError("Không chuyển được file YouTube sang MP4.")
            chosen = remux
        os.makedirs(os.path.dirname(dest_mp4) or ".", exist_ok=True)
        shutil.move(chosen, dest_mp4)
        size = os.path.getsize(dest_mp4)
        if size <= 0:
            raise ValueError("Downloaded stream is empty (0 bytes)")
        return size
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(ytdlp_error_message(e)) from e
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


class YoutubeScraper(BaseScraper):
    """Resolve YouTube watch / Shorts / youtu.be links through yt-dlp."""

    ALLOWED_DOMAINS = ["youtube.com", "youtu.be", "youtube-nocookie.com"]

    def _metadata_from_info(self, info: Dict[str, Any], original_url: str) -> VideoMetadata:
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                raise RuntimeError("Playlist/kênh YouTube không có video.")
            info = entries[0]
        vid = str(info.get("id") or extract_youtube_video_id(original_url) or "")
        if not VIDEO_ID_RE.fullmatch(vid):
            raise RuntimeError("Không đọc được ID video YouTube.")
        title = str(info.get("title") or vid).strip() or vid
        author = str(info.get("uploader") or info.get("channel") or info.get("uploader_id") or "")
        try:
            duration = float(info.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        webpage = str(info.get("webpage_url") or info.get("original_url") or "")
        if not is_youtube_url(webpage):
            webpage = watch_url_for_id(vid)
        return VideoMetadata(
            video_id=vid,
            platform="youtube",
            original_url=original_url,
            direct_stream_url=webpage,
            title=title[:200],
            author=author,
            duration=duration,
            watermark_free=True,
            stream_headers={"Referer": "https://www.youtube.com/", "User-Agent": "Mozilla/5.0"},
        )

    def _profile_from_info(self, info: Dict[str, Any], url: str) -> Dict[str, Any]:
        channel = str(info.get("channel") or info.get("uploader") or info.get("title") or "YouTube")
        channel_id = str(info.get("channel_id") or info.get("uploader_id") or info.get("id") or "")
        thumb = ""
        thumbs = info.get("thumbnails") or []
        if isinstance(thumbs, list) and thumbs:
            last = thumbs[-1] if isinstance(thumbs[-1], dict) else {}
            thumb = str(last.get("url") or "")
        if not thumb:
            thumb = str(info.get("thumbnail") or "")
        count = info.get("playlist_count") or len([e for e in (info.get("entries") or []) if e]) or 0
        try:
            count_i = int(count or 0)
        except (TypeError, ValueError):
            count_i = 0
        try:
            followers = int(info.get("channel_follower_count") or 0)
        except (TypeError, ValueError):
            followers = 0
        return {
            "sec_user_id": channel_id,
            "uid": channel_id,
            "nickname": channel[:120],
            "unique_id": channel_id,
            "signature": str(info.get("description") or info.get("title") or url)[:500],
            "aweme_count": count_i if count_i else len(catalog),
            "follower_count": followers,
            "avatar": thumb,
            "url": str(info.get("channel_url") or info.get("webpage_url") or info.get("original_url") or url),
            "platform": "youtube",
        }

    def _normalize_feed_url(self, url: str) -> str:
        m = re.search(r"(https?://(?:www\.)?youtube\.com/@[\w.-]+)(?:/)?(?:\?.*)?$", url or "", re.I)
        if m:
            return m.group(1) + "/videos"
        return url

    def _extract_info_sync(
        self,
        url: str,
        *,
        noplaylist: bool,
        extract_flat: bool = False,
        playlistend: Optional[int] = None,
    ) -> Dict[str, Any]:
        opts = _ydl_opts(
            noplaylist=noplaylist,
            skip_download=True,
            extract_flat=extract_flat,
            playlistend=playlistend,
        )
        try:
            return run_yt_dlp_extract(url, opts)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(ytdlp_error_message(e)) from e

    async def extract(self, url: str) -> VideoMetadata:
        self.validate_url(url)
        self._check_test_error_triggers(url)
        url_clean = self.extract_url_from_text(url.strip())
        video_id = extract_youtube_video_id(url_clean)
        if is_youtube_feed_url(url_clean) and not video_id:
            raise ValueError(
                "Đây là link kênh hoặc playlist YouTube. Hãy dùng tab «2. Reup Theo Kênh»."
            )
        info = await asyncio.to_thread(self._extract_info_sync, url_clean, noplaylist=True)
        return self._metadata_from_info(info, url_clean)

    async def collect_feed(self, text: str, max_videos: int = 8) -> Dict[str, Any]:
        urls = extract_youtube_urls(text)
        if not urls:
            cleaned = self.extract_url_from_text((text or "").strip())
            if is_youtube_url(cleaned):
                urls = [cleaned]
        if not urls:
            raise ValueError("Không thấy URL YouTube")

        max_videos = max(1, min(int(max_videos or 8), 500))
        video_ids: List[str] = []
        catalog: List[Dict[str, str]] = []
        profile: Dict[str, Any] = {}

        for url in urls:
            remaining = max_videos - len(video_ids)
            if remaining <= 0:
                break
            as_feed = is_youtube_feed_url(url) or bool(extract_youtube_playlist_id(url))
            if as_feed:
                target = self._normalize_feed_url(url)
                info = await asyncio.to_thread(
                    self._extract_info_sync,
                    target,
                    noplaylist=False,
                    extract_flat=True,
                    playlistend=remaining,
                )
                if not profile:
                    profile = self._profile_from_info(info, url)
                entries = info.get("entries") if info.get("_type") == "playlist" else [info]
                for entry in entries or []:
                    if not entry:
                        continue
                    if isinstance(entry, str):
                        eid = extract_youtube_video_id(entry) or (entry if VIDEO_ID_RE.fullmatch(entry) else "")
                    else:
                        eid = str(
                            entry.get("id")
                            or extract_youtube_video_id(str(entry.get("url") or entry.get("webpage_url") or ""))
                            or ""
                        )
                    if eid and VIDEO_ID_RE.fullmatch(eid) and eid not in video_ids:
                        video_ids.append(eid)
                        title = ""
                        if isinstance(entry, dict):
                            title = str(entry.get("title") or "")
                        catalog.append({
                            "video_id": eid,
                            "title": title,
                            "url": watch_url_for_id(eid),
                        })
                    if len(video_ids) >= max_videos:
                        break
                continue

            vid = extract_youtube_video_id(url)
            if vid and vid not in video_ids:
                video_ids.append(vid)
                catalog.append({"video_id": vid, "title": "", "url": watch_url_for_id(vid)})
            if not profile:
                try:
                    info = await asyncio.to_thread(self._extract_info_sync, url, noplaylist=True)
                    profile = self._profile_from_info(info, url)
                except Exception as e:
                    logger.debug(f"youtube profile fetch skipped: {e}")
                    profile = self._profile_from_info({"id": vid or "", "title": vid or "YouTube"}, url)

        if not profile:
            profile = {
                "sec_user_id": "",
                "uid": "",
                "nickname": "YouTube",
                "unique_id": "",
                "signature": urls[0],
                "aweme_count": len(video_ids),
                "follower_count": 0,
                "avatar": "",
                "url": urls[0],
                "platform": "youtube",
            }

        hint = ""
        if not video_ids:
            nick = profile.get("nickname") or "kênh này"
            hint = (
                f"Đã nhận YouTube «{nick}» nhưng chưa lấy được danh sách video. "
                "Thử dán link watch (youtube.com/watch?v=...) hoặc playlist công khai, mỗi dòng một link."
            )
        if not profile.get("aweme_count"):
            profile["aweme_count"] = len(video_ids)
        return {
            "profile": profile,
            "video_ids": video_ids[:max_videos],
            "catalog": catalog[:max_videos],
            "photo_ids": [],
            "hint": hint,
            "channel_url": profile.get("url") or urls[0],
            "platform": "youtube",
        }
