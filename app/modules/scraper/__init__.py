"""
Multi-Platform Scrapers Package.
=================================
Provides video downloaders and metadata scrapers for Douyin, Kuaishou, and Xiaohongshu.
"""

from app.scraper.manager import ScraperManager
from app.scraper.douyin import DouyinScraper
from app.scraper.kuaishou import KuaishouScraper
from app.scraper.xiaohongshu import XiaohongshuScraper

__all__ = [
    "ScraperManager",
    "DouyinScraper",
    "KuaishouScraper",
    "XiaohongshuScraper"
]
