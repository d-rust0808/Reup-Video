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
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List

from app.modules.videotrans.runner import PyVideoTransRunner

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_VIDEOTRANS_PATH = str(PROJECT_ROOT / "app" / "modules" / "videotrans")
_WHISPER_CACHE: Dict[str, Any] = {"model": None, "name": None, "device": None}


def _is_invalid_translation(text: Optional[str]) -> bool:
    """Detects HTTP error responses, rate-limits, or HTML blobs leaked into translations."""
    if not text or not isinstance(text, str):
        return True
    low = text.lower().strip()
    if not low:
        return True
    error_patterns = [
        "error 500",
        "server error",
        "that's an error",
        "that’s an error",
        "please try again later",
        "too many requests",
        "429 too many",
        "<html",
        "<!doctype",
        "result-container",
        "unsupported translate type",
    ]
    return any(p in low for p in error_patterns)


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", text or ""))


def _translation_matches_target(text: Optional[str], target_lang: str) -> bool:
    """Reject source-language leakage before a translated cue reaches hardsub/TTS."""
    if _is_invalid_translation(text):
        return False
    if not any(char.isalnum() for char in text or ""):
        return False
    lang = (target_lang or "").lower().split("-")[0]
    if lang == "vi" and _contains_cjk(text or ""):
        return False
    return True


_DETACHED_CJK_PARTICLES = {
    "吗", "呢", "吧", "嘛", "么", "啊", "呀", "啦", "呗", "了", "的", "地", "得",
}


def _merge_fragmented_cues(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Join nearby ASR fragments into sentence-sized cues before translation/TTS."""
    merged: List[Dict[str, Any]] = []
    for segment in segments:
        copied = dict(segment)
        text = re.sub(r"\s+", " ", str(copied.get("text") or "").strip())
        compact = re.sub(r"\s+", "", text)
        cjk_only = "".join(char for char in compact if _contains_cjk(char))
        has_spoken_content = any(char.isalnum() for char in text)
        duration = max(
            0.0,
            float(copied.get("end_time") or 0.0) - float(copied.get("start_time") or 0.0),
        )
        is_fragment = (
            not has_spoken_content
            or cjk_only in _DETACHED_CJK_PARTICLES
            or (bool(cjk_only) and len(cjk_only) <= 3 and duration <= 0.9)
        )

        if merged:
            previous = merged[-1]
            gap = float(copied.get("start_time") or 0.0) - float(previous.get("end_time") or 0.0)
            previous_text = str(previous.get("text") or "").rstrip()
            combined_duration = (
                float(copied.get("end_time") or 0.0)
                - float(previous.get("start_time") or 0.0)
            )
            combined_chars = len(re.sub(r"\s+", "", previous_text + text))
            has_terminal = bool(re.search(r"[.!?。！？…]$", previous_text))
            should_join_sentence = (
                gap <= 0.12
                and not has_terminal
                and combined_duration <= 5.0
                and combined_chars <= 42
            )
            if gap <= 0.35 and (is_fragment or should_join_sentence):
                separator = ""
                if has_spoken_content and not (_contains_cjk(previous_text[-1:]) and _contains_cjk(text[:1])):
                    separator = " "
                previous["text"] = f"{previous_text}{separator}{text}".strip()
                previous["end_time"] = max(
                    float(previous.get("end_time") or 0.0),
                    float(copied.get("end_time") or 0.0),
                )
                previous["duration"] = max(
                    0.0,
                    float(previous["end_time"]) - float(previous.get("start_time") or 0.0),
                )
                continue

        if not has_spoken_content:
            continue
        copied["text"] = text
        merged.append(copied)

    for index, segment in enumerate(merged, start=1):
        segment["index"] = index
    return merged


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

    def _extract_stt_wav(self, video_or_audio_path: str, target_dir: str, max_seconds: Optional[float] = None) -> str:
        """Extract 16 kHz mono WAV so Whisper does not decode the full video."""
        from app.services.audio_service import find_ffmpeg_binary
        ffmpeg_bin = find_ffmpeg_binary()
        wav_path = os.path.join(target_dir, os.path.splitext(os.path.basename(video_or_audio_path))[0] + ".stt.wav")
        if not ffmpeg_bin:
            return video_or_audio_path
        cmd = [
            ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
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

    def speech_to_text(
        self,
        video_or_audio_path: str,
        output_dir: Optional[str] = None,
        model_name: str = "base",
        recogn_type: int = 0,
        detect_lang: str = "auto",
        max_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Performs Speech-to-Text (STT) transcription with cached faster-whisper."""
        if not os.path.exists(video_or_audio_path):
            raise FileNotFoundError(f"Input file not found: {video_or_audio_path}")

        target_dir = os.path.abspath(output_dir) if output_dir else os.path.dirname(os.path.abspath(video_or_audio_path))
        os.makedirs(target_dir, exist_ok=True)

        w_lang = self._normalize_whisper_lang(detect_lang)
        device, compute_type = self._whisper_device()
        requested = (model_name or "base").strip() or "base"
        # tiny wrecks Chinese + BGM; small is too slow on CPU. Prefer cached base.
        if requested == "tiny":
            requested = "base"
        model_candidates: List[str] = []
        for name in (requested, "base", "small", "tiny"):
            if name not in model_candidates:
                model_candidates.append(name)

        audio_for_stt = self._extract_stt_wav(video_or_audio_path, target_dir, max_seconds=max_seconds)
        tmp_wav = audio_for_stt if audio_for_stt != video_or_audio_path else None

        try:
            from app.services.performance import gpu_task_slot
            with gpu_task_slot(enabled=device == "cuda"):
                fw_model, used_name = self._load_whisper_model(model_candidates, device, compute_type)
                logger.info(f"STT using faster-whisper '{used_name}' device={device} lang={w_lang or 'auto'}")
                segments, info = fw_model.transcribe(
                    audio_for_stt,
                    language=w_lang,
                    vad_filter=True,
                    beam_size=1,
                    best_of=1,
                    condition_on_previous_text=False,
                    word_timestamps=True,
                )
                # faster-whisper yields lazily; materialize while the GPU slot is held.
                segments = list(segments)
            base_stem = os.path.splitext(os.path.basename(video_or_audio_path))[0]
            srt_path = os.path.join(target_dir, f"{base_stem}.srt")
            words = []
            seg_list = []
            for seg in segments:
                seg_list.append(seg)
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
            if words:
                from app.services.lipsync_service import regroup_words_to_cues, write_cues_srt
                cues = regroup_words_to_cues(words)
                count = write_cues_srt(srt_path, cues)
                logger.info(f"STT lip-sync regrouped {len(words)} words -> {count} cues")
            else:
                count = self._write_srt(srt_path, seg_list)
            detected = getattr(info, "language", None)
            if count > 0 and os.path.exists(srt_path) and os.path.getsize(srt_path) > 0:
                logger.info(f"STT wrote {count} cues ({detected}) -> {srt_path}")
                return {
                    "status": "success",
                    "srt_path": srt_path,
                    "detected_language": detected,
                    "model": used_name,
                    "cue_count": count,
                }
            if os.path.exists(srt_path):
                try:
                    os.remove(srt_path)
                except OSError:
                    pass
            logger.warning("STT produced zero cues (silent / music-only clip)")
            return {"status": "empty", "srt_path": None, "detected_language": detected, "model": used_name}
        except Exception as e:
            logger.warning(f"faster_whisper STT failed: {e}")
            return {
                "status": "fallback",
                "srt_path": None,
                "warning": f"STT failed: {e}",
            }
        finally:
            if tmp_wav and os.path.exists(tmp_wav):
                try:
                    os.remove(tmp_wav)
                except OSError:
                    pass

    def translate_subtitles(
        self,
        subtitle_file_path: str,
        target_lang: str = "vi",
        translate_provider: int = 0,
        output_dir: Optional[str] = None,
        style: str = "dub",
        title: str = "",
        duration: float = 0.0,
    ) -> Dict[str, Any]:
        """Translates subtitle file (SRT/VTT) into target language using DeepSeek, Grok, or fallbacks."""
        if not os.path.exists(subtitle_file_path):
            raise FileNotFoundError(f"Subtitle file not found: {subtitle_file_path}")

        target_dir = os.path.abspath(output_dir) if output_dir else os.path.dirname(os.path.abspath(subtitle_file_path))
        os.makedirs(target_dir, exist_ok=True)
        ai_provider_available = False
        ai_failure_reason = ""

        # 1. DeepSeek AI screenplay localization in one context-aware request.
        try:
            from app.services.ai_scriptwriter_service import ai_scriptwriter_service
            from app.services.tts_service import parse_srt_segments, format_srt_timestamp
            if ai_scriptwriter_service.is_available():
                ai_provider_available = True
                segs = _merge_fragmented_cues(parse_srt_segments(subtitle_file_path))
                if segs and any((s.get("text") or "").strip() for s in segs):
                    localized = ai_scriptwriter_service.localize_script(
                        segs,
                        target_lang=target_lang,
                        genre=style or "dub",
                        title=title or "",
                    )
                    if localized and len(localized) == len(segs):
                        final_texts = [
                            (item.get("translated_text") or "").strip()
                            for item in localized
                        ]
                        base_stem = os.path.splitext(os.path.basename(subtitle_file_path))[0]
                        out_srt = os.path.join(target_dir, f"{base_stem}_{target_lang}.srt")
                        lines = []
                        for i, (seg, vi) in enumerate(zip(segs, final_texts), start=1):
                            text = (vi or "").strip()
                            if not _translation_matches_target(text, target_lang):
                                lines = []
                                break
                            lines.append(
                                f"{i}\n{format_srt_timestamp(seg['start_time'])} --> {format_srt_timestamp(seg['end_time'])}\n{text}\n"
                            )
                        with open(out_srt, "w", encoding="utf-8") as f:
                            f.write("\n".join(lines) + ("\n" if lines else ""))
                        if subtitle_matches_target_language(out_srt, target_lang):
                            logger.info(f"DeepSeek localized {len(lines)} screenplay cues -> {out_srt}")
                            return {
                                "status": "success",
                                "srt_path": out_srt,
                                "provider": "deepseek",
                                "localized": True,
                            }
                ai_failure_reason = ai_scriptwriter_service.last_error
        except Exception as e:
            logger.warning(f"DeepSeek subtitle translation failed: {e}. Falling back to next provider.")

        # 2. Grok localization (natural Vietnamese, pacing-aware)
        try:
            from app.services.xai_media_service import translate_cues, is_available as grok_ok
            from app.services.tts_service import parse_srt_segments, format_srt_timestamp
            if grok_ok():
                ai_provider_available = True
                segs = _merge_fragmented_cues(parse_srt_segments(subtitle_file_path))
                src_texts = [(s.get("text") or "").strip() for s in segs]
                if src_texts and all(src_texts):
                    grok_out = translate_cues(
                        src_texts,
                        target_lang=target_lang,
                        style=style or "dub",
                        title=title or "",
                    )
                    if grok_out and len(grok_out) == len(segs):
                        base_stem = os.path.splitext(os.path.basename(subtitle_file_path))[0]
                        out_srt = os.path.join(target_dir, f"{base_stem}_{target_lang}.srt")
                        lines = []
                        for i, (seg, vi) in enumerate(zip(segs, grok_out), start=1):
                            text = (vi or "").strip()
                            if not _translation_matches_target(text, target_lang):
                                lines = []
                                break
                            lines.append(
                                f"{i}\n{format_srt_timestamp(seg['start_time'])} --> {format_srt_timestamp(seg['end_time'])}\n{text}\n"
                            )
                        with open(out_srt, "w", encoding="utf-8") as f:
                            f.write("\n".join(lines) + ("\n" if lines else ""))
                        if subtitle_matches_target_language(out_srt, target_lang):
                            logger.info(f"Grok translated {len(lines)} cues -> {out_srt}")
                            return {"status": "success", "srt_path": out_srt, "provider": "grok"}
        except Exception as e:
            logger.warning(f"Grok subtitle translation failed: {e}. Falling back to Google.")

        # A configured AI returning invalid output is safer to surface as a clear
        # failure than to silently burn a fragmented word-by-word machine translation.
        if ai_provider_available:
            warning = ai_failure_reason or (
                "AI không tạo được kịch bản Việt hợp lệ; đã bỏ qua Vietsub để tránh xuất bản dịch vô nghĩa."
            )
            logger.error(warning)
            return {"status": "failed", "srt_path": None, "warning": warning}

        # 3. Native Deep Translator (with strict error filtering)
        try:
            from deep_translator import GoogleTranslator
            translator = GoogleTranslator(source="auto", target=target_lang)
            base_stem = os.path.splitext(os.path.basename(subtitle_file_path))[0]
            out_srt = os.path.join(target_dir, f"{base_stem}_{target_lang}.srt")
            with open(subtitle_file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            text_indices = []
            text_payload = []
            for i, line in enumerate(lines):
                stripped = line.strip()
                if not stripped or stripped.isdigit() or "-->" in stripped:
                    continue
                text_indices.append(i)
                text_payload.append(stripped)

            translated_map = {}
            CHUNK = 12
            for start in range(0, len(text_payload), CHUNK):
                chunk = text_payload[start:start + CHUNK]
                blob = "\n".join(chunk)
                try:
                    translated_blob = translator.translate(blob)
                    if _is_invalid_translation(translated_blob):
                        translated_blob = None
                    parts = [p.strip() for p in str(translated_blob or "").split("\n") if p.strip()]
                    if parts and len(parts) == len(chunk) and not any(_is_invalid_translation(p) for p in parts):
                        for j, part in enumerate(parts):
                            translated_map[start + j] = part
                    else:
                        # Fallback to per-line
                        for j, src in enumerate(chunk):
                            try:
                                single_t = translator.translate(src)
                                if _translation_matches_target(single_t, target_lang):
                                    translated_map[start + j] = single_t
                                else:
                                    translated_map[start + j] = None
                            except Exception:
                                translated_map[start + j] = None
                except Exception:
                    for j, src in enumerate(chunk):
                        try:
                            single_t = translator.translate(src)
                            if _translation_matches_target(single_t, target_lang):
                                translated_map[start + j] = single_t
                            else:
                                translated_map[start + j] = None
                        except Exception:
                            translated_map[start + j] = None

            out_lines = list(lines)
            for local_i, line_idx in enumerate(text_indices):
                vi = translated_map.get(local_i)
                if isinstance(vi, str) and _translation_matches_target(vi, target_lang):
                    out_lines[line_idx] = vi + "\n"

            if len(translated_map) != len(text_payload) or any(
                not _translation_matches_target(translated_map.get(i), target_lang)
                for i in range(len(text_payload))
            ):
                raise PyVideoTransError("Google left one or more subtitle cues untranslated")

            with open(out_srt, "w", encoding="utf-8") as f:
                f.writelines(out_lines)
            if subtitle_matches_target_language(out_srt, target_lang):
                return {"status": "success", "srt_path": out_srt}
        except Exception as e:
            logger.warning(f"Native deep_translator failed: {e}. Falling back to CLI...")

        args = [
            "--task", "sts",
            "--name", os.path.abspath(subtitle_file_path),
            "--target_language_code", target_lang,
            "--translate_type", str(translate_provider)
        ]
        if output_dir:
            args.extend(["--output-dir", os.path.abspath(output_dir)])

        res = self.run_cli_command(args)
        srt_path = self._find_srt_file(subtitle_file_path, target_dir)
        if not subtitle_matches_target_language(srt_path, target_lang):
            logger.error("All subtitle translators failed target-language validation")
            return {
                "status": "failed",
                "srt_path": None,
                "warning": f"Subtitle output is not fully translated to {target_lang}",
            }
        res["srt_path"] = srt_path

        return res

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
