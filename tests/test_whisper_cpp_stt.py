import subprocess
import wave
from pathlib import Path

from app.services import whisper_cpp_stt


def test_find_cli_walks_nested_bin(tmp_path):
    nested = tmp_path / "Release" / "bin"
    nested.mkdir(parents=True)
    exe = nested / "whisper-cli.exe"
    exe.write_bytes(b"fake")
    assert whisper_cpp_stt.find_cli(str(tmp_path)) == str(exe)


def test_find_cli_skips_cublas_tree(tmp_path):
    cuda = tmp_path / "whisper-cpp"
    cuda.mkdir()
    (cuda / "cublas64_12.dll").write_bytes(b"x")
    (cuda / "whisper-cli.exe").write_bytes(b"cuda")
    cpu = tmp_path / "cpu"
    cpu.mkdir()
    exe = cpu / "whisper-cli.exe"
    exe.write_bytes(b"cpu")
    assert whisper_cpp_stt.find_cli(str(tmp_path)) == str(exe)


def test_transcribe_wav_parses_cli_srt(monkeypatch, tmp_path):
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"RIFF")
    exe = tmp_path / "whisper-cli.exe"
    exe.write_bytes(b"fake")
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"m" * (11 * 1024 * 1024))

    monkeypatch.setattr(whisper_cpp_stt, "_models_root", lambda: str(tmp_path))
    monkeypatch.setattr(whisper_cpp_stt, "_ensure_cli", lambda on_status=None: str(exe))
    monkeypatch.setattr(whisper_cpp_stt, "_ensure_model", lambda on_status=None: str(model))

    def fake_run(cmd, **kwargs):
        of = cmd[cmd.index("-of") + 1]
        Path(of + ".srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,800\n今天跟闺蜜去看海\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(whisper_cpp_stt.subprocess, "run", fake_run)
    cues, lang = whisper_cpp_stt.transcribe_wav(str(wav), language="zh")
    assert lang == "zh"
    assert len(cues) == 1
    assert "看海" in cues[0]["text"]


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
