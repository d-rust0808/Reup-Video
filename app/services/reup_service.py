"""
Reup Video Transformation Engine.
=================================
Constructs and executes a single-pass FFmpeg master filtergraph pass for
video (hflip, setpts, crop, scale, eq, hue, unsharp) and audio (atempo, asetrate, aresample).

Target Path: app/services/reup_service.py
"""

import json
import os
import shutil
import subprocess
import logging
import tempfile
from typing import Dict, Any, Optional, Tuple, List

from app.models.job import ReupConfig
from app.services.md5_service import calculate_file_md5, modify_video_md5_fast, modify_md5

logger = logging.getLogger(__name__)


def find_ffmpeg_binary() -> Optional[str]:
    """Locates ffmpeg executable in PATH or standard system installation paths."""
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
    path = shutil.which("ffmpeg")
    if path:
        return path
    for candidate in [
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ]:
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


_LIBASS_CACHE: Optional[bool] = None


def ffmpeg_supports_libass(ffmpeg_bin: Optional[str] = None) -> bool:
    """True if this ffmpeg has the `subtitles` filter (built with libass).
    Slim Homebrew `ffmpeg` builds omit it; `ffmpeg-full` includes it."""
    global _LIBASS_CACHE
    if _LIBASS_CACHE is not None:
        return _LIBASS_CACHE
    binary = ffmpeg_bin or find_ffmpeg_binary()
    if not binary:
        _LIBASS_CACHE = False
        return False
    try:
        res = subprocess.run(
            [binary, "-hide_banner", "-filters"],
            capture_output=True, text=True, check=False,
        )
        _LIBASS_CACHE = " subtitles " in (res.stdout or "")
    except Exception:
        _LIBASS_CACHE = False
    return _LIBASS_CACHE


def browser_safe_encode_args(ffmpeg_bin: str, force_software: bool = False) -> list:
    """H.264/AAC flags that HTML5 players (Chrome/Safari) can actually play.

    VideoToolbox ignores setpts=PTS/speed, so speed-changed jobs must use libx264
    or the picture keeps original timing while audio is atempo'd (or the reverse).
    """
    if force_software:
        encoder_name, encoder_flags = "libx264", ["-preset", "veryfast", "-crf", "20"]
    else:
        encoder_name, encoder_flags = detect_h264_encoder(ffmpeg_bin)
    args = ["-c:v", encoder_name, *encoder_flags, "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if encoder_name == "libx264":
        args.extend(["-profile:v", "main", "-level", "4.0"])
    return args


def remux_faststart(path: str) -> bool:
    """Move moov atom to the front so the browser can start playback immediately."""
    ffmpeg_bin = find_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(path):
        return False
    tmp = path + ".faststart.mp4"
    cmd = [
        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error", "-i", path,
        "-map", "0", "-c", "copy", "-movflags", "+faststart", tmp,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        os.replace(tmp, path)
        return True
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return False


def repair_output_speed(path: str, source_dur: float, speed: float) -> bool:
    """If setpts was ignored, re-encode both streams at the requested speed with libx264."""
    from app.services.audio_service import ensure_av_lock

    speed = float(speed or 1.0)
    source_dur = float(source_dur or 0.0)
    if not path or not os.path.exists(path):
        return False
    if abs(speed - 1.0) <= 0.01:
        return ensure_av_lock(path)
    if ensure_av_lock(path, source_dur=source_dur, speed=speed):
        return True
    ffmpeg_bin = find_ffmpeg_binary()
    if not ffmpeg_bin:
        return False
    tempo = ",".join(_build_atempo_nodes(speed) + ["asetpts=PTS-STARTPTS"])
    if not tempo:
        tempo = "anull"
    tmp = path + ".speedfix.mp4"
    out_t = max(0.4, source_dur / max(0.5, speed)) if source_dur > 0.4 else 0.0
    has_audio = detect_audio_stream(path)
    if has_audio:
        fc = (
            f"[0:v]setpts=PTS/{speed:.4f},fps=30,setpts=PTS-STARTPTS[v];"
            f"[0:a]{tempo}[a]"
        )
        maps = ["-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    else:
        fc = f"[0:v]setpts=PTS/{speed:.4f},fps=30,setpts=PTS-STARTPTS[v]"
        maps = ["-map", "[v]", "-an"]
    cmd = [
        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-i", path,
        "-filter_complex", fc,
        *maps,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-profile:v", "main", "-level", "4.0",
        "-fps_mode", "cfr", "-r", "30",
        "-movflags", "+faststart",
        "-muxdelay", "0", "-muxpreload", "0",
    ]
    if out_t > 0.4:
        cmd.extend(["-t", f"{out_t:.3f}"])
    cmd.append(tmp)
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        os.replace(tmp, path)
        logger.info("Re-encoded sped output with libx264 after setpts was ignored")
        return ensure_av_lock(path, source_dur=source_dur, speed=speed)
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    logger.error("Speed repair encode failed (%s): %s", res.returncode, (res.stderr or "")[-400:])
    return False


def detect_h264_encoder(ffmpeg_bin: str) -> Tuple[str, List[str]]:
    """
    Probes FFmpeg for hardware acceleration support.
    Returns (encoder_name, rate_control_flags_list).
    """
    try:
        res = subprocess.run([ffmpeg_bin, "-encoders"], capture_output=True, text=True, check=True)
        if "h264_videotoolbox" in res.stdout:
            return "h264_videotoolbox", ["-b:v", "4M"]
        elif "h264_nvenc" in res.stdout:
            return "h264_nvenc", ["-preset", "p4", "-cq", "23"]
    except Exception as e:
        logger.warning(f"Failed to probe FFmpeg encoders: {e}")

    return "libx264", ["-preset", "veryfast", "-crf", "20"]


def detect_audio_stream(input_path: str) -> bool:
    """
    Probes input file using ffprobe (or ffmpeg fallback) to check if an audio stream exists.
    Returns False ONLY if prober successfully analyzes the file and confirms no audio stream.
    Defaults to True if file cannot be probed (e.g. synthetic test files or prober error).
    """
    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        for candidate in ["/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "/usr/bin/ffprobe"]:
            if os.path.exists(candidate) and os.access(candidate, os.X_OK):
                ffprobe_bin = candidate
                break

    if ffprobe_bin:
        try:
            cmd = [
                ffprobe_bin, "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index",
                "-of", "csv=p=0",
                input_path
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0:
                return bool(res.stdout.strip())
        except Exception as e:
            logger.warning(f"ffprobe audio detection failed: {e}")

    # Fallback to ffmpeg -i inspection
    ffmpeg_bin = find_ffmpeg_binary()
    if ffmpeg_bin:
        try:
            cmd = [ffmpeg_bin, "-i", input_path]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            output = res.stderr or res.stdout or ""
            if "Audio:" in output:
                return True
            if "Video:" in output and "Audio:" not in output:
                return False
        except Exception as e:
            logger.warning(f"ffmpeg audio detection fallback failed: {e}")

    return True


def _build_atempo_nodes(tempo_factor: float) -> List[str]:
    """Helper to chain FFmpeg atempo filters within [0.5, 2.0] bounds."""
    if tempo_factor <= 0.0 or tempo_factor < 0.01:
        logger.warning(f"Invalid tempo_factor={tempo_factor} passed to _build_atempo_nodes")
        return []
    nodes = []
    t = tempo_factor
    while t > 2.0:
        nodes.append("atempo=2.0")
        t /= 2.0
    while t < 0.5:
        nodes.append("atempo=0.5")
        t /= 0.5
    if abs(t - 1.0) > 1e-4:
        nodes.append(f"atempo={t:.4f}")
    return nodes


def build_reup_filtergraph(
    cfg: ReupConfig,
    target_width: int = 1920,
    target_height: int = 1080,
    audio_sample_rate: int = 0,
    has_audio: bool = True,
    burn_srt_path: Optional[str] = None,
    speech_intervals: Optional[List[Tuple[float, float]]] = None,
    frame_size: Optional[Tuple[int, int]] = None,
    duck_command_path: Optional[str] = None,
) -> Tuple[str, bool, str, str]:
    """
    Constructs unified single-pass complex filtergraph string alongside individual
    video and audio filter chains.

    Returns:
        (filter_complex_str, includes_audio_stream, video_filters_str, audio_filters_str)
    """
    from app.services.caption_cover import (
        caption_cover_drawbox,
        clamp_subtitle_box_h,
        cover_band_height,
        drop_delogo_nodes,
        normalize_caption_cover,
        should_crop_bottom,
        subtitle_force_style,
        subtitle_plate_drawbox,
    )

    vf_nodes = []
    extra_cover = (getattr(cfg, "text_cover_vf", None) or "").strip()
    if extra_cover:
        vf_nodes.extend(drop_delogo_nodes(extra_cover))

    if cfg.hflip:
        vf_nodes.append("hflip")

    cover = normalize_caption_cover(getattr(cfg, "caption_cover", "off"))
    bottom = float(getattr(cfg, "subtitle_bottom_crop", 0.0) or 0.0)
    force_crop = bool(getattr(cfg, "force_bottom_crop", False))
    cover_h = cover_band_height(bottom, cover)
    cover_pad = float(getattr(cfg, "cover_pad", 0.0) or 0.0)
    # Image banner keeps 9:16 (no empty letterbox). Color plate paints. Crop cuts pixels.
    do_bottom_crop = should_crop_bottom(cover, force_crop, bottom)
    if do_bottom_crop:
        cover = "off"
        vf_nodes.append(f"crop=iw:trunc(ih*(1-{bottom:.4f})/2)*2:0:0")

    if getattr(cfg, "dynamic_motion", False):
        # Dynamic micro-zoom / subtle temporal breathing to disrupt Meta TMK/PDQ spatial feature kernels
        vf_nodes.append("crop=w='trunc(iw*(1-0.03*abs(sin(2*PI*t/12)))/2)*2':h='trunc(ih*(1-0.03*abs(sin(2*PI*t/12)))/2)*2':x='(iw-ow)/2':y='(ih-oh)/2'")
    elif cfg.crop_percent > 0:
        p = cfg.crop_percent
        vf_nodes.append(f"crop=iw*(1-2*{p:.4f}):ih*(1-2*{p:.4f})")

    # Always force even dimensions after crop so yuv420p / x264 never rejects the encode
    vf_nodes.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

    if cover != "off":
        bar = caption_cover_drawbox(cover, bottom, cover_pad=cover_pad)
        if bar:
            vf_nodes.append(bar)

    # Burn Vietsub on original timestamps BEFORE setpts so SRT does not need rescaling
    if burn_srt_path and os.path.exists(burn_srt_path):
        if frame_size and len(frame_size) == 2:
            frame_w, frame_h = int(frame_size[0] or target_width), int(frame_size[1] or target_height)
        else:
            frame_w, frame_h = int(target_width or 1920), int(target_height or 1080)
        cue_y = float(getattr(cfg, "subtitle_y", 0.0) or 0.0)
        box_h = clamp_subtitle_box_h(getattr(cfg, "subtitle_box_h", 0.08))
        plate = subtitle_plate_drawbox(
            cover,
            float(getattr(cfg, "subtitle_box_w", 0.88) or 0.88),
            box_h,
            subtitle_y=cue_y,
            band_h=cover_h if cover == "image" else bottom,
            video_w=frame_w,
            video_h=frame_h,
            srt_path=burn_srt_path,
        )
        if plate:
            vf_nodes.append(plate)
        style = subtitle_force_style(
            cover,
            cover_h if cover == "image" else bottom,
            video_w=frame_w, video_h=frame_h,
            subtitle_y=cue_y,
            cover_pad=cover_pad,
            subtitle_box_h=box_h,
        )
        vf_nodes.append(_subtitles_filter(burn_srt_path, style, frame_w, frame_h))

    # Cinematic border — printed onto every frame (after picture, under/with subs)
    if getattr(cfg, "frame_enabled", False):
        thick = int(getattr(cfg, "frame_thickness", 16) or 0)
        color = str(getattr(cfg, "frame_color", "black") or "black").strip() or "black"
        if color.startswith("#") and len(color) == 7:
            color = color.lstrip("#")
            color = f"0x{color}"
        if thick > 0:
            inner = max(2, thick // 6)
            vf_nodes.append(f"drawbox=x=0:y=0:w=iw:h=ih:t={thick}:color={color}@1")
            vf_nodes.append(
                f"drawbox=x={thick}:y={thick}:w=iw-{thick*2}:h=ih-{thick*2}:t={inner}:color=white@0.88"
            )

    s_ratio = float(cfg.speed_factor or 1.0)
    if abs(s_ratio - 1.0) > 1e-3:
        vf_nodes.append(f"setpts=PTS/{s_ratio:.4f}")
        vf_nodes.append("fps=30")
        vf_nodes.append("setpts=PTS-STARTPTS")

    if cfg.color_adjust or (cfg.brightness != 0.0 or cfg.contrast != 1.0 or cfg.saturation != 1.0):
        b, c, sat = cfg.brightness, cfg.contrast, cfg.saturation
        vf_nodes.append(f"eq=brightness={b:.4f}:contrast={c:.4f}:saturation={sat:.4f}")

    if cfg.hue_shift != 0.0:
        vf_nodes.append(f"hue=h={cfg.hue_shift}")

    grain = float(getattr(cfg, "film_grain", 0.0) or 0.0)
    if grain > 0:
        grain_val = max(1, int(round(grain)))
        vf_nodes.append(f"noise=alls={grain_val}:allf=t")

    if cfg.sharpen and cfg.unsharp_amount > 0:
        vf_nodes.append(f"unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount={cfg.unsharp_amount}")

    af_nodes = []
    mute_complex = ""
    if has_audio:
        if cfg.enable_vocal_mute and (cfg.vocal_mute_strategy == "mute_all" or not cfg.preserve_bgm):
            af_nodes.append("volume=0")
        elif cfg.enable_vocal_mute:
            if speech_intervals:
                from app.services.audio_service import build_timed_speech_ducking_filter
                leftover = leftover_original_vocal_volume(cfg)
                timed_filter = build_timed_speech_ducking_filter(
                    speech_intervals,
                    duck_volume=leftover,
                    command_path=duck_command_path,
                )
                if timed_filter:
                    af_nodes.append(timed_filter)
            else:
                from app.services.audio_service import build_vocal_mute_ffmpeg_filter
                vm_filter = build_vocal_mute_ffmpeg_filter(
                    preserve_bgm=cfg.preserve_bgm,
                    vocal_mute_strategy=cfg.vocal_mute_strategy,
                    is_stereo=True
                )
                if vm_filter and ";" in vm_filter:
                    mute_complex = vm_filter
                elif vm_filter:
                    af_nodes.append(vm_filter)

        if abs(s_ratio - 1.0) > 0.04:
            # Large speed-up must be a single atempo chain. Splitting 1.50x into
            # asetrate*1.03 + atempo*1.456 drifted when the probed rate was wrong
            # and left speech seconds behind the picture.
            af_nodes.extend(_build_atempo_nodes(s_ratio))
            af_nodes.append("asetpts=PTS-STARTPTS")
        elif cfg.pitch_shift:
            # Tiny anti-detect pitch only when speed stays near 1.0.
            raw_p_ratio = cfg.pitch_factor if cfg.pitch_factor != 1.0 else s_ratio
            p_ratio = max(0.97, min(1.03, raw_p_ratio))
            src_rate = int(audio_sample_rate or 0)
            applied_pitch = False
            if src_rate >= 8000 and abs(p_ratio - 1.0) > 1e-4:
                af_nodes.append(f"asetrate={src_rate}*{p_ratio:.4f},aresample={src_rate}")
                applied_pitch = True
            remaining_speed = (
                (s_ratio / p_ratio) if (applied_pitch and p_ratio) else s_ratio
            )
            if abs(remaining_speed - 1.0) > 1e-4:
                af_nodes.extend(_build_atempo_nodes(remaining_speed))
            af_nodes.append("asetpts=PTS-STARTPTS")
        elif abs(s_ratio - 1.0) > 1e-4:
            af_nodes.extend(_build_atempo_nodes(s_ratio))
            af_nodes.append("asetpts=PTS-STARTPTS")

    vf_str = ",".join(vf_nodes) if vf_nodes else ""
    af_str = ",".join(af_nodes) if af_nodes else ""
    if mute_complex:
        af_str = mute_complex + ((";" + af_str) if af_str else "")

    vf_graph = vf_str if vf_str else "null"
    if has_audio and (af_nodes or mute_complex):
        if mute_complex and af_nodes:
            filter_complex = (
                f"[0:v]{vf_graph}[v_out];"
                f"[0:a]{mute_complex}[a_muted];"
                f"[a_muted]{','.join(af_nodes)}[a_out]"
            )
        elif mute_complex:
            filter_complex = f"[0:v]{vf_graph}[v_out];[0:a]{mute_complex}[a_out]"
        else:
            filter_complex = f"[0:v]{vf_graph}[v_out];[0:a]{af_str}[a_out]"
        return filter_complex, True, vf_str, af_str
    if has_audio:
        filter_complex = f"[0:v]{vf_graph}[v_out];[0:a]anull[a_out]"
        return filter_complex, True, vf_str, "anull"
    filter_complex = f"[0:v]{vf_graph}[v_out]"
    return filter_complex, False, vf_str, af_str


def apply_vietnamese_dubbing(video_path: str, text_to_translate: str, output_path: Optional[str] = None) -> bool:
    """Translates text to Vietnamese via AGY only, generates Edge-TTS, and replaces video audio."""
    import asyncio
    try:
        from app.services import agy_cli_service
        from app.services.vietsub_rules import is_invalid_translation
        import edge_tts

        tts_file = video_path + ".vi_voice.mp3"
        vi_text = ""
        
        async def _gen_tts_and_translate():
            nonlocal vi_text
            if agy_cli_service.is_available():
                try:
                    lines = await asyncio.to_thread(
                        lambda: agy_cli_service.translate_cues(
                            [text_to_translate],
                            target_lang="vi",
                            style="dub",
                        )
                    )
                    vi_text = (lines or [""])[0]
                except Exception:
                    vi_text = ""
            if not vi_text or is_invalid_translation(vi_text):
                raise RuntimeError("AGY did not produce Vietnamese dubbing text")

            communicator = edge_tts.Communicate(vi_text, "vi-VN-HoaiMyNeural")
            await communicator.save(tts_file)
            
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import threading
                def _run_in_thread():
                    asyncio.run(_gen_tts_and_translate())
                t = threading.Thread(target=_run_in_thread)
                t.start()
                t.join()
            else:
                loop.run_until_complete(_gen_tts_and_translate())
        except Exception:
            asyncio.run(_gen_tts_and_translate())
        
        if os.path.exists(tts_file) and os.path.getsize(tts_file) > 0:
            target_out = output_path or video_path + ".vi.mp4"
            tmp_out = target_out + ".tmp_dub.mp4"
            cmd = [
                "ffmpeg", "-y", "-threads", "0", "-i", video_path, "-i", tts_file,
                "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0", "-shortest", tmp_out
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res.returncode == 0 and os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
                os.replace(tmp_out, target_out)
                if os.path.exists(tts_file):
                    os.remove(tts_file)
                return True
    except Exception as e:
        logger.warning(f"Vietnamese dubbing pass failed: {e}")
    return False


def _ffmpeg_subtitles_path(path: str) -> str:
    """Escape a filesystem path for FFmpeg subtitles= filter."""
    p = os.path.abspath(path).replace("\\", "/")
    p = p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return p


def _subtitles_filter(srt_path: str, style: str, video_w: int, video_h: int) -> str:
    """libass burn with PlayRes matching the actual frame so MarginV is in pixels."""
    sub_path = _ffmpeg_subtitles_path(srt_path)
    width = max(2, int(video_w or 1920))
    height = max(2, int(video_h or 1080))
    # libass force_style parses SSA v4 alignment (Top-center is 6, bottom-center is 2).
    # Alignment=8 (ASS v4+ numpad top-center) is unrecognized in force_style and falls back
    # to center (height/2), ignoring MarginV. Map Alignment=8 -> Alignment=6.
    cleaned_style = (style or "").replace("Alignment=8", "Alignment=6")
    if cleaned_style and "PlayResX" not in cleaned_style:
        cleaned_style = f"PlayResX={width},PlayResY={height},{cleaned_style}"
    return (
        f"subtitles='{sub_path}':original_size={width}x{height}:force_style='{cleaned_style}'"
    )


def _find_subtitle_font() -> str:
    """Pick a Latin-extended font that can render Vietnamese diacritics."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return ""


def drop_audio_chains(filter_complex: str) -> str:
    """Keep video/overlay chains when replacing the audio with looped BGM."""
    kept: List[str] = []
    for part in (filter_complex or "").split(";"):
        item = part.strip()
        if not item:
            continue
        if item.endswith("[a_out]") or item.endswith("[a_muted]"):
            continue
        if item.startswith("[0:a]") or item.startswith("[a_muted]") or item.startswith("[1:a]"):
            continue
        kept.append(item)
    return ";".join(kept)


def scale_srt_timestamps(srt_path: str, factor: float, output_path: Optional[str] = None) -> str:
    """Scale SRT start/end times by factor (use 1/speed_ratio after setpts=PTS/speed)."""
    from app.services.tts_service import parse_srt_segments, format_srt_timestamp

    if not srt_path or not os.path.exists(srt_path):
        raise FileNotFoundError(f"SRT not found: {srt_path}")
    if factor <= 0:
        factor = 1.0
    dest = output_path or (srt_path + ".scaled.srt")
    segs = parse_srt_segments(srt_path)
    lines = []
    for i, seg in enumerate(segs, start=1):
        start = max(0.0, float(seg["start_time"]) / factor)
        end = max(start + 0.04, float(seg["end_time"]) / factor)
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"{i}\n{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n{text}\n")
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    return dest


def subtitle_output_mode(cfg: ReupConfig) -> str:
    """Return the normalized subtitle mode while honoring the legacy enable flag."""
    if not getattr(cfg, "burn_subtitles", True):
        return "off"
    return "hard" if getattr(cfg, "subtitle_mode", "hard") == "hard" else "soft"


def abort_incomplete_reup(output_path: Optional[str], message: str) -> None:
    """Delete a half-built file so a FAILED job cannot be downloaded as the product."""
    if output_path and os.path.isfile(output_path):
        try:
            os.remove(output_path)
        except OSError:
            logger.warning("Could not remove incomplete output %s", output_path)
    raise RuntimeError(message)


def require_complete_reup(
    cfg: ReupConfig,
    *,
    translated_srt: Optional[str] = None,
    synced_tts_audio: Optional[str] = None,
    detail: str = "",
) -> None:
    """Refuse to encode a half-built file when the user asked for Vietsub/TTS."""
    missing: List[str] = []
    mode = subtitle_output_mode(cfg)
    if mode in ("hard", "soft"):
        if not (isinstance(translated_srt, str) and os.path.exists(translated_srt)):
            missing.append("Vietsub")
    if getattr(cfg, "enable_tts", False):
        if not (
            isinstance(synced_tts_audio, str)
            and os.path.exists(synced_tts_audio)
            and os.path.getsize(synced_tts_audio) > 2048
        ):
            missing.append("lồng tiếng")
    if not missing:
        return
    why = " ".join(str(detail or "").split())
    raise RuntimeError(
        "Reup thiếu " + " và ".join(missing) + " — không xuất file dở."
        + (f" {why}" if why else "")
    )


def _existing_media(path: Optional[str], min_bytes: int = 2048) -> Optional[str]:
    if isinstance(path, str) and os.path.isfile(path) and os.path.getsize(path) > min_bytes:
        return path
    return None


def find_cached_tts_audio(*roots: str) -> Optional[str]:
    """Reuse a previously rendered TTS wav so a failed encode does not re-read hours of cues."""
    stems: List[str] = []
    dirs: List[str] = []
    allow_global = False
    for root in roots:
        if not root:
            continue
        if os.path.isdir(root):
            dirs.append(root)
            continue
        base = root[:-4] if root.lower().endswith(".mp4") else os.path.splitext(root)[0]
        stems.append(os.path.basename(base))
        dirs.append(os.path.dirname(os.path.abspath(base)) or ".")
        norm = os.path.abspath(root).replace("\\", "/")
        if "/data/input/" in norm or "/data/outputs/" in norm:
            allow_global = True
    if allow_global:
        try:
            from app.config import Settings
            dirs.append(Settings().TTS_OUTPUT_DIR)
        except Exception:
            dirs.append("data/outputs/tts")
    seen = set()
    for stem in stems:
        for folder in dirs:
            for name in (
                f"{stem}_synced_tts.wav",
                f"{stem}.vi.wav",
                f"{stem}.vi.mp3",
            ):
                path = os.path.join(folder, name)
                if path in seen:
                    continue
                seen.add(path)
                found = _existing_media(path)
                if found:
                    return found
    return None


def find_cached_vietsub_srt(*roots: str) -> Optional[str]:
    """Prefer the aligned Vietsub next to the source so retry skips STT/translate."""
    from app.services.pyvideotrans_service import subtitle_matches_target_language

    aligned: List[str] = []
    plain: List[str] = []
    for root in roots:
        if not root:
            continue
        base = root[:-4] if str(root).lower().endswith(".mp4") else os.path.splitext(str(root))[0]
        aligned.extend([base + "_vi.aligned.srt", base + ".vi.aligned.srt"])
        plain.extend([base + "_vi.srt", base + ".vi.srt"])
    for path in aligned:
        if os.path.isfile(path) and subtitle_matches_target_language(path, "vi"):
            return path
    # Plain Vietsub is only reused together with a TTS wav (failed-encode retry).
    if find_cached_tts_audio(*roots):
        for path in plain:
            if os.path.isfile(path) and subtitle_matches_target_language(path, "vi"):
                return path
    return None


def refuse_incomplete_output(
    cfg: ReupConfig,
    *,
    output_path: str,
    burned_sub: bool = False,
    dubbed_vi: bool = False,
    subtitle_sidecar: Optional[str] = None,
    softsub_embedded: bool = False,
    had_srt: bool = False,
    had_tts: bool = False,
) -> None:
    """Refuse to keep an encoded file that is missing requested Vietsub or TTS."""
    missing: List[str] = []
    mode = subtitle_output_mode(cfg)
    if mode == "hard" and had_srt and not burned_sub:
        missing.append("Vietsub in cứng")
    if mode == "soft" and had_srt and not (
        (isinstance(subtitle_sidecar, str) and os.path.exists(subtitle_sidecar))
        or softsub_embedded
    ):
        missing.append("Vietsub")
    if getattr(cfg, "enable_tts", False) and had_tts and not dubbed_vi:
        missing.append("lồng tiếng")
    if not missing:
        return
    abort_incomplete_reup(
        output_path,
        "Reup thiếu " + " và ".join(missing) + " — không xuất file dở.",
    )


def prepare_output_subtitle(srt_path: str, output_path: str, speed_factor: float = 1.0) -> Optional[str]:
    """Write a sidecar SRT whose timestamps match the transformed output timeline."""
    if not srt_path or not os.path.exists(srt_path):
        return None
    sidecar = os.path.splitext(output_path)[0] + ".vi.srt"
    try:
        from app.services.vietsub_rules import write_display_srt

        display = write_display_srt(srt_path, sidecar)
        if abs(float(speed_factor or 1.0) - 1.0) > 1e-3:
            return scale_srt_timestamps(display, speed_factor, sidecar)
        return display
    except Exception as e:
        logger.warning(f"Could not prepare output Vietsub sidecar: {e}")
        return None


def mux_toggleable_subtitle(video_path: str, srt_path: str, output_path: Optional[str] = None) -> bool:
    """Embed a Vietnamese mov_text track that compatible players can toggle."""
    ffmpeg_bin = find_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(video_path) or not os.path.exists(srt_path):
        return False

    target = output_path or video_path
    tmp_out = target + ".softsub.tmp.mp4"
    cmd = [
        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path,
        "-i", srt_path,
        "-map", "0:v:0", "-map", "0:a?", "-map", "1:0",
        "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
        "-metadata:s:s:0", "language=vie",
        "-metadata:s:s:0", "title=Vietsub",
        "-disposition:s:0", "0",
        "-movflags", "+faststart",
        tmp_out,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
            os.replace(tmp_out, target)
            return True
        logger.warning(f"Soft subtitle mux failed ({res.returncode}): {(res.stderr or '')[-500:]}")
    except Exception as e:
        logger.warning(f"Soft subtitle mux failed: {e}")
    finally:
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except OSError:
                pass
    return False


def _probe_video_size(path: str) -> Tuple[int, int]:
    """Return display (w, h) via ffprobe, swapping axes when the stream is rotated."""
    from app.services.audio_service import find_ffprobe_binary
    probe = find_ffprobe_binary()
    if probe:
        try:
            res = subprocess.run(
                [
                    probe, "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=width,height",
                    "-show_entries", "stream_tags=rotate",
                    "-show_entries", "stream_side_data=rotation",
                    "-of", "json", path,
                ],
                capture_output=True, text=True, check=False,
            )
            stream = ((json.loads(res.stdout or "{}") or {}).get("streams") or [{}])[0]
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            rotate = (stream.get("tags") or {}).get("rotate")
            if rotate is None:
                for side in stream.get("side_data_list") or []:
                    if side.get("rotation") is not None:
                        rotate = side.get("rotation")
                        break
            try:
                angle = abs(int(float(rotate))) % 360
            except (TypeError, ValueError):
                angle = 0
            if angle in (90, 270):
                width, height = height, width
            if width > 0 and height > 0:
                return width, height
        except Exception:
            pass
    return 1080, 1920


def _filtered_video_size(path: str, cfg: ReupConfig) -> Tuple[int, int]:
    """Estimate the stable output dimensions before overlays are appended."""
    from app.services.caption_cover import should_crop_bottom

    width, height = _probe_video_size(path)
    bottom = float(getattr(cfg, "subtitle_bottom_crop", 0.0) or 0.0)
    if should_crop_bottom(
        getattr(cfg, "caption_cover", "off"),
        bool(getattr(cfg, "force_bottom_crop", False)),
        bottom,
    ):
        height = int(height * (1.0 - bottom))
    if not getattr(cfg, "dynamic_motion", False):
        crop = float(getattr(cfg, "crop_percent", 0.0) or 0.0)
        if crop > 0:
            width = int(width * (1.0 - 2.0 * crop))
            height = int(height * (1.0 - 2.0 * crop))
    return max(2, width // 2 * 2), max(2, height // 2 * 2)


def _burn_hardsub_overlay(
    ffmpeg_bin: str,
    video_path: str,
    srt_path: str,
    output_path: str,
    cfg: Optional[ReupConfig] = None,
) -> bool:
    """libass-free hardsub: render cues to PNGs (Pillow) and composite via overlay."""
    import tempfile
    from app.services.caption_cover import clamp_subtitle_box_h
    from app.services.subtitle_overlay import render_srt_to_overlays, write_overlay_concat_list

    w, h = _probe_video_size(video_path)
    tmp_dir = tempfile.mkdtemp(prefix="visub_ovl_")
    try:
        box_h = clamp_subtitle_box_h(getattr(cfg, "subtitle_box_h", 0.08)) if cfg else 0.08
        overlays = render_srt_to_overlays(
            srt_path, w, h, tmp_dir,
            cover_kind=str(getattr(cfg, "caption_cover", "off") or "off") if cfg else "off",
            cover_band=float(getattr(cfg, "subtitle_bottom_crop", 0.0) or 0.0) if cfg else 0.0,
            subtitle_y=float(getattr(cfg, "subtitle_y", 0.0) or 0.0) if cfg else 0.0,
            cover_pad=float(getattr(cfg, "cover_pad", 0.0) or 0.0) if cfg else 0.0,
            subtitle_box_w=float(getattr(cfg, "subtitle_box_w", 0.88) or 0.88) if cfg else 0.88,
            subtitle_box_h=box_h,
        )
        if not overlays:
            logger.warning("Subtitle overlay produced no cues; leaving video unchanged")
            return False
        from PIL import Image

        concat_path = os.path.join(tmp_dir, "frames.txt")
        transparent_path = os.path.join(tmp_dir, "transparent.png")
        Image.new("RGBA", (w, h), (0, 0, 0, 0)).save(transparent_path)
        if not write_overlay_concat_list(overlays, concat_path, transparent_path):
            return False
        fc = (
            "[0:v][1:v]overlay=0:0:eof_action=pass:repeatlast=0[v_out]"
        )
        tmp_out = output_path + ".ovlsub.tmp.mp4"
        encode_args = browser_safe_encode_args(ffmpeg_bin)
        cmd = [
            ffmpeg_bin, "-y", "-threads", "0", "-i", video_path,
            "-f", "concat", "-safe", "0", "-i", concat_path,
            "-filter_complex", fc, "-map", "[v_out]", "-map", "0:a?",
            *encode_args, "-c:a", "copy", tmp_out,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
            os.replace(tmp_out, output_path)
            logger.info("Burned Vietnamese hardsub via PIL overlay (%d cues)", len(overlays))
            return True
        logger.warning("Overlay hardsub failed (%s): %s", res.returncode, (res.stderr or "")[-500:])
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def burn_vietnamese_hardsub(
    video_path: str,
    srt_path: str,
    output_path: str,
    speed_factor: float = 1.0,
    cfg: Optional[ReupConfig] = None,
) -> bool:
    """Burns a Vietnamese SRT onto video as hardsub. Returns True on success.
    Uses native libass `subtitles=` when available, else a Pillow PNG overlay."""
    if not os.path.exists(video_path) or not os.path.exists(srt_path):
        return False
    ffmpeg_bin = find_ffmpeg_binary()
    if not ffmpeg_bin:
        return False

    work_srt = srt_path
    scaled = None
    try:
        from app.services.vietsub_rules import write_display_srt

        display = srt_path + ".display.srt"
        work_srt = write_display_srt(srt_path, display)
        scaled = display
    except Exception as e:
        logger.warning(f"Display SRT split skipped ({e}); burning original cues")
        work_srt = srt_path
    if abs(speed_factor - 1.0) > 1e-3:
        try:
            scaled = video_path + ".vi.scaled.srt"
            work_srt = scale_srt_timestamps(work_srt, speed_factor, scaled)
        except Exception as e:
            logger.warning(f"SRT time-scale failed ({e}); burning original timings")
            work_srt = srt_path

    if not ffmpeg_supports_libass(ffmpeg_bin):
        ok = _burn_hardsub_overlay(ffmpeg_bin, video_path, work_srt, output_path, cfg=cfg)
        if scaled and os.path.exists(scaled) and scaled != srt_path:
            try:
                os.remove(scaled)
            except OSError:
                pass
        return ok

    from app.services.caption_cover import (
        clamp_subtitle_box_h,
        cover_band_height,
        normalize_caption_cover,
        subtitle_force_style,
        subtitle_plate_drawbox,
    )
    frame_w, frame_h = _probe_video_size(video_path)
    cover = normalize_caption_cover(getattr(cfg, "caption_cover", "off") if cfg else "off")
    bottom = float(getattr(cfg, "subtitle_bottom_crop", 0.0) or 0.0) if cfg else 0.0
    cue_y = float(getattr(cfg, "subtitle_y", 0.0) or 0.0) if cfg else 0.0
    box_h = clamp_subtitle_box_h(getattr(cfg, "subtitle_box_h", 0.08)) if cfg else 0.08
    band = cover_band_height(bottom, cover)
    plate = subtitle_plate_drawbox(
        cover,
        float(getattr(cfg, "subtitle_box_w", 0.88) or 0.88) if cfg else 0.88,
        box_h,
        subtitle_y=cue_y,
        band_h=band if cover == "image" else bottom,
        video_w=frame_w,
        video_h=frame_h,
        srt_path=work_srt,
    )
    style = subtitle_force_style(
        cover, band if cover == "image" else bottom,
        video_w=frame_w, video_h=frame_h, subtitle_y=cue_y,
        cover_pad=float(getattr(cfg, "cover_pad", 0.0) or 0.0) if cfg else 0.0,
        subtitle_box_h=box_h,
    )
    vf = ",".join([p for p in (plate, _subtitles_filter(work_srt, style, frame_w, frame_h)) if p])

    tmp_out = output_path + ".hardsub.tmp.mp4"
    encode_args = browser_safe_encode_args(ffmpeg_bin, force_software=True)
    cmd = [
        ffmpeg_bin, "-y", "-threads", "0", "-i", video_path,
        "-vf", vf,
        *encode_args,
        "-c:a", "copy",
        "-muxdelay", "0", "-muxpreload", "0",
        tmp_out,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
            os.replace(tmp_out, output_path)
            logger.info(f"Burned Vietnamese hardsub from {os.path.basename(work_srt)}")
            return True
        logger.warning(f"Hardsub burn failed ({res.returncode}): {(res.stderr or '')[-500:]}")
    except Exception as e:
        logger.warning(f"Hardsub burn exception: {e}")
    finally:
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except OSError:
                pass
        if scaled and os.path.exists(scaled) and scaled != srt_path:
            try:
                os.remove(scaled)
            except OSError:
                pass
    return False


def build_tts_bgm_mix_filter(
    original_vocal_volume: Optional[float] = None,
    original_vocal_speed: float = 1.0,
) -> str:
    """Overlay Vietnamese TTS onto an already-processed original bed.

    [0:a] must already contain cleaned original audio (BGM / leftover Chinese /
    mute). original_vocal_volume is intentionally unused here so source-gain
    controls cannot touch the Vietnamese voice.
    """
    _ = original_vocal_volume, original_vocal_speed
    # asplit is required: an FFmpeg pad can only be consumed once. Reusing
    # [voice] for both sidechain and amix dropped the TTS track entirely.
    return (
        "[1:a]aresample=async=1:first_pts=0,aformat=channel_layouts=stereo,volume=2.20,"
        "highpass=f=80,lowpass=f=12000,"
        "acompressor=threshold=-18dB:ratio=2.0:attack=5:release=80:makeup=2.0,"
        "asplit=2[voice_duck][voice_mix];"
        "[0:a]aresample=async=1:first_pts=0,aformat=channel_layouts=stereo,volume=0.95[bed];"
        "[bed][voice_duck]sidechaincompress=threshold=0.08:ratio=4:attack=12:release=160:makeup=1[ducked];"
        "[ducked][voice_mix]amix=inputs=2:duration=first:dropout_transition=0:weights=1.00 1.15:normalize=0[aout]"
    )


def leftover_original_vocal_volume(cfg: ReupConfig) -> float:
    """How much source speech stays under Vietnamese TTS (song tiếng)."""
    try:
        raw = float(getattr(cfg, "original_vocal_volume", 0.10) or 0.0)
    except (TypeError, ValueError):
        raw = 0.10
    return max(0.0, min(0.30, raw))


def should_use_demucs_for_dubbing(cfg: ReupConfig, _speech_intervals: Optional[List[Tuple[float, float]]]) -> bool:
    """Reserve slow neural separation for users who explicitly select Demucs."""
    return bool(
        cfg.enable_vocal_mute
        and cfg.preserve_bgm
        and cfg.vocal_mute_strategy in ("demucs", "demucs_duck")
    )


def mix_tts_with_background(
    video_path: str,
    tts_audio_path: str,
    output_path: str,
    original_vocal_path: Optional[str] = None,
    original_vocal_volume: float = 0.10,
    original_vocal_speed: float = 1.0,
) -> bool:
    """Lay the Vietnamese TTS track on top of already-cleaned original audio.

    Source controls must already be baked into `video_path` audio. The leftover
    Chinese slider never enters this mix.
    """
    _ = original_vocal_path, original_vocal_volume, original_vocal_speed
    ffmpeg_bin = find_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(video_path) or not os.path.exists(tts_audio_path):
        return False

    from app.services.tts_service import get_audio_duration
    vid_dur = get_audio_duration(video_path)
    audio_to_use = tts_audio_path
    temp_padded = None
    if vid_dur > 0:
        temp_padded = video_path + ".padded_tts.wav"
        pad_cmd = [
            ffmpeg_bin, "-y", "-threads", "0", "-i", tts_audio_path,
            "-af", "apad", "-t", f"{vid_dur:.3f}",
            temp_padded,
        ]
        pad_res = subprocess.run(pad_cmd, capture_output=True, text=True, check=False)
        if pad_res.returncode == 0 and os.path.exists(temp_padded) and os.path.getsize(temp_padded) > 0:
            audio_to_use = temp_padded

    tmp_out = output_path + ".tmp_tts_mix.mp4"
    has_audio = detect_audio_stream(video_path)
    if has_audio:
        fc = build_tts_bgm_mix_filter()
        cmd = [
            ffmpeg_bin, "-y", "-threads", "0",
            "-i", video_path,
            "-i", audio_to_use,
            "-filter_complex", fc,
            "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ac", "2",
            "-muxdelay", "0", "-muxpreload", "0",
            tmp_out,
        ]
    else:
        cmd = [
            ffmpeg_bin, "-y", "-threads", "0",
            "-i", video_path,
            "-i", audio_to_use,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ac", "2",
            "-muxdelay", "0", "-muxpreload", "0",
            tmp_out,
        ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0 and os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
            os.replace(tmp_out, output_path)
            return True
        logger.warning(f"TTS+BGM mix failed ({res.returncode}): {(res.stderr or '')[-500:]}")
        if has_audio:
            # Fallback: keep BGM loud instead of crushing it
            fc2 = (
                "[0:a]aresample=async=1:first_pts=0,aformat=channel_layouts=stereo,volume=0.95[bed];"
                "[1:a]aresample=async=1:first_pts=0,aformat=channel_layouts=stereo,volume=2.20,asplit=2[voice_duck][voice_mix];"
                "[bed][voice_duck]sidechaincompress=threshold=0.08:ratio=4:attack=12:release=160:makeup=1[ducked];"
                "[ducked][voice_mix]amix=inputs=2:duration=first:dropout_transition=0:weights=1.00 1.15:normalize=0[aout]"
            )
            cmd[cmd.index("-filter_complex") + 1] = fc2
            res2 = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if res2.returncode == 0 and os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
                os.replace(tmp_out, output_path)
                return True
        return False
    finally:
        if temp_padded and os.path.exists(temp_padded):
            try:
                os.remove(temp_padded)
            except OSError:
                pass
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except OSError:
                pass


def process_reup_video(
    input_path: str,
    output_path: str,
    hflip: bool = True,
    speed_ratio: float = 1.03,
    pitch_shift: bool = True,
    crop_percent: float = 0.02,
    brightness: float = 0.01,
    contrast: float = 1.02,
    saturation: float = 1.03,
    modify_md5: bool = True,
    vietnamese_dubbing: bool = False,
    text_for_dubbing: Optional[str] = None,
    enable_vocal_mute: bool = False,
    vocal_mute_strategy: str = "auto",
    preserve_bgm: bool = True,
    audio_ducking: bool = False,
    cfg: Optional[ReupConfig] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Executes single-pass FFmpeg transformation, optional Vietnamese TTS dubbing, and fast MD5 modification.
    Enforces strict input verification and parameter boundary checks.
    """
    # 1. Input Validation
    def _report(progress: float, message: str) -> None:
        cb = kwargs.get("stage_progress_callback")
        if not callable(cb):
            return
        cb(float(progress), message)

    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if os.path.getsize(input_path) == 0:
        raise ValueError("Input file is empty")

    if speed_ratio <= 0.0 or speed_ratio > 10.0:
        raise ValueError("speed_ratio must be between 0.1 and 10.0")
    if crop_percent < 0.0 or crop_percent >= 0.5:
        raise ValueError("crop_percent must be between 0 and 0.5")
    if brightness < -1.0 or brightness > 1.0:
        raise ValueError("brightness must be between -1.0 and 1.0")
    if contrast <= 0:
        raise ValueError("contrast must be greater than 0")
    if saturation < 0:
        raise ValueError("saturation must be non-negative")

    # 2. Build Configuration
    if cfg is None:
        cfg = ReupConfig(
            hflip=kwargs.get("hflip", hflip),
            speed_factor=speed_ratio,
            pitch_shift=kwargs.get("pitch_shift", pitch_shift),
            crop_percent=crop_percent,
            brightness=brightness,
            contrast=contrast,
            saturation=saturation,
            modify_md5=modify_md5,
            color_adjust=(brightness != 0.0 or contrast != 1.0 or saturation != 1.0),
            film_grain=kwargs.get("film_grain", 0.0),
            dynamic_motion=kwargs.get("dynamic_motion", False),
            meta_compliance_mode=kwargs.get("meta_compliance_mode", False),
            youtube_compliance_mode=kwargs.get("youtube_compliance_mode", False),
            enable_vocal_mute=kwargs.get("enable_vocal_mute", enable_vocal_mute),
            vocal_mute_strategy=kwargs.get("vocal_mute_strategy", vocal_mute_strategy),
            preserve_bgm=kwargs.get("preserve_bgm", preserve_bgm),
            audio_ducking=kwargs.get("audio_ducking", audio_ducking)
        )

    has_audio = detect_audio_stream(input_path)
    demucs_bgm_path: Optional[str] = None
    lib_bgm_path: Optional[str] = None
    raw_bgm = getattr(cfg, "bgm_path", None)
    if raw_bgm:
        try:
            from app.services.bgm_library import resolve_bgm
            lib_bgm_path = resolve_bgm(str(raw_bgm)) or (raw_bgm if os.path.isfile(raw_bgm) else None)
        except Exception:
            lib_bgm_path = raw_bgm if os.path.isfile(str(raw_bgm)) else None
    srt_override = kwargs.get("srt_override")
    subtitle_mode = subtitle_output_mode(cfg)
    # Bake subtitles into the master graph via libass or one timed APNG track.
    wants_hardsub = bool(
        srt_override
        and os.path.exists(srt_override)
        and subtitle_mode == "hard"
    )
    libass_hardsub = bool(wants_hardsub and ffmpeg_supports_libass())
    burn_in_graph = libass_hardsub
    subtitle_track_dir: Optional[str] = None
    duck_command_path: Optional[str] = None
    last_ffmpeg_err = ""

    speech_intervals = kwargs.get("speech_intervals")
    # Prefer a separated music stem for the "remove speech, keep BGM" mode.
    use_demucs = should_use_demucs_for_dubbing(cfg, speech_intervals)
    if (not lib_bgm_path) and has_audio and cfg.enable_vocal_mute and cfg.vocal_mute_strategy in ("auto", "demucs", "demucs_duck") and use_demucs:
        from app.services.audio_service import (
            check_demucs_available,
            extract_audio_stream,
            extract_vocals_demucs,
            mix_separated_stems,
        )
        if not check_demucs_available():
            if cfg.vocal_mute_strategy == "demucs":
                raise RuntimeError("Demucs strategy requested but demucs is not installed")
        else:
            extracted_a: Optional[str] = None
            bgm_out: Optional[str] = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_a:
                    extracted_a = tmp_a.name
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_bgm:
                    bgm_out = tmp_bgm.name
                if extract_audio_stream(input_path, extracted_a):
                    from app.services.activity import heartbeat
                    _report(0.90, "🎧 Đang tách giọng/BGM bằng Demucs — bước này thường mất 1–3 phút...")
                    with tempfile.TemporaryDirectory(prefix="demucs_reup_") as demucs_dir:
                        with heartbeat(
                            lambda msg: _report(0.90, msg),
                            "Demucs đang tách vocal/BGM",
                            interval=8.0,
                        ):
                            vocal_path, separated_bgm_path = extract_vocals_demucs(extracted_a, demucs_dir)
                        _report(0.91, "✅ Đã tách xong vocal và nhạc nền")
                        if cfg.vocal_mute_strategy == "demucs_duck":
                            if not mix_separated_stems(
                                separated_bgm_path,
                                vocal_path,
                                bgm_out,
                                vocal_volume=cfg.original_vocal_volume,
                            ):
                                raise RuntimeError("Failed to mix separated source stems")
                        else:
                            shutil.copyfile(separated_bgm_path, bgm_out)
                        demucs_bgm_path = bgm_out
                elif cfg.vocal_mute_strategy == "demucs":
                    raise RuntimeError("Failed to extract audio stream for Demucs vocal separation")
            except Exception as e:
                logger.warning(f"Demucs processing step in process_reup_video failed: {e}")
                if cfg.vocal_mute_strategy == "demucs":
                    raise
            finally:
                if extracted_a and os.path.exists(extracted_a):
                    try:
                        os.remove(extracted_a)
                    except OSError:
                        pass
                if bgm_out and bgm_out != demucs_bgm_path and os.path.exists(bgm_out):
                    try:
                        os.remove(bgm_out)
                    except OSError:
                        pass

    try:
        # Original-audio cleanup (Demucs/BGM/leftover Chinese) is already baked
        # into this stem. Later TTS overlay must not re-apply those gains.
        graph_cfg = cfg.model_copy(update={"enable_vocal_mute": False}) if demucs_bgm_path else cfg
        # Keep SFX/animals: duck only human-speech windows on the original mix.
        # (Previously mute=on zeroed intervals and fell through to a center-kill
        # filter that also erased impacts, whooshes and animal calls.)
        if demucs_bgm_path or not cfg.enable_vocal_mute:
            audio_speech_intervals = None
        else:
            audio_speech_intervals = speech_intervals
        duck_command_path = None
        if audio_speech_intervals:
            duck_command_path = output_path + ".duck.txt"
        main_size = _filtered_video_size(input_path, cfg)
        from app.services.audio_service import probe_audio_sample_rate, probe_stream_duration_sec

        source_audio_rate = probe_audio_sample_rate(input_path) or 0
        if demucs_bgm_path:
            source_audio_rate = probe_audio_sample_rate(demucs_bgm_path) or source_audio_rate
        filter_complex, includes_audio, vf_str, af_str = build_reup_filtergraph(
            graph_cfg,
            has_audio=has_audio,
            audio_sample_rate=source_audio_rate,
            burn_srt_path=srt_override if libass_hardsub else None,
            duck_command_path=duck_command_path,
            speech_intervals=audio_speech_intervals,
            frame_size=main_size,
        )
        from app.services.overlay_service import (
            append_overlay_filter,
            overlay_input_args,
            overlays_for_job,
        )
        from app.services.caption_cover import (
            clamp_subtitle_box_h,
            cover_band_height,
            normalize_caption_cover,
            resolve_caption_cover_image,
        )
        cover_kind = normalize_caption_cover(getattr(cfg, "caption_cover", "off"))
        cover_img = resolve_caption_cover_image(
            getattr(cfg, "caption_cover_image", None),
            getattr(cfg, "caption_cover_url", None),
        )
        band = cover_band_height(
            float(getattr(cfg, "subtitle_bottom_crop", 0.0) or 0.0),
            cover_kind,
        )
        overlay_items, plate_banners = overlays_for_job(cfg, main_size[0], main_size[1])
        if cover_kind == "image" and cover_img:
            logger.info(
                "Caption cover banner path=%s band_h=%.2f plate=%d",
                cover_img, band, len(plate_banners),
            )
            if plate_banners:
                _report(0.93, "🖼️ Logo đặt dưới khung 9:16 — không zoom, không đè video")
            else:
                _report(0.93, f"🖼️ Phủ dải đáy bằng ảnh ({int(round(band * 100))}%) — giữ khung 9:16")
        overlay_paths: List[str] = []
        extra_audio = bool(lib_bgm_path) or bool(demucs_bgm_path and os.path.exists(demucs_bgm_path))
        first_ov = 2 if extra_audio else 1
        if overlay_items:
            filter_complex, overlay_paths = append_overlay_filter(
                filter_complex, overlay_items, first_overlay_index=first_ov, main_size=main_size
            )
        subtitle_input_args: List[str] = []
        if wants_hardsub and not libass_hardsub and not getattr(cfg, "dynamic_motion", False):
            from app.services.subtitle_overlay import (
                inject_timed_overlay_before_speed,
                render_srt_to_concat_track,
            )

            subtitle_track_dir = tempfile.mkdtemp(prefix="visub_track_")
            timed_srt = srt_override
            try:
                from app.services.vietsub_rules import write_display_srt

                timed_srt = write_display_srt(
                    srt_override,
                    os.path.join(subtitle_track_dir, "display.srt"),
                )
            except Exception as e:
                logger.warning(f"Display SRT split skipped ({e})")
            subtitle_track = render_srt_to_concat_track(
                timed_srt,
                main_size[0],
                main_size[1],
                os.path.join(subtitle_track_dir, "frames.txt"),
                cover_band=band if cover_kind != "off" else 0.0,
                cover_kind=cover_kind,
                subtitle_y=float(getattr(cfg, "subtitle_y", 0.0) or 0.0),
                cover_pad=float(getattr(cfg, "cover_pad", 0.0) or 0.0),
                subtitle_box_w=float(getattr(cfg, "subtitle_box_w", 0.88) or 0.88),
                subtitle_box_h=clamp_subtitle_box_h(getattr(cfg, "subtitle_box_h", 0.08)),
            )
            if subtitle_track:
                subtitle_index = first_ov + len(overlay_paths)
                filter_complex = inject_timed_overlay_before_speed(filter_complex, subtitle_index)
                subtitle_input_args = ["-f", "concat", "-safe", "0", "-i", subtitle_track]
                burn_in_graph = True
        input_md5 = calculate_file_md5(input_path)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        # 3. FFmpeg Execution or Safe Fallback Copy
        ffmpeg_bin = find_ffmpeg_binary()
        ffmpeg_success = False

        if ffmpeg_bin:
            try:
                encode_args = browser_safe_encode_args(
                    ffmpeg_bin,
                    force_software=abs(float(cfg.speed_factor or 1.0) - 1.0) > 0.01,
                )
                cmd = [ffmpeg_bin, "-y", "-threads", "0", "-i", input_path]
                if lib_bgm_path and os.path.exists(lib_bgm_path):
                    from app.services.tts_service import get_audio_duration
                    source_audio_rate = probe_audio_sample_rate(lib_bgm_path) or source_audio_rate
                    in_dur = get_audio_duration(input_path) or 8.0
                    speed = float(getattr(cfg, "speed_factor", 1.0) or 1.0)
                    out_dur = max(0.4, in_dur / max(0.5, speed))
                    vol = float(getattr(cfg, "bgm_volume", 0.85) or 0.85)
                    vf_part = drop_audio_chains(filter_complex)
                    bgm_af = f"volume={vol:.3f},aformat=channel_layouts=stereo"
                    if abs(speed - 1.0) > 1e-3:
                        bgm_af = (
                            f"{bgm_af},"
                            + ",".join(_build_atempo_nodes(speed))
                            + ",asetpts=PTS-STARTPTS"
                        )
                    fc = f"{vf_part};[1:a]{bgm_af}[a_out]"
                    includes_audio = True
                    cmd.extend(["-stream_loop", "-1", "-t", f"{out_dur:.3f}", "-i", lib_bgm_path])
                elif demucs_bgm_path and os.path.exists(demucs_bgm_path):
                    fc = filter_complex.replace("[0:a]", "[1:a]")
                    cmd.extend(["-i", demucs_bgm_path])
                else:
                    fc = filter_complex
                cmd.extend(overlay_input_args(overlay_paths))
                cmd.extend(subtitle_input_args)
                cmd.extend(["-filter_complex", fc])

                cmd.extend(["-map", "[v_out]"])
                cmd.extend(encode_args)
                if abs(float(cfg.speed_factor or 1.0) - 1.0) > 1e-3:
                    cmd.extend(["-fps_mode", "cfr", "-r", "30"])
                if includes_audio:
                    cmd.extend(["-map", "[a_out]", "-c:a", "aac", "-b:a", "128k", "-ac", "2"])
                    if source_audio_rate >= 8000:
                        cmd.extend(["-ar", str(source_audio_rate)])
                    if abs(float(cfg.speed_factor or 1.0) - 1.0) > 1e-3:
                        cmd.append("-shortest")
                else:
                    cmd.extend(["-an"])
                video_dur = probe_stream_duration_sec(input_path, "v:0")
                if video_dur <= 0:
                    video_dur = probe_stream_duration_sec(input_path, "a:0")
                if video_dur > 0.2:
                    speed = max(0.5, float(cfg.speed_factor or 1.0))
                    cmd.extend(["-t", f"{video_dur / speed:.3f}"])
                cmd.extend(["-muxdelay", "0", "-muxpreload", "0"])
                cmd.append(output_path)

                from app.services.activity import heartbeat
                encoder_label = encode_args[1] if len(encode_args) > 1 else "h264"
                _report(0.94, f"🎞️ Đang encode FFmpeg ({encoder_label}) — crop/speed/grain/mix audio...")
                with heartbeat(
                    lambda msg: _report(0.94, msg),
                    f"FFmpeg đang encode ({encoder_label})",
                    interval=8.0,
                ):
                    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                last_ffmpeg_err = (res.stderr or res.stdout or "")[-1500:]
                if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    from app.services.audio_service import probe_media_duration_sec
                    src_dur = video_dur if video_dur > 0.2 else probe_media_duration_sec(input_path, "v:0")
                    job_speed = max(0.5, float(cfg.speed_factor or 1.0))
                    if not repair_output_speed(output_path, src_dur, job_speed):
                        logger.error("A/V lock failed for %s (speed=%.3f)", output_path, job_speed)
                        raise RuntimeError(
                            "Encode xong nhưng hình/tiếng lệch — đã chặn file lệch, hãy chạy lại job."
                        )
                    ffmpeg_success = True
                    _report(0.96, "✅ FFmpeg render xong — hình và tiếng cùng nhịp")
                else:
                    err = (res.stderr or res.stdout or "")
                    logger.error("FFmpeg reup failed (%s): %s", res.returncode, err[-1500:])
                    if encoder_name := (encode_args[1] if len(encode_args) > 1 else ""):
                        if encoder_name != "libx264" and (
                            "Cannot create compression session" in err
                            or "Error while opening encoder" in err
                        ):
                            logger.warning("Hardware H.264 encoder unavailable; retrying with libx264")
                            software_args = [
                                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                                "-profile:v", "main", "-level", "4.0",
                            ]
                            video_arg_index = cmd.index("-c:v")
                            cmd[video_arg_index:video_arg_index + len(encode_args)] = software_args
                            _report(0.94, "🎞️ Encoder phần cứng lỗi — đang encode lại bằng libx264...")
                            res_sw = subprocess.run(cmd, capture_output=True, text=True, check=False)
                            if res_sw.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                                src_dur = video_dur if video_dur > 0.2 else 0.0
                                job_speed = max(0.5, float(cfg.speed_factor or 1.0))
                                if not repair_output_speed(output_path, src_dur, job_speed):
                                    raise RuntimeError(
                                        "Encode xong nhưng hình/tiếng lệch — đã chặn file lệch, hãy chạy lại job."
                                    )
                                ffmpeg_success = True
                                _report(0.96, "✅ FFmpeg render xong (libx264) — hình và tiếng cùng nhịp")
                    if not ffmpeg_success and "delogo" in fc and "Logo area is outside" in err:
                        logger.warning("Retrying encode without mid-text delogo")
                        cfg.text_cover_vf = ""
                        filter_complex, includes_audio, vf_str, af_str = build_reup_filtergraph(
                            graph_cfg,
                            has_audio=has_audio,
                            audio_sample_rate=source_audio_rate,
                            burn_srt_path=srt_override if burn_in_graph else None,
                            speech_intervals=audio_speech_intervals,
                            frame_size=main_size,
                            duck_command_path=duck_command_path,
                        )
                        cmd[cmd.index("-filter_complex") + 1] = filter_complex
                        res2 = subprocess.run(cmd, capture_output=True, text=True, check=False)
                        if res2.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                            src_dur = video_dur if video_dur > 0.2 else 0.0
                            job_speed = max(0.5, float(cfg.speed_factor or 1.0))
                            if not repair_output_speed(output_path, src_dur, job_speed):
                                raise RuntimeError(
                                    "Encode xong nhưng hình/tiếng lệch — đã chặn file lệch, hãy chạy lại job."
                                )
                            ffmpeg_success = True
            except RuntimeError:
                raise
            except Exception as e:
                logger.warning("FFmpeg reup encode failed: %s", e)
    finally:
        if subtitle_track_dir:
            shutil.rmtree(subtitle_track_dir, ignore_errors=True)
        if demucs_bgm_path and os.path.exists(demucs_bgm_path):
            try:
                os.remove(demucs_bgm_path)
            except OSError:
                pass
        if duck_command_path and os.path.exists(duck_command_path):
            try:
                os.remove(duck_command_path)
            except OSError:
                pass

    if not ffmpeg_success:
        err_msg = f"FFmpeg execution failed for input '{input_path}'."
        if not ffmpeg_bin:
            err_msg = "FFmpeg binary executable not found on system PATH."
        why = " ".join(str(last_ffmpeg_err or "").split())
        if why:
            err_msg = err_msg + " " + why[-500:]
        logger.error(err_msg)
        abort_incomplete_reup(output_path, err_msg)

    # 4. Optional Vietnamese TTS Dubbing Pass (mix with BGM, never replace)
    dubbed_vi = False
    tts_audio_override = kwargs.get("tts_audio_override")
    had_tts = bool(
        tts_audio_override
        and os.path.exists(tts_audio_override)
        and os.path.getsize(tts_audio_override) > 0
    )
    if had_tts:
        _report(0.97, "🎙️ Đang phủ giọng Việt riêng lên audio gốc đã chỉnh...")
        dubbed_vi = mix_tts_with_background(
            output_path,
            tts_audio_override,
            output_path,
        )
        if not dubbed_vi:
            abort_incomplete_reup(
                output_path,
                "Reup thiếu lồng tiếng — không xuất file dở. Mix TTS với BGM thất bại.",
            )
        from app.services.audio_service import ensure_av_lock
        if not ensure_av_lock(output_path):
            abort_incomplete_reup(
                output_path,
                "Encode xong nhưng hình/tiếng lệch — đã chặn file lệch, hãy chạy lại job.",
            )
        _report(0.98, "✅ Đã phủ giọng Việt (lớp riêng, to hơn audio gốc)")
    elif vietnamese_dubbing and text_for_dubbing:
        dubbed_vi = apply_vietnamese_dubbing(output_path, text_for_dubbing, output_path=output_path)
        if not dubbed_vi:
            abort_incomplete_reup(
                output_path,
                "Reup thiếu lồng tiếng — không xuất file dở. Dubbing thất bại.",
            )

    # 4b. Add the selected subtitle output after TTS mixing.
    burned_sub = bool(burn_in_graph)
    if (not burned_sub) and srt_override and os.path.exists(srt_override) and subtitle_mode == "hard":
        burned_sub = burn_vietnamese_hardsub(
            output_path, srt_override, output_path, speed_factor=cfg.speed_factor, cfg=cfg
        )
    subtitle_sidecar = None
    softsub_embedded = False
    if srt_override and os.path.exists(srt_override) and subtitle_mode != "off":
        subtitle_sidecar = prepare_output_subtitle(srt_override, output_path, cfg.speed_factor)
        if subtitle_mode == "soft" and subtitle_sidecar:
            softsub_embedded = mux_toggleable_subtitle(output_path, subtitle_sidecar, output_path)

    refuse_incomplete_output(
        cfg,
        output_path=output_path,
        burned_sub=burned_sub,
        dubbed_vi=dubbed_vi,
        subtitle_sidecar=subtitle_sidecar,
        softsub_embedded=softsub_embedded,
        had_srt=bool(srt_override and os.path.exists(srt_override)),
        had_tts=had_tts or bool(vietnamese_dubbing and text_for_dubbing),
    )

    from app.services.audio_service import ensure_av_lock
    if not ensure_av_lock(output_path):
        abort_incomplete_reup(
            output_path,
            "Encode xong nhưng hình/tiếng lệch — đã chặn file lệch, hãy chạy lại job.",
        )

    # 5. Browser-safe remux then MD5 trailer (trailer MUST come last)
    remux_faststart(output_path)
    if not ensure_av_lock(output_path):
        abort_incomplete_reup(
            output_path,
            "Encode xong nhưng hình/tiếng lệch — đã chặn file lệch, hãy chạy lại job.",
        )
    if cfg.modify_md5:
        modify_video_md5_fast(output_path, compute_hash=False)

    output_md5 = calculate_file_md5(output_path)

    return {
        "status": "completed",
        "input_path": input_path,
        "output_path": output_path,
        "input_md5": input_md5,
        "output_md5": output_md5,
        "md5_modified": cfg.modify_md5 and (input_md5 != output_md5),
        "vietnamese_dubbed": dubbed_vi,
        "hardsub_burned": burned_sub,
        "softsub_embedded": softsub_embedded,
        "subtitle_mode": subtitle_mode,
        "subtitle_sidecar": subtitle_sidecar,
        "video_filters": vf_str,
        "audio_filters": af_str,
        "filter_complex": filter_complex,
        "parameters": {
            "hflip": cfg.hflip,
            "speed_ratio": cfg.speed_factor,
            "pitch_shift": cfg.pitch_shift,
            "crop_percent": cfg.crop_percent,
            "brightness": cfg.brightness,
            "contrast": cfg.contrast,
            "saturation": cfg.saturation,
            "modify_md5": cfg.modify_md5,
            "vietnamese_dubbing": vietnamese_dubbing,
            "enable_vocal_mute": cfg.enable_vocal_mute,
            "vocal_mute_strategy": cfg.vocal_mute_strategy,
            "preserve_bgm": cfg.preserve_bgm,
            "audio_ducking": cfg.audio_ducking,
            "enable_tts": cfg.enable_tts,
            "tts_voice": cfg.tts_voice,
            "target_lang": cfg.target_lang,
        }
    }


def source_clip_title(video_path: str, cfg: Optional[ReupConfig] = None) -> str:
    """Title from job config or sidecar JSON (Douyin desc), stripped of hashtags."""
    import json
    import re

    raw = ""
    if cfg is not None:
        raw = (getattr(cfg, "post_title", None) or "") or ""
    if not raw:
        jpath = os.path.splitext(video_path)[0] + ".json"
        if os.path.isfile(jpath):
            try:
                with open(jpath, "r", encoding="utf-8") as f:
                    meta = json.load(f) or {}
                raw = meta.get("title") or meta.get("desc") or ""
            except Exception:
                raw = ""
    text = re.sub(r"#\S+", " ", raw or "")
    text = re.sub(r"https?://\S+", " ", text)
    cleaned = []
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or ch.isalnum() or ch in " ，。！？、,.!? ":
            cleaned.append(ch)
        elif ch.isspace():
            cleaned.append(" ")
    text = re.sub(r"\s+", " ", "".join(cleaned)).strip(" ，。！？、,.!?-～")
    return text[:80]


def write_single_cue_srt(path: str, text: str, duration: float) -> str:
    dur = max(1.2, float(duration or 4.0))
    h = int(dur // 3600)
    m = int((dur % 3600) // 60)
    s = int(dur % 60)
    ms = int((dur % 1) * 1000)
    body = f"1\n00:00:00,000 --> {h:02d}:{m:02d}:{s:02d},{ms:03d}\n{text.strip()}\n\n"
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def srt_looks_like_bgm_lyrics(srt_path: str, title: str) -> bool:
    """True when STT is a song dump that barely overlaps the on-screen title."""
    import re

    if not srt_path or not os.path.isfile(srt_path):
        return False
    try:
        blob = open(srt_path, encoding="utf-8").read()
    except Exception:
        return False
    lines = [
        ln.strip()
        for ln in blob.splitlines()
        if ln.strip() and "-->" not in ln and not ln.strip().isdigit()
    ]
    text = "".join(lines)
    cjk_title = "".join(re.findall(r"[\u4e00-\u9fff]", title or ""))
    cjk_stt = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    if len(cjk_title) >= 4 and cjk_stt:
        overlap = sum(1 for ch in set(cjk_title) if ch in cjk_stt)
        if overlap <= max(1, len(set(cjk_title)) // 3) and len(cjk_stt) > len(cjk_title) * 1.5:
            return True
    latin = len(re.findall(r"[A-Za-z]", text))
    if len(cjk_stt) < 4 and latin > 24:
        return True
    return False


def trim_video_input(input_path: str, trim_start: float = 0.0, trim_end: float = 0.0) -> Tuple[str, bool]:
    """
    If trim_start > 0 or trim_end > 0, cuts video accurately to produce a clean working video.
    Returns (effective_video_path, is_temporary).
    """
    if trim_start <= 0.0 and trim_end <= 0.0:
        return input_path, False

    from app.services.tts_service import get_audio_duration
    total_dur = get_audio_duration(input_path) or 0.0
    target_dur = None
    if total_dur > 0.0:
        target_dur = max(0.5, total_dur - trim_start - trim_end)

    ffmpeg_bin = find_ffmpeg_binary()
    if not ffmpeg_bin:
        return input_path, False

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_f:
        trimmed_path = tmp_f.name

    cmd = [ffmpeg_bin, "-y", "-threads", "0"]
    if trim_start > 0:
        cmd.extend(["-ss", f"{trim_start:.3f}"])
    cmd.extend(["-i", input_path])
    if target_dur is not None:
        cmd.extend(["-t", f"{target_dur:.3f}"])
    cmd.extend(["-c", "copy", trimmed_path])

    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode == 0 and os.path.exists(trimmed_path) and os.path.getsize(trimmed_path) > 1024:
        logger.info(f"Trimmed video via stream copy: {trim_start}s -> {target_dur}s ({trimmed_path})")
        return trimmed_path, True

    # Fallback to fast transcode if stream copy boundary fails
    cmd = [ffmpeg_bin, "-y", "-threads", "0"]
    if trim_start > 0:
        cmd.extend(["-ss", f"{trim_start:.3f}"])
    cmd.extend(["-i", input_path])
    if target_dur is not None:
        cmd.extend(["-t", f"{target_dur:.3f}"])
    cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-c:a", "aac", trimmed_path])
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode == 0 and os.path.exists(trimmed_path) and os.path.getsize(trimmed_path) > 1024:
        logger.info(f"Trimmed video via fast re-encode: {trim_start}s -> {target_dur}s ({trimmed_path})")
        return trimmed_path, True

    return input_path, False


class ReupService:
    """Unified Orchestrator for Reup Video Transformations, Vocal Muting, and TTS Dubbing/Sync."""

    @staticmethod
    def process_reup_video(*args, **kwargs) -> Dict[str, Any]:
        """Static method delegate for process_reup_video."""
        return process_reup_video(*args, **kwargs)

    @classmethod
    def process_reup_pipeline(
        cls,
        video_path: str,
        config: Optional[ReupConfig] = None,
        output_path: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Executes end-to-end Reup processing pipeline:
        1. Video trimming (if trim_start_sec or trim_end_sec requested)
        2. Subtitle STT / extraction
        3. Subtitle translation to target language
        4. TTS voice synthesis with SRT segment speed alignment
        5. Vocal muting / background audio preservation
        6. Audio-video single-pass FFmpeg merging
        7. Hash modification
        """
        cfg = config or ReupConfig()
        stage_callback = kwargs.get("stage_progress_callback")
        quality_out = kwargs.get("quality_out")
        if not isinstance(quality_out, dict):
            quality_out = {}
            kwargs["quality_out"] = quality_out
        quality_out.setdefault("status", "PENDING")
        quality_out.setdefault("report", {})

        def report_stage(progress: float, message: str) -> None:
            if not callable(stage_callback):
                return
            stage_callback(float(progress), message)

        def report_busy(progress: float, start_message: str, label: str):
            from app.services.activity import heartbeat
            report_stage(progress, start_message)
            return heartbeat(lambda msg: report_stage(progress, msg), label, interval=8.0)
        # Only use text_cover_vf if explicitly provided by configuration, avoiding unsolicited delogo blurring
        if not output_path:
            base, ext = os.path.splitext(video_path)
            output_path = f"{base}_reup{ext}"

        original_media = video_path
        # Trim video at the start of the pipeline so STT, Vietsub and mixing align 100% with trimmed timestamps
        trim_st = float(getattr(cfg, "trim_start_sec", 0.0) or 0.0)
        trim_en = float(getattr(cfg, "trim_end_sec", 0.0) or 0.0)
        if trim_st > 0.0 or trim_en > 0.0:
            report_stage(0.74, f"✂️ Đang cắt video (bỏ {trim_st:.1f}s đầu, {trim_en:.1f}s cuối)...")
        effective_video_path, is_temp_trimmed = trim_video_input(video_path, trim_st, trim_en)
        if is_temp_trimmed:
            video_path = effective_video_path
            report_stage(0.75, "✅ Đã cắt video xong, bắt đầu nhận lời thoại")

        synced_tts_audio: Optional[str] = None
        translated_srt: Optional[str] = None
        srt_path: Optional[str] = None
        tts_warning: Optional[str] = None

        cached_srt = find_cached_vietsub_srt(original_media, video_path)
        cached_tts = find_cached_tts_audio(original_media, video_path)

        preset_srt = getattr(cfg, "srt_path", None) or cached_srt
        preset_tts = getattr(cfg, "tts_audio_path", None) or cached_tts
        if cached_tts and not getattr(cfg, "tts_audio_path", None):
            logger.info("Reusing rendered TTS (skip re-read): %s", cached_tts)
            report_stage(0.86, "🎙️ Dùng lại file thuyết minh đã render — không đọc lại hàng trăm câu.")
        if cached_srt and not getattr(cfg, "srt_path", None):
            logger.info("Reusing Vietsub SRT: %s", cached_srt)
        if isinstance(preset_srt, str) and os.path.exists(preset_srt):
            from app.services.pyvideotrans_service import subtitle_matches_target_language
            if subtitle_matches_target_language(preset_srt, cfg.target_lang):
                translated_srt = preset_srt
                quality_out["status"] = "PASS"
                quality_out["report"]["preset_srt"] = True
                logger.info(f"Using pre-built SRT: {translated_srt}")
            else:
                logger.warning(f"Rejected mixed/untranslated pre-built SRT: {preset_srt}")
        if isinstance(preset_tts, str) and os.path.exists(preset_tts) and os.path.getsize(preset_tts) > 2048:
            synced_tts_audio = preset_tts
            logger.info(f"Using pre-built TTS audio: {synced_tts_audio}")

        subtitle_mode = subtitle_output_mode(cfg)
        want_subs = bool(subtitle_mode != "off" or cfg.enable_tts)
        if not want_subs:
            quality_out["status"] = "PASS"
            quality_out["report"]["skipped"] = True
            quality_out["report"]["reason"] = "subtitle_off"
        if want_subs and not translated_srt:
            try:
                from app.services.pyvideotrans_service import (
                    PyVideoTransService,
                    subtitle_matches_target_language,
                )
                from app.services.tts_service import tts_service, get_audio_duration

                pyvideotrans = PyVideoTransService()

                src_lang = cfg.source_lang or "auto"
                if src_lang in ("", "auto"):
                    from app.services.vietsub_rules import infer_stt_source_lang

                    src_lang = infer_stt_source_lang(video_path, "") or src_lang
                from app.services.vietsub_rules import resolve_vietsub_style, review_label
                from app.services.xai_media_service import LANG_DEFAULT_VOICE
                from app.services.tts_service import get_audio_duration
                vid_dur = get_audio_duration(video_path)
                style = resolve_vietsub_style(getattr(cfg, "vietsub_style", "dub") or "dub")
                cfg.vietsub_style = style
                quality_out["report"]["style"] = style
                lang = (cfg.target_lang or "vi").lower()
                voice = cfg.tts_voice or ""
                if lang != "vi" and (
                    not voice or voice.startswith("vi-") or voice.startswith("vieneu:")
                ):
                    cfg.tts_voice = LANG_DEFAULT_VOICE.get(lang, voice)
                    cfg.tts_engine = "edge-tts"

                stt_max = None
                with report_busy(
                    0.76,
                    "🎧 Bắt đầu nhận dạng lời thoại gốc (Whisper)...",
                    "Whisper đang nhận dạng lời thoại",
                ):
                    stt_res = pyvideotrans.speech_to_text(
                        video_path,
                        detect_lang=src_lang,
                        model_name="base",
                        max_seconds=stt_max,
                        on_status=lambda msg: report_stage(0.76, msg),
                    )
                srt_path = stt_res.get("srt_path")
                stt_status = stt_res.get("status")
                is_unusable = stt_status in ("fallback", "empty", "needs_review")
                cue_count = int(stt_res.get("cue_count") or 0)
                detected = stt_res.get("detected_language") or src_lang or "auto"
                used_model = stt_res.get("model") or "base"
                quality_out["report"]["stt_model"] = used_model
                quality_out["report"]["cue_count_in"] = cue_count
                if is_unusable or not (isinstance(srt_path, str) and os.path.exists(srt_path)):
                    fail_reason = stt_res.get("stt_fail_reason") or (
                        "engine_failed"
                        if stt_status == "fallback"
                        else ("empty_audio" if stt_status == "empty" else "looped_phrases")
                    )
                    quality_out["status"] = "NEEDS_REVIEW"
                    quality_out["report"]["stt_fail_reason"] = fail_reason
                    report_stage(
                        0.80,
                        f"⚠️ {review_label(fail_reason)} — dừng job, không xuất file thiếu Vietsub.",
                    )
                    require_complete_reup(
                        cfg,
                        translated_srt=None,
                        synced_tts_audio=synced_tts_audio,
                        detail=review_label(fail_reason),
                    )
                else:
                    report_stage(
                        0.80,
                        f"✅ Whisper xong: {cue_count} câu · model {used_model} · lang {detected}. Chuẩn bị dịch kịch bản...",
                    )

                if isinstance(srt_path, str) and os.path.exists(srt_path) and not is_unusable:
                    with report_busy(
                        0.82,
                        f"🌐 Đang dịch {cue_count} câu sang tiếng Việt...",
                        "Đang dịch kịch bản sang tiếng Việt",
                    ):
                        trans_res = pyvideotrans.translate_subtitles(
                            srt_path,
                            target_lang=cfg.target_lang,
                            style=style,
                            title=getattr(cfg, "post_title", "") or "",
                            duration=vid_dur or 0,
                            on_status=lambda msg: report_stage(0.82, msg),
                        )
                    raw_trans_srt = trans_res.get("srt_path") if trans_res else None
                    if (
                        trans_res
                        and trans_res.get("status") == "success"
                        and isinstance(raw_trans_srt, str)
                        and subtitle_matches_target_language(raw_trans_srt, cfg.target_lang)
                    ):
                        translated_srt = raw_trans_srt
                        provider = (trans_res or {}).get("provider") or "agy"
                        quality_out["status"] = "PASS"
                        quality_out["report"]["agy_model"] = provider
                        quality_out["report"]["cue_count_out"] = cue_count
                        report_stage(
                            0.85,
                            f"✅ Dịch tiếng Việt xong ({provider}, {cue_count} câu); bắt đầu tổng hợp giọng đọc...",
                        )
                        logger.info(f"Vietsub SRT ready: {translated_srt}")
                    else:
                        translated_srt = None
                        fail_reason = (trans_res or {}).get("translate_fail_reason") or "agy_failed"
                        tts_warning = (trans_res or {}).get("warning") or (
                            "Dịch phụ đề chưa hoàn tất; đã chặn bản trộn ngôn ngữ khỏi video."
                        )
                        quality_out["status"] = "NEEDS_REVIEW"
                        quality_out["report"]["translate_fail_reason"] = fail_reason
                        logger.error(tts_warning)
                        report_stage(0.84, f"⚠️ {review_label(fail_reason)}: {tts_warning}")
                        require_complete_reup(
                            cfg,
                            translated_srt=None,
                            synced_tts_audio=synced_tts_audio,
                            detail=tts_warning,
                        )
                elif quality_out.get("status") != "NEEDS_REVIEW":
                    tts_warning = "STT không nhận được lời thoại. Không xuất file thiếu Vietsub."
                    quality_out["status"] = "NEEDS_REVIEW"
                    quality_out["report"]["stt_fail_reason"] = "empty_audio"
                    logger.warning(tts_warning)
                    report_stage(0.84, f"⚠️ {tts_warning}")
                    require_complete_reup(
                        cfg,
                        translated_srt=None,
                        synced_tts_audio=synced_tts_audio,
                        detail=tts_warning,
                    )

                if cfg.enable_tts and not synced_tts_audio and translated_srt and os.path.exists(translated_srt):
                    from app.config import Settings
                    settings = Settings()
                    settings.ensure_directories()
                    video_name, _ = os.path.splitext(os.path.basename(video_path))
                    tts_out_path = os.path.join(settings.TTS_OUTPUT_DIR, f"{video_name}_synced_tts.wav")
                    vid_dur = get_audio_duration(video_path)
                    import asyncio

                    tts_voice = cfg.tts_voice
                    tts_engine = cfg.tts_engine
                    if (
                        str(tts_voice or "").lower().startswith("vieneu:")
                        or str(tts_engine or "").lower().startswith("vieneu")
                    ):
                        from app.modules.tts.providers import VieNeuTTSProvider
                        from app.services.tts_service import edge_voice_for_vieneu

                        if not VieNeuTTSProvider.runtime_available():
                            tts_voice = edge_voice_for_vieneu(tts_voice or "", cfg.target_lang)
                            tts_engine = "edge-tts"
                            report_stage(
                                0.86,
                                f"🎙️ VieNeu/ONNX DLL lỗi — chuyển sang Edge-TTS ({tts_voice})...",
                            )

                    async def _run_tts():
                        return await tts_service.synthesize_synchronized_tts(
                            srt_path=translated_srt,
                            output_audio_path=tts_out_path,
                            voice=tts_voice,
                            lang=cfg.target_lang,
                            engine=tts_engine,
                            total_duration=vid_dur,
                            enable_lipsync=getattr(cfg, "enable_lipsync", True) and style == "dub",
                            timeline_speed=cfg.speed_factor,
                            progress_callback=kwargs.get("tts_progress_callback"),
                        )

                    tts_result = None
                    with report_busy(
                        0.86,
                        f"🎙️ Đang tổng hợp thuyết minh tiếng Việt ({tts_voice or 'vieneu'})...",
                        "TTS đang đọc từng câu phụ đề",
                    ):
                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_running():
                                import threading
                                result_holder = {}
                                def _run():
                                    result_holder["value"] = asyncio.run(_run_tts())
                                t = threading.Thread(target=_run)
                                t.start()
                                t.join()
                                tts_result = result_holder.get("value")
                            else:
                                tts_result = loop.run_until_complete(_run_tts())
                        except Exception:
                            tts_result = asyncio.run(_run_tts())

                    if os.path.exists(tts_out_path) and os.path.getsize(tts_out_path) > 2048:
                        synced_tts_audio = tts_out_path
                        from app.services.vietsub_rules import prefer_speech_timed_srt

                        translated_srt = prefer_speech_timed_srt(tts_result, translated_srt)
                        report_stage(0.93, "✅ Thuyết minh tiếng Việt đã sẵn sàng; bắt đầu render...")
                    else:
                        tts_warning = "TTS tạo file rỗng/im lặng."
                        logger.warning(tts_warning)
                        report_stage(0.90, f"⚠️ {tts_warning}")
                        require_complete_reup(
                            cfg,
                            translated_srt=translated_srt,
                            synced_tts_audio=None,
                            detail=tts_warning,
                        )
            except RuntimeError as e:
                require_complete_reup(
                    cfg,
                    translated_srt=translated_srt,
                    synced_tts_audio=synced_tts_audio,
                    detail=str(e),
                )
                raise
            except Exception as e:
                tts_warning = f"Pipeline vietsub/TTS thất bại: {e}"
                quality_out["status"] = "NEEDS_REVIEW"
                quality_out["report"]["translate_fail_reason"] = "agy_failed"
                logger.warning(tts_warning)
                report_stage(0.86, f"⚠️ {tts_warning}")
                require_complete_reup(
                    cfg,
                    translated_srt=translated_srt,
                    synced_tts_audio=synced_tts_audio,
                    detail=tts_warning,
                )

        # If SRT was provided (sidecar / preset) but TTS audio is still missing, synthesize it
        if cfg.enable_tts and not synced_tts_audio and translated_srt and os.path.exists(translated_srt):
            try:
                from app.config import Settings
                from app.services.tts_service import tts_service, get_audio_duration
                settings = Settings()
                settings.ensure_directories()
                video_name, _ = os.path.splitext(os.path.basename(video_path))
                tts_out_path = os.path.join(settings.TTS_OUTPUT_DIR, f"{video_name}_synced_tts.wav")
                vid_dur = get_audio_duration(video_path)
                import asyncio

                async def _run_preset_tts():
                    return await tts_service.synthesize_synchronized_tts(
                        srt_path=translated_srt,
                        output_audio_path=tts_out_path,
                        voice=cfg.tts_voice,
                        lang=cfg.target_lang,
                        engine=cfg.tts_engine,
                        total_duration=vid_dur,
                        enable_lipsync=getattr(cfg, "enable_lipsync", True) and (getattr(cfg, "vietsub_style", "dub") == "dub"),
                        timeline_speed=cfg.speed_factor,
                        progress_callback=kwargs.get("tts_progress_callback"),
                    )

                tts_result = None
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        import threading
                        result_holder = {}

                        def _run():
                            result_holder["value"] = asyncio.run(_run_preset_tts())

                        t = threading.Thread(target=_run)
                        t.start()
                        t.join()
                        tts_result = result_holder.get("value")
                    else:
                        tts_result = loop.run_until_complete(_run_preset_tts())
                except Exception:
                    tts_result = asyncio.run(_run_preset_tts())

                if os.path.exists(tts_out_path) and os.path.getsize(tts_out_path) > 2048:
                    synced_tts_audio = tts_out_path
                    from app.services.vietsub_rules import prefer_speech_timed_srt

                    translated_srt = prefer_speech_timed_srt(tts_result, translated_srt)
                else:
                    logger.warning("Preset-SRT TTS created an empty/silent file")
            except RuntimeError as e:
                require_complete_reup(
                    cfg,
                    translated_srt=translated_srt,
                    synced_tts_audio=synced_tts_audio,
                    detail=str(e),
                )
                raise
            except Exception as e:
                logger.warning(f"Preset-SRT TTS synthesis failed: {e}")

        speech_intervals: List[Tuple[float, float]] = []
        target_srt_for_vad = translated_srt or srt_path
        if isinstance(target_srt_for_vad, str) and os.path.exists(target_srt_for_vad):
            try:
                from app.services.tts_service import group_long_form_tts_segments, parse_srt_segments
                from app.services.vietsub_rules import stretch_cue_times_to_next_shot

                segs = stretch_cue_times_to_next_shot(
                    group_long_form_tts_segments(parse_srt_segments(target_srt_for_vad))
                )
                for s in segs:
                    st_val = float(s.get("start_time", s.get("start", 0.0)) or 0.0)
                    en_val = float(s.get("end_time", s.get("end", 0.0)) or 0.0)
                    if en_val > st_val:
                        speech_intervals.append((st_val, en_val))
            except Exception as e:
                logger.warning(f"Could not parse speech intervals from SRT: {e}")

        require_complete_reup(
            cfg,
            translated_srt=translated_srt,
            synced_tts_audio=synced_tts_audio,
            detail=tts_warning or "",
        )

        report_stage(0.93, "🎞️ Bắt đầu render video (tách BGM nếu cần, FFmpeg encode)...")
        try:
            res = process_reup_video(
                input_path=video_path,
                output_path=output_path,
                cfg=cfg,
                tts_audio_override=synced_tts_audio,
                srt_override=translated_srt if subtitle_mode != "off" else None,
                speech_intervals=speech_intervals,
                **kwargs
            )
            report_stage(0.99, "✅ Render video biến đổi hoàn tất")
            quality_out["status"] = "PASS"
            res["quality_status"] = "PASS"
            res["quality_report"] = quality_out.get("report") or {}
            return res["output_path"]
        finally:
            if is_temp_trimmed and os.path.exists(effective_video_path):
                try:
                    os.remove(effective_video_path)
                except OSError:
                    pass
