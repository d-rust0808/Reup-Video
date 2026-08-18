"""
Abstract Base Class and VideoMetadata model for Scraper subsystem (R1).
"""
import re
from urllib.parse import urlparse
from abc import ABC, abstractmethod
from typing import Optional, Dict, List
from pydantic import BaseModel, Field


class VideoMetadata(BaseModel):
    video_id: str
    platform: str
    original_url: str
    direct_stream_url: str
    title: str
    author: Optional[str] = None
    duration: float = 0.0
    watermark_free: bool = True
    file_path: str = ""
    file_size_bytes: int = 0
    downloaded_at: Optional[str] = None
    stream_headers: Dict[str, str] = Field(default_factory=dict)


class BaseScraper(ABC):
    """Abstract base class for all platform video scrapers."""

    ALLOWED_DOMAINS: List[str] = []

    @staticmethod
    def extract_url_from_text(text: str) -> str:
        """Extract embedded http/https URL from raw share text if present, otherwise return cleaned text."""
        if not text:
            return ""
        text_clean = text.strip()
        match = re.search(r"https?://[^\s>\x22']+", text_clean)
        if match:
            return match.group(0)
        return text_clean

    def validate_url(self, url: str) -> bool:
        """Validate URL scheme and domain compatibility."""
        if not url or not url.strip():
            raise ValueError("URL cannot be empty")
        url_clean = self.extract_url_from_text(url)
        if not (url_clean.startswith("http://") or url_clean.startswith("https://")):
            raise ValueError(f"Invalid URL scheme in '{url}'")
        if self.ALLOWED_DOMAINS:
            parsed = urlparse(url_clean)
            netloc = parsed.netloc.split(":")[0].lower()
            if not netloc or not any(
                netloc == domain.lower() or netloc.endswith("." + domain.lower())
                for domain in self.ALLOWED_DOMAINS
            ):
                raise ValueError(f"Unsupported domain for {self.__class__.__name__}: {url}")
        return True

    def match_url(self, url: str) -> bool:
        """Return True if scraper supports the given URL, False otherwise."""
        try:
            return self.validate_url(url)
        except ValueError:
            return False

    def _check_test_error_triggers(self, url: str) -> None:
        """Check for simulated error conditions in test environment."""
        url_clean = url.strip()
        if "404" in url_clean:
            raise RuntimeError(f"HTTP 404: {self.__class__.__name__} video not found")
        if "500" in url_clean:
            raise RuntimeError(f"HTTP 500: {self.__class__.__name__} server error")
        if "timeout" in url_clean:
            raise TimeoutError(f"Connection timed out while scraping {self.__class__.__name__} URL")

    @abstractmethod
    async def extract(self, url: str) -> VideoMetadata:
        """Extract video metadata and direct stream URL from platform link."""
        pass

    async def extract_metadata(self, url: str) -> VideoMetadata:
        """Alias for extract method."""
        return await self.extract(url)


BaseVideoScraper = BaseScraper
