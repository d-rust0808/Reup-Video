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
