import json
import subprocess
import sys
from pathlib import Path

from app.services.pyvideotrans_service import (
    PyVideoTransService,
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
    assert "ASR có thể cắt một câu" in captured["payload"]["messages"][0]["content"]
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


def test_invalid_deepseek_rewrite_blocks_machine_translation_fallback(monkeypatch, tmp_path):
    from app.services.ai_scriptwriter_service import ai_scriptwriter_service

    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_scriptwriter_service, "is_available", lambda: True)
    monkeypatch.setattr(
        ai_scriptwriter_service,
        "localize_script",
        lambda segments, **_kwargs: [{**segments[0], "translated_text": "?"}],
    )
    result = PyVideoTransService().translate_subtitles(
        str(source),
        target_lang="vi",
        output_dir=str(tmp_path),
    )

    assert result["status"] == "failed"
    assert result["srt_path"] is None
    assert "tránh xuất bản dịch vô nghĩa" in result["warning"]


def test_deepseek_rewrite_exception_blocks_machine_translation_fallback(monkeypatch, tmp_path):
    from app.services.ai_scriptwriter_service import ai_scriptwriter_service

    source = tmp_path / "source.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n你好\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ai_scriptwriter_service, "is_available", lambda: True)
    def fail_localize(*_args, **_kwargs):
        raise RuntimeError("temporary DeepSeek QA failure")

    monkeypatch.setattr(ai_scriptwriter_service, "localize_script", fail_localize)
    result = PyVideoTransService().translate_subtitles(
        str(source),
        target_lang="vi",
        output_dir=str(tmp_path),
    )

    assert result["status"] == "failed"
    assert result["srt_path"] is None


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
