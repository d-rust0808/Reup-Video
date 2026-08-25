"""Regression tests for Douyin detail and jingxuan fallbacks."""

import json
from urllib.parse import quote

import pytest

from app.scraper.douyin import DouyinScraper


def _aweme(video_id: str):
    return {
        "aweme_id": video_id,
        "desc": "Mèo đang ngủ",
        "duration": 29000,
        "author": {"nickname": "Sen"},
        "video": {
            "play_addr_h264": {
                "url_list": [
                    "https://v1.douyinvod.com/playwm/?video_id=abc&watermark=1",
                    "https://v2.douyinvod.com/play/?video_id=abc&watermark=0",
                ]
            }
        },
    }


def test_douyin_iteminfo_payload_produces_real_cdn_candidates():
    scraper = DouyinScraper()
    video_id = "7661486421500316969"

    metadata = scraper._metadata_from_payload(
        {"item_list": [_aweme(video_id)]},
        video_id,
        f"https://www.douyin.com/jingxuan?modal_id={video_id}",
    )

    assert metadata is not None
    assert metadata.video_id == video_id
    assert metadata.duration == 29.0
    assert len(metadata.stream_url_candidates) == 2
    assert all("playwm" not in url for url in metadata.stream_url_candidates)
    assert metadata.direct_stream_url == metadata.stream_url_candidates[0]


def test_douyin_jingxuan_render_data_fallback_is_url_decoded():
    scraper = DouyinScraper()
    video_id = "7661486421500316969"
    payload = quote(json.dumps({"loaderData": {"video": _aweme(video_id)}}))
    page = f'<html><script id="RENDER_DATA" type="application/json">{payload}</script></html>'

    metadata = scraper._metadata_from_html(
        page,
        video_id,
        f"https://www.douyin.com/jingxuan?modal_id={video_id}",
    )

    assert metadata is not None
    assert metadata.title == "Mèo đang ngủ"
    assert metadata.author == "Sen"


def test_real_douyin_id_containing_500_is_not_a_simulated_server_error():
    scraper = DouyinScraper()

    scraper._check_test_error_triggers(
        "https://www.douyin.com/jingxuan?modal_id=7661486421500316969"
    )

    with pytest.raises(RuntimeError, match="HTTP 500"):
        scraper._check_test_error_triggers("https://mock.test/video/500")
