from app.services.post_writer import (
    _ensure_contacts,
    _fallback_post,
    _posts_from_envelope,
    extract_contacts,
    usable_brand_title,
    video_brief_from_srt,
    write_facebook_posts,
)


def test_usable_brand_title_drops_source_ids():
    assert usable_brand_title("douyin_123") == ""
    assert usable_brand_title("xiaohongshu_abc") == ""
    assert usable_brand_title("Đập phá nhà") == "Đập phá nhà"


def test_extract_and_keep_phone():
    intent = "Cần đập phá tháo dỡ nhà, lột gạch liên hệ 0777704099"
    assert "0777704099" in extract_contacts(intent)
    filled = _ensure_contacts("Clip hay về phá dỡ.", intent)
    assert "0777704099" in filled
    assert "đập phá" in filled.lower() or "0777704099" in filled


def test_fallback_posts_are_unique_per_page():
    intent = "Tháo dỡ nhà lột gạch LH 0777704099"
    a = _fallback_post(intent, "Đập phá", "cốt thép sàn đổ tại chỗ", "Page A", 0)
    b = _fallback_post(intent, "Đập phá", "cốt thép sàn đổ tại chỗ", "Page B", 1)
    assert a["caption"] != b["caption"]
    assert "0777704099" in a["caption"]
    assert "0777704099" in b["caption"]


def test_video_brief_strips_srt_timestamps(tmp_path):
    srt = tmp_path / "a.vi.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nTrước hết nói về thép sàn\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nphải làm đúng bản vẽ\n",
        encoding="utf-8",
    )
    brief = video_brief_from_srt(str(srt))
    assert "thép sàn" in brief
    assert "-->" not in brief


def test_write_posts_uses_agy_envelope(monkeypatch):
    from app.services import post_writer

    envelope = {
        "status": "SUCCESS",
        "structured_output": {
            "posts": [
                {
                    "index": 1,
                    "title": "Đập phá nhà đúng cách",
                    "caption": "Xem clip rồi gọi 0777704099",
                    "hashtags": ["xaydung"],
                },
                {
                    "index": 2,
                    "title": "Tháo dỡ không ẩu",
                    "caption": "Lột gạch liên hệ 0777704099",
                    "hashtags": ["thaodo"],
                },
            ]
        },
    }
    from app.services import agy_cli_service

    monkeypatch.setattr(agy_cli_service, "is_available", lambda: True)
    monkeypatch.setattr(agy_cli_service, "complete", lambda *a, **k: envelope)

    posts = write_facebook_posts(
        intent="Cần đập phá tháo dỡ nhà liên hệ 0777704099",
        brand_title="Đập phá",
        video_brief="cốt thép sàn",
        page_names=["Page A", "Page B"],
    )
    assert len(posts) == 2
    assert posts[0]["title"] != posts[1]["title"]
    assert "0777704099" in posts[0]["caption"]
    assert _posts_from_envelope(envelope, 2)[1]["title"] == "Tháo dỡ không ẩu"


def test_write_posts_fallback_when_agy_missing(monkeypatch):
    from app.services import agy_cli_service

    monkeypatch.setattr(agy_cli_service, "is_available", lambda: False)
    posts = write_facebook_posts(
        intent="LH 0909123456 đập phá nhà",
        brand_title="Đập phá Đức Thắng",
        video_brief="đổ bê tông sàn",
        page_names=["P1", "P2", "P3"],
    )
    assert len(posts) == 3
    captions = {p["caption"] for p in posts}
    assert len(captions) == 3
    assert all("0909123456" in p["caption"] for p in posts)
