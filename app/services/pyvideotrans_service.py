"""
PyVideoTrans Integration Service.
=================================
Bridges pyVideoTrans AI translation, speech recognition (STT), subtitle translation (STS),
and text-to-speech (TTS) engines into the unified Reup-Video processing pipeline.

Target Path: app/services/pyvideotrans_service.py
"""

import os
import sys
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List

from app.modules.videotrans.runner import PyVideoTransRunner

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_VIDEOTRANS_PATH = str(PROJECT_ROOT / "app" / "modules" / "videotrans")
_WHISPER_CACHE = {"model": None, "name": None, "device": None}


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

    def _extract_stt_wav(self, video_or_audio_path: str, target_dir: str) -> str:
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
            wav_path,
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0 and os.path.exists(wav_path) and os.path.getsize(wav_path) > 1024:
                return wav_path
        except Exception as e:
            logger.warning(f"STT wav extract failed: {e}")
        return video_or_audio_path

    def _load_whisper_model(self, names: List[str], device: str, compute_type: str):
        import faster_whisper
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
        detect_lang: str = "auto"
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

        audio_for_stt = self._extract_stt_wav(video_or_audio_path, target_dir)
        tmp_wav = audio_for_stt if audio_for_stt != video_or_audio_path else None

        try:
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
        output_dir: Optional[str] = None,
        translate_provider: int = 0
    ) -> Dict[str, Any]:
        """Translates subtitle file (SRT/VTT) into target language using GoogleTranslator or CLI."""
        if not os.path.exists(subtitle_file_path):
            raise FileNotFoundError(f"Subtitle file not found: {subtitle_file_path}")

        target_dir = os.path.abspath(output_dir) if output_dir else os.path.dirname(os.path.abspath(subtitle_file_path))
        os.makedirs(target_dir, exist_ok=True)

        # 1. Grok localization (natural Vietnamese, pacing-aware)
        try:
            from app.services.xai_media_service import translate_cues, is_available as grok_ok
            from app.services.tts_service import parse_srt_segments, format_srt_timestamp
            if grok_ok():
                segs = parse_srt_segments(subtitle_file_path)
                src_texts = [(s.get("text") or "").strip() for s in segs]
                if src_texts and all(src_texts):
                    grok_out = translate_cues(src_texts, target_lang=target_lang)
                    if grok_out and len(grok_out) == len(segs):
                        base_stem = os.path.splitext(os.path.basename(subtitle_file_path))[0]
                        out_srt = os.path.join(target_dir, f"{base_stem}_{target_lang}.srt")
                        lines = []
                        for i, (seg, vi) in enumerate(zip(segs, grok_out), start=1):
                            text = (vi or seg.get("text") or "").strip()
                            if not text:
                                continue
                            lines.append(
                                f"{i}\n{format_srt_timestamp(seg['start_time'])} --> {format_srt_timestamp(seg['end_time'])}\n{text}\n"
                            )
                        with open(out_srt, "w", encoding="utf-8") as f:
                            f.write("\n".join(lines) + ("\n" if lines else ""))
                        if os.path.exists(out_srt) and os.path.getsize(out_srt) > 0:
                            logger.info(f"Grok translated {len(lines)} cues -> {out_srt}")
                            return {"status": "success", "srt_path": out_srt, "provider": "grok"}
        except Exception as e:
            logger.warning(f"Grok subtitle translation failed: {e}. Falling back to Google.")

        # 2. Native High-Reliability Deep Translator (batch paragraphs, keep cue structure)
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
                    translated_blob = translator.translate(blob) or blob
                    parts = [p.strip() for p in str(translated_blob).split("\n") if p.strip()]
                    if len(parts) == len(chunk):
                        for j, part in enumerate(parts):
                            translated_map[start + j] = part
                    else:
                        # Fallback to per-line if Google collapsed the batch
                        for j, src in enumerate(chunk):
                            try:
                                translated_map[start + j] = translator.translate(src) or src
                            except Exception:
                                translated_map[start + j] = src
                except Exception:
                    for j, src in enumerate(chunk):
                        try:
                            translated_map[start + j] = translator.translate(src) or src
                        except Exception:
                            translated_map[start + j] = src

            out_lines = list(lines)
            for local_i, line_idx in enumerate(text_indices):
                vi = translated_map.get(local_i)
                if vi:
                    out_lines[line_idx] = vi + "\n"

            with open(out_srt, "w", encoding="utf-8") as f:
                f.writelines(out_lines)
            if os.path.exists(out_srt) and os.path.getsize(out_srt) > 0:
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
        res["srt_path"] = srt_path or subtitle_file_path

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
