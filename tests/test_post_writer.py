from app.services.post_writer import (
    _ensure_contacts,
    _fallback_post,
    _posts_from_envelope,
    _sanitize_post,
    caption_from_brief,
    extract_contacts,
    is_lazy_caption,
    is_lazy_title,
    needs_generated_copy,
    title_from_brief,
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
    assert a["title"] != b["title"]
    assert "0777704099" in a["caption"]
    assert "0777704099" in b["caption"]


def test_write_posts_never_duplicate_across_pages(monkeypatch):
    from app.services import agy_cli_service

    same = {
        "status": "SUCCESS",
        "structured_output": {
            "posts": [
                {"index": 1, "title": "Cùng một tiêu đề", "caption": "Cùng một caption kể chuyện.", "hashtags": ["reviewphim"]},
                {"index": 2, "title": "Cùng một tiêu đề", "caption": "Cùng một caption kể chuyện.", "hashtags": ["reviewphim"]},
                {"index": 3, "title": "Cùng một tiêu đề", "caption": "Cùng một caption kể chuyện.", "hashtags": ["reviewphim"]},
            ]
        },
    }
    monkeypatch.setattr(agy_cli_service, "is_available", lambda: True)
    monkeypatch.setattr(agy_cli_service, "complete", lambda *a, **k: same)
    posts = write_facebook_posts(
        intent="",
        video_brief="Phế phi bị đày vào lãnh cung. Cửu hoàng tử bị bỏ rơi. Thái hậu không cho gặp mặt.",
        page_names=["Page A", "Page B", "Page C"],
    )
    assert len(posts) == 3
    assert len({p["title"] for p in posts}) == 3
    assert len({p["caption"] for p in posts}) == 3


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


def test_title_from_brief_summarizes_not_placeholder():
    assert is_lazy_title("Video mới")
    assert is_lazy_title("Video Reup #abc123")
    assert is_lazy_title("Video #575015 · YouTube Shorts")
    assert title_from_brief("Video #575015 · YouTube Shorts") != "Video #575015 · YouTube Shorts"
    assert is_lazy_caption("Video mới #vietsub #youtube")
    assert needs_generated_copy("Video mới", "Video mới #vietsub #youtube")
    spoken = "Chưa chết thì mau dậy đi, các cô nương đợi chải đầu kìa, giờ nào rồi còn trốn trong phòng."
    assert needs_generated_copy(spoken[:50], spoken, brief=spoken + " Chuộc thân á.")
    title = title_from_brief(
        "Phế phi bị đày vào lãnh cung. Cửu hoàng tử còn đỏ hỏn đã bị bỏ rơi."
    )
    assert "lãnh cung" in title.lower() or "phế phi" in title.lower() or "hoàng tử" in title.lower()
    assert "vietsub" not in title.lower()
    assert title != "Video mới"
    caption = caption_from_brief(
        "Phế phi bị đày vào lãnh cung. Cửu hoàng tử còn đỏ hỏn đã bị bỏ rơi. Thái hậu không cho gặp mặt."
    )
    assert "phế phi" in caption.lower() or "lãnh cung" in caption.lower()
    assert "vietsub" not in caption.lower()
    assert "tháo dỡ" not in caption.lower()


def test_sanitize_rewrites_youtube_vietsub_placeholder():
    brief = (
        "Phế phi bị đày vào lãnh cung. Cửu hoàng tử còn đỏ hỏn đã bị bỏ rơi. "
        "Thái hậu không cho mẹ con gặp nhau."
    )
    cleaned = _sanitize_post(
        {
            "title": "Video mới",
            "caption": "Video mới #vietsub #youtube",
            "hashtags": ["vietsub", "youtube"],
        },
        brief=brief,
    )
    blob = (cleaned["title"] + " " + cleaned["caption"] + " " + " ".join(cleaned["hashtags"])).lower()
    assert "video mới" not in cleaned["title"].lower()
    assert "vietsub" not in blob
    assert "#youtube" not in blob
    assert "youtube" not in cleaned["hashtags"]
    assert "lãnh cung" in blob or "phế phi" in blob or "hoàng tử" in blob
    assert "video mới" not in cleaned["caption"].lower()
    tags = {t.lower() for t in cleaned["hashtags"]}
    assert "youtube" not in tags
    assert "video" not in tags
    assert "chưa" not in tags
    assert "chết" not in tags
    assert "nương" not in tags
    assert any(tag in tags for tag in ("reviewphim", "cotrang", "phimhay", "xuhuong"))


def test_hashtags_are_viral_not_spoken_words():
    brief = "Chưa chết thì mau dậy đi, các cô nương đợi chải đầu kìa, trốn trong phòng chuộc tội."
    cleaned = _sanitize_post(
        {"title": "Video mới", "caption": "Video mới #youtube", "hashtags": ["youtube", "video"]},
        brief=brief,
    )
    tags = {t.lower() for t in cleaned["hashtags"]}
    blob = cleaned["caption"].lower()
    assert "video mới" not in cleaned["title"].lower()
    assert "#youtube" not in blob
    assert "#video" not in blob
    for junk in ("chưa", "chết", "nương", "chải", "trốn", "phòng", "chuộc"):
        assert junk not in tags
        assert f"#{junk}" not in blob
    assert tags <= ({t.lower() for t in (
        "fyp", "viral", "xuhuong", "reviewphim", "phimhay", "phimtrung",
        "cotrang", "ngontinh", "drama", "review",
    )} | tags)
    assert len(cleaned["hashtags"]) <= 6


def test_posts_never_include_reup_word(monkeypatch):
    from app.services import agy_cli_service
    from app.services.post_writer import strip_reup_mentions

    assert "reup" not in strip_reup_mentions("Video reup hay #reup").lower()
    envelope = {
        "status": "SUCCESS",
        "structured_output": {
            "posts": [
                {
                    "index": 1,
                    "title": "Reup clip đập phá",
                    "caption": "Video reup này hay. Vietsub full. Gọi 0777704099 #reup #vietsub",
                    "hashtags": ["reup", "vietsub", "xaydung"],
                }
            ]
        },
    }
    monkeypatch.setattr(agy_cli_service, "is_available", lambda: True)
    monkeypatch.setattr(agy_cli_service, "complete", lambda *a, **k: envelope)
    posts = write_facebook_posts(
        intent="LH 0777704099 đập phá",
        video_brief="tháo dỡ nhà",
        page_names=["Page A"],
    )
    blob = (posts[0]["title"] + " " + posts[0]["caption"] + " " + " ".join(posts[0]["hashtags"])).lower()
    assert "reup" not in blob
    assert "vietsub" not in blob
    assert "0777704099" in posts[0]["caption"]
    assert "xaydung" in posts[0]["hashtags"]


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
    assert all("vietsub" not in (p["title"] + p["caption"] + " ".join(p["hashtags"])).lower() for p in posts)
    assert all(not is_lazy_title(p["title"]) for p in posts)
