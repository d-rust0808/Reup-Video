"""
Unified Text-to-Speech (TTS) Service Engine.
=============================================
Provides a single interface for generating multi-language speech audio
supporting Edge-TTS, gTTS, and Coqui TTS engines integrated via app.modules.tts.
"""

import os
import logging
import re
import subprocess
import asyncio
import shutil
import tempfile
from typing import Optional, Dict, Any, List

from app.modules.tts.providers import get_tts_provider

logger = logging.getLogger(__name__)

SUPPORTED_ENGINES = [
    "vieneu", "vieneu-tts", "edge-tts", "kokoro", "kokoro-tts", "kokoro-82m",
    "gtts", "coqui-tts", "melo-tts", "melo"
]


DEFAULT_VOICES = {
    "vi": {
        "female": "vi-VN-HoaiMy-Fast",
        "male": "vi-VN-NamMinh-Fast",
        "elder_male": "vi-VN-NamMinh-Deep",
        "elder_female": "vi-VN-HoaiMy-Warm",
        "child": "vi-VN-HoaiMyNeural",
        "narrator": "vi-VN-NamMinh-Deep",
        "neutral": "vi-VN-HoaiMy-Fast"
    },
    "en": {
        "female": "en-US-JennyNeural",
        "male": "en-US-GuyNeural",
        "elder_male": "en-US-RyanNeural",
        "elder_female": "en-US-AriaNeural",
        "child": "en-US-JennyNeural",
        "narrator": "en-US-AriaNeural",
        "neutral": "en-US-AvaNeural"
    },
    "zh": {"female": "zh-CN-XiaoxiaoNeural", "male": "zh-CN-YunjianNeural"},
    "ja": {"female": "ja-JP-NanamiNeural", "male": "ja-JP-KeitaNeural"},
    "ko": {"female": "ko-KR-SunHiNeural", "male": "ko-KR-InJoonNeural"},
    "th": {"female": "th-TH-PremwadeeNeural", "male": "th-TH-NiwatNeural"},
    "id": {"female": "id-ID-GadisNeural", "male": "id-ID-ArdiNeural"},
    "es": {"female": "es-ES-ElviraNeural", "male": "es-ES-AlvaroNeural"},
    "fr": {"female": "fr-FR-DeniseNeural", "male": "fr-FR-HenriNeural"},
    "de": {"female": "de-DE-KatjaNeural", "male": "de-DE-ConradNeural"},
    "ru": {"female": "ru-RU-SvetlanaNeural", "male": "ru-RU-DmitryNeural"},
    "pt": {"female": "pt-BR-FranciscaNeural", "male": "pt-BR-AntonioNeural"},
    "hi": {"female": "hi-IN-SwaraNeural", "male": "hi-IN-MadhurNeural"},
    "it": {"female": "it-IT-ElsaNeural", "male": "it-IT-DiegoNeural"},
    "ar": {"female": "ar-SA-ZariyahNeural", "male": "ar-SA-HamedNeural"},
}


def parse_srt_timestamp(timestamp_str: str) -> float:
    """Parses SRT timestamp string into float seconds."""
    ts = str(timestamp_str).strip()
    if not ts:
        return 0.0
    ts = ts.split()[0].replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        hours = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
        return hours * 3600.0 + minutes * 60.0 + seconds
    elif len(parts) == 2:
        minutes = float(parts[0])
        seconds = float(parts[1])
        return minutes * 60.0 + seconds
    return float(ts)


def format_srt_timestamp(seconds: float) -> str:
    """Formats float seconds into standard SRT timestamp string 'HH:MM:SS,mmm'."""
    hours = int(seconds // 3600)
    rem = seconds % 3600
    minutes = int(rem // 60)
    secs = rem % 60
    sec_int = int(secs)
    millis = int(round((secs - sec_int) * 1000))
    if millis >= 1000:
        sec_int += 1
        millis -= 1000
    return f"{hours:02d}:{minutes:02d}:{sec_int:02d},{millis:03d}"


def parse_srt_segments(srt_content_or_path: str) -> List[Dict[str, Any]]:
    """Parses SRT file content or filepath into a list of segment dictionaries."""
    if os.path.exists(srt_content_or_path):
        with open(srt_content_or_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    else:
        content = srt_content_or_path

    content = content.replace("\r\n", "\n").replace("\r", "\n")

    segments = []
    blocks = content.strip().split("\n\n")
    for block in blocks:
        lines = [line.strip() for line in block.strip().split("\n") if line.strip()]
        if len(lines) < 2:
            continue

        idx = 0
        if lines[idx].isdigit():
            idx += 1
        if idx >= len(lines):
            continue

        time_line = lines[idx]
        if "-->" not in time_line:
            continue

        parts = time_line.split("-->")
        if len(parts) != 2:
            continue

        try:
            start_t = parse_srt_timestamp(parts[0])
            end_t = parse_srt_timestamp(parts[1])
            duration = max(0.0, end_t - start_t)
            text = " ".join(lines[idx + 1:])

            segments.append({
                "index": len(segments) + 1,
                "start_time": start_t,
                "end_time": end_t,
                "duration": duration,
                "text": text
            })
        except Exception as e:
            logger.warning(f"Failed to parse SRT block '{block}': {e}")
            continue

    return segments


def get_audio_duration(audio_path: str) -> float:
    """Probes audio file using ffprobe or wave module fallback to get duration in seconds."""
    if not os.path.exists(audio_path):
        return 0.0

    if audio_path.lower().endswith(".wav"):
        try:
            import wave
            with wave.open(audio_path, "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0:
                    return frames / float(rate)
        except Exception:
            pass

    from app.services.audio_service import find_ffprobe_binary, find_ffmpeg_binary
    ffprobe_bin = find_ffprobe_binary()
    if ffprobe_bin:
        try:
            cmd = [
                ffprobe_bin, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                audio_path
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0 and res.stdout.strip():
                return float(res.stdout.strip())
        except Exception:
            pass

    ffmpeg_bin = find_ffmpeg_binary()
    if ffmpeg_bin:
        try:
            cmd = [ffmpeg_bin, "-i", audio_path]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            out = res.stderr or res.stdout or ""
            if "Duration:" in out:
                dur_str = out.split("Duration:")[1].split(",")[0].strip()
                return parse_srt_timestamp(dur_str)
        except Exception:
            pass

    return 0.0


def scale_audio_speed_ffmpeg(
    input_audio_path: str,
    output_audio_path: str,
    speed_factor: float,
    sample_rate: int = 44100
) -> bool:
    """Scales audio playback speed using FFmpeg atempo filter."""
    if speed_factor <= 0.0 or speed_factor < 0.01:
        logger.warning(f"Invalid speed_factor={speed_factor} passed to scale_audio_speed_ffmpeg")
        return False

    from app.services.audio_service import find_ffmpeg_binary
    ffmpeg_bin = find_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(input_audio_path):
        return False

    os.makedirs(os.path.dirname(os.path.abspath(output_audio_path)), exist_ok=True)

    nodes = []
    t = speed_factor
    while t > 2.0:
        nodes.append("atempo=2.0")
        t /= 2.0
    while t < 0.5:
        nodes.append("atempo=0.5")
        t /= 0.5
    if abs(t - 1.0) > 1e-4:
        nodes.append(f"atempo={t:.4f}")

    filter_str = ",".join(nodes) if nodes else "anull"
    cmd = [
        ffmpeg_bin, "-y",
        "-i", input_audio_path,
        "-af", filter_str,
        "-ar", str(sample_rate),
        output_audio_path
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return res.returncode == 0 and os.path.exists(output_audio_path) and os.path.getsize(output_audio_path) > 0
    except Exception as e:
        logger.error(f"Error scaling audio speed for {input_audio_path}: {e}")
        return False


def _create_silent_wav(output_path: str, duration_sec: float = 1.0, sample_rate: int = 44100) -> str:
    """Generates a silent PCM WAV file of given duration in seconds."""
    import wave
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    num_samples = max(1, int(duration_sec * sample_rate))
    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * num_samples)
    return output_path


class TTSServiceError(Exception):
    """Raised when TTS audio generation fails."""
    pass


def group_long_form_tts_segments(
    segments: List[Dict[str, Any]],
    min_segment_count: int = 60,
) -> List[Dict[str, Any]]:
    """Reduce remote TTS calls for long clips by joining nearby translated cues."""
    if len(segments) < min_segment_count:
        return segments

    grouped: List[Dict[str, Any]] = []
    for segment in segments:
        current = dict(segment)
        current["text"] = re.sub(r"\s+", " ", str(current.get("text") or "").strip())
        if not grouped:
            grouped.append(current)
            continue

        previous = grouped[-1]
        gap = float(current["start_time"]) - float(previous["end_time"])
        combined_span = float(current["end_time"]) - float(previous["start_time"])
        combined_text = f"{previous.get('text', '').rstrip()} {current.get('text', '').lstrip()}".strip()
        if gap <= 1.5 and combined_span <= 12.0 and len(combined_text) <= 180:
            previous["text"] = combined_text
            previous["end_time"] = float(current["end_time"])
            previous["duration"] = combined_span
            continue
        grouped.append(current)

    for index, segment in enumerate(grouped, start=1):
        segment["index"] = index
    logger.info("Grouped %d subtitle cues into %d long-form TTS requests", len(segments), len(grouped))
    return grouped


class TTSService:
    """Unified Speech Generation Manager."""

    def __init__(self, default_engine: str = "edge-tts", output_dir: str = "data/outputs/tts"):
        self.default_engine = default_engine
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    async def generate_speech_edge_tts(
        self,
        text: str,
        voice: str = "vi-VN-HoaiMy-Fast",
        output_path: Optional[str] = None,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%"
    ) -> str:
        """Generates speech using Edge-TTS provider."""
        try:
            import asyncio as _asyncio
            last_err = None
            for attempt in range(3):
                try:
                    provider = get_tts_provider("edge-tts")
                    return await provider.generate(
                        text=text, voice=voice, output_path=output_path, rate=rate, pitch=pitch, volume=volume
                    )
                except Exception as e:
                    last_err = e
                    logger.warning(f"Edge-TTS attempt {attempt + 1}/3 failed: {e}")
                    await _asyncio.sleep(0.5 * (attempt + 1))
            raise TTSServiceError(f"Edge-TTS failed: {last_err}") from last_err
        except TTSServiceError:
            raise
        except Exception as e:
            raise TTSServiceError(f"Edge-TTS failed: {e}") from e

    def generate_speech_gtts(
        self,
        text: str,
        lang: str = "vi",
        output_path: Optional[str] = None
    ) -> str:
        """Generates speech using gTTS provider."""
        try:
            provider = get_tts_provider("gtts")
            return asyncio.run(provider.generate(text=text, lang=lang, output_path=output_path))
        except Exception as e:
            raise TTSServiceError(f"gTTS failed: {e}") from e

    def generate_speech_coqui(
        self,
        text: str,
        model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2",
        output_path: Optional[str] = None
    ) -> str:
        """Generates speech using Coqui TTS provider."""
        try:
            provider = get_tts_provider("coqui-tts")
            return asyncio.run(provider.generate(text=text, output_path=output_path, model_name=model_name))
        except Exception as e:
            raise TTSServiceError(f"Coqui TTS failed: {e}") from e

    async def generate_speech(
        self,
        text: str,
        lang: str = "vi",
        voice: Optional[str] = None,
        engine: Optional[str] = None,
        output_path: Optional[str] = None,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%"
    ) -> str:
        """Main method to synthesize speech based on engine selection with fallback strategy."""
        target_engine = (engine or self.default_engine or "edge-tts").lower()
        selected_voice = voice or DEFAULT_VOICES.get(lang, {}).get("female", "vi-VN-HoaiMy-Fast")

        # Voice id implies engine
        vlow = (selected_voice or "").lower()
        if vlow.startswith("kokoro"):
            target_engine = "kokoro"
        elif vlow.startswith("vieneu:"):
            target_engine = "vieneu"
        elif vlow.startswith("vi-vn-"):
            target_engine = "edge-tts"
        elif vlow.startswith("en-us-") and "multilingual" in vlow and lang == "vi":
            target_engine = "edge-tts"
        elif vlow.startswith("gtts") or vlow == "gtts-vi":
            target_engine = "gtts"
        elif vlow.startswith("melo"):
            target_engine = "melo-tts"

        # Unknown Edge voice ids (kokoro-af_heart, HoaiMy-Fast already mapped in provider)
        EDGE_PREFIXES = ("vi-vn-", "en-us-", "en-gb-", "zh-cn-", "ja-jp-", "ko-kr-", "th-th-", "fr-fr-", "es-es-", "de-de-", "ru-ru-", "id-id-")
        if target_engine == "edge-tts" and selected_voice and not any(vlow.startswith(p) for p in EDGE_PREFIXES) and vlow not in ("gtts-vi",):
            logger.warning(f"Unknown Edge-TTS voice '{selected_voice}', remapping to the language default")
            selected_voice = DEFAULT_VOICES.get(lang, {}).get("female", "vi-VN-HoaiMy-Fast")

        if target_engine in ("vieneu", "vieneu-tts"):
            provider = get_tts_provider("vieneu")
            return await provider.generate(
                text=text,
                lang=lang,
                voice=selected_voice,
                output_path=output_path,
            )

        if target_engine in ("kokoro", "kokoro-tts", "kokoro-82m"):
            try:
                provider = get_tts_provider("kokoro")
                return await provider.generate(text=text, lang=lang, voice=selected_voice, output_path=output_path)
            except Exception as e:
                logger.warning(f"Kokoro TTS failed ({e}), falling back to Edge-TTS Hoài My")
                target_engine = "edge-tts"
                selected_voice = DEFAULT_VOICES.get(lang, {}).get("female", "vi-VN-HoaiMy-Fast")

        if target_engine in ("melo", "melo-tts"):
            try:
                provider = get_tts_provider("melo-tts")
                return await provider.generate(text=text, lang=lang, voice=selected_voice, output_path=output_path)
            except Exception as e:
                logger.warning(f"Melo TTS failed ({e}), falling back to Edge-TTS")
                target_engine = "edge-tts"
                selected_voice = DEFAULT_VOICES.get(lang, {}).get("female", "vi-VN-HoaiMy-Fast")

        if target_engine == "edge-tts":
            try:
                return await self.generate_speech_edge_tts(
                    text, voice=selected_voice, output_path=output_path, rate=rate, pitch=pitch, volume=volume
                )
            except Exception as e:
                logger.warning(f"Edge-TTS failed ({e}), falling back to gTTS")
                target_engine = "gtts"

        if target_engine == "gtts":
            try:
                provider = get_tts_provider("gtts")
                return await provider.generate(text=text, lang=lang, output_path=output_path)
            except Exception as e:
                logger.warning(f"gTTS failed ({e}), attempting Coqui TTS")
                target_engine = "coqui-tts"

        if target_engine == "coqui-tts":
            try:
                provider = get_tts_provider("coqui-tts")
                return await provider.generate(text=text, output_path=output_path)
            except Exception as e:
                logger.warning(f"Coqui TTS failed ({e})")

        raise TTSServiceError(
            f"All TTS engines failed for voice={selected_voice} engine={engine}. Refusing silent WAV."
        )

    def _assemble_synchronized_audio(self, clips: List[Dict[str, Any]], total_duration: float, output_path: str) -> bool:
        """Assembles timed audio clips using FFmpeg adelay, amix, and EBU R128 loudnorm volume mastering."""
        from app.services.audio_service import find_ffmpeg_binary
        ffmpeg_bin = find_ffmpeg_binary()
        if not ffmpeg_bin or not clips:
            logger.warning("TTS assemble skipped: no ffmpeg or no clips")
            return False

        clips_sorted = sorted(clips, key=lambda c: float(c["segment"]["start_time"]))
        inputs = []
        filter_nodes = []
        map_labels = []

        for idx, item in enumerate(clips_sorted):
            clip_path = item["clip_path"]
            start = float(item["segment"]["start_time"])
            start_ms = max(0, int(start * 1000))
            inputs.extend(["-i", clip_path])
            label = f"a{idx}"
            filter_nodes.append(
                f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS,adelay={start_ms}|{start_ms}:all=1[{label}]"
            )
            map_labels.append(f"[{label}]")

        # No loudnorm here — stacking + loudnorm is what made voices echo/double.
        mix_filter = (
            "".join(map_labels)
            + f"amix=inputs={len(clips_sorted)}:dropout_transition=0:normalize=0:duration=longest[aout]"
        )
        filter_complex = ";".join(filter_nodes + [mix_filter])

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        cmd = [ffmpeg_bin, "-y"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[aout]"
        ]
        if total_duration > 0:
            cmd.extend(["-t", f"{total_duration:.3f}"])
        cmd.extend([
            "-c:a", "pcm_s16le" if output_path.endswith(".wav") else "aac",
            output_path
        ])

        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True

        logger.warning(f"TTS mix assemble failed ({res.returncode}): {(res.stderr or '')[-400:]}")
        return False

    async def synthesize_synchronized_tts(
        self,
        srt_path: str,
        output_audio_path: str,
        voice: str = "vi-VN-HoaiMy-Fast",
        lang: str = "vi",
        engine: Optional[str] = None,
        total_duration: Optional[float] = None,
        enable_lipsync: bool = True,
        timeline_speed: float = 1.0,
    ) -> Dict[str, Any]:
        """Synthesizes exact subtitle text and keeps its spoken timeline synchronized."""
        if not os.path.exists(srt_path):
            raise FileNotFoundError(f"SRT file not found: {srt_path}")

        raw_segments = parse_srt_segments(srt_path)
        if not raw_segments:
            raise TTSServiceError(f"No valid subtitle segments found in SRT file: {srt_path}")

        # Translation already produced this SRT. Rewriting it here made the visible
        # subtitle and spoken script disagree, so TTS must use this exact text.
        speed = max(0.1, float(timeline_speed or 1.0))
        segments = [
            {
                **seg,
                "start_time": float(seg["start_time"]) / speed,
                "end_time": float(seg["end_time"]) / speed,
                "duration": float(seg["duration"]) / speed,
            }
            for seg in raw_segments
        ]
        if (engine or self.default_engine or "edge-tts").lower() == "edge-tts":
            segments = group_long_form_tts_segments(segments)

        temp_dir = tempfile.mkdtemp(prefix="tts_sync_")
        processed_clips = []

        try:
            srt_max_end = max(seg["end_time"] for seg in segments) if segments else 0.0
            if total_duration is not None and total_duration > 0:
                effective_total_duration = max(total_duration / speed, srt_max_end)
            else:
                effective_total_duration = srt_max_end

            async def _synth_one(seg, next_start: Optional[float] = None):
                raw_clip_path = os.path.join(temp_dir, f"seg_{seg['index']}_raw.mp3")
                scaled_clip_path = os.path.join(temp_dir, f"seg_{seg['index']}_scaled.wav")
                text_to_speak = (seg.get("text") or "").strip()
                if (
                    not text_to_speak
                    or text_to_speak.startswith("[")
                    or not any(char.isalnum() for char in text_to_speak)
                ):
                    logger.info(
                        "Skipping non-spoken TTS segment %s: %r",
                        seg["index"],
                        text_to_speak,
                    )
                    return None
                srt_dur = float(seg.get("duration") or 0.0)
                emotion = seg.get("emotion", "neutral")
                rate_val = "+0%"
                pitch_val = "+0Hz"
                if emotion in ("angry", "terrified", "dramatic"):
                    pitch_val = "+4Hz"
                elif emotion in ("gentle", "reassuring", "sad"):
                    pitch_val = "+1Hz"
                elif emotion in ("surprised", "excited"):
                    pitch_val = "+6Hz"

                lipsync_on = bool(enable_lipsync)
                if lipsync_on:
                    from app.services.lipsync_service import lipsync_prepare_segment
                    prep = lipsync_prepare_segment(text_to_speak, srt_dur)
                    text_to_speak = prep["text"]
                    rate_val = prep["rate"]
                else:
                    if emotion in ("angry", "terrified", "dramatic"):
                        rate_val = "+5%"
                    elif emotion in ("gentle", "reassuring", "sad"):
                        rate_val = "-3%"
                    elif emotion in ("surprised", "excited"):
                        rate_val = "+6%"

                # Keep an explicitly selected review voice consistent across every segment.
                seg_gender = (seg.get("gender") or "neutral").lower().strip()
                seg_voice = voice
                if lang in DEFAULT_VOICES:
                    lang_voices = DEFAULT_VOICES[lang]
                    if not voice:
                        seg_voice = lang_voices.get(seg_gender) or lang_voices.get("female") or voice

                try:
                    await self.generate_speech(
                        text=text_to_speak,
                        lang=lang,
                        voice=seg_voice,
                        engine=engine,
                        output_path=raw_clip_path,
                        rate=rate_val,
                        pitch=pitch_val
                    )
                except Exception as e:
                    logger.warning(f"TTS synthesis failed for segment {seg['index']}: {e}")
                    return None
                if not os.path.exists(raw_clip_path) or os.path.getsize(raw_clip_path) < 256:
                    logger.warning(f"Skipping empty TTS clip for segment {seg['index']}")
                    return None

                if lipsync_on:
                    from app.services.lipsync_service import fit_clip_to_window
                    ok = fit_clip_to_window(raw_clip_path, scaled_clip_path, srt_dur)
                    clip_to_use = scaled_clip_path if ok else raw_clip_path
                    clamped_speed = get_audio_duration(raw_clip_path) / max(0.18, srt_dur)
                else:
                    audio_dur = get_audio_duration(raw_clip_path)
                    if srt_dur > 0.1 and audio_dur > 0.1:
                        speed_factor = audio_dur / srt_dur
                    else:
                        speed_factor = 1.0
                    clamped_speed = max(0.85, min(1.30, speed_factor))
                    if abs(clamped_speed - 1.0) > 0.05:
                        success = scale_audio_speed_ffmpeg(raw_clip_path, scaled_clip_path, clamped_speed)
                        clip_to_use = scaled_clip_path if success else raw_clip_path
                    else:
                        clip_to_use = raw_clip_path

                final_clip_dur = get_audio_duration(clip_to_use)
                return {
                    "segment": {**seg, "text": text_to_speak, "duration": srt_dur},
                    "clip_path": clip_to_use,
                    "audio_dur": get_audio_duration(raw_clip_path),
                    "target_dur": srt_dur,
                    "speed_factor": clamped_speed,
                    "final_dur": final_clip_dur
                }

            batch_size = 6
            for i in range(0, len(segments), batch_size):
                batch = segments[i:i + batch_size]
                coros = []
                for j, seg in enumerate(batch):
                    nxt = None
                    abs_i = i + j
                    if abs_i + 1 < len(segments):
                        nxt = float(segments[abs_i + 1]["start_time"])
                    coros.append(_synth_one(seg, nxt))
                results = await asyncio.gather(*coros, return_exceptions=True)
                for item in results:
                    if isinstance(item, dict) and item.get("clip_path"):
                        processed_clips.append(item)
                    elif isinstance(item, Exception):
                        logger.warning(f"TTS batch item failed: {item}")

            # Preserve every spoken word. When a translated sentence needs more
            # room, move the following cue forward and use the same adjusted
            # timing for both the audio and the burned subtitle.
            previous_end = 0.0
            for item in sorted(processed_clips, key=lambda c: float(c["segment"]["start_time"])):
                desired_start = float(item["segment"]["start_time"])
                start = max(desired_start, previous_end + (0.04 if previous_end > 0 else 0.0))
                duration = max(0.12, float(item.get("final_dur") or 0.0))
                item["segment"]["start_time"] = start
                item["segment"]["end_time"] = start + duration
                item["segment"]["duration"] = duration
                previous_end = start + duration

            if processed_clips:
                effective_total_duration = max(
                    effective_total_duration,
                    max(float(item["segment"]["end_time"]) for item in processed_clips),
                )

            aligned_srt_path = os.path.splitext(srt_path)[0] + ".aligned.srt"
            aligned_lines = []
            for idx, item in enumerate(sorted(processed_clips, key=lambda c: float(c["segment"]["start_time"])), start=1):
                seg = item["segment"]
                # Subtitles are burned before setpts=PTS/speed, so store their
                # timestamps in the pre-speed timeline.
                start = float(seg["start_time"]) * speed
                end = float(seg["end_time"]) * speed
                aligned_lines.append(
                    f"{idx}\n{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n{seg['text']}\n"
                )
            with open(aligned_srt_path, "w", encoding="utf-8") as aligned_file:
                aligned_file.write("\n".join(aligned_lines) + ("\n" if aligned_lines else ""))

            os.makedirs(os.path.dirname(os.path.abspath(output_audio_path)), exist_ok=True)
            if not processed_clips:
                raise TTSServiceError("No TTS clips were generated from SRT segments")
            ok = self._assemble_synchronized_audio(processed_clips, effective_total_duration, output_audio_path)
            if not ok:
                raise TTSServiceError("Failed to assemble synchronized TTS audio")

            return {
                "status": "completed",
                "output_audio_path": output_audio_path,
                "segment_count": len(processed_clips),
                "total_duration": effective_total_duration,
                "aligned_srt_path": aligned_srt_path,
                "clips": processed_clips
            }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


tts_service = TTSService()
