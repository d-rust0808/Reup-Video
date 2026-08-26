"""Unit tests for Douyin/Kuaishou channel clone helpers."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.scraper.channel import (
    extract_kuaishou_photo_ids,
    extract_kuaishou_user_id,
    extract_sec_user_id,
    extract_video_ids,
    is_channel_url,
    video_page_url,
)


def test_douyin_profile_url_is_not_capped_at_forty():
    import inspect

    from app.scraper.channel import ChannelCloneService

    source = inspect.getsource(ChannelCloneService.collect)
    assert "extract_sec_user_id(raw)" in source
    assert "cap = 500" in source


def test_extract_sec_user_id_from_profile():
    url = "https://www.douyin.com/user/MS4wLjABAAAAlCWf7AEWzaYV3orgY8V4S8k8A6PNya_V_6SAadaB_6V0qJ9eQMeAfeReayknaeaG"
    sec = extract_sec_user_id(url)
    assert sec and sec.startswith("MS4wLjAB")
    assert is_channel_url(url) is True


def test_extract_video_ids_from_jingxuan():
    url = "https://www.douyin.com/jingxuan?modal_id=7671702421463764224"
    ids = extract_video_ids(url)
    assert ids == ["7671702421463764224"]
    assert is_channel_url(url) is False


def test_extract_video_ids_from_watch_path():
    blob = "https://www.douyin.com/video/7659671111111111111\n7652692222222222222"
    ids = extract_video_ids(blob)
    assert "7659671111111111111" in ids
    assert "7652692222222222222" in ids


def test_bare_id_only_on_own_line():
    blob = "note 7671702421463764224 in a sentence\n7671702421463764224"
    ids = extract_video_ids(blob)
    assert ids == ["7671702421463764224"]


def test_kuaishou_profile_and_photo():
    profile = "https://www.kuaishou.com/profile/3xabcDEF"
    assert extract_kuaishou_user_id(profile) == "3xabcDEF"
    assert is_channel_url(profile) is True
    photo = "https://www.kuaishou.com/short-video/3xphoto99"
    assert extract_kuaishou_photo_ids(photo) == ["3xphoto99"]


def test_video_page_url():
    assert video_page_url("7671702421463764224").endswith("/video/7671702421463764224")
    assert "kuaishou.com/short-video/" in video_page_url("abc", "kuaishou")


def test_empty_is_not_channel():
    assert is_channel_url("") is False
    assert extract_sec_user_id("") is None
    assert extract_video_ids("") == []


def test_catalog_entries_from_awemes_skips_junk():
    from app.scraper.douyin_list import catalog_entries_from_awemes

    rows = catalog_entries_from_awemes(
        [
            {"aweme_id": "7675543491506976430", "desc": "开饭起飞"},
            {"aweme_id": "7675543491506976430", "desc": "dup"},
            {"id": "12", "desc": "too short"},
            {"aweme_id": "abc", "desc": "not digits"},
            None,
            {"aweme_id": "7658077670655177563", "title": "绝世逃荒"},
        ]
    )
    assert [r["video_id"] for r in rows] == ["7675543491506976430", "7658077670655177563"]
    assert rows[0]["title"] == "开饭起飞"
    assert rows[0]["url"].endswith("/video/7675543491506976430")
    assert rows[1]["title"] == "绝世逃荒"


def test_douyin_collect_uses_browser_catalog(monkeypatch):
    import asyncio

    from app.scraper.channel import ChannelCloneService

    async def fake_profile(self, sec):
        return {
            "sec_user_id": sec,
            "uid": "",
            "nickname": "开饭说漫",
            "unique_id": "96885436754",
            "signature": "",
            "aweme_count": 149,
            "follower_count": 136000,
            "avatar": "",
            "url": f"https://www.douyin.com/user/{sec}",
            "platform": "douyin",
        }

    async def fake_list(self, sec, max_videos, uid=""):
        assert max_videos >= 40
        return {
            "video_ids": ["7675543491506976430", "7658077670655177563"],
            "catalog": [
                {
                    "video_id": "7675543491506976430",
                    "title": "胆子真是肥嘟嘟",
                    "url": "https://www.douyin.com/video/7675543491506976430",
                },
                {
                    "video_id": "7658077670655177563",
                    "title": "绝世逃荒",
                    "url": "https://www.douyin.com/video/7658077670655177563",
                },
            ],
        }

    monkeypatch.setattr(ChannelCloneService, "resolve_profile", fake_profile)
    monkeypatch.setattr(ChannelCloneService, "list_douyin_videos", fake_list)

    url = "https://www.douyin.com/user/MS4wLjABAAAAUCheO6MMkKFzs1MrJSMOIRz3chPjF4fhjK74-PzN3Hqj3znbUs5uyzKJ8AFINMX2"
    result = asyncio.run(ChannelCloneService().collect(url, max_videos=500))
    assert result["platform"] == "douyin"
    assert result["profile"]["nickname"] == "开饭说漫"
    assert result["video_ids"] == ["7675543491506976430", "7658077670655177563"]
    assert result["catalog"][0]["title"] == "胆子真是肥嘟嘟"
    assert result["hint"] == ""


def test_youtube_playlist_is_channel_and_watch_is_not():
    watch = "https://www.youtube.com/watch?v=cwCFU4oa11Q"
    playlist = "https://www.youtube.com/playlist?list=PL0fu92VVHU6QLihrj4A3XyuC5GoH-B2Ai"
    watch_list = watch + "&list=PL0fu92VVHU6QLihrj4A3XyuC5GoH-B2Ai"
    assert is_channel_url(playlist) is True
    assert is_channel_url("https://www.youtube.com/@builder") is True
    assert is_channel_url(watch) is False
    assert is_channel_url(watch_list) is True
    assert video_page_url("cwCFU4oa11Q", "youtube").endswith("watch?v=cwCFU4oa11Q")
