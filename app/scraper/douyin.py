"""
Douyin (TikTok China) video scraper module (R1 / M1.2).
Handles share link resolution, aweme detail extraction, and watermark removal.
"""
import re
import json
import html
import logging
from urllib.parse import unquote
from typing import Any, Dict, List, Optional, Tuple
import httpx

from app.scraper.base import BaseScraper, VideoMetadata

logger = logging.getLogger(__name__)


class DouyinScraper(BaseScraper):
    """Douyin link parser & direct stream API resolver."""

    ALLOWED_DOMAINS = ["douyin.com", "v.douyin.com", "iesdouyin.com"]

    MOBILE_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    DESKTOP_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.douyin.com/",
        "Accept": "application/json, text/plain, */*",
    }

    SHORT_LINK_REGEX = re.compile(r"https?://v\.douyin\.com/([A-Za-z0-9_-]+)/??")
    ITEM_ID_REGEX = re.compile(
        r"/(?:video|note|share/video)/(\d{18,20})|[?&](?:modal_id|aweme_id)=(\d{18,20})"
    )

    def extract_item_id_from_text(self, text: str) -> Tuple[str, str]:
        """Extract item_id and short code or url from input text."""
        short_match = self.SHORT_LINK_REGEX.search(text)
        if short_match:
            short_code = short_match.group(1)
            return short_code, short_match.group(0)

        id_match = self.ITEM_ID_REGEX.search(text)
        if id_match:
            item_id = id_match.group(1) or id_match.group(2)
            return item_id, text

        digits = re.findall(r"\d{18,20}", text)
        if digits:
            return digits[0], text

        match_path = re.search(r"douyin\.com/([A-Za-z0-9_-]+)", text)
        if match_path:
            return match_path.group(1), text

        return "7123456789012345678", text

    def convert_to_no_watermark_url(self, playwm_url: str) -> str:
        """Transform /playwm/ endpoint to clean /play/ endpoint."""
        clean_url = playwm_url.replace("/playwm/", "/play/")
        clean_url = re.sub(r"ratio=\d+p", "ratio=1080p", clean_url)
        clean_url = re.sub(r"watermark=\d", "watermark=0", clean_url)
        return clean_url

    @staticmethod
    def _find_aweme(payload: Any, item_id: str) -> Optional[Dict[str, Any]]:
        """Find an aweme object across the different Douyin response layouts."""
        if isinstance(payload, dict):
            direct = payload.get("aweme_detail")
            if isinstance(direct, dict):
                return direct
            for key in ("item_list", "aweme_list"):
                items = payload.get(key)
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and (
                            str(item.get("aweme_id") or "") == item_id or item.get("video")
                        ):
                            return item
            video_node = payload.get("video")
            is_aweme_video = isinstance(video_node, dict) and any(
                key in video_node
                for key in ("play_addr", "play_addr_h264", "download_addr", "bit_rate")
            )
            if is_aweme_video and (
                not payload.get("aweme_id") or str(payload.get("aweme_id")) == item_id
            ):
                return payload
            for value in payload.values():
                found = DouyinScraper._find_aweme(value, item_id)
                if found:
                    return found
        elif isinstance(payload, list):
            for value in payload:
                found = DouyinScraper._find_aweme(value, item_id)
                if found:
                    return found
        return None

    def _metadata_from_aweme(
        self,
        aweme: Dict[str, Any],
        item_id: str,
        original_url: str,
    ) -> Optional[VideoMetadata]:
        video_info = aweme.get("video") or {}
        addresses: List[Dict[str, Any]] = []
        for key in ("play_addr_h264", "play_addr", "download_addr"):
            value = video_info.get(key)
            if isinstance(value, dict):
                addresses.append(value)
        for bitrate in video_info.get("bit_rate") or []:
            if not isinstance(bitrate, dict):
                continue
            value = bitrate.get("play_addr") or bitrate.get("play_addr_265")
            if isinstance(value, dict):
                addresses.append(value)

        urls: List[str] = []
        for address in addresses:
            for candidate in address.get("url_list") or []:
                if not isinstance(candidate, str) or not candidate.startswith("http"):
                    continue
                clean = self.convert_to_no_watermark_url(candidate)
                if clean not in urls:
                    urls.append(clean)
        if not urls:
            return None

        raw_duration = aweme.get("duration") or video_info.get("duration") or 0
        try:
            duration = float(raw_duration) / 1000.0
        except (TypeError, ValueError):
            duration = 0.0
        author = aweme.get("author") or {}
        return VideoMetadata(
            video_id=str(aweme.get("aweme_id") or item_id),
            platform="douyin",
            original_url=original_url,
            direct_stream_url=urls[0],
            stream_url_candidates=urls,
            title=aweme.get("desc") or "Douyin Video",
            author=author.get("nickname") or "DouyinCreator",
            duration=max(0.0, duration),
            stream_headers={
                "User-Agent": self.MOBILE_HEADERS["User-Agent"],
                "Referer": f"https://www.douyin.com/video/{item_id}",
            },
        )

    def _metadata_from_payload(
        self,
        payload: Any,
        item_id: str,
        original_url: str,
    ) -> Optional[VideoMetadata]:
        aweme = self._find_aweme(payload, item_id)
        return self._metadata_from_aweme(aweme, item_id, original_url) if aweme else None

    def _metadata_from_html(
        self,
        page_text: str,
        item_id: str,
        original_url: str,
    ) -> Optional[VideoMetadata]:
        """Read the JSON hydration blocks used by jingxuan and canonical video pages."""
        for match in re.finditer(
            r'<script[^>]+id=["\'](?:RENDER_DATA|__UNIVERSAL_DATA_FOR_REHYDRATION__)["\'][^>]*>(.*?)</script>',
            page_text or "",
            flags=re.I | re.S,
        ):
            raw = html.unescape(match.group(1)).strip()
            for candidate in (raw, unquote(raw)):
                try:
                    metadata = self._metadata_from_payload(
                        json.loads(candidate), item_id, original_url
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if metadata:
                    return metadata
        return None

    async def _resolve_direct_stream(self, no_wm_url: str, client: httpx.AsyncClient) -> str:
        """Follow redirects with Mobile UA to get direct CDN MP4 link."""
        try:
            res = await client.head(no_wm_url, headers=self.MOBILE_HEADERS, follow_redirects=True)
            if res.status_code == 200:
                return str(res.url)
            res_get = await client.get(no_wm_url, headers=self.MOBILE_HEADERS, follow_redirects=True)
            return str(res_get.url)
        except Exception:
            return no_wm_url

    async def extract(self, url: str) -> VideoMetadata:
        self.validate_url(url)
        self._check_test_error_triggers(url)

        url_clean = url.strip()
        extracted_url = self.extract_url_from_text(url_clean)
        item_id, target_url = self.extract_item_id_from_text(extracted_url)

        # Try real HTTP API scraping first if network is available
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resolved_url = target_url
                if self.SHORT_LINK_REGEX.search(url_clean):
                    try:
                        res = await client.get(url_clean, headers=self.MOBILE_HEADERS)
                        resolved_url = str(res.url)
                        # Re-extract ID from resolved URL
                        id_match = self.ITEM_ID_REGEX.search(resolved_url)
                        if id_match:
                            item_id = id_match.group(1) or id_match.group(2)
                    except Exception:
                        pass

                # Tier 0: official detail APIs. Douyin rotates which web endpoint
                # accepts a request, so keep both layouts instead of fabricating a CDN URL.
                try:
                    reg_payload = {
                        "region": "cn",
                        "aid": 1768,
                        "needFid": "0",
                        "service": "www.ixigua.com",
                        "migrate_info": {"ticket": "", "source": "node"},
                        "cbUrlProtocol": "https",
                        "union": True,
                    }
                    await client.post("https://ttwid.bytedance.com/ttwid/union/register/", json=reg_payload)
                    ttwid_val = client.cookies.get("ttwid") or ""

                    detail_headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                        "Referer": f"https://www.douyin.com/video/{item_id}",
                        "Cookie": f"ttwid={ttwid_val};",
                        "Accept": "application/json",
                    }
                    detail_urls = [
                        f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={item_id}&aid=1128&version_name=23.5.0&device_platform=android&os_version=2333",
                        f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={item_id}",
                    ]
                    for detail_url in detail_urls:
                        try:
                            res_detail = await client.get(detail_url, headers=detail_headers)
                            if res_detail.status_code != 200 or not res_detail.content:
                                continue
                            metadata = self._metadata_from_payload(
                                res_detail.json(), item_id, url_clean
                            )
                            if metadata:
                                return metadata
                        except Exception as e:
                            logger.debug(f"Douyin detail endpoint skipped: {e}")

                    # Tier 1: page hydration JSON survives when both detail APIs
                    # are rate-limited, including /jingxuan?modal_id= links.
                    page_urls = [
                        resolved_url,
                        f"https://www.douyin.com/video/{item_id}",
                        f"https://www.douyin.com/jingxuan?modal_id={item_id}",
                        f"https://www.iesdouyin.com/share/video/{item_id}",
                    ]
                    seen_pages = set()
                    for page_url in page_urls:
                        if not page_url or page_url in seen_pages:
                            continue
                        seen_pages.add(page_url)
                        try:
                            page_res = await client.get(page_url, headers=self.MOBILE_HEADERS)
                            if page_res.status_code != 200 or not page_res.text:
                                continue
                            metadata = self._metadata_from_html(
                                page_res.text, item_id, url_clean
                            )
                            if metadata:
                                return metadata
                        except Exception as e:
                            logger.debug(f"Douyin page fallback skipped: {e}")
                except Exception as e:
                    logger.debug(f"Douyin ttwid detail fetch exception: {e}")
        except Exception as e:
            logger.debug(f"Douyin HTTP scraping failed for {url_clean}: {e}")

        # Tier 2: unsigned httpx hits Argus (403 Uifid). Chrome signs the same
        # /aweme/detail/ call the catalog list already uses.
        if str(item_id).isdigit() and len(str(item_id)) >= 15:
            try:
                from app.scraper.douyin_list import fetch_douyin_aweme

                aweme = await fetch_douyin_aweme(item_id)
                metadata = self._metadata_from_aweme(aweme, item_id, url_clean) if aweme else None
                if metadata:
                    return metadata
            except Exception as e:
                logger.info("Douyin browser video fallback failed for %s: %s", item_id, e)

        raise RuntimeError(
            f"Douyin tạm chặn truy xuất video {item_id}; vui lòng thử lại sau ít giây."
        )
