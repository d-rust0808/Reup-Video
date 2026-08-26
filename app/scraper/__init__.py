"""
Scraper & Downloader Subsystem (R1).
Exposes platform scrapers, stream downloader, scraper manager, and VideoMetadata schema.
"""
from app.scraper.base import VideoMetadata, BaseScraper, BaseVideoScraper
from app.scraper.douyin import DouyinScraper
from app.scraper.kuaishou import KuaishouScraper
from app.scraper.xiaohongshu import XiaohongshuScraper
from app.scraper.youtube import YoutubeScraper
from app.scraper.downloader import StreamDownloader, AsyncStreamDownloader, VideoDownloader
from app.scraper.manager import ScraperManager
from app.scraper.channel import ChannelCloneService, is_channel_url, extract_sec_user_id, extract_video_ids

__all__ = [
    "VideoMetadata",
    "BaseScraper",
    "BaseVideoScraper",
    "DouyinScraper",
    "KuaishouScraper",
    "XiaohongshuScraper",
    "YoutubeScraper",
    "StreamDownloader",
    "AsyncStreamDownloader",
    "VideoDownloader",
    "ScraperManager",
    "ChannelCloneService",
    "is_channel_url",
    "extract_sec_user_id",
    "extract_video_ids",
]
