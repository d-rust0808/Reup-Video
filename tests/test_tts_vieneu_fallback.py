import wave

import pytest

from app.modules.tts.providers import VieNeuTTSProvider
from app.services.tts_service import edge_voice_for_vieneu, tts_service


def test_edge_voice_for_vieneu_maps_gender():
    assert edge_voice_for_vieneu("vieneu:Ngọc Huyền") == "vi-VN-HoaiMyNeural"
    assert edge_voice_for_vieneu("vieneu:Trúc Ly") == "vi-VN-HoaiMyNeural"
    assert edge_voice_for_vieneu("vieneu:Phạm Tuyên") == "vi-VN-NamMinhNeural"
    assert edge_voice_for_vieneu("vieneu:Adam") == "vi-VN-NamMinhNeural"


def test_runtime_available_detects_poisoned_onnxruntime(monkeypatch):
    VieNeuTTSProvider._runtime_ok = None
    monkeypatch.setitem(
        __import__("sys").modules,
        "onnxruntime",
        type("BrokenORT", (), {})(),
    )
    assert VieNeuTTSProvider.runtime_available() is False


@pytest.mark.anyio
async def test_generate_speech_falls_back_to_edge_when_ort_is_broken(tmp_path, monkeypatch):
    VieNeuTTSProvider._runtime_ok = False
    calls = []

    class Edge:
        async def generate(self, **kwargs):
            calls.append(kwargs)
            path = kwargs["output_path"]
            with open(path, "wb") as handle:
                handle.write(b"ID3" + b"\x00" * 400)
            return path

    monkeypatch.setattr(
        "app.services.tts_service.get_tts_provider",
        lambda name: Edge() if name == "edge-tts" else (_ for _ in ()).throw(AssertionError(name)),
    )
    output = str(tmp_path / "clip.mp3")
    result = await tts_service.generate_speech(
        text="Xin chào",
        lang="vi",
        voice="vieneu:Ngọc Huyền",
        engine="vieneu",
        output_path=output,
    )
    assert result == output
    assert calls[0]["voice"] == "vi-VN-HoaiMyNeural"


@pytest.mark.anyio
async def test_generate_speech_marks_vieneu_dead_after_sessionoptions_error(tmp_path, monkeypatch):
    VieNeuTTSProvider._runtime_ok = True
    calls = []

    class Boom:
        async def generate(self, **kwargs):
            raise AttributeError("module 'onnxruntime' has no attribute 'SessionOptions'")

    class Edge:
        async def generate(self, **kwargs):
            calls.append(kwargs)
            path = kwargs["output_path"]
            with open(path, "wb") as handle:
                handle.write(b"ID3" + b"\x00" * 400)
            return path

    def get_provider(name):
        if name == "vieneu":
            return Boom()
        if name == "edge-tts":
            return Edge()
        raise AssertionError(name)

    monkeypatch.setattr("app.services.tts_service.get_tts_provider", get_provider)
    output = str(tmp_path / "clip.wav")
    await tts_service.generate_speech(
        text="Xin chào",
        lang="vi",
        voice="vieneu:Ngọc Huyền",
        engine="vieneu",
        output_path=output,
    )
    assert VieNeuTTSProvider._runtime_ok is False
    assert calls[0]["voice"] == "vi-VN-HoaiMyNeural"


@pytest.mark.anyio
async def test_synthesize_uses_edge_voice_when_vieneu_runtime_is_dead(tmp_path, monkeypatch):
    from app.services import tts_service as tts_module

    VieNeuTTSProvider._runtime_ok = False
    srt = tmp_path / "voice.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,500\nXin chào các bạn\n",
        encoding="utf-8",
    )
    voices = []

    async def fake_generate_speech(*, voice, output_path, **_kwargs):
        voices.append(voice)
        with wave.open(output_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x40" * 8000)
        return output_path

    monkeypatch.setattr(tts_service, "generate_speech", fake_generate_speech)
    monkeypatch.setattr(tts_module, "scale_audio_speed_ffmpeg", lambda *a, **k: False)
    result = await tts_service.synthesize_synchronized_tts(
        srt_path=str(srt),
        output_audio_path=str(tmp_path / "out.wav"),
        voice="vieneu:Ngọc Huyền",
        engine="vieneu",
        enable_lipsync=False,
        timeline_speed=1.0,
    )
    assert result["segment_count"] == 1
    assert voices == ["vi-VN-HoaiMyNeural"]


@pytest.mark.anyio
async def test_tts_does_not_chop_voice_to_crumbled_srt_span(monkeypatch, tmp_path):
    from app.services import tts_service as tts_module
    from app.services.tts_service import parse_srt_segments

    srt = tmp_path / "voice.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:00,613\n"
        "Sáng sớm vừa từ ngoài đồng hái về những trái ớt đỏ tươi\n\n"
        "2\n00:00:11,612 --> 00:00:12,295\nNhững trái ớt dày thịt\n",
        encoding="utf-8",
    )

    async def fake_generate_speech(*, output_path, **_kwargs):
        with wave.open(output_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x40" * int(16000 * 2.0))
        return output_path

    monkeypatch.setattr(tts_service, "generate_speech", fake_generate_speech)
    monkeypatch.setattr(tts_module, "scale_audio_speed_ffmpeg", lambda *a, **k: False)
    result = await tts_service.synthesize_synchronized_tts(
        srt_path=str(srt),
        output_audio_path=str(tmp_path / "out.wav"),
        enable_lipsync=False,
        timeline_speed=1.0,
    )
    first = parse_srt_segments(result["aligned_srt_path"])[0]
    assert first["duration"] >= 1.8
    assert result["clips"][0]["final_dur"] >= 1.8
