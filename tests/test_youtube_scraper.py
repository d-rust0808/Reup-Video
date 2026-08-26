"""YouTube extract / channel routing tests (no live network)."""

import os
from unittest.mock import patch

import pytest

from app.scraper.base import VideoMetadata
from app.scraper.channel import ChannelCloneService, is_channel_url, video_page_url
from app.scraper.downloader import StreamDownloader
from app.scraper.manager import ScraperManager
from app.scraper.youtube import (
    YoutubeScraper,
    extract_youtube_playlist_id,
    extract_youtube_urls,
    extract_youtube_video_id,
    is_youtube_feed_url,
    is_youtube_url,
)


WATCH = "https://www.youtube.com/watch?v=cwCFU4oa11Q"
WATCH_LIST = (
    "https://www.youtube.com/watch?v=cwCFU4oa11Q&list=PL0fu92VVHU6QLihrj4A3XyuC5GoH-B2Ai"
)
SHORTS = "https://www.youtube.com/shorts/cwCFU4oa11Q"
YOUTU_BE = "https://youtu.be/cwCFU4oa11Q"
PLAYLIST = "https://www.youtube.com/playlist?list=PL0fu92VVHU6QLihrj4A3XyuC5GoH-B2Ai"
CHANNEL = "https://www.youtube.com/@somehandle/videos"


def test_youtube_url_shapes():
    assert is_youtube_url(WATCH)
    assert is_youtube_url(WATCH_LIST)
    assert is_youtube_url(SHORTS)
    assert is_youtube_url(YOUTU_BE)
    assert is_youtube_url("https://m.youtube.com/watch?v=cwCFU4oa11Q")
    assert is_youtube_url("https://music.youtube.com/watch?v=cwCFU4oa11Q")
    assert not is_youtube_url("https://www.douyin.com/video/123")
    assert not is_youtube_url("https://example.com/watch?v=cwCFU4oa11Q")


def test_extract_video_id_from_watch_and_share_shapes():
    assert extract_youtube_video_id(WATCH) == "cwCFU4oa11Q"
    assert extract_youtube_video_id(WATCH_LIST) == "cwCFU4oa11Q"
    assert extract_youtube_video_id(SHORTS) == "cwCFU4oa11Q"
    assert extract_youtube_video_id(YOUTU_BE) == "cwCFU4oa11Q"
    assert extract_youtube_video_id("https://www.youtube.com/embed/cwCFU4oa11Q") == "cwCFU4oa11Q"
    assert extract_youtube_playlist_id(WATCH_LIST) == "PL0fu92VVHU6QLihrj4A3XyuC5GoH-B2Ai"
    assert extract_youtube_playlist_id(WATCH) is None


def test_feed_vs_single_video():
    assert is_youtube_feed_url(PLAYLIST) is True
    assert is_youtube_feed_url(CHANNEL) is True
    assert is_youtube_feed_url("https://www.youtube.com/@somehandle") is True
    assert is_youtube_feed_url(WATCH) is False
    assert is_youtube_feed_url(WATCH_LIST) is False
    assert is_channel_url(PLAYLIST) is True
    assert is_channel_url(CHANNEL) is True
    assert is_channel_url(WATCH_LIST) is True
    assert is_channel_url(WATCH) is False
    assert is_channel_url("https://www.douyin.com/video/7659671111111111111") is False


def test_video_page_url_youtube():
    assert video_page_url("cwCFU4oa11Q", "youtube") == WATCH
    assert "kuaishou.com/short-video/" in video_page_url("abc", "kuaishou")


def test_scraper_manager_routes_youtube():
    mgr = ScraperManager(output_dir="/tmp")
    assert isinstance(mgr.get_scraper_for_url(WATCH), YoutubeScraper)
    assert isinstance(mgr.get_scraper_for_url(WATCH_LIST), YoutubeScraper)
    assert isinstance(mgr.get_scraper_for_url(YOUTU_BE), YoutubeScraper)
    with pytest.raises(ValueError, match="No scraper"):
        mgr.get_scraper_for_url("https://example.com/watch?v=cwCFU4oa11Q")


@pytest.mark.anyio
async def test_youtube_scraper_rejects_playlist_in_video_mode():
    scraper = YoutubeScraper()
    with pytest.raises(ValueError, match="Reup Theo Kênh"):
        await scraper.extract(PLAYLIST)


@pytest.mark.anyio
async def test_extract_single_watch_uses_noplaylist(monkeypatch):
    captured = {}

    def fake_extract(url, opts):
        captured["url"] = url
        captured["noplaylist"] = opts.get("noplaylist")
        return {
            "id": "cwCFU4oa11Q",
            "title": "Clip xây dựng",
            "uploader": "Kênh Thợ",
            "duration": 42,
            "webpage_url": WATCH,
        }

    monkeypatch.setattr("app.scraper.youtube.run_yt_dlp_extract", fake_extract)
    meta = await YoutubeScraper().extract(WATCH_LIST)
    assert captured["noplaylist"] is True
    assert meta.video_id == "cwCFU4oa11Q"
    assert meta.platform == "youtube"
    assert meta.title == "Clip xây dựng"
    assert meta.author == "Kênh Thợ"
    assert meta.direct_stream_url.startswith("https://www.youtube.com/watch?v=")


@pytest.mark.anyio
async def test_collect_playlist_caps_max_videos(monkeypatch):
    def fake_extract(url, opts):
        assert opts.get("noplaylist") is False
        assert opts.get("playlistend") == 3
        return {
            "_type": "playlist",
            "id": "PL0fu92VVHU6QLihrj4A3XyuC5GoH-B2Ai",
            "title": "Playlist xây",
            "uploader": "Kênh Thợ",
            "channel": "Kênh Thợ",
            "entries": [
                {"id": "aaaaaaaaaaa", "title": "1"},
                {"id": "bbbbbbbbbbb", "title": "2"},
                {"id": "ccccccccccc", "title": "3"},
                {"id": "ddddddddddd", "title": "4"},
            ],
        }

    monkeypatch.setattr("app.scraper.youtube.run_yt_dlp_extract", fake_extract)
    result = await YoutubeScraper().collect_feed(WATCH_LIST, max_videos=3)
    assert result["platform"] == "youtube"
    assert result["video_ids"] == ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]
    assert result["profile"]["nickname"] == "Kênh Thợ"
    assert result["catalog"][0]["video_id"] == "aaaaaaaaaaa"


@pytest.mark.anyio
async def test_youtube_channel_full_catalog_is_not_capped_at_40(monkeypatch):
    captured = {}

    def fake_extract(url, opts):
        captured["playlistend"] = opts.get("playlistend")
        return {
            "_type": "playlist",
            "channel": "Happy renovation worker",
            "entries": [
                {"id": f"aaaaaaaaaa{i}", "title": f"clip {i}"}
                for i in range(10)
            ],
        }

    monkeypatch.setattr("app.scraper.youtube.run_yt_dlp_extract", fake_extract)
    result = await YoutubeScraper().collect_feed(
        "https://www.youtube.com/@hongguo/videos", max_videos=500
    )
    assert captured["playlistend"] == 500
    assert len(result["video_ids"]) == 10
    assert result["catalog"][0]["title"] == "clip 0"


@pytest.mark.anyio
async def test_youtube_download_goes_through_ytdlp(tmp_path, monkeypatch):
    payload = b"\x00\x00\x00\x1cftypisom" + (b"\xaa" * 4000)

    def fake_dl(url, dest):
        assert "cwCFU4oa11Q" in url
        with open(dest, "wb") as f:
            f.write(payload)
        return len(payload)

    monkeypatch.setattr("app.scraper.downloader.download_youtube_to_file", fake_dl)
    meta = VideoMetadata(
        video_id="cwCFU4oa11Q",
        platform="youtube",
        original_url=WATCH_LIST,
        direct_stream_url=WATCH,
        title="Clip xây dựng",
        author="Kênh Thợ",
    )
    downloader = StreamDownloader(output_dir=str(tmp_path))
    updated = await downloader.download(meta, output_dir=str(tmp_path))
    assert os.path.isfile(updated.file_path)
    assert os.path.getsize(updated.file_path) == len(payload)
    assert os.path.isfile(os.path.join(tmp_path, "cwCFU4oa11Q.mp4"))
    assert os.path.isfile(os.path.join(tmp_path, "cwCFU4oa11Q.json"))


@pytest.mark.anyio
async def test_channel_collect_routes_youtube(monkeypatch):
    async def fake_feed(self, text, max_videos=8):
        return {
            "platform": "youtube",
            "video_ids": ["cwCFU4oa11Q"],
            "profile": {"nickname": "Kênh Thợ", "platform": "youtube"},
            "hint": "",
            "channel_url": text,
            "photo_ids": [],
        }

    monkeypatch.setattr(YoutubeScraper, "collect_feed", fake_feed)
    result = await ChannelCloneService().collect(WATCH_LIST, max_videos=4)
    assert result["platform"] == "youtube"
    assert result["video_ids"] == ["cwCFU4oa11Q"]


def test_extract_youtube_urls_from_blob():
    blob = f"xem {WATCH_LIST} và {YOUTU_BE}"
    urls = extract_youtube_urls(blob)
    assert WATCH_LIST in urls
    assert YOUTU_BE in urls
