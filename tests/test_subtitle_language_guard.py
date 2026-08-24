from app.services.pyvideotrans_service import (
    _translation_matches_target,
    subtitle_matches_target_language,
)


def test_vietnamese_translation_rejects_chinese_leakage():
    assert _translation_matches_target("Chẳng phải đôi chân nhỏ đó sao?", "vi") is True
    assert _translation_matches_target("我说怎么闻到一股骚", "vi") is False
    assert _translation_matches_target("Xin chào 他自己闻不到吗", "vi") is False


def test_mixed_vietnamese_srt_is_rejected(tmp_path):
    srt = tmp_path / "mixed_vi.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,200\n我说怎么闻到一股骚\n\n"
        "2\n00:00:01,200 --> 00:00:03,000\nĐôi chân nhỏ có mùi quá.\n",
        encoding="utf-8",
    )
    assert subtitle_matches_target_language(str(srt), "vi") is False


def test_fully_vietnamese_srt_is_accepted(tmp_path):
    srt = tmp_path / "clean_vi.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,200\nSao lại có mùi lạ thế?\n\n"
        "2\n00:00:01,200 --> 00:00:03,000\nĐôi chân nhỏ có mùi quá.\n",
        encoding="utf-8",
    )
    assert subtitle_matches_target_language(str(srt), "vi") is True
