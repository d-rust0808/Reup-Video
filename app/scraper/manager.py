"""
ScraperManager platform router, batch orchestrator, and fallback coordinator (R1 / M1.6).
"""
import asyncio
import logging
from typing import List, Optional, Any

from app.scraper.base import BaseScraper, VideoMetadata
from app.scraper.douyin import DouyinScraper
from app.scraper.kuaishou import KuaishouScraper
from app.scraper.xiaohongshu import XiaohongshuScraper
from app.scraper.downloader import StreamDownloader

logger = logging.getLogger(__name__)


class ScraperManager:
    """Platform router and scraper manager."""

    def __init__(self, output_dir: str = "data/input/raw"):
        self.output_dir = output_dir
        self.douyin = DouyinScraper()
        self.kuaishou = KuaishouScraper()
        self.xiaohongshu = XiaohongshuScraper()
        self.scrapers: List[BaseScraper] = [
            self.douyin,
            self.kuaishou,
            self.xiaohongshu,
        ]
        self.downloader = StreamDownloader(output_dir=output_dir)

    def get_scraper_for_url(self, url: str) -> BaseScraper:
        """Find matching scraper for given URL scheme and domain."""
        if not url or not isinstance(url, str) or not url.strip():
            raise ValueError("URL cannot be empty")
        url_clean = url.strip()
        for scraper in self.scrapers:
            if scraper.match_url(url_clean):
                return scraper
        raise ValueError(f"No scraper available for URL: '{url}'")

    def route_scraper(self, url: str) -> BaseScraper:
        """Alias for get_scraper_for_url."""
        return self.get_scraper_for_url(url)

    async def extract(self, url: str) -> VideoMetadata:
        """Extract metadata for single URL by routing to appropriate platform scraper."""
        scraper = self.get_scraper_for_url(url)
        return await scraper.extract(url)

    async def extract_batch(self, urls: List[str]) -> List[VideoMetadata]:
        """Extract metadata concurrently for a batch of URLs."""
        tasks = [self.extract(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        final_list: List[VideoMetadata] = []
        for url, res in zip(urls, results):
            if isinstance(res, BaseException):
                logger.error(f"Batch extract error for {url}: {res}")
            elif isinstance(res, VideoMetadata):
                final_list.append(res)
        return final_list

    async def download_batch(
        self,
        urls_or_metadatas: List[Any],
        output_dir: Optional[str] = None,
        ignore_errors: bool = False,
    ) -> List[VideoMetadata]:
        """
        Extract metadata and download stream concurrently for a list of URLs or VideoMetadata objects using asyncio.gather.
        If ignore_errors is True, individual item failures are skipped instead of raising.
        """
        target_dir = output_dir or self.output_dir

        async def _process_item(item: Any) -> VideoMetadata:
            if isinstance(item, str):
                metadata = await self.extract(item)
            elif isinstance(item, VideoMetadata):
                metadata = item
            else:
                raise ValueError(f"Invalid batch item type: {type(item)}")

            return await self.downloader.download(metadata, output_dir=target_dir)

        tasks = [_process_item(item) for item in urls_or_metadatas]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        downloaded_list: List[VideoMetadata] = []
        for item, res in zip(urls_or_metadatas, results):
            if isinstance(res, BaseException):
                if not ignore_errors:
                    raise res
                logger.warning(f"Batch item failed (ignored) for {item}: {res}")
            elif isinstance(res, VideoMetadata):
                downloaded_list.append(res)

        return downloaded_list
