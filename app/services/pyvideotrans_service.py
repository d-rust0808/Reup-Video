"""
PyVideoTrans Integration Service.
=================================
Bridges pyVideoTrans AI translation, speech recognition (STT), subtitle translation (STS),
and text-to-speech (TTS) engines into the unified Reup-Video processing pipeline.

Target Path: app/services/pyvideotrans_service.py
"""

import os
import re
import sys
import json
import hashlib
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Dict, Any, Optional, List, Tuple

from app.modules.videotrans.runner import PyVideoTransRunner
from app.services.vietsub_rules import (
    CHUNK_SIZE,
    PROMPT_VERSION,
    contains_cjk,
    infer_stt_source_lang,
    is_invalid_translation,
    merge_particles_and_shorts,
    reflow_incomplete_sentences,
    regroup_words_to_sentences,
    resolve_vietsub_style,
    salvage_stt_cues,
    split_long_cues,
    VI_REFLOW_MAX_CHARS,
    VI_REFLOW_MAX_DUR,
    VI_REFLOW_MAX_GAP,
    stt_hard_fail_reason,
    translation_matches_target,
    vietnamese_fail_reason,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_VIDEOTRANS_PATH = str(PROJECT_ROOT / "app" / "modules" / "videotrans")
_WHISPER_CACHE: Dict[str, Any] = {"model": None, "name": None, "device": None}
_STT_DECODE_TAG = "b5c4"
STT_GAP_MIN_SEC = 8.0
STT_OPENING_SEC = 150.0
STT_OPENING_MIN_GAP = 3.5
STT_OPENING_MIN_COVERAGE = 0.28
STT_GAP_PAD_SEC = 0.40
STT_GAP_MAX_FILL = 16
_STT_VAD = {
    "threshold": 0.35,
    "min_silence_duration_ms": 700,
    "speech_pad_ms": 450,
    "min_speech_duration_ms": 120,
}

_is_invalid_translation = is_invalid_translation
_contains_cjk = contains_cjk
_translation_matches_target = translation_matches_target


def _is_ai_unavailable_error(reason: Optional[str]) -> bool:
    """True when a configured LLM cannot be used (auth/billing), so fallbacks may run."""
    text = (reason or "").lower()
    if not text:
        return False
    return any(
        token in text
        for token in (
            "http 401",
            "http 402",
            "http 403",
            "payment required",
            "không còn quota",
            "từ chối xác thực",
        )
    )


def _merge_fragmented_cues(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Particle/short-fragment safety net. Sentence grouping happens at STT time."""
    return merge_particles_and_shorts(segments)


def subtitle_matches_target_language(srt_path: Optional[str], target_lang: str = "vi") -> bool:
    """Return True only when every non-metadata SRT line matches the requested language."""
    if not srt_path or not os.path.isfile(srt_path):
        return False
    try:
        with open(srt_path, "r", encoding="utf-8-sig") as f:
            text_lines = [
                line.strip() for line in f
                if line.strip() and not line.strip().isdigit() and "-->" not in line
            ]
    except (OSError, UnicodeError):
        return False
    return bool(text_lines) and all(_translation_matches_target(line, target_lang) for line in text_lines)


def find_stt_gaps(
    cues: List[Dict[str, Any]],
    duration: float,
    min_gap: float = STT_GAP_MIN_SEC,
    opening_sec: float = STT_OPENING_SEC,
    opening_min_gap: float = STT_OPENING_MIN_GAP,
) -> List[Tuple[float, float]]:
    """Return [start, end) holes on the timeline with no STT cue (opening narration, VAD skips)."""
    dur = max(0.0, float(duration or 0.0))
    if dur < min(min_gap, opening_min_gap):
        return []
    spans = []
    for cue in cues or []:
        start = float(cue.get("start_time") or 0.0)
        end = float(cue.get("end_time") or start)
        if end > start:
            spans.append((start, end))
    spans.sort()
    gaps: List[Tuple[float, float]] = []
    cursor = 0.0
    for start, end in spans:
        need = opening_min_gap if cursor < opening_sec else min_gap
        if start - cursor >= need:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    need = opening_min_gap if cursor < opening_sec else min_gap
    if dur - cursor >= need:
        gaps.append((cursor, dur))
    return gaps


def opening_speech_coverage(cues: List[Dict[str, Any]], window: float = 90.0) -> float:
    """Seconds of STT inside the first `window` seconds."""
    win = max(1.0, float(window or 90.0))
    covered = 0.0
    for cue in cues or []:
        start = float(cue.get("start_time") or 0.0)
        end = float(cue.get("end_time") or start)
        a = max(0.0, start)
        b = min(win, end)
        if b > a:
            covered += b - a
    return covered


def _cues_to_srt(segments: List[Dict[str, Any]], out_srt: str) -> str:
    """Write cue dicts to an SRT file, skipping empty texts."""
    from app.services.tts_service import format_srt_timestamp

    blocks: List[str] = []
    index = 1
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start_time") or 0.0)
        end = max(start + 0.2, float(segment.get("end_time") or 0.0))
        blocks.append(
            f"{index}\n{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n{text}\n"
        )
        index += 1
    os.makedirs(os.path.dirname(os.path.abspath(out_srt)) or ".", exist_ok=True)
    with open(out_srt, "w", encoding="utf-8") as handle:
        handle.write("\n".join(blocks) + ("\n" if blocks else ""))
    return out_srt


def _google_translate_segments(
    segments: List[Dict[str, Any]],
    target_lang: str,
    on_status: Optional[Callable[[str], None]] = None,
) -> tuple[List[Dict[str, Any]], int]:
    """Translate merged ASR cues with Google. Drop leftover source-language lines instead of failing all."""
    from deep_translator import GoogleTranslator
    from app.services.activity import emit_status

    if not segments:
        return [], 0
    translator = GoogleTranslator(source="auto", target=target_lang or "vi")
    emit_status(on_status, f"🌐 Google Translate: {len(segments)} câu...")
    texts = [str(segment.get("text") or "").strip() for segment in segments]
    translated: List[Optional[str]] = [None] * len(texts)
    chunk_size = 8
    for start in range(0, len(texts), chunk_size):
        chunk = texts[start:start + chunk_size]
        try:
            blob = translator.translate("\n".join(chunk))
            parts = [part.strip() for part in str(blob or "").split("\n") if part.strip()]
            if len(parts) != len(chunk):
                raise ValueError("translated line count mismatch")
            for offset, part in enumerate(parts):
                translated[start + offset] = part
        except Exception:
            for offset, source in enumerate(chunk):
                try:
                    translated[start + offset] = translator.translate(source)
                except Exception:
                    translated[start + offset] = None
        emit_status(
            on_status,
            f"🌐 Google Translate {min(start + chunk_size, len(texts))}/{len(texts)} câu...",
        )

    kept: List[Dict[str, Any]] = []
    skipped = 0
    for segment, text in zip(segments, translated):
        if _translation_matches_target(text, target_lang):
            cue = dict(segment)
            cue["text"] = str(text).strip()
            kept.append(cue)
        else:
            skipped += 1
    return kept, skipped


class PyVideoTransError(Exception):
    """Raised when PyVideoTrans operation fails."""
    pass


class PyVideoTransService:
    """Service wrapper for PyVideoTrans translation, STT, STS, and TTS engine."""

    def __init__(self, repo_path: Optional[str] = None):
        self.repo_path = repo_path or MODULE_VIDEOTRANS_PATH
        self.runner = PyVideoTransRunner(repo_path=self.repo_path)
        self.cli_path = os.path.join(self.repo_path, "cli.py")
        if not os.path.exists(self.cli_path):
            raise PyVideoTransError(f"pyVideoTrans CLI entrypoint not found at {self.cli_path}")

    def run_cli_command(self, args_list: List[str], timeout: int = 600) -> Dict[str, Any]:
        """Runs PyVideoTrans CLI command asynchronously or synchronously."""
        python_executable = sys.executable
        cmd = [python_executable, self.cli_path] + args_list

        env = dict(os.environ)
        parent_modules = str(Path(self.repo_path).resolve().parent)
        env["PYTHONPATH"] = parent_modules + os.pathsep + env.get("PYTHONPATH", "")

        logger.info(f"Executing PyVideoTrans CLI: {' '.join(cmd)}")
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            cwd=self.repo_path,
            env=env
        )

        if proc.returncode != 0:
            stderr_msg = proc.stderr.strip() if proc.stderr else "Unknown error"
            logger.error(f"PyVideoTrans CLI execution failed ({proc.returncode}): {stderr_msg}")
            raise PyVideoTransError(f"PyVideoTrans error: {stderr_msg}")

        return {
            "status": "success",
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip()
        }

    def _find_srt_file(self, source_path: str, search_dir: str) -> Optional[str]:
        """Discovers output SRT file matching source filename in search directory."""
        return self.runner._find_srt_file(source_path, search_dir)

    def _create_stub_srt(self, video_or_audio_path: str, target_dir: str) -> str:
        """Generates a stub fallback .srt subtitle file when STT fails or whisper is unavailable."""
        base_stem = os.path.splitext(os.path.basename(video_or_audio_path))[0]
        stub_srt_path = os.path.join(target_dir, f"{base_stem}.srt")
        os.makedirs(target_dir, exist_ok=True)
        stub_content = "1\n00:00:00,000 --> 00:00:05,000\n[Sample Subtitle]\n"
        with open(stub_srt_path, "w", encoding="utf-8") as f:
            f.write(stub_content)
        return stub_srt_path

    def _whisper_device(self) -> tuple:
        from app.services.onnx_whisper import torch_import_broken

        if torch_import_broken():
            return "cpu", "int8"
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda", "float16"
        except Exception:
            pass
        return "cpu", "int8"

    def _normalize_whisper_lang(self, detect_lang: str) -> Optional[str]:
        if not detect_lang or detect_lang in ("auto", "unknown"):
            return None
        lang = detect_lang.lower().strip()
        aliases = {
            "zh-cn": "zh", "zh-hans": "zh", "zh-hant": "zh", "zh-tw": "zh",
            "chinese": "zh", "cn": "zh", "vi-vn": "vi", "vietnamese": "vi",
            "en-us": "en", "en-gb": "en", "english": "en",
            "ja-jp": "ja", "japanese": "ja", "ko-kr": "ko", "korean": "ko",
        }
        return aliases.get(lang, lang.split("-")[0])

    def _resolve_whisper_lang(self, detect_lang: str, media_path: str = "") -> Optional[str]:
        resolved = self._normalize_whisper_lang(detect_lang)
        if resolved:
            return resolved
        inferred = infer_stt_source_lang(media_path)
        return self._normalize_whisper_lang(inferred or "")

    def _whisper_download_root(self) -> str:
        try:
            from app.config import settings
            root = settings.MODELS_DIR
            if not os.path.isabs(root):
                root = os.path.join(str(settings.BASE_DIR), root)
            root = os.path.abspath(root)
        except Exception:
            root = os.path.abspath(os.path.join(PROJECT_ROOT, "data", "models"))
        os.makedirs(root, exist_ok=True)
        return root

    def _whisper_is_cached(self, name: str, download_root: str) -> bool:
        folder = os.path.join(download_root, f"models--Systran--faster-whisper-{name}")
        return os.path.isdir(folder)

    def _stt_cache_dir(self) -> str:
        try:
            from app.config import settings
            root = settings.CACHE_DIR
            if not os.path.isabs(root):
                root = os.path.join(str(settings.BASE_DIR), root)
            path = os.path.join(os.path.abspath(root), "stt")
        except Exception:
            path = os.path.abspath(os.path.join(PROJECT_ROOT, "data", "cache", "stt"))
        os.makedirs(path, exist_ok=True)
        return path

    def _stt_cache_key(
        self,
        video_or_audio_path: str,
        detect_lang: Optional[str],
        model_name: str,
        max_seconds: Optional[float],
    ) -> str:
        try:
            st = os.stat(video_or_audio_path)
            stamp = f"{int(st.st_mtime)}:{st.st_size}"
        except OSError:
            stamp = "missing"
        payload = "|".join(
            [
                os.path.abspath(video_or_audio_path),
                stamp,
                str(detect_lang or "auto"),
                str(model_name or "base"),
                str(max_seconds or ""),
                _STT_DECODE_TAG,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def _stt_cache_load(self, key: str, dest_srt: str) -> Optional[Dict[str, Any]]:
        cache_dir = self._stt_cache_dir()
        srt_src = os.path.join(cache_dir, f"{key}.srt")
        meta_src = os.path.join(cache_dir, f"{key}.json")
        if not (os.path.isfile(srt_src) and os.path.getsize(srt_src) > 32):
            return None
        try:
            os.makedirs(os.path.dirname(dest_srt), exist_ok=True)
            with open(srt_src, "r", encoding="utf-8") as src, open(dest_srt, "w", encoding="utf-8") as dst:
                dst.write(src.read())
            meta: Dict[str, Any] = {}
            if os.path.isfile(meta_src):
                meta = json.loads(Path(meta_src).read_text(encoding="utf-8"))
            cue_count = int(meta.get("cue_count") or 0)
            if cue_count <= 0:
                cue_count = len(re.findall(r"^\d+\s*$", Path(dest_srt).read_text(encoding="utf-8"), re.M))
            from app.services.tts_service import parse_srt_segments

            split = split_long_cues(parse_srt_segments(dest_srt))
            if split:
                _cues_to_srt(split, dest_srt)
                cue_count = len(split)
            return {
                "status": "success",
                "srt_path": dest_srt,
                "detected_language": meta.get("detected_language"),
                "model": meta.get("model") or "cache",
                "cue_count": cue_count,
                "cached": True,
            }
        except Exception as exc:
            logger.warning(f"STT cache load failed: {exc}")
            return None

    def _stt_cache_store(self, key: str, srt_path: str, info: Dict[str, Any]) -> None:
        cache_dir = self._stt_cache_dir()
        try:
            dest = os.path.join(cache_dir, f"{key}.srt")
            with open(srt_path, "r", encoding="utf-8") as src, open(dest, "w", encoding="utf-8") as dst:
                dst.write(src.read())
            Path(os.path.join(cache_dir, f"{key}.json")).write_text(
                json.dumps(info, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"STT cache store failed: {exc}")

    def _media_duration_sec(self, path: str) -> float:
        try:
            from app.services.tts_service import get_audio_duration
            return float(get_audio_duration(path) or 0.0)
        except Exception:
            return 0.0

    def _extract_stt_wav(self, video_or_audio_path: str, target_dir: str, max_seconds: Optional[float] = None) -> str:
        """Extract 16 kHz mono WAV so Whisper does not decode the full video."""
        from app.services.audio_service import find_ffmpeg_binary
        ffmpeg_bin = find_ffmpeg_binary()
        wav_path = os.path.join(target_dir, os.path.splitext(os.path.basename(video_or_audio_path))[0] + ".stt.wav")
        if not ffmpeg_bin:
            return video_or_audio_path
        cmd = [
            ffmpeg_bin, "-y", "-threads", "0", "-hide_banner", "-loglevel", "error",
            "-i", video_or_audio_path,
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        ]
        if max_seconds and max_seconds > 0:
            cmd.extend(["-t", f"{float(max_seconds):.2f}"])
        cmd.append(wav_path)
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0 and os.path.exists(wav_path) and os.path.getsize(wav_path) > 1024:
                return wav_path
        except Exception as e:
            logger.warning(f"STT wav extract failed: {e}")
        return video_or_audio_path

    def _load_whisper_model(self, names: List[str], device: str, compute_type: str):
        import faster_whisper
        from app.config import settings
        download_root = self._whisper_download_root()
        cache = _WHISPER_CACHE
        for name in names:
            if cache["model"] is not None and cache["name"] == name and cache["device"] == device:
                return cache["model"], name
        last_err = None
        cached = [n for n in names if self._whisper_is_cached(n, download_root)]
        uncached = [n for n in names if n not in cached]
        ordered = cached + uncached
        for name in ordered:
            for local_only in (True, False):
                try:
                    model = faster_whisper.WhisperModel(
                        name,
                        device=device,
                        compute_type=compute_type,
                        download_root=download_root,
                        local_files_only=local_only,
                        cpu_threads=settings.STT_CPU_THREADS if device == "cpu" else 0,
                        num_workers=settings.STT_WORKERS,
                    )
                    cache["model"] = model
                    cache["name"] = name
                    cache["device"] = device
                    logger.info(f"Loaded faster-whisper '{name}' local_only={local_only} root={download_root}")
                    return model, name
                except Exception as e:
                    last_err = e
                    logger.warning(f"faster-whisper '{name}' local_only={local_only} failed: {e}")
        raise RuntimeError(f"Could not load any whisper model: {last_err}")

    def _whisper_segments_to_cues(self, segments) -> List[Dict[str, Any]]:
        words = []
        raw_cues: List[Dict[str, Any]] = []
        for seg in segments or []:
            wlist = getattr(seg, "words", None) or []
            if wlist:
                for w in wlist:
                    token = (getattr(w, "word", None) or getattr(w, "text", None) or "").strip()
                    if not token:
                        continue
                    words.append({
                        "text": token,
                        "start": float(getattr(w, "start", 0.0) or 0.0),
                        "end": float(getattr(w, "end", 0.0) or 0.0),
                    })
            text = (getattr(seg, "text", None) or "").strip()
            text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
            if text:
                start = float(getattr(seg, "start", 0.0) or 0.0)
                end = float(getattr(seg, "end", start + 0.5) or (start + 0.5))
                raw_cues.append({
                    "start_time": start,
                    "end_time": max(start + 0.35, end),
                    "duration": max(0.35, end - start),
                    "text": text,
                })
        if words:
            return regroup_words_to_sentences(words)
        return split_long_cues(raw_cues)

    def _decode_whisper_cues(
        self,
        model,
        audio_path: str,
        *,
        language: Optional[str],
        use_vad: bool,
        audio_dur: float = 0.0,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Tuple[List[Dict[str, Any]], Any]:
        from app.services.activity import emit_status

        kwargs: Dict[str, Any] = {
            "language": language,
            "vad_filter": use_vad,
            "beam_size": 5,
            "best_of": 5,
            "temperature": 0.0,
            "condition_on_previous_text": False,
            "word_timestamps": True,
            "no_speech_threshold": 0.45,
        }
        if use_vad:
            kwargs["vad_parameters"] = _STT_VAD
        prev_tqdm = os.environ.get("TQDM_DISABLE")
        os.environ["TQDM_DISABLE"] = "1"
        try:
            segments_iter, info = model.transcribe(audio_path, **kwargs)
            segments = []
            last_emit_end = -30.0
            for seg in segments_iter:
                segments.append(seg)
                end = float(getattr(seg, "end", 0.0) or 0.0)
                if end - last_emit_end >= 20 or len(segments) == 1:
                    last_emit_end = end
                    if audio_dur > 0:
                        pct = max(0, min(100, int(end * 100 / audio_dur)))
                        emit_status(
                            on_status,
                            f"🎧 Whisper đã nhận {len(segments)} câu (~{end:.0f}/{audio_dur:.0f}s, {pct}%)...",
                        )
                    else:
                        emit_status(
                            on_status,
                            f"🎧 Whisper đã nhận {len(segments)} câu (~{end:.0f}s audio)...",
                        )
        finally:
            if prev_tqdm is None:
                os.environ.pop("TQDM_DISABLE", None)
            else:
                os.environ["TQDM_DISABLE"] = prev_tqdm
        return self._whisper_segments_to_cues(segments), info

    def _shift_cues(self, cues: List[Dict[str, Any]], offset: float) -> List[Dict[str, Any]]:
        shifted = []
        for cue in cues:
            item = dict(cue)
            start = float(item.get("start_time") or 0.0) + offset
            end = float(item.get("end_time") or start) + offset
            item["start_time"] = max(0.0, start)
            item["end_time"] = max(item["start_time"] + 0.2, end)
            item["duration"] = item["end_time"] - item["start_time"]
            shifted.append(item)
        return shifted

    def _merge_stt_cues(
        self,
        existing: List[Dict[str, Any]],
        extras: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        merged = list(existing) + list(extras)
        merged.sort(key=lambda item: float(item.get("start_time") or 0.0))
        merged = split_long_cues(reflow_incomplete_sentences(merge_particles_and_shorts(merged)))
        for i, item in enumerate(merged, start=1):
            item["index"] = i
        return merged

    def _transcribe_audio_slice(
        self,
        model,
        audio_path: str,
        start: float,
        end: float,
        *,
        language: Optional[str],
        ffmpeg_bin: str,
        use_vad: bool = False,
    ) -> List[Dict[str, Any]]:
        slice_start = max(0.0, float(start))
        slice_end = max(slice_start + 0.5, float(end))
        slice_dur = slice_end - slice_start
        handle, slice_path = tempfile.mkstemp(prefix="stt_gap_", suffix=".wav")
        os.close(handle)
        try:
            cmd = [
                ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{slice_start:.3f}", "-t", f"{slice_dur:.3f}",
                "-i", audio_path,
                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                slice_path,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode != 0 or not os.path.isfile(slice_path) or os.path.getsize(slice_path) < 2048:
                return []
            kwargs: Dict[str, Any] = {
                "language": language,
                "vad_filter": use_vad,
                "beam_size": 5,
                "best_of": 5,
                "temperature": 0.0,
                "condition_on_previous_text": False,
                "word_timestamps": True,
                "no_speech_threshold": 0.45,
            }
            if use_vad:
                kwargs["vad_parameters"] = _STT_VAD
            segments_iter, _info = model.transcribe(slice_path, **kwargs)
            sliced = self._whisper_segments_to_cues(list(segments_iter))
            return self._shift_cues(sliced, slice_start)
        except Exception as exc:
            logger.warning("STT slice %.1f-%.1fs failed: %s", slice_start, slice_end, exc)
            return []
        finally:
            try:
                os.remove(slice_path)
            except OSError:
                pass

    def _refill_stt_gaps(
        self,
        model,
        audio_path: str,
        cues: List[Dict[str, Any]],
        *,
        language: Optional[str],
        duration: float,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Re-run Whisper on long holes so opening narration is not left mute."""
        from app.services.activity import emit_status
        from app.services.audio_service import find_ffmpeg_binary

        ffmpeg_bin = find_ffmpeg_binary()
        if not ffmpeg_bin:
            return cues
        extras: List[Dict[str, Any]] = []
        work = list(cues)
        opening = min(STT_OPENING_SEC, float(duration or 0.0))
        if opening >= 20 and opening_speech_coverage(work, opening) < opening * STT_OPENING_MIN_COVERAGE:
            emit_status(
                on_status,
                f"🎧 Whisper chạy lại {opening:.0f}s đầu (lời nữ/kể chuyện dưới nhạc)...",
            )
            recovered = self._transcribe_audio_slice(
                model,
                audio_path,
                0.0,
                opening,
                language=language,
                ffmpeg_bin=ffmpeg_bin,
                use_vad=False,
            )
            holes = find_stt_gaps(
                work,
                opening,
                min_gap=STT_OPENING_MIN_GAP,
                opening_sec=opening,
                opening_min_gap=STT_OPENING_MIN_GAP,
            )
            for cue in recovered:
                mid = (float(cue["start_time"]) + float(cue["end_time"])) / 2.0
                if any(start - 0.15 <= mid <= end + 0.15 for start, end in holes):
                    extras.append(cue)
            if extras:
                logger.info("STT opening refill recovered %d cues in first %.0fs", len(extras), opening)
                work = self._merge_stt_cues(work, extras)
                extras = []
        gaps = find_stt_gaps(work, duration, min_gap=STT_GAP_MIN_SEC)
        if not gaps and not extras:
            return work
        for index, (gap_start, gap_end) in enumerate(gaps[:STT_GAP_MAX_FILL], start=1):
            slice_start = max(0.0, gap_start - STT_GAP_PAD_SEC)
            slice_end = min(duration, gap_end + STT_GAP_PAD_SEC)
            slice_dur = slice_end - slice_start
            if slice_dur < 3.0:
                continue
            emit_status(
                on_status,
                f"🎧 Whisper chạy lại khoảng trống {gap_start:.0f}–{gap_end:.0f}s ({index}/{min(len(gaps), STT_GAP_MAX_FILL)})...",
            )
            shifted = self._transcribe_audio_slice(
                model,
                audio_path,
                slice_start,
                slice_end,
                language=language,
                ffmpeg_bin=ffmpeg_bin,
                use_vad=False,
            )
            kept = []
            for cue in shifted:
                mid = (float(cue["start_time"]) + float(cue["end_time"])) / 2.0
                if gap_start - 0.05 <= mid <= gap_end + 0.05:
                    kept.append(cue)
            extras.extend(kept)
            logger.info(
                "STT gap fill %.1f-%.1fs recovered %d cues",
                gap_start, gap_end, len(kept),
            )
        if not extras:
            return work
        return self._merge_stt_cues(work, extras)

    def _write_srt(self, srt_path: str, segments) -> int:
        count = 0
        with open(srt_path, "w", encoding="utf-8") as f:
            for seg in segments:
                text = (getattr(seg, "text", None) or "").strip()
                text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
                if not text:
                    continue
                start = float(getattr(seg, "start", 0.0) or 0.0)
                end = float(getattr(seg, "end", start + 0.5) or (start + 0.5))
                if end <= start:
                    end = start + 0.4
                t_s = f"{int(start//3600):02d}:{int((start%3600)//60):02d}:{int(start%60):02d},{int((start%1)*1000):03d}"
                t_e = f"{int(end//3600):02d}:{int((end%3600)//60):02d}:{int(end%60):02d},{int((end%1)*1000):03d}"
                count += 1
                f.write(f"{count}\n{t_s} --> {t_e}\n{text}\n\n")
        return count

    def _write_cue_dicts(self, srt_path: str, cues: List[Dict[str, Any]]) -> int:
        if not cues:
            return 0
        _cues_to_srt(cues, srt_path)
        return len(cues)

    def _stt_gate_result(
        self,
        srt_path: str,
        detected: Optional[str],
        model_name: str,
        cue_count: int,
    ) -> Dict[str, Any]:
        from app.services.tts_service import parse_srt_segments

        cues = parse_srt_segments(srt_path) if os.path.isfile(srt_path) else []
        salvaged = salvage_stt_cues(cues)
        if salvaged and [str(c.get("text") or "") for c in salvaged] != [str(c.get("text") or "") for c in cues]:
            self._write_cue_dicts(srt_path, salvaged)
            cues = salvaged
            cue_count = len(cues)
        fail_reason = stt_hard_fail_reason(cues)
        if fail_reason:
            logger.warning(f"STT hard-fail ({fail_reason}): {len(cues)} cues -> skip Vietsub")
            return {
                "status": "needs_review",
                "srt_path": None,
                "detected_language": detected,
                "model": model_name,
                "cue_count": cue_count or len(cues),
                "stt_fail_reason": fail_reason,
            }
        return {
            "status": "success",
            "srt_path": srt_path,
            "detected_language": detected,
            "model": model_name,
            "cue_count": cue_count or len(cues),
        }

    def _stt_finish_from_cues(
        self,
        cues: Optional[List[Dict[str, Any]]],
        detected: Optional[str],
        used_name: str,
        srt_path: str,
        cache_key: str,
    ) -> Dict[str, Any]:
        count = self._write_cue_dicts(srt_path, cues) if cues else 0
        if count > 0 and os.path.exists(srt_path) and os.path.getsize(srt_path) > 0:
            gated = self._stt_gate_result(srt_path, detected, used_name, count)
            if gated["status"] == "success":
                logger.info("STT %s wrote %s cues (%s) -> %s", used_name, count, detected, srt_path)
                self._stt_cache_store(
                    cache_key,
                    srt_path,
                    {
                        "detected_language": detected,
                        "model": used_name,
                        "cue_count": count,
                    },
                )
                return gated
            return gated
        logger.warning("STT %s produced zero cues", used_name)
        return {
            "status": "empty",
            "srt_path": None,
            "detected_language": detected,
            "model": used_name,
            "stt_fail_reason": "empty_audio",
        }

    def speech_to_text(
        self,
        video_or_audio_path: str,
        output_dir: Optional[str] = None,
        model_name: str = "base",
        recogn_type: int = 0,
        detect_lang: str = "auto",
        max_seconds: Optional[float] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Performs Speech-to-Text (STT) transcription with cached faster-whisper."""
        from app.services.activity import emit_status

        if not os.path.exists(video_or_audio_path):
            raise FileNotFoundError(f"Input file not found: {video_or_audio_path}")

        target_dir = os.path.abspath(output_dir) if output_dir else os.path.dirname(os.path.abspath(video_or_audio_path))
        os.makedirs(target_dir, exist_ok=True)

        w_lang = self._resolve_whisper_lang(detect_lang, video_or_audio_path)
        requested = (model_name or "base").strip() or "base"
        # tiny wrecks Chinese + BGM; small is never selected (SPEC: base only).
        if requested in ("tiny", "small"):
            requested = "base"
        model_candidates: List[str] = ["base"]

        base_stem = os.path.splitext(os.path.basename(video_or_audio_path))[0]
        srt_path = os.path.join(target_dir, f"{base_stem}.srt")
        cache_key = self._stt_cache_key(video_or_audio_path, w_lang, requested, max_seconds)
        cached = self._stt_cache_load(cache_key, srt_path)
        if cached:
            gated = self._stt_gate_result(
                srt_path,
                cached.get("detected_language"),
                cached.get("model") or "cache",
                int(cached.get("cue_count") or 0),
            )
            if gated["status"] == "success":
                emit_status(
                    on_status,
                    f"🎧 Dùng lại Whisper đã nhận trước đó ({cached.get('cue_count') or 0} câu) — bỏ qua nhận dạng lại.",
                )
                return gated
            logger.info("STT cache failed quality gate; re-running Whisper")

        emit_status(on_status, "🎧 Đang tách audio WAV cho Whisper...")
        audio_for_stt = self._extract_stt_wav(video_or_audio_path, target_dir, max_seconds=max_seconds)
        tmp_wav = audio_for_stt if audio_for_stt != video_or_audio_path else None
        audio_dur = self._media_duration_sec(audio_for_stt)

        try:
            from app.services.onnx_whisper import torch_import_broken
            from app.services.performance import gpu_task_slot
            from app.services.whisper_cpp_stt import transcribe_wav as cpp_transcribe_wav

            if torch_import_broken():
                # Do not import onnxruntime here: on this Windows box it raises
                # WinError 1114 or access-violates, and the job never reaches a
                # working STT engine. whisper-cli.exe is a separate process.
                try:
                    emit_status(
                        on_status,
                        "🎧 Torch/ONNX DLL lỗi — chuyển sang whisper.cpp (file .exe độc lập)...",
                    )
                    cues, detected = cpp_transcribe_wav(
                        audio_for_stt,
                        language=w_lang or "zh",
                        on_status=on_status,
                    )
                    return self._stt_finish_from_cues(
                        cues, detected, "whisper-cpp-base", srt_path, cache_key
                    )
                except Exception as exc:
                    logger.warning("faster_whisper STT failed: %s", exc)
                    return {
                        "status": "fallback",
                        "srt_path": None,
                        "warning": f"STT failed: {exc}",
                        "stt_fail_reason": "engine_failed",
                    }

            device, compute_type = self._whisper_device()
            with gpu_task_slot(enabled=device == "cuda"):
                emit_status(
                    on_status,
                    f"🎧 Đang nạp model Whisper '{requested}' ({device}/{compute_type})...",
                )
                fw_model, used_name = self._load_whisper_model(model_candidates, device, compute_type)
                logger.info(f"STT using faster-whisper '{used_name}' device={device} lang={w_lang or 'auto'}")
                if audio_dur >= 120 and device == "cpu":
                    eta = "3–10 phút trên CPU vì audio dài"
                elif audio_dur >= 120:
                    eta = "1–3 phút"
                else:
                    eta = "30–90s"
                dur_note = f", audio {int(audio_dur)}s" if audio_dur > 0 else ""
                emit_status(
                    on_status,
                    f"🎧 Whisper '{used_name}' đang nhận dạng lời thoại (lang={w_lang or 'auto'}{dur_note}) — {eta}...",
                )
                cues, info = self._decode_whisper_cues(
                    fw_model,
                    audio_for_stt,
                    language=w_lang,
                    use_vad=True,
                    audio_dur=audio_dur,
                    on_status=on_status,
                )
                count = 0
                if cues:
                    logger.info("STT decoded %d cues (vad=on)", len(cues))
                    with gpu_task_slot(enabled=device == "cuda"):
                        filled = self._refill_stt_gaps(
                            fw_model,
                            audio_for_stt,
                            cues,
                            language=w_lang,
                            duration=audio_dur,
                            on_status=on_status,
                        )
                    if filled:
                        cues = filled
                    count = self._write_cue_dicts(srt_path, cues)
            detected = getattr(info, "language", None)
            gated: Optional[Dict[str, Any]] = None
            if count > 0 and os.path.exists(srt_path) and os.path.getsize(srt_path) > 0:
                gated = self._stt_gate_result(srt_path, detected, used_name, count)
            if gated is None or gated.get("status") != "success":
                emit_status(
                    on_status,
                    "🎧 Whisper chạy lại không VAD (lần trước bị lọc nhạc/ảo giác)...",
                )
                retry_lang = w_lang or self._normalize_whisper_lang(detected or "") or "zh"
                retry_cues, retry_info = self._decode_whisper_cues(
                    fw_model,
                    audio_for_stt,
                    language=retry_lang,
                    use_vad=False,
                    audio_dur=audio_dur,
                    on_status=on_status,
                )
                if retry_cues:
                    retry_cues = self._refill_stt_gaps(
                        fw_model,
                        audio_for_stt,
                        retry_cues,
                        language=retry_lang,
                        duration=audio_dur,
                        on_status=on_status,
                    )
                    count = self._write_cue_dicts(srt_path, retry_cues)
                    detected = getattr(retry_info, "language", None) or retry_lang or detected
                    gated = self._stt_gate_result(srt_path, detected, used_name, count)
            if gated and gated.get("status") == "success":
                logger.info(f"STT wrote {count} cues ({detected}) -> {srt_path}")
                self._stt_cache_store(
                    cache_key,
                    srt_path,
                    {
                        "detected_language": detected,
                        "model": used_name,
                        "cue_count": count,
                    },
                )
                return gated
            if gated:
                return gated
            if os.path.exists(srt_path):
                try:
                    os.remove(srt_path)
                except OSError:
                    pass
            logger.warning("STT produced zero cues (silent / music-only clip)")
            return {
                "status": "empty",
                "srt_path": None,
                "detected_language": detected,
                "model": used_name,
                "stt_fail_reason": "empty_audio",
            }
        except Exception as e:
            logger.warning(f"faster_whisper STT failed: {e}")
            return {
                "status": "fallback",
                "srt_path": None,
                "warning": f"STT failed: {e}",
                "stt_fail_reason": "engine_failed",
            }
        finally:
            if tmp_wav and os.path.exists(tmp_wav):
                try:
                    os.remove(tmp_wav)
                except OSError:
                    pass

    def _subtitle_translator_engine(self) -> str:
        return "agy"

    def _vietsub_cache_dir(self) -> str:
        try:
            from app.config import settings
            root = settings.CACHE_DIR
            if not os.path.isabs(root):
                root = os.path.join(str(settings.BASE_DIR), root)
            path = os.path.join(os.path.abspath(root), "vietsub")
        except Exception:
            path = os.path.abspath(os.path.join(PROJECT_ROOT, "data", "cache", "vietsub"))
        os.makedirs(path, exist_ok=True)
        return path

    def _vietsub_cache_key(
        self,
        cues: List[Dict[str, Any]],
        target_lang: str,
        style: str,
        model: str,
    ) -> str:
        payload = json.dumps(
            {
                "cues": [
                    {
                        "i": int(c.get("index") or 0),
                        "s": round(float(c.get("start_time") or 0.0), 3),
                        "e": round(float(c.get("end_time") or 0.0), 3),
                        "t": str(c.get("text") or ""),
                    }
                    for c in cues
                ],
                "lang": target_lang,
                "style": style,
                "model": model,
                "prompt": PROMPT_VERSION,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def _vietsub_cache_load(self, key: str) -> Optional[List[str]]:
        path = os.path.join(self._vietsub_cache_dir(), f"{key}.json")
        if not os.path.isfile(path):
            return None
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            lines = data.get("lines") if isinstance(data, dict) else None
            if isinstance(lines, list) and all(isinstance(item, str) for item in lines):
                return lines
        except Exception as exc:
            logger.warning(f"Vietsub cache load failed: {exc}")
        return None

    def _vietsub_cache_store(self, key: str, lines: List[str]) -> None:
        path = os.path.join(self._vietsub_cache_dir(), f"{key}.json")
        tmp = path + ".tmp"
        try:
            Path(tmp).write_text(
                json.dumps({"lines": lines, "version": PROMPT_VERSION}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except Exception as exc:
            logger.warning(f"Vietsub cache store failed: {exc}")
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _translate_with_agy(
        self,
        subtitle_file_path: str,
        target_lang: str,
        target_dir: str,
        title: str = "",
        style: str = "dub",
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        from app.services import agy_cli_service
        from app.services.activity import emit_status
        from app.services.tts_service import parse_srt_segments

        if not agy_cli_service.is_available():
            return {
                "status": "failed",
                "srt_path": None,
                "provider": "agy",
                "warning": "Chưa có agy",
                "translate_fail_reason": "agy_missing",
            }
        cues = _merge_fragmented_cues(parse_srt_segments(subtitle_file_path))
        texts = [str(cue.get("text") or "").strip() for cue in cues]
        if not any(texts):
            return {
                "status": "failed",
                "srt_path": None,
                "provider": "agy",
                "warning": "Không có câu nguồn để dịch",
                "translate_fail_reason": "empty",
            }
        style_n = resolve_vietsub_style(style)
        cache_key = self._vietsub_cache_key(cues, target_lang, style_n, agy_cli_service.default_model())
        translated = self._vietsub_cache_load(cache_key)
        if translated and len(translated) == len(cues):
            emit_status(on_status, f"🌐 Dùng lại bản dịch AGY đã cache ({len(translated)} câu).")
        else:
            translated = agy_cli_service.translate_cues(
                texts,
                target_lang=target_lang,
                title=title,
                style=style_n,
                on_status=on_status,
                chunk_size=CHUNK_SIZE,
            )
        if not translated or len(translated) != len(cues):
            return {
                "status": "failed",
                "srt_path": None,
                "provider": "agy",
                "warning": "AGY không trả đủ câu",
                "translate_fail_reason": "count_mismatch",
            }
        fail_reason = vietnamese_fail_reason(translated, len(cues), target_lang)
        if fail_reason:
            return {
                "status": "failed",
                "srt_path": None,
                "provider": "agy",
                "warning": f"AGY không đạt ({fail_reason})",
                "translate_fail_reason": fail_reason,
            }
        self._vietsub_cache_store(cache_key, list(translated))
        kept = []
        for cue, text in zip(cues, translated):
            item = dict(cue)
            item["text"] = text.strip()
            kept.append(item)
        kept = reflow_incomplete_sentences(
            kept,
            max_gap=VI_REFLOW_MAX_GAP,
            max_duration=VI_REFLOW_MAX_DUR,
            max_chars=VI_REFLOW_MAX_CHARS,
        )
        base_stem = os.path.splitext(os.path.basename(subtitle_file_path))[0]
        out_srt = os.path.join(target_dir, f"{base_stem}_{target_lang}.srt")
        _cues_to_srt(kept, out_srt)
        return {
            "status": "success",
            "srt_path": out_srt,
            "provider": "agy",
            "localized": True,
            "warning": None,
        }

    def _translate_with_google(
        self,
        subtitle_file_path: str,
        target_lang: str,
        target_dir: str,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        from app.services.activity import emit_status
        from app.services.tts_service import parse_srt_segments

        source_cues = _merge_fragmented_cues(parse_srt_segments(subtitle_file_path))
        kept, skipped = _google_translate_segments(source_cues, target_lang, on_status=on_status)
        min_keep = max(1, int(len(source_cues) * 0.35)) if source_cues else 1
        if not (kept and len(kept) >= min_keep):
            emit_status(on_status, "⚠️ Google Translate không đủ câu tiếng Việt.")
            return None
        base_stem = os.path.splitext(os.path.basename(subtitle_file_path))[0]
        out_srt = os.path.join(target_dir, f"{base_stem}_{target_lang}.srt")
        _cues_to_srt(kept, out_srt)
        warning = ""
        if skipped:
            warning = f"Google dịch được {len(kept)}/{len(source_cues)} câu; đã bỏ {skipped} câu còn tiếng gốc."
            emit_status(on_status, f"⚠️ {warning}")
        else:
            emit_status(on_status, f"✅ Google Translate xong {len(kept)} câu.")
        return {
            "status": "success",
            "srt_path": out_srt,
            "provider": "google",
            "warning": warning or None,
        }

    def _translate_with_cli(
        self,
        subtitle_file_path: str,
        target_lang: str,
        target_dir: str,
        translate_provider: int,
        output_dir: Optional[str],
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        from app.services.activity import emit_status

        # Channel 0 = Google Translate built into pyVideoTrans CLI.
        google_channel = 0
        emit_status(on_status, "🌐 Đang dịch bằng pyVideoTrans Google CLI...")
        args = [
            "--task", "sts",
            "--name", os.path.abspath(subtitle_file_path),
            "--target_language_code", target_lang,
            "--translate_type", str(google_channel if translate_provider in (None, 0) else translate_provider),
        ]
        if output_dir:
            args.extend(["--output-dir", os.path.abspath(output_dir)])
        res = self.run_cli_command(args, timeout=90)
        srt_path = self._find_srt_file(subtitle_file_path, target_dir)
        if not srt_path or not os.path.isfile(srt_path):
            return None
        if subtitle_matches_target_language(srt_path, target_lang):
            res["srt_path"] = srt_path
            res["provider"] = "cli-google"
            emit_status(on_status, "✅ Google CLI dịch phụ đề xong.")
            return res

        from app.services.tts_service import parse_srt_segments

        cues = parse_srt_segments(srt_path)
        kept = [cue for cue in cues if _translation_matches_target(cue.get("text"), target_lang)]
        skipped = max(0, len(cues) - len(kept))
        min_keep = max(1, int(len(cues) * 0.35)) if cues else 1
        if kept and len(kept) >= min_keep:
            _cues_to_srt(kept, srt_path)
            warning = f"Google CLI dịch được {len(kept)}/{len(cues)} câu; đã bỏ {skipped} câu còn tiếng gốc."
            emit_status(on_status, f"⚠️ {warning}")
            res["srt_path"] = srt_path
            res["provider"] = "cli-google"
            res["warning"] = warning
            return res
        return None

    def _translate_with_deepseek(
        self,
        subtitle_file_path: str,
        target_lang: str,
        target_dir: str,
        style: str,
        title: str,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        from app.config import settings
        from app.services.activity import emit_status
        from app.services.ai_scriptwriter_service import ai_scriptwriter_service
        from app.services.tts_service import parse_srt_segments, format_srt_timestamp

        if not ai_scriptwriter_service.is_available():
            return None
        segs = _merge_fragmented_cues(parse_srt_segments(subtitle_file_path))
        if not (segs and any((s.get("text") or "").strip() for s in segs)):
            return None
        emit_status(
            on_status,
            f"🌐 Đang gửi {len(segs)} câu sang DeepSeek ({settings.DEEPSEEK_MODEL})...",
        )
        localized = ai_scriptwriter_service.localize_script(
            segs,
            target_lang=target_lang,
            genre=style or "dub",
            title=title or "",
        )
        if not (localized and len(localized) == len(segs)):
            return None
        final_texts = [(item.get("translated_text") or "").strip() for item in localized]
        base_stem = os.path.splitext(os.path.basename(subtitle_file_path))[0]
        out_srt = os.path.join(target_dir, f"{base_stem}_{target_lang}.srt")
        lines = []
        for i, (seg, vi) in enumerate(zip(segs, final_texts), start=1):
            text = (vi or "").strip()
            if not _translation_matches_target(text, target_lang):
                return None
            lines.append(
                f"{i}\n{format_srt_timestamp(seg['start_time'])} --> {format_srt_timestamp(seg['end_time'])}\n{text}\n"
            )
        if not lines:
            return None
        with open(out_srt, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        if not subtitle_matches_target_language(out_srt, target_lang):
            return None
        logger.info(f"DeepSeek localized {len(lines)} screenplay cues -> {out_srt}")
        return {
            "status": "success",
            "srt_path": out_srt,
            "provider": "deepseek",
            "localized": True,
        }

    def translate_subtitles(
        self,
        subtitle_file_path: str,
        target_lang: str = "vi",
        translate_provider: int = 0,
        output_dir: Optional[str] = None,
        style: str = "dub",
        title: str = "",
        duration: float = 0.0,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Translate subtitles with local AGY only. No Google/DeepSeek/Grok fallback."""
        from app.services.activity import emit_status

        del translate_provider, duration
        if not os.path.exists(subtitle_file_path):
            raise FileNotFoundError(f"Subtitle file not found: {subtitle_file_path}")

        target_dir = os.path.abspath(output_dir) if output_dir else os.path.dirname(os.path.abspath(subtitle_file_path))
        os.makedirs(target_dir, exist_ok=True)
        style_n = resolve_vietsub_style(style)
        emit_status(on_status, f"🌐 Dịch kịch bản bằng agy (style={style_n})...")
        try:
            result = self._translate_with_agy(
                subtitle_file_path,
                target_lang,
                target_dir,
                title=title,
                style=style_n,
                on_status=on_status,
            )
        except Exception as e:
            logger.warning(f"Antigravity CLI translation failed: {e}")
            emit_status(on_status, f"⚠️ agy timeout/lỗi: {e}")
            return {
                "status": "failed",
                "srt_path": None,
                "provider": "agy",
                "warning": str(e),
                "translate_fail_reason": "agy_failed",
            }
        if result and result.get("status") == "success" and result.get("srt_path"):
            return result
        warning = (result or {}).get("warning") or f"Subtitle output is not fully translated to {target_lang}"
        logger.error("AGY subtitle translation failed validation")
        return {
            "status": "failed",
            "srt_path": None,
            "provider": "agy",
            "warning": warning,
            "translate_fail_reason": (result or {}).get("translate_fail_reason") or "agy_failed",
        }

    def text_to_speech(
        self,
        subtitle_file_path: str,
        voice_role: str = "vi-VN-HoaiMyNeural",
        output_dir: Optional[str] = None,
        tts_type: int = 0
    ) -> Dict[str, Any]:
        """Generates synchronized TTS audio from subtitle file."""
        if not os.path.exists(subtitle_file_path):
            raise FileNotFoundError(f"Subtitle file not found: {subtitle_file_path}")

        args = [
            "--task", "tts",
            "--name", os.path.abspath(subtitle_file_path),
            "--tts_type", str(tts_type),
            "--voice_role", voice_role
        ]
        if output_dir:
            args.extend(["--output-dir", os.path.abspath(output_dir)])

        return self.run_cli_command(args)

    def translate_video(
        self,
        video_path: str,
        source_lang: str = "zh-cn",
        target_lang: str = "vi",
        voice_role: str = "vi-VN-HoaiMyNeural",
        output_dir: Optional[str] = None,
        use_cuda: bool = False
    ) -> Dict[str, Any]:
        """Performs full AI Video Translation & Dubbing pipeline."""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        args = [
            "--task", "vtv",
            "--name", os.path.abspath(video_path),
            "--source_language_code", source_lang,
            "--target_language_code", target_lang,
            "--voice_role", voice_role
        ]
        if output_dir:
            args.extend(["--output-dir", os.path.abspath(output_dir)])
        if use_cuda:
            args.append("--cuda")

        return self.run_cli_command(args)
