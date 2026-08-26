import json
import os
import subprocess
import sys
from pathlib import Path

from app.services.pyvideotrans_service import (
    PyVideoTransService,
    _is_ai_unavailable_error,
    _merge_fragmented_cues,
    _translation_matches_target,
    subtitle_matches_target_language,
)


def test_scriptwriter_sends_source_context_and_rewrites_once(monkeypatch):
    from app.services.ai_scriptwriter_service import AIScriptwriterService

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            body = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "dialogues": [{
                                "index": 1,
                                "speaker_id": "spk_1",
                                "character_name": "Người con",
                                "gender": "male",
                                "emotion": "sad",
                                "translated_text": "Ngày ấy, tôi và cha đã thực sự rạn nứt.",
                            }],
                        }, ensure_ascii=False),
                    },
                }],
            }
            return json.dumps(body, ensure_ascii=False).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    service = AIScriptwriterService(api_key="test-key")
    result = service.localize_script(
        [{
            "index": 1,
            "start_time": 0.0,
            "end_time": 2.2,
            "duration": 2.2,
            "text": "那一天我和父亲彻底决裂了",
        }],
        target_lang="vi",
        title="Cha và con",
    )

    user_payload = json.loads(captured["payload"]["messages"][1]["content"])
    assert user_payload["video_title"] == "Cha và con"
    assert user_payload["dialogues"][0]["source_text"] == "那一天我和父亲彻底决裂了"
    assert "ASR có thể nhận diện tách mảnh câu" in captured["payload"]["messages"][0]["content"]
    assert "JSON phải hợp lệ tuyệt đối" in captured["payload"]["messages"][0]["content"]
    assert "max_tokens" not in captured["payload"]
    assert captured["timeout"] == 120
    assert result[0]["translated_text"] == "Ngày ấy, tôi và cha đã thực sự rạn nứt."


def test_scriptwriter_recovers_truncated_json_with_chunked_translation(monkeypatch):
    from app.services.ai_scriptwriter_service import AIScriptwriterService

    responses = [
        {
            "choices": [{
                "message": {
                    "content": '{"dialogues":[{"index":1,"translated_text":"Chuỗi bị cắt',
                },
                "finish_reason": "length",
            }],
        },
        {
            "choices": [{
                "message": {
                    "content": "1. Xin chào.\n2. Bạn khỏe không?",
                },
                "finish_reason": "stop",
            }],
        },
    ]

    class FakeResponse:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.body, ensure_ascii=False).encode("utf-8")

    def fake_urlopen(_request, timeout):
        assert timeout in (60, 120)
        return FakeResponse(responses.pop(0))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    service = AIScriptwriterService(api_key="test-key")
    result = service.localize_script([
        {
            "index": 1,
            "start_time": 0.0,
            "end_time": 1.0,
            "duration": 1.0,
            "text": "你好",
        },
        {
            "index": 2,
            "start_time": 1.0,
            "end_time": 2.0,
            "duration": 1.0,
            "text": "你好吗",
        },
    ])

    assert responses == []
    assert [item["translated_text"] for item in result] == ["Xin chào.", "Bạn khỏe không?"]


def test_pyvideotrans_cli_starts_without_qt_gui_dependency():
    project_root = Path(__file__).resolve().parents[1]
    cli_path = project_root / "app" / "modules" / "videotrans" / "cli.py"

    result = subprocess.run(
        [sys.executable, str(cli_path), "--list", "languages"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Vietnamese" in result.stdout


def test_vietnamese_translation_rejects_chinese_leakage():
    assert _translation_matches_target("Chẳng phải đôi chân nhỏ đó sao?", "vi") is True
    assert _translation_matches_target("我说怎么闻到一股骚", "vi") is False
    assert _translation_matches_target("Xin chào 他自己闻不到吗", "vi") is False
    assert _translation_matches_target("?", "vi") is False


def test_detached_question_particle_is_merged_into_previous_cue():
    merged = _merge_fragmented_cues([
        {
            "index": 1,
            "start_time": 1.0,
            "end_time": 2.0,
            "duration": 1.0,
            "text": "你这个月工资发下来了",
        },
        {
            "index": 2,
            "start_time": 2.0,
            "end_time": 2.2,
            "duration": 0.2,
            "text": "吗？",
        },
    ])

    assert len(merged) == 1
    assert merged[0]["text"] == "你这个月工资发下来了吗？"
    assert merged[0]["end_time"] == 2.2


def test_contiguous_asr_fragments_are_grouped_before_ai_translation():
    merged = _merge_fragmented_cues([
        {
            "index": 1,
            "start_time": 0.0,
            "end_time": 1.8,
            "duration": 1.8,
            "text": "那一天我和父亲彻底决",
        },
        {
            "index": 2,
            "start_time": 1.8,
            "end_time": 2.2,
            "duration": 0.4,
            "text": "裂了",
        },
    ])

    assert len(merged) == 1
    assert merged[0]["text"] == "那一天我和父亲彻底决裂了"


def test_deepseek_translation_is_localized_before_writing_srt(monkeypatch, tmp_path):
    from app.services.ai_scriptwriter_service import ai_scriptwriter_service
    from app.services.tts_service import parse_srt_segments

    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n你这个月工资发下来了\n\n"
        "2\n00:00:02,000 --> 00:00:02,200\n吗\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ai_scriptwriter_service, "is_available", lambda: True)
    monkeypatch.setenv("SUBTITLE_TRANSLATOR", "deepseek")

    def fake_localize(segments, **_kwargs):
        assert segments[0]["text"] == "你这个月工资发下来了吗"
        return [{**segments[0], "translated_text": "Lương tháng này về chưa?"}]

    monkeypatch.setattr(ai_scriptwriter_service, "localize_script", fake_localize)

    result = PyVideoTransService().translate_subtitles(
        str(source),
        target_lang="vi",
        output_dir=str(tmp_path),
        style="dub",
    )

    output_segments = parse_srt_segments(result["srt_path"])
    assert result["provider"] == "deepseek"
    assert result["localized"] is True
    assert [segment["text"] for segment in output_segments] == ["Lương tháng này về chưa?"]
    assert output_segments[0]["end_time"] == 2.2


def test_invalid_deepseek_rewrite_falls_through_to_google(monkeypatch, tmp_path):
    from app.services.ai_scriptwriter_service import ai_scriptwriter_service

    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SUBTITLE_TRANSLATOR", "deepseek")
    monkeypatch.setattr(ai_scriptwriter_service, "is_available", lambda: True)
    monkeypatch.setattr(
        ai_scriptwriter_service,
        "localize_script",
        lambda segments, **_kwargs: [{**segments[0], "translated_text": "?"}],
    )

    class FakeTranslator:
        def __init__(self, source, target):
            self.source = source
            self.target = target

        def translate(self, text):
            return "Xin chào"

    monkeypatch.setattr("deep_translator.GoogleTranslator", FakeTranslator)
    monkeypatch.setattr(PyVideoTransService, "_translate_with_cli", lambda *args, **kwargs: None)
    result = PyVideoTransService().translate_subtitles(
        str(source),
        target_lang="vi",
        output_dir=str(tmp_path),
    )

    assert result["status"] == "success"
    assert result["provider"] == "google"
    assert "Xin chào" in Path(result["srt_path"]).read_text(encoding="utf-8")


def test_deepseek_rewrite_exception_blocks_machine_translation_fallback(monkeypatch, tmp_path):
    from app.services.ai_scriptwriter_service import ai_scriptwriter_service

    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SUBTITLE_TRANSLATOR", "deepseek")
    monkeypatch.setattr(ai_scriptwriter_service, "is_available", lambda: True)
    def fail_localize(*_args, **_kwargs):
        raise RuntimeError("temporary DeepSeek QA failure")

    monkeypatch.setattr(ai_scriptwriter_service, "localize_script", fail_localize)

    class FakeTranslator:
        def __init__(self, source, target):
            self.source = source
            self.target = target

        def translate(self, text):
            return "Xin chào"

    monkeypatch.setattr("deep_translator.GoogleTranslator", FakeTranslator)
    monkeypatch.setattr(PyVideoTransService, "_translate_with_cli", lambda *args, **kwargs: None)
    result = PyVideoTransService().translate_subtitles(
        str(source),
        target_lang="vi",
        output_dir=str(tmp_path),
    )

    assert result["status"] == "success"
    assert result["provider"] == "google"


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


def test_legacy_deepseek_chat_alias_maps_to_v4_flash():
    from app.config import normalize_deepseek_model

    assert normalize_deepseek_model("deepseek-chat") == "deepseek-v4-flash"
    assert normalize_deepseek_model("deepseek-reasoner") == "deepseek-v4-pro"
    assert normalize_deepseek_model("deepseek-v4-flash") == "deepseek-v4-flash"
    assert normalize_deepseek_model("deepseek-v4-pro") == "deepseek-v4-pro"


def test_deepseek_billing_error_is_treated_as_unavailable():
    assert _is_ai_unavailable_error(
        "DeepSeek API trả HTTP 402: tài khoản hoặc API key không còn quota/thanh toán."
    )
    assert _is_ai_unavailable_error("DeepSeek API từ chối xác thực (HTTP 401).")
    assert not _is_ai_unavailable_error("AI không tạo được kịch bản Việt hợp lệ")


def test_deepseek_billing_error_allows_google_fallback(monkeypatch, tmp_path):
    from app.services.ai_scriptwriter_service import ai_scriptwriter_service

    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_scriptwriter_service, "is_available", lambda: True)

    def fake_localize(segments, **_kwargs):
        ai_scriptwriter_service.last_error = (
            "DeepSeek API trả HTTP 402: tài khoản hoặc API key không còn quota/thanh toán."
        )
        return [{**segments[0], "translated_text": segments[0]["text"]}]

    class FakeTranslator:
        def __init__(self, source, target):
            self.source = source
            self.target = target

        def translate(self, text):
            mapping = {"你好": "Xin chào"}
            if "\n" in str(text):
                return "\n".join(mapping.get(part, "Xin chào") for part in str(text).split("\n"))
            return mapping.get(str(text), "Xin chào")

    monkeypatch.setattr(ai_scriptwriter_service, "localize_script", fake_localize)
    monkeypatch.setattr("deep_translator.GoogleTranslator", FakeTranslator)
    monkeypatch.setattr(PyVideoTransService, "_translate_with_cli", lambda *args, **kwargs: None)
    monkeypatch.setenv("SUBTITLE_TRANSLATOR", "google")

    result = PyVideoTransService().translate_subtitles(
        str(source),
        target_lang="vi",
        output_dir=str(tmp_path),
    )

    assert result["status"] == "success"
    assert result["srt_path"]
    assert subtitle_matches_target_language(result["srt_path"], "vi") is True
    assert "Xin chào" in Path(result["srt_path"]).read_text(encoding="utf-8")


def test_default_translator_is_system_google_cli():
    engine = PyVideoTransService()._subtitle_translator_engine()
    assert engine in ("agy", "cli", "google")
    from app.config import settings
    assert (os.getenv("SUBTITLE_TRANSLATOR") or settings.SUBTITLE_TRANSLATOR) in ("agy", "cli", "google")


def test_resolve_agy_skips_missing_configured_bin(monkeypatch, tmp_path):
    from app.services import agy_cli_service

    missing = str(tmp_path / "no-such-agy.exe")
    monkeypatch.setenv("AGY_BIN", missing)
    resolved = agy_cli_service.resolve_agy_bin()
    assert resolved != missing


def test_silence_user_mcp_restores_original_config(tmp_path, monkeypatch):
    from app.services import agy_cli_service

    monkeypatch.setattr(agy_cli_service.Path, "home", staticmethod(lambda: tmp_path))
    cfg_dir = tmp_path / ".gemini" / "config"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "mcp_config.json"
    cfg.write_text('{"mcpServers":{"gpm-mcp":{"command":"node"}}}', encoding="utf-8")
    with agy_cli_service._silence_user_mcp():
        assert json.loads(cfg.read_text(encoding="utf-8")) == {"mcpServers": {}}
    assert "gpm-mcp" in cfg.read_text(encoding="utf-8")
    assert not (cfg_dir / "mcp_config.json.reup-backup").exists()


def test_silence_user_mcp_strips_settings_json_servers(tmp_path, monkeypatch):
    from app.services import agy_cli_service

    monkeypatch.setattr(agy_cli_service.Path, "home", staticmethod(lambda: tmp_path))
    gemini = tmp_path / ".gemini"
    (gemini / "config").mkdir(parents=True)
    (gemini / "config" / "mcp_config.json").write_text(
        '{"mcpServers":{"gpm-mcp":{"command":"node"}}}',
        encoding="utf-8",
    )
    settings = gemini / "settings.json"
    settings.write_text(
        '{"mcpServers":{"donut-browser":{"type":"http"}},"foo":1}',
        encoding="utf-8",
    )
    with agy_cli_service._silence_user_mcp():
        silenced = json.loads(settings.read_text(encoding="utf-8"))
        assert silenced["mcpServers"] == {}
        assert silenced["foo"] == 1
    restored = json.loads(settings.read_text(encoding="utf-8"))
    assert "donut-browser" in restored["mcpServers"]
    assert restored["foo"] == 1
    assert not (settings.with_name("settings.json.reup-backup")).exists()


def test_agy_timeout_kills_child_process():
    import time
    from app.services.agy_cli_service import _popen_communicate

    started = time.monotonic()
    try:
        _popen_communicate(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_sec=1,
            cwd=".",
        )
        raise AssertionError("expected timeout")
    except RuntimeError as exc:
        assert "timed out" in str(exc)
    assert time.monotonic() - started < 8


def test_agy_model_slug_adds_effort_for_flash():
    from app.services.agy_cli_service import _model_and_effort

    assert _model_and_effort("gemini-3.7-flash-high") == ("gemini-3.7-flash", "high")
    assert _model_and_effort("gemini-3.7-flash") == ("gemini-3.7-flash", "high")
    assert _model_and_effort("claude-sonnet-4-6")[1] is None


def test_agy_cli_parses_structured_translation(monkeypatch):
    from app.services import agy_cli_service

    envelope = {
        "status": "SUCCESS",
        "response": "",
        "structured_output": {
            "lines": [
                {"index": 1, "text": "Xin chào"},
                {"index": 2, "text": "Bạn khỏe không?"},
            ]
        },
    }

    monkeypatch.setattr(agy_cli_service, "is_available", lambda: True)
    monkeypatch.setattr(agy_cli_service, "complete", lambda *args, **kwargs: envelope)
    out = agy_cli_service.translate_cues(["你好", "你好吗"], target_lang="vi")
    assert out == ["Xin chào", "Bạn khỏe không?"]


def test_agy_translate_retries_then_succeeds(monkeypatch):
    from app.services import agy_cli_service

    calls = {"n": 0}
    envelope = {
        "status": "SUCCESS",
        "response": "",
        "structured_output": {"lines": [{"index": 1, "text": "Xin chào"}]},
    }

    def fake_complete(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("agy CLI timed out after 90s")
        return envelope

    monkeypatch.setattr(agy_cli_service, "is_available", lambda: True)
    monkeypatch.setattr(agy_cli_service, "complete", fake_complete)
    out = agy_cli_service.translate_cues(["你好"], target_lang="vi")
    assert out == ["Xin chào"]
    assert calls["n"] == 2


def test_stt_cache_roundtrip(tmp_path, monkeypatch):
    from app.services.pyvideotrans_service import PyVideoTransService

    svc = PyVideoTransService()
    monkeypatch.setattr(svc, "_stt_cache_dir", lambda: str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir()
    srt = tmp_path / "out.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nXin chào\n", encoding="utf-8")
    svc._stt_cache_store("abc123", str(srt), {"detected_language": "zh", "model": "base", "cue_count": 1})
    dest = tmp_path / "copy.srt"
    loaded = svc._stt_cache_load("abc123", str(dest))
    assert loaded is not None
    assert loaded["cue_count"] == 1
    assert loaded["detected_language"] == "zh"
    assert "Xin chào" in dest.read_text(encoding="utf-8")


def test_google_keeps_vietnamese_cues_and_drops_leftover_chinese(monkeypatch, tmp_path):
    from app.services.ai_scriptwriter_service import ai_scriptwriter_service

    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\n他自己闻不到吗\n\n"
        "3\n00:00:06,000 --> 00:00:08,000\n今天天气很好\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_scriptwriter_service, "is_available", lambda: False)
    monkeypatch.setattr("app.services.xai_media_service.is_available", lambda: False)

    class FakeTranslator:
        def __init__(self, source, target):
            self.source = source
            self.target = target

        def translate(self, text):
            mapping = {
                "你好": "Xin chào",
                "他自己闻不到吗": "他自己闻不到吗",
                "今天天气很好": "Hôm nay thời tiết rất đẹp",
            }
            if "\n" in str(text):
                return "\n".join(mapping.get(part, part) for part in str(text).split("\n"))
            return mapping.get(str(text), str(text))

    monkeypatch.setattr("deep_translator.GoogleTranslator", FakeTranslator)
    monkeypatch.setattr(PyVideoTransService, "_translate_with_cli", lambda *args, **kwargs: None)
    monkeypatch.setenv("SUBTITLE_TRANSLATOR", "google")

    result = PyVideoTransService().translate_subtitles(
        str(source),
        target_lang="vi",
        output_dir=str(tmp_path),
    )

    assert result["status"] == "success"
    body = Path(result["srt_path"]).read_text(encoding="utf-8")
    assert "Xin chào" in body
    assert "Hôm nay thời tiết rất đẹp" in body
    assert "吗" not in body
    assert subtitle_matches_target_language(result["srt_path"], "vi") is True
