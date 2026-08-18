"""
Scraper & Downloader Subsystem (R1).
Exposes platform scrapers, stream downloader, scraper manager, and VideoMetadata schema.
"""
from app.scraper.base import VideoMetadata, BaseScraper, BaseVideoScraper
from app.scraper.douyin import DouyinScraper
from app.scraper.kuaishou import KuaishouScraper
from app.scraper.xiaohongshu import XiaohongshuScraper
from app.scraper.downloader import StreamDownloader, AsyncStreamDownloader, VideoDownloader
from app.scraper.manager import ScraperManager

__all__ = [
    "VideoMetadata",
    "BaseScraper",
    "BaseVideoScraper",
    "DouyinScraper",
    "KuaishouScraper",
    "XiaohongshuScraper",
    "StreamDownloader",
    "AsyncStreamDownloader",
    "VideoDownloader",
    "ScraperManager",
]
