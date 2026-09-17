import wave

from app.services import onnx_whisper


def test_onnx_tokens_to_cues_splits_on_timestamps():
    class _Tok:
        def token_to_id(self, name):
            return {
                "<|endoftext|>": 50257,
                "<|notimestamps|>": 50363,
            }.get(name)

        def decode(self, ids):
            mapping = {1: "今", 2: "天", 3: "去", 4: "看", 5: "海"}
            return "".join(mapping.get(i, "") for i in ids)

    tokens = [50258, 50260, 50359, 50364, 1, 2, 50389, 3, 4, 5, 50414]
    cues = onnx_whisper._tokens_to_cues(tokens, _Tok(), 0.0)
    assert len(cues) >= 2
    assert "今" in cues[0]["text"]
    assert cues[0]["start_time"] == 0.0


def test_log_mel_shape_is_whisper_base():
    import numpy as np

    wav = (np.sin(np.linspace(0, 400, 16000 * 2)) * 0.1).astype("float32")
    mel = onnx_whisper.log_mel_spectrogram(wav)
    assert mel.shape[0] == 80
    assert mel.shape[1] > 10


def test_speech_to_text_uses_cpp_when_torch_is_broken(monkeypatch, tmp_path):
    from app.services.pyvideotrans_service import PyVideoTransService

    wav = tmp_path / "clip.stt.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 16000)

    mp4 = tmp_path / "7683114262881429474.mp4"
    mp4.write_bytes(b"fake-mp4")

    monkeypatch.setattr("app.services.onnx_whisper.torch_import_broken", lambda: True)

    def boom(*_a, **_k):
        raise AssertionError("ONNX must not run when torch/ORT DLLs are broken")

    monkeypatch.setattr("app.services.onnx_whisper.transcribe_wav", boom)

    def fake_cpp(path, language="zh", on_status=None):
        del path, on_status
        assert language == "zh"
        return (
            [{
                "index": 1,
                "start_time": 0.0,
                "end_time": 1.8,
                "duration": 1.8,
                "text": "今天跟闺蜜去看海",
            }],
            "zh",
        )

    monkeypatch.setattr("app.services.whisper_cpp_stt.transcribe_wav", fake_cpp)
    monkeypatch.setattr(
        PyVideoTransService,
        "_extract_stt_wav",
        lambda self, *_a, **_k: str(wav),
    )
    monkeypatch.setattr(PyVideoTransService, "_media_duration_sec", lambda self, _p: 1.0)
    monkeypatch.setattr(PyVideoTransService, "_stt_cache_load", lambda *a, **k: None)
    monkeypatch.setattr(PyVideoTransService, "_stt_cache_store", lambda *a, **k: None)

    result = PyVideoTransService().speech_to_text(str(mp4), output_dir=str(tmp_path), detect_lang="auto")
    assert result["status"] == "success"
    assert result["model"] == "whisper-cpp-base"
    assert result["cue_count"] == 1
    assert result["srt_path"] and (tmp_path / "7683114262881429474.srt").is_file()
