"""
Audio Processing & Vocal Muting / Extraction Service.
=====================================================
Target Path: app/services/audio_service.py

Provides vocal separation, vocal muting filtergraphs (center-channel cancellation,
speech bandpass/bandreject filtering), Demucs stem separation fallback execution,
audio channel probing, and audio track mixing/ducking.
"""

import os
import shutil
import subprocess
import logging
import tempfile
from typing import Dict, Any, Optional, Tuple, List

from app.models.job import ReupConfig

logger = logging.getLogger(__name__)


def find_ffmpeg_binary() -> Optional[str]:
    """Locates ffmpeg executable in PATH or standard system installation paths."""
    path = shutil.which("ffmpeg")
    if not path:
        for candidate in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"]:
            if os.path.exists(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return path


def find_ffprobe_binary() -> Optional[str]:
    """Locates ffprobe executable in PATH or standard system installation paths."""
    path = shutil.which("ffprobe")
    if not path:
        for candidate in ["/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "/usr/bin/ffprobe"]:
            if os.path.exists(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return path


def check_demucs_available() -> bool:
    """
    Checks if demucs is available either as a CLI executable or Python module.
    Returns True if demucs can be executed, False otherwise.
    """
    if shutil.which("demucs"):
        return True
    try:
        import demucs  # type: ignore # noqa: F401
        return True
    except ImportError:
        pass
    return False


def probe_audio_channels(audio_path: str) -> int:
    """
    Probes audio file to determine number of channels (1 for mono, 2 for stereo).
    Defaults to 2 (stereo) if probing fails or is inconclusive.
    """
    ffprobe_bin = find_ffprobe_binary()
    if ffprobe_bin and os.path.exists(audio_path):
        try:
            cmd = [
                ffprobe_bin, "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=channels",
                "-of", "csv=p=0",
                audio_path
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0 and res.stdout.strip().isdigit():
                return int(res.stdout.strip())
        except Exception as e:
            logger.warning(f"Failed to probe audio channels via ffprobe: {e}")

    # Fallback inspection via ffmpeg -i
    ffmpeg_bin = find_ffmpeg_binary()
    if ffmpeg_bin and os.path.exists(audio_path):
        try:
            cmd = [ffmpeg_bin, "-i", audio_path]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            out = res.stderr or res.stdout or ""
            if "mono" in out:
                return 1
            if "stereo" in out or "5.1" in out:
                return 2
        except Exception as e:
            logger.warning(f"Failed to probe audio channels via ffmpeg: {e}")

    return 2


def extract_audio_stream(video_path: str, output_audio_path: str, sample_rate: int = 44100) -> bool:
    """
    Extracts raw audio track from video file into 16-bit PCM WAV audio format.
    """
    ffmpeg_bin = find_ffmpeg_binary()
    if not ffmpeg_bin:
        logger.error("FFmpeg binary not found for audio extraction")
        return False

    os.makedirs(os.path.dirname(os.path.abspath(output_audio_path)), exist_ok=True)
    cmd = [
        ffmpeg_bin, "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        output_audio_path
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return res.returncode == 0 and os.path.exists(output_audio_path) and os.path.getsize(output_audio_path) > 0
    except Exception as e:
        logger.error(f"Error extracting audio stream from {video_path}: {e}")
        return False


def extract_vocals_demucs(
    input_audio_path: str,
    output_dir: str,
    model_name: str = "htdemucs"
) -> Tuple[Optional[str], Optional[str]]:
    """
    Executes Demucs stem separation on input audio.
    Returns tuple of (vocal_wav_path, bgm_wav_path).
    Raises RuntimeError if Demucs execution fails or binary is missing.
    """
    if not os.path.exists(input_audio_path):
        raise FileNotFoundError(f"Input audio file not found: {input_audio_path}")

    demucs_bin = shutil.which("demucs")
    os.makedirs(output_dir, exist_ok=True)

    if demucs_bin:
        cmd = [
            demucs_bin,
            "-n", model_name,
            "-o", output_dir,
            "--two-stems", "vocals",
            input_audio_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            track_name = os.path.splitext(os.path.basename(input_audio_path))[0]
            separated_dir = os.path.join(output_dir, model_name, track_name)
            vocal_path = os.path.join(separated_dir, "vocals.wav")
            bgm_path = os.path.join(separated_dir, "no_vocals.wav")

            if os.path.exists(vocal_path) and os.path.exists(bgm_path):
                return vocal_path, bgm_path

    # Try Python demucs module entry point if available
    try:
        import demucs.separate  # type: ignore
        sys_args = ["-n", model_name, "-o", output_dir, "--two-stems", "vocals", input_audio_path]
        demucs.separate.main(sys_args)

        track_name = os.path.splitext(os.path.basename(input_audio_path))[0]
        separated_dir = os.path.join(output_dir, model_name, track_name)
        vocal_path = os.path.join(separated_dir, "vocals.wav")
        bgm_path = os.path.join(separated_dir, "no_vocals.wav")

        if os.path.exists(vocal_path) and os.path.exists(bgm_path):
            return vocal_path, bgm_path
    except Exception as e:
        logger.warning(f"Python demucs separation execution failed: {e}")

    raise RuntimeError(f"Demucs vocal extraction failed or is unavailable for {input_audio_path}")


def build_timed_speech_ducking_filter(
    speech_intervals: List[Tuple[float, float]],
    duck_volume: float = 0.12
) -> str:
    """
    Constructs an FFmpeg volume ducking filter around detected speech timestamps. The
    original track remains present at a low level, preserving effects such as meows,
    footsteps, and room ambience instead of replacing the whole soundtrack with a
    Demucs music stem.
    """
    if not speech_intervals:
        return ""

    conditions = []
    for start, end in speech_intervals:
        s = max(0.0, float(start) - 0.10)
        e = float(end) + 0.15
        if e > s:
            conditions.append(f"between(t,{s:.3f},{e:.3f})")

    if not conditions:
        return ""

    expr = "+".join(conditions)
    return f"volume='if({expr},{duck_volume:.2f},1.0)':eval=frame"


def build_vocal_mute_ffmpeg_filter(
    preserve_bgm: bool = True,
    vocal_mute_strategy: str = "auto",
    is_stereo: bool = True
) -> str:
    """
    Constructs clean, high-fidelity FFmpeg audio filter for vocal muting / ducking.
    Eliminates destructive phase inversion (pan L-R) to prevent hollow buzzing 'ồ ồ ồ' artifacts.
    Preserves clean stereo spatial imaging, crisp music acoustics, and smooth background level.
    """
    if vocal_mute_strategy == "mute_all" or not preserve_bgm:
        return "volume=0"

    # Stereo mid-side vocal suppression: cancels center vocals while keeping the
    # remaining track near its original loudness.
    return (
        "asplit=2[vm_bass_in][vm_mid_in];"
        "[vm_bass_in]lowpass=f=160:poles=2,volume=0.85[vm_bass];"
        "[vm_mid_in]highpass=f=160,stereotools=mlev=0.02:slev=1.35[vm_sides];"
        "[vm_bass][vm_sides]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,volume=1.0"
    )



def apply_ffmpeg_vocal_mute(
    input_audio_path: str,
    output_audio_path: str,
    preserve_bgm: bool = True,
    vocal_mute_strategy: str = "auto",
    is_stereo: Optional[bool] = None
) -> bool:
    """
    Applies FFmpeg vocal muting/attenuation filtergraph to input audio file.
    """
    ffmpeg_bin = find_ffmpeg_binary()
    if not ffmpeg_bin:
        logger.error("FFmpeg binary not found for vocal muting")
        return False

    if is_stereo is None:
        num_channels = probe_audio_channels(input_audio_path)
        is_stereo = num_channels >= 2

    af_filter = build_vocal_mute_ffmpeg_filter(
        preserve_bgm=preserve_bgm,
        vocal_mute_strategy=vocal_mute_strategy,
        is_stereo=is_stereo
    )

    os.makedirs(os.path.dirname(os.path.abspath(output_audio_path)), exist_ok=True)
    if ";" in (af_filter or ""):
        cmd = [
            ffmpeg_bin, "-y",
            "-i", input_audio_path,
            "-filter_complex", f"[0:a]{af_filter}[aout]",
            "-map", "[aout]",
            "-c:a", "pcm_s16le" if output_audio_path.endswith(".wav") else "aac",
            output_audio_path,
        ]
    else:
        cmd = [
            ffmpeg_bin, "-y",
            "-i", input_audio_path,
            "-af", af_filter,
            "-c:a", "pcm_s16le" if output_audio_path.endswith(".wav") else "aac",
            output_audio_path,
        ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return res.returncode == 0 and os.path.exists(output_audio_path) and os.path.getsize(output_audio_path) > 0
    except Exception as e:
        logger.error(f"Failed to execute FFmpeg vocal mute filter on {input_audio_path}: {e}")
        return False


def mix_separated_stems(
    background_path: str,
    vocal_path: str,
    output_path: str,
    vocal_volume: float = 0.10,
) -> bool:
    """Keeps the separated background intact while retaining quiet source dialogue."""
    ffmpeg_bin = find_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(background_path) or not os.path.exists(vocal_path):
        return False

    gain = max(0.0, min(1.0, float(vocal_volume)))
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    filter_complex = (
        "[0:a]aresample=44100,aformat=channel_layouts=stereo[bg];"
        f"[1:a]aresample=44100,aformat=channel_layouts=stereo,volume={gain:.3f}[voc];"
        "[bg][voc]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[aout]"
    )
    cmd = [
        ffmpeg_bin, "-y",
        "-i", background_path,
        "-i", vocal_path,
        "-filter_complex", filter_complex,
        "-map", "[aout]",
        "-c:a", "pcm_s16le" if output_path.endswith(".wav") else "aac",
        output_path,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
        logger.warning(f"Separated stem mix failed ({res.returncode}): {(res.stderr or '')[-400:]}")
    except Exception as e:
        logger.warning(f"Separated stem mix failed: {e}")
    return False


def mix_audio_tracks(
    bgm_path: str,
    voiceover_path: str,
    output_path: str,
    bgm_volume: float = 0.28,
    voice_volume: float = 1.35,
    audio_ducking: bool = True
) -> bool:
    """
    Mixes preserved background audio (BGM) with new TTS/voiceover audio track.
    Supports dynamic sidechain audio ducking and loudness normalization.
    """
    ffmpeg_bin = find_ffmpeg_binary()
    if not ffmpeg_bin:
        logger.error("FFmpeg binary not found for audio mixing")
        return False

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if audio_ducking:
        filter_complex = (
            f"[0:a]volume={bgm_volume:.2f}[bgm];"
            f"[1:a]volume={voice_volume:.2f}[vox];"
            f"[bgm][vox]sidechaincompress=threshold=0.08:ratio=4:attack=15:release=350[ducked];"
            f"[ducked][vox]amix=inputs=2:duration=first:dropout_transition=2,loudnorm=I=-14:LRA=7:TP=-1.0[a_out]"
        )
    else:
        filter_complex = (
            f"[0:a]volume={bgm_volume:.2f}[bgm];"
            f"[1:a]volume={voice_volume:.2f}[vox];"
            f"[bgm][vox]amix=inputs=2:duration=first:dropout_transition=2,loudnorm=I=-14:LRA=7:TP=-1.0[a_out]"
        )

    cmd = [
        ffmpeg_bin, "-y",
        "-i", bgm_path,
        "-i", voiceover_path,
        "-filter_complex", filter_complex,
        "-map", "[a_out]",
        "-c:a", "aac", "-b:a", "192k",
        output_path
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        logger.error(f"Error mixing audio tracks ({bgm_path}, {voiceover_path}): {e}")
        return False


def process_vocal_muting(
    input_audio_path: str,
    output_audio_path: str,
    config: Optional[ReupConfig] = None
) -> Dict[str, Any]:
    """
    Main entry point for vocal extraction, separation, and muting.

    Handles Demucs execution with automatic fallback to FFmpeg filtergraph vocal muting.
    Returns dictionary with details on execution status and separation method used.
    """
    cfg = config or ReupConfig()
    if not os.path.exists(input_audio_path):
        raise FileNotFoundError(f"Input audio file not found: {input_audio_path}")

    strategy = cfg.vocal_mute_strategy.lower()
    preserve_bgm = cfg.preserve_bgm
    enable_mute = cfg.enable_vocal_mute

    if not enable_mute:
        shutil.copyfile(input_audio_path, output_audio_path)
        return {
            "status": "completed",
            "method": "none",
            "vocal_mute_enabled": False,
            "input_audio_path": input_audio_path,
            "output_audio_path": output_audio_path
        }

    if strategy == "mute_all" or not preserve_bgm:
        success = apply_ffmpeg_vocal_mute(
            input_audio_path,
            output_audio_path,
            preserve_bgm=False,
            vocal_mute_strategy="mute_all"
        )
        return {
            "status": "completed" if success else "failed",
            "method": "mute_all",
            "input_audio_path": input_audio_path,
            "output_audio_path": output_audio_path
        }

    demucs_attempted = False
    demucs_success = False

    if strategy in ("auto", "demucs", "demucs_duck"):
        demucs_attempted = True
        if check_demucs_available():
            try:
                with tempfile.TemporaryDirectory(prefix="demucs_out_") as tmp_dir:
                    vocal_path, bgm_path = extract_vocals_demucs(input_audio_path, tmp_dir)
                    if bgm_path and os.path.exists(bgm_path):
                        if strategy == "demucs_duck":
                            if not vocal_path or not mix_separated_stems(
                                bgm_path,
                                vocal_path,
                                output_audio_path,
                                vocal_volume=cfg.original_vocal_volume,
                            ):
                                raise RuntimeError("Failed to mix the separated vocal and background stems")
                        else:
                            shutil.copyfile(bgm_path, output_audio_path)
                        demucs_success = True
                        return {
                            "status": "completed",
                            "method": strategy if strategy == "demucs_duck" else "demucs",
                            "input_audio_path": input_audio_path,
                            "output_audio_path": output_audio_path,
                            "demucs_vocal_path": vocal_path,
                            "demucs_bgm_path": bgm_path
                        }
            except Exception as e:
                logger.warning(f"Demucs extraction failed for {input_audio_path}: {e}")
                if strategy == "demucs":
                    raise RuntimeError(f"Demucs vocal separation explicitly requested but failed: {e}")
            if strategy == "demucs":
                raise RuntimeError("Demucs strategy requested but demucs separation failed")
        else:
            if strategy == "demucs":
                raise RuntimeError("Demucs strategy requested but demucs is not installed")

    ffmpeg_success = apply_ffmpeg_vocal_mute(
        input_audio_path,
        output_audio_path,
        preserve_bgm=preserve_bgm,
        vocal_mute_strategy=strategy
    )

    return {
        "status": "completed" if ffmpeg_success else "failed",
        "method": "ffmpeg_filter",
        "demucs_attempted": demucs_attempted,
        "demucs_success": demucs_success,
        "input_audio_path": input_audio_path,
        "output_audio_path": output_audio_path
    }
