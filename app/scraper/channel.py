"""Clone a Douyin/Kuaishou creator: resolve profile, collect video IDs, prepare reup."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.scraper.base import VideoMetadata
from app.scraper.douyin import DouyinScraper

logger = logging.getLogger(__name__)

SEC_UID_RE = re.compile(
    r"(?:douyin\.com/user/|sec_uid=|sec_user_id=)(MS4wLjAB[A-Za-z0-9_-]+)",
    re.I,
)
VIDEO_ID_RE = re.compile(
    r"(?:modal_id|aweme_id|item_ids?)=(\d{18,20})|(?:/(?:video|note|share/video)/)(\d{18,20})"
)
BARE_ID_RE = re.compile(r"(?m)^\s*(\d{18,20})\s*$")
SHORT_RE = re.compile(r"https?://v\.douyin\.com/[A-Za-z0-9_-]+/?", re.I)
KS_PROFILE_RE = re.compile(r"kuaishou\.com/profile/([A-Za-z0-9_-]+)", re.I)
KS_PHOTO_RE = re.compile(r"kuaishou\.com/(?:short-video|photo)/([A-Za-z0-9_-]+)", re.I)

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def is_channel_url(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if SEC_UID_RE.search(t):
        return True
    if re.search(r"douyin\.com/user/", t, re.I):
        return True
    if KS_PROFILE_RE.search(t):
        return True
    return False


def extract_sec_user_id(text: str) -> Optional[str]:
    m = SEC_UID_RE.search(text or "")
    return m.group(1) if m else None


def extract_kuaishou_user_id(text: str) -> Optional[str]:
    m = KS_PROFILE_RE.search(text or "")
    return m.group(1) if m else None


def extract_video_ids(text: str) -> List[str]:
    ids: List[str] = []
    seen = set()

    def _add(vid: Optional[str]) -> None:
        if not vid or vid in seen:
            return
        seen.add(vid)
        ids.append(vid)

    for m in VIDEO_ID_RE.finditer(text or ""):
        _add(m.group(1) or m.group(2))
    for m in BARE_ID_RE.finditer(text or ""):
        _add(m.group(1))
    return ids


def extract_kuaishou_photo_ids(text: str) -> List[str]:
    ids: List[str] = []
    seen = set()
    for m in KS_PHOTO_RE.finditer(text or ""):
        pid = m.group(1)
        if pid and pid not in seen:
            seen.add(pid)
            ids.append(pid)
    return ids


def video_page_url(video_id: str, platform: str = "douyin") -> str:
    if platform == "kuaishou":
        return f"https://www.kuaishou.com/short-video/{video_id}"
    return f"https://www.douyin.com/video/{video_id}"


class ChannelCloneService:
    def __init__(self) -> None:
        self.douyin = DouyinScraper()

    async def _ttwid_client(self) -> httpx.AsyncClient:
        client = httpx.AsyncClient(timeout=18.0, follow_redirects=True)
        try:
            await client.post(
                "https://ttwid.bytedance.com/ttwid/union/register/",
                json={
                    "region": "cn",
                    "aid": 1768,
                    "needFid": "0",
                    "service": "www.ixigua.com",
                    "migrate_info": {"ticket": "", "source": "node"},
                    "cbUrlProtocol": "https",
                    "union": True,
                },
            )
        except Exception as e:
            logger.debug(f"ttwid register skipped: {e}")
        return client

    def _headers(self, referer: str, ttwid: str = "") -> Dict[str, str]:
        cookie = f"ttwid={ttwid};" if ttwid else ""
        return {
            "User-Agent": DESKTOP_UA,
            "Referer": referer,
            "Cookie": cookie,
            "Accept": "application/json",
        }

    async def resolve_profile(self, sec_user_id: str) -> Dict[str, Any]:
        empty = {
            "sec_user_id": sec_user_id,
            "uid": "",
            "nickname": "",
            "unique_id": "",
            "signature": "",
            "aweme_count": 0,
            "follower_count": 0,
            "avatar": "",
            "url": f"https://www.douyin.com/user/{sec_user_id}",
            "platform": "douyin",
        }
        client = await self._ttwid_client()
        try:
            ttwid = client.cookies.get("ttwid") or ""
            headers = self._headers(f"https://www.douyin.com/user/{sec_user_id}", ttwid)
            url = (
                "https://www.douyin.com/aweme/v1/web/user/profile/other/"
                f"?sec_user_id={sec_user_id}&aid=6383&device_platform=webapp"
            )
            res = await client.get(url, headers=headers)
            data = res.json() if res.content else {}
            user = data.get("user") or {}
            if not user:
                ies = await client.get(
                    f"https://www.iesdouyin.com/web/api/v2/user/info/?sec_uid={sec_user_id}",
                    headers=headers,
                )
                payload = ies.json() if ies.content else {}
                user = payload.get("user_info") or {}
            avatar = ""
            thumb = user.get("avatar_thumb") or user.get("avatar_168x168") or {}
            if isinstance(thumb, dict):
                urls = thumb.get("url_list") or []
                if urls:
                    avatar = urls[0]
            elif isinstance(thumb, str):
                avatar = thumb
            return {
                "sec_user_id": sec_user_id,
                "uid": str(user.get("uid") or ""),
                "nickname": user.get("nickname") or "",
                "unique_id": str(user.get("unique_id") or user.get("short_id") or ""),
                "signature": user.get("signature") or "",
                "aweme_count": int(user.get("aweme_count") or 0),
                "follower_count": int(user.get("follower_count") or 0),
                "avatar": avatar,
                "url": f"https://www.douyin.com/user/{sec_user_id}",
                "platform": "douyin",
            }
        except Exception as e:
            logger.warning(f"profile fetch failed: {e}")
            return empty
        finally:
            await client.aclose()

    async def sec_uid_from_video(self, video_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Return (sec_uid, nickname) from a video id via aweme/detail."""
        client = await self._ttwid_client()
        try:
            ttwid = client.cookies.get("ttwid") or ""
            headers = self._headers(f"https://www.douyin.com/video/{video_id}", ttwid)
            url = (
                "https://www.douyin.com/aweme/v1/web/aweme/detail/"
                f"?aweme_id={video_id}&aid=1128&version_name=23.5.0"
                "&device_platform=android&os_version=2333"
            )
            res = await client.get(url, headers=headers)
            data = res.json() if res.content else {}
            author = ((data.get("aweme_detail") or {}).get("author") or {})
            return author.get("sec_uid"), author.get("nickname")
        except Exception as e:
            logger.debug(f"sec_uid from video failed: {e}")
            return None, None
        finally:
            await client.aclose()

    async def try_list_snssdk(self, uid: str, max_videos: int) -> List[str]:
        if not uid:
            return []
        url = (
            "https://aweme.snssdk.com/aweme/v1/aweme/post/"
            f"?user_id={uid}&max_cursor=0&count={min(20, max_videos)}"
            "&aid=1128&version_code=150900&app_name=aweme&device_platform=android"
        )
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                res = await client.get(
                    url,
                    headers={
                        "User-Agent": (
                            "com.ss.android.ugc.aweme/150901 (Linux; U; Android 13; zh_CN; "
                            "Pixel 7; Build/TQ3A; Cronet/TTNetVersion)"
                        ),
                        "Accept": "application/json",
                    },
                )
                data = res.json() if res.content else {}
                ids = []
                for item in data.get("aweme_list") or []:
                    if isinstance(item, dict):
                        vid = str(item.get("aweme_id") or item.get("id") or "")
                        if vid:
                            ids.append(vid)
                return ids[:max_videos]
        except Exception as e:
            logger.debug(f"snssdk list skipped: {e}")
            return []

    async def try_list_via_f2(self, sec_user_id: str, max_videos: int) -> List[str]:
        try:
            from f2.apps.douyin.handler import DouyinHandler

            kwargs = {
                "headers": {
                    "User-Agent": DESKTOP_UA,
                    "Referer": "https://www.douyin.com/",
                },
                "cookie": "",
                "proxies": {"http": None, "https": None},
                "timeout": 8,
            }
            handler = DouyinHandler(kwargs)
            ids: List[str] = []

            async def _drain() -> List[str]:
                async for filt in handler.fetch_user_post_videos(
                    sec_user_id, page_counts=min(18, max_videos), max_counts=max_videos
                ):
                    collected: List[str] = []
                    if hasattr(filt, "aweme_id"):
                        raw_ids = filt.aweme_id
                        if isinstance(raw_ids, list):
                            collected.extend(str(x) for x in raw_ids if x)
                        elif raw_ids:
                            collected.append(str(raw_ids))
                    if not collected and hasattr(filt, "_to_list"):
                        lst = filt._to_list() or []
                        for item in lst:
                            if isinstance(item, dict):
                                vid = str(item.get("aweme_id") or item.get("id") or "")
                                if vid:
                                    collected.append(vid)
                    for vid in collected:
                        if vid not in ids:
                            ids.append(vid)
                    if len(ids) >= max_videos:
                        break
                return ids

            return (await asyncio.wait_for(_drain(), timeout=18.0))[:max_videos]
        except Exception as e:
            logger.info(f"f2 channel list unavailable: {e}")
            return []

    async def try_list_kuaishou(self, user_id: str, max_videos: int) -> List[str]:
        if not user_id:
            return []
        query = (
            "query visionProfilePhotoList($pcursor: String, $userId: String, $page: String) {"
            "  visionProfilePhotoList(pcursor: $pcursor, userId: $userId, page: $page) {"
            "    result pcursor feeds { photo { id } }"
            "  }"
            "}"
        )
        payload = {
            "operationName": "visionProfilePhotoList",
            "variables": {"userId": user_id, "pcursor": "", "page": "profile"},
            "query": query,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                res = await client.post(
                    "https://www.kuaishou.com/graphql",
                    json=payload,
                    headers={
                        "User-Agent": DESKTOP_UA,
                        "Referer": f"https://www.kuaishou.com/profile/{user_id}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    cookies={"did": f"web_{uuid.uuid4().hex}"},
                )
                data = res.json() if res.content else {}
                node = (data.get("data") or {}).get("visionProfilePhotoList") or {}
                ids: List[str] = []
                for feed in node.get("feeds") or []:
                    photo = (feed or {}).get("photo") or {}
                    pid = str(photo.get("id") or "")
                    if pid:
                        ids.append(pid)
                    if len(ids) >= max_videos:
                        break
                return ids[:max_videos]
        except Exception as e:
            logger.debug(f"kuaishou list skipped: {e}")
            return []

    async def _expand_short_links(self, text: str) -> List[str]:
        ids: List[str] = []
        for short in SHORT_RE.findall(text or ""):
            try:
                meta = await self.douyin.extract(short)
                if meta and meta.video_id:
                    ids.append(meta.video_id)
            except Exception:
                continue
        return ids

    async def collect(self, text: str, max_videos: int = 8) -> Dict[str, Any]:
        raw = (text or "").strip()
        if not raw:
            raise ValueError("URL kênh trống")

        max_videos = max(1, min(int(max_videos or 8), 40))
        sec = extract_sec_user_id(raw)
        ks_user = extract_kuaishou_user_id(raw)
        video_ids = extract_video_ids(raw)
        ks_photos = extract_kuaishou_photo_ids(raw)

        extra_shorts = await self._expand_short_links(raw)
        for vid in extra_shorts:
            if vid not in video_ids:
                video_ids.append(vid)

        if not sec and video_ids:
            sec, _ = await self.sec_uid_from_video(video_ids[0])

        profile: Dict[str, Any] = {}
        platform = "kuaishou" if ks_user and not sec else "douyin"

        if sec:
            profile = await self.resolve_profile(sec)
            platform = "douyin"
            uid = profile.get("uid") or ""
            extras: List[str] = []
            if len(video_ids) < max_videos:
                try:
                    extras = await asyncio.wait_for(
                        self.try_list_via_f2(sec, max_videos), timeout=20.0
                    )
                except Exception:
                    extras = []
                if len(extras) < 1 and uid:
                    extras = await self.try_list_snssdk(uid, max_videos)
            for vid in extras:
                if vid not in video_ids:
                    video_ids.append(vid)

        if ks_user:
            platform = "kuaishou"
            if not profile:
                profile = {
                    "sec_user_id": ks_user,
                    "nickname": ks_user,
                    "unique_id": ks_user,
                    "aweme_count": 0,
                    "url": f"https://www.kuaishou.com/profile/{ks_user}",
                    "platform": "kuaishou",
                    "avatar": "",
                    "follower_count": 0,
                    "signature": "",
                    "uid": ks_user,
                }
            ks_ids = await self.try_list_kuaishou(ks_user, max_videos)
            for pid in ks_ids + ks_photos:
                if pid not in video_ids:
                    video_ids.append(pid)

        video_ids = video_ids[:max_videos]
        hint = ""
        if not video_ids:
            nick = profile.get("nickname") or "kênh này"
            count = profile.get("aweme_count") or 0
            count_txt = f" (~{count} video)" if count else ""
            hint = (
                f"Đã nhận kênh «{nick}»{count_txt} nhưng Douyin/Kuaishou chặn danh sách bài công khai "
                "từ máy chủ. Dán thêm vài link video trên kênh (mỗi dòng một link, hoặc modal_id) "
                "rồi bấm quét lại — hệ thống sẽ tải và reup hết."
            )
        return {
            "profile": profile,
            "video_ids": video_ids,
            "photo_ids": ks_photos,
            "hint": hint,
            "channel_url": profile.get("url") or raw.splitlines()[0].strip(),
            "platform": platform,
        }
