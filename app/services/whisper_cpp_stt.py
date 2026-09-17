"""Standalone whisper.cpp STT — no torch, no ONNX Runtime.

On this Windows install both `torch` (c10.dll) and `onnxruntime` fail with
WinError 1114 / DLL init. whisper-cli.exe is a separate process and does not
load those Python wheels.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_CLI_ZIPS = (
    "https://github.com/ggml-org/whisper.cpp/releases/download/v1.7.6/whisper-bin-x64.zip",
    "https://github.com/ggerganov/whisper.cpp/releases/download/v1.7.6/whisper-bin-x64.zip",
)
_MODEL_URLS = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin?download=true",
    "https://github.com/ggerganov/whisper.cpp/raw/master/models/ggml-base.bin",
)
_MODEL_NAME = "ggml-base.bin"
_UA = "Reup-Video-whisper-cpp"


def _models_root() -> str:
    try:
        from app.config import settings

        root = settings.MODELS_DIR
        if not os.path.isabs(root):
            root = os.path.join(str(settings.BASE_DIR), root)
        path = os.path.join(os.path.abspath(root), "whisper-cpp")
    except Exception:
        path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "models", "whisper-cpp")
        )
    os.makedirs(path, exist_ok=True)
    return path


def _emit(on_status: Optional[Callable[[str], None]], message: str) -> None:
    from app.services.activity import emit_status

    emit_status(on_status, message)


def _http_download(url: str, dest: str, on_status: Optional[Callable[[str], None]], label: str) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    tmp = dest + ".part"
    req = Request(url, headers={"User-Agent": _UA})
    with urlopen(req, timeout=90) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        last_pct = -1
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            if total > 0:
                pct = int(got * 100 / total)
                if pct != last_pct and pct % 5 == 0:
                    last_pct = pct
                    _emit(on_status, f"🎧 Đang tải {label} {pct}%...")
            elif got % (8 * 1024 * 1024) < 1024 * 1024:
                _emit(on_status, f"🎧 Đang tải {label} {got / (1024 * 1024):.0f}MB...")
    if os.path.getsize(tmp) < 1024:
        os.remove(tmp)
        raise RuntimeError(f"Download too small: {url}")
    os.replace(tmp, dest)


def _download_first(urls: Tuple[str, ...], dest: str, on_status, label: str) -> str:
    last_err: Optional[Exception] = None
    for url in urls:
        try:
            logger.info("Downloading %s from %s", label, url)
            _http_download(url, dest, on_status, label)
            return dest
        except Exception as exc:
            last_err = exc
            logger.warning("Download %s failed (%s): %s", label, url, exc)
            if os.path.isfile(dest + ".part"):
                try:
                    os.remove(dest + ".part")
                except OSError:
                    pass
    raise RuntimeError(f"Không tải được {label}: {last_err}")


def _hf_download(repo_id: str, filename: str, dest_dir: str) -> str:
    from huggingface_hub import hf_hub_download

    return hf_hub_download(repo_id=repo_id, filename=filename, local_dir=dest_dir)


def find_cli(root: Optional[str] = None) -> Optional[str]:
    base = root or _models_root()
    names = ("whisper-cli.exe", "whisper-cli", "main.exe", "main")
    preferred = (
        os.path.join(base, "cpu"),
        os.path.join(base, "cpu", "Release"),
        os.path.join(base, "Release"),
        base,
    )
    for folder in preferred:
        if not os.path.isdir(folder):
            continue
        if _dir_has_cublas(folder):
            continue
        for want in names:
            hit = os.path.join(folder, want)
            if os.path.isfile(hit):
                return hit
    for dirpath, dirnames, files in os.walk(base):
        if _dir_has_cublas(dirpath):
            dirnames[:] = []
            continue
        lower = {name.lower(): name for name in files}
        for want in names:
            hit = lower.get(want.lower())
            if hit:
                return os.path.join(dirpath, hit)
    return None


def _dir_has_cublas(path: str) -> bool:
    try:
        names = os.listdir(path)
    except OSError:
        return False
    return any(name.lower().startswith("cublas") for name in names)


def _ensure_cli(on_status: Optional[Callable[[str], None]] = None) -> str:
    root = _models_root()
    existing = find_cli(root)
    if existing and os.path.isfile(existing):
        return existing
    cpu_dir = os.path.join(root, "cpu")
    os.makedirs(cpu_dir, exist_ok=True)
    zip_path = os.path.join(cpu_dir, "whisper-bin-x64.zip")
    _emit(on_status, "🎧 Đang tải whisper-cli.exe CPU (không dùng CUDA/torch)...")
    _download_first(_CLI_ZIPS, zip_path, on_status, "whisper-cli")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(cpu_dir)
    found = find_cli(root)
    if not found:
        raise RuntimeError("Zip whisper.cpp CPU không chứa whisper-cli.exe")
    return found


def _ensure_model(on_status: Optional[Callable[[str], None]] = None) -> str:
    root = _models_root()
    path = os.path.join(root, _MODEL_NAME)
    if os.path.isfile(path) and os.path.getsize(path) > 10 * 1024 * 1024:
        return path
    _emit(on_status, "🎧 Đang tải model Whisper ggml-base (~140MB, một lần)...")
    try:
        src = _hf_download("ggerganov/whisper.cpp", _MODEL_NAME, root)
        if os.path.abspath(src) != os.path.abspath(path):
            import shutil

            shutil.copy2(src, path)
        return path
    except Exception as exc:
        logger.warning("HF ggml-base download failed: %s", exc)
        return _download_first(_MODEL_URLS, path, on_status, "ggml-base")


def _parse_srt(path: str) -> List[Dict[str, Any]]:
    from app.services.tts_service import parse_srt_segments

    return parse_srt_segments(path)


def transcribe_wav(
    wav_path: str,
    *,
    language: Optional[str] = "zh",
    on_status: Optional[Callable[[str], None]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    cli = _ensure_cli(on_status)
    model = _ensure_model(on_status)
    lang = (language or "zh").split("-")[0] or "zh"
    work = os.path.dirname(os.path.abspath(wav_path)) or "."
    prefix = os.path.join(work, "whisper_cpp_out")
    srt_path = prefix + ".srt"
    for stale in (srt_path, prefix + ".txt"):
        if os.path.isfile(stale):
            try:
                os.remove(stale)
            except OSError:
                pass
    _emit(on_status, "🎧 whisper.cpp đang nhận dạng lời thoại (ggml-base, CPU)...")
    cmd = [
        cli,
        "-t", str(max(1, min(4, (os.cpu_count() or 4) - 1))),
        "-ng",
        "-f", os.path.abspath(wav_path),
        "-osrt",
        "-nth", "0.6",
        "-sns",
        "-np",
        "-l", lang,
        "-m", model,
        "-of", prefix,
    ]
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.run(
        cmd,
        cwd=os.path.dirname(cli) or work,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        creationflags=creation,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise RuntimeError(f"whisper.cpp exit={proc.returncode}: {err or 'không có file SRT'}")
    if not os.path.isfile(srt_path):
        logger.warning("whisper.cpp produced no SRT")
        return [], lang
    cues = _parse_srt(srt_path)
    try:
        os.remove(srt_path)
    except OSError:
        pass
    for index, cue in enumerate(cues, start=1):
        cue["index"] = index
    logger.info("whisper.cpp wrote %s cues lang=%s", len(cues), lang)
    return cues, lang
