"""
Reup Video Transformation Engine.
=================================
Constructs and executes a single-pass FFmpeg master filtergraph pass for
video (hflip, setpts, crop, scale, eq, hue, unsharp) and audio (atempo, asetrate, aresample).

Target Path: app/services/reup_service.py
"""

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
    path = shutil.which("ffmpeg")
    if not path:
        for candidate in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"]:
            if os.path.exists(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return path


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


def browser_safe_encode_args(ffmpeg_bin: str) -> list:
    """H.264/AAC flags that HTML5 players (Chrome/Safari) can actually play."""
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
    cmd = [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-c", "copy", "-movflags", "+faststart", tmp]
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

    return "libx264", ["-preset", "ultrafast", "-crf", "23"]


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
    audio_sample_rate: int = 44100,
    has_audio: bool = True,
    burn_srt_path: Optional[str] = None,
    speech_intervals: Optional[List[Tuple[float, float]]] = None,
) -> Tuple[str, bool, str, str]:
    """
    Constructs unified single-pass complex filtergraph string alongside individual
    video and audio filter chains.

    Returns:
        (filter_complex_str, includes_audio_stream, video_filters_str, audio_filters_str)
    """
    vf_nodes = []
    extra_cover = (getattr(cfg, "text_cover_vf", None) or "").strip()
    if extra_cover:
        # delogo on the original frame BEFORE hflip/crop so pixel coords stay valid
        vf_nodes.extend([p for p in extra_cover.split(",") if p.strip()])

    if cfg.hflip:
        vf_nodes.append("hflip")

    bottom = float(getattr(cfg, "subtitle_bottom_crop", 0.0) or 0.0)
    if bottom > 0:
        vf_nodes.append(f"crop=iw:trunc(ih*(1-{bottom:.4f})/2)*2:0:0")

    if getattr(cfg, "dynamic_motion", False):
        # Dynamic micro-zoom / subtle temporal breathing to disrupt Meta TMK/PDQ spatial feature kernels
        vf_nodes.append("crop=w='trunc(iw*(1-0.03*abs(sin(2*PI*t/12)))/2)*2':h='trunc(ih*(1-0.03*abs(sin(2*PI*t/12)))/2)*2':x='(iw-ow)/2':y='(ih-oh)/2'")
    elif cfg.crop_percent > 0:
        p = cfg.crop_percent
        vf_nodes.append(f"crop=iw*(1-2*{p:.4f}):ih*(1-2*{p:.4f})")

    # Always force even dimensions after crop so yuv420p / x264 never rejects the encode
    vf_nodes.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

    # Burn Vietsub on original timestamps BEFORE setpts so SRT does not need rescaling
    if burn_srt_path and os.path.exists(burn_srt_path):
        # High-contrast, clean subtitle plate: bold white text, black border & semi-transparent dark plate
        style = (
            "FontName=DejaVu Sans,FontSize=18,Bold=1,Alignment=2,"
            "MarginV=24,MarginL=36,MarginR=36,BorderStyle=3,Outline=4,Shadow=0,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&HA0000000"
        )
        sub_path = _ffmpeg_subtitles_path(burn_srt_path)
        vf_nodes.append(f"subtitles='{sub_path}':force_style='{style}'")

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

    s_ratio = cfg.speed_factor
    if s_ratio != 1.0:
        vf_nodes.append(f"setpts=PTS/{s_ratio:.4f}")
        vf_nodes.append("fps=30")

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
                timed_filter = build_timed_speech_ducking_filter(speech_intervals, duck_volume=0.12)
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

        if cfg.pitch_shift:
            # Bound pitch ratio between 0.97 and 1.03 to preserve human voice timbre & natural formants
            raw_p_ratio = cfg.pitch_factor if cfg.pitch_factor != 1.0 else s_ratio
            p_ratio = max(0.97, min(1.03, raw_p_ratio))
            if abs(p_ratio - 1.0) > 1e-4:
                af_nodes.append(f"asetrate={audio_sample_rate}*{p_ratio:.4f},aresample={audio_sample_rate}")

            remaining_speed = s_ratio / p_ratio if p_ratio != 0 else s_ratio
            if abs(remaining_speed - 1.0) > 1e-4:
                af_nodes.extend(_build_atempo_nodes(remaining_speed))
        else:
            if abs(s_ratio - 1.0) > 1e-4:
                af_nodes.extend(_build_atempo_nodes(s_ratio))

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
    else:
        filter_complex = f"[0:v]{vf_graph}[v_out]"
        return filter_complex, False, vf_str, af_str


def apply_vietnamese_dubbing(video_path: str, text_to_translate: str, output_path: Optional[str] = None) -> bool:
    """Translates text to Vietnamese, generates Edge-TTS speech audio, and replaces video audio stream."""
    import asyncio
    try:
        from app.services.ai_scriptwriter_service import ai_scriptwriter_service
        from app.services.pyvideotrans_service import _is_invalid_translation
        import edge_tts

        tts_file = video_path + ".vi_voice.mp3"
        vi_text = ""
        
        async def _gen_tts_and_translate():
            nonlocal vi_text
            if ai_scriptwriter_service.is_available():
                vi_text = await asyncio.to_thread(
                    lambda: ai_scriptwriter_service.translate_text(text_to_translate, target_lang="vi")
                )
            if not vi_text or _is_invalid_translation(vi_text):
                try:
                    from deep_translator import GoogleTranslator
                    raw_res = await asyncio.to_thread(
                        lambda: GoogleTranslator(source="auto", target="vi").translate(text_to_translate)
                    )
                    if raw_res and not _is_invalid_translation(raw_res):
                        vi_text = raw_res
                except Exception:
                    pass
            if not vi_text or _is_invalid_translation(vi_text):
                vi_text = text_to_translate

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
                "ffmpeg", "-y", "-i", video_path, "-i", tts_file,
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


def _probe_video_size(path: str) -> Tuple[int, int]:
    """Return (w, h) via ffprobe, defaulting to 1080x1920 on failure."""
    from app.services.audio_service import find_ffprobe_binary
    probe = find_ffprobe_binary()
    if probe:
        try:
            res = subprocess.run(
                [probe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
                capture_output=True, text=True, check=False,
            )
            w, h = res.stdout.strip().split("x")
            return int(w), int(h)
        except Exception:
            pass
    return 1080, 1920


def _filtered_video_size(path: str, cfg: ReupConfig) -> Tuple[int, int]:
    """Estimate the stable output dimensions before overlays are appended."""
    width, height = _probe_video_size(path)
    bottom = float(getattr(cfg, "subtitle_bottom_crop", 0.0) or 0.0)
    if bottom > 0:
        height = int(height * (1.0 - bottom))
    if not getattr(cfg, "dynamic_motion", False):
        crop = float(getattr(cfg, "crop_percent", 0.0) or 0.0)
        if crop > 0:
            width = int(width * (1.0 - 2.0 * crop))
            height = int(height * (1.0 - 2.0 * crop))
    return max(2, width // 2 * 2), max(2, height // 2 * 2)


def _burn_hardsub_overlay(ffmpeg_bin: str, video_path: str, srt_path: str, output_path: str) -> bool:
    """libass-free hardsub: render cues to PNGs (Pillow) and composite via overlay."""
    import tempfile
    from app.services.subtitle_overlay import render_srt_to_overlays, build_overlay_filter

    w, h = _probe_video_size(video_path)
    tmp_dir = tempfile.mkdtemp(prefix="visub_ovl_")
    try:
        overlays = render_srt_to_overlays(srt_path, w, h, tmp_dir)
        if not overlays:
            logger.warning("Subtitle overlay produced no cues; leaving video unchanged")
            return False
        fc, input_args = build_overlay_filter(overlays)
        tmp_out = output_path + ".ovlsub.tmp.mp4"
        encode_args = browser_safe_encode_args(ffmpeg_bin)
        cmd = [ffmpeg_bin, "-y", "-i", video_path, *input_args,
               "-filter_complex", fc, "-map", "[v_out]", "-map", "0:a?",
               *encode_args, "-c:a", "copy", tmp_out]
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


def burn_vietnamese_hardsub(video_path: str, srt_path: str, output_path: str, speed_factor: float = 1.0) -> bool:
    """Burns a Vietnamese SRT onto video as hardsub. Returns True on success.
    Uses native libass `subtitles=` when available, else a Pillow PNG overlay."""
    if not os.path.exists(video_path) or not os.path.exists(srt_path):
        return False
    ffmpeg_bin = find_ffmpeg_binary()
    if not ffmpeg_bin:
        return False

    work_srt = srt_path
    scaled = None
    if abs(speed_factor - 1.0) > 1e-3:
        try:
            scaled = video_path + ".vi.scaled.srt"
            work_srt = scale_srt_timestamps(srt_path, speed_factor, scaled)
        except Exception as e:
            logger.warning(f"SRT time-scale failed ({e}); burning original timings")
            work_srt = srt_path

    if not ffmpeg_supports_libass(ffmpeg_bin):
        ok = _burn_hardsub_overlay(ffmpeg_bin, video_path, work_srt, output_path)
        if scaled and os.path.exists(scaled) and scaled != srt_path:
            try:
                os.remove(scaled)
            except OSError:
                pass
        return ok

    fontfile = _find_subtitle_font()
    style = (
        "FontName=DejaVu Sans,FontSize=18,Bold=1,Alignment=2,"
        "MarginV=24,MarginL=36,MarginR=36,BorderStyle=3,Outline=4,Shadow=0,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&HA0000000"
    )
    sub_path = _ffmpeg_subtitles_path(work_srt)
    if fontfile:
        font_esc = _ffmpeg_subtitles_path(fontfile)
        vf = f"subtitles='{sub_path}':fontsdir='{os.path.dirname(fontfile).replace(chr(92), '/')}':force_style='{style}'"
        # fontsdir + FontName is more portable than fontfile= on older ffmpeg
        vf = f"subtitles='{sub_path}':force_style='{style}'"
        _ = font_esc  # keep helper used for path safety
    else:
        vf = f"subtitles='{sub_path}':force_style='{style}'"

    tmp_out = output_path + ".hardsub.tmp.mp4"
    encode_args = browser_safe_encode_args(ffmpeg_bin)
    cmd = [
        ffmpeg_bin, "-y", "-i", video_path,
        "-vf", vf,
        *encode_args,
        "-c:a", "copy",
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


def build_tts_bgm_mix_filter() -> str:
    """Strip center-channel speech (Chinese voice) from background track, then duck under Vietnamese TTS voiceover."""
    return (
        "[1:a]aresample=44100,aformat=channel_layouts=stereo,volume=1.12,highpass=f=80,lowpass=f=12000,"
        "acompressor=threshold=-22dB:ratio=2.5:attack=8:release=90:makeup=2.0[voice];"
        "[voice]asplit=2[sc][vox];"
        "[0:a]aresample=44100,aformat=channel_layouts=stereo,volume=0.85[bgraw];"
        "[bgraw][sc]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=250:makeup=1:knee=3[bg];"
        "[bg][vox]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[amixed];"
        "[amixed]alimiter=limit=0.95[aout]"
    )


def should_use_demucs_for_dubbing(cfg: ReupConfig, _speech_intervals: Optional[List[Tuple[float, float]]]) -> bool:
    """Reserve slow neural separation for users who explicitly select Demucs."""
    return bool(
        cfg.enable_vocal_mute
        and cfg.preserve_bgm
        and cfg.vocal_mute_strategy == "demucs"
    )


def mix_tts_with_background(video_path: str, tts_audio_path: str, output_path: str) -> bool:
    """
    Mix TTS voiceover with original (possibly vocal-muted) audio.
    Sidechain-ducks BGM under speech so silent stretches keep music body.
    Does NOT replace BGM. Pads TTS to video length. Never uses -shortest.
    """
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
            ffmpeg_bin, "-y", "-i", tts_audio_path,
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
            ffmpeg_bin, "-y",
            "-i", video_path,
            "-i", audio_to_use,
            "-filter_complex", fc,
            "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            tmp_out,
        ]
    else:
        cmd = [
            ffmpeg_bin, "-y",
            "-i", video_path,
            "-i", audio_to_use,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
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
                "[0:a]aresample=44100,aformat=channel_layouts=stereo,volume=0.85[bg];"
                "[1:a]aresample=44100,aformat=channel_layouts=stereo,volume=1.10[voice];"
                "[bg][voice]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[amixed];"
                "[amixed]dynaudnorm=f=120:g=10:p=0.95,alimiter=limit=0.94[aout]"
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
    enable_vocal_mute: bool = True,
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
    # Bake subtitles into the master graph via libass or one timed APNG track.
    wants_hardsub = bool(
        srt_override
        and os.path.exists(srt_override)
        and getattr(cfg, "burn_subtitles", True)
    )
    libass_hardsub = bool(wants_hardsub and ffmpeg_supports_libass())
    burn_in_graph = libass_hardsub
    subtitle_track_dir: Optional[str] = None

    speech_intervals = kwargs.get("speech_intervals")
    # Prefer a separated music stem for the "remove speech, keep BGM" mode.
    use_demucs = should_use_demucs_for_dubbing(cfg, speech_intervals)
    if (not lib_bgm_path) and has_audio and cfg.enable_vocal_mute and cfg.vocal_mute_strategy in ("auto", "demucs") and use_demucs:
        from app.services.audio_service import check_demucs_available, extract_audio_stream, process_vocal_muting
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
                    res_vm = process_vocal_muting(extracted_a, bgm_out, config=cfg)
                    if res_vm.get("method") == "demucs" and os.path.exists(bgm_out):
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
        # A Demucs BGM stem is already voice-free; only apply visual and timing FX to it.
        graph_cfg = cfg.model_copy(update={"enable_vocal_mute": False}) if demucs_bgm_path else cfg
        audio_speech_intervals = None if cfg.enable_vocal_mute else speech_intervals
        filter_complex, includes_audio, vf_str, af_str = build_reup_filtergraph(
            graph_cfg,
            has_audio=has_audio,
            burn_srt_path=srt_override if libass_hardsub else None,
            speech_intervals=audio_speech_intervals,
        )
        from app.services.overlay_service import append_overlay_filter, normalize_overlays, overlay_input_args
        overlay_items = normalize_overlays(getattr(cfg, "overlays", None))
        overlay_paths: List[str] = []
        extra_audio = bool(lib_bgm_path) or bool(demucs_bgm_path and os.path.exists(demucs_bgm_path))
        first_ov = 2 if extra_audio else 1
        if overlay_items:
            main_size = _filtered_video_size(input_path, cfg)
            filter_complex, overlay_paths = append_overlay_filter(
                filter_complex, overlay_items, first_overlay_index=first_ov, main_size=main_size
            )
        subtitle_input_args: List[str] = []
        if wants_hardsub and not libass_hardsub and not getattr(cfg, "dynamic_motion", False):
            from app.services.subtitle_overlay import (
                append_timed_subtitle_filter,
                render_srt_to_apng,
            )

            subtitle_track_dir = tempfile.mkdtemp(prefix="visub_track_")
            timed_srt = srt_override
            if abs(float(cfg.speed_factor or 1.0) - 1.0) > 1e-3:
                timed_srt = scale_srt_timestamps(
                    srt_override,
                    cfg.speed_factor,
                    os.path.join(subtitle_track_dir, "timed.srt"),
                )
            subtitle_track = render_srt_to_apng(
                timed_srt,
                *_filtered_video_size(input_path, cfg),
                os.path.join(subtitle_track_dir, "subtitles.png"),
            )
            if subtitle_track:
                subtitle_index = first_ov + len(overlay_paths)
                filter_complex = append_timed_subtitle_filter(filter_complex, subtitle_index)
                subtitle_input_args = ["-i", subtitle_track]
                burn_in_graph = True
        input_md5 = calculate_file_md5(input_path)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        # 3. FFmpeg Execution or Safe Fallback Copy
        ffmpeg_bin = find_ffmpeg_binary()
        ffmpeg_success = False

        if ffmpeg_bin:
            try:
                encode_args = browser_safe_encode_args(ffmpeg_bin)
                cmd = [ffmpeg_bin, "-y", "-threads", "0", "-i", input_path]
                if lib_bgm_path and os.path.exists(lib_bgm_path):
                    from app.services.tts_service import get_audio_duration
                    in_dur = get_audio_duration(input_path) or 8.0
                    speed = float(getattr(cfg, "speed_factor", 1.0) or 1.0)
                    out_dur = max(0.4, in_dur / max(0.5, speed))
                    vol = float(getattr(cfg, "bgm_volume", 0.85) or 0.85)
                    vf_part = filter_complex.split(";")[0]
                    fc = f"{vf_part};[1:a]volume={vol:.3f},aresample=44100,aformat=channel_layouts=stereo[a_out]"
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
                if includes_audio:
                    cmd.extend(["-map", "[a_out]", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"])
                else:
                    cmd.extend(["-an"])
                cmd.append(output_path)

                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    ffmpeg_success = True
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
                                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                                "-profile:v", "main", "-level", "4.0",
                            ]
                            video_arg_index = cmd.index("-c:v")
                            cmd[video_arg_index:video_arg_index + len(encode_args)] = software_args
                            res_sw = subprocess.run(cmd, capture_output=True, text=True, check=False)
                            if res_sw.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                                ffmpeg_success = True
                    if not ffmpeg_success and "delogo" in fc and "Logo area is outside" in err:
                        logger.warning("Retrying encode without mid-text delogo")
                        cfg.text_cover_vf = ""
                        filter_complex, includes_audio, vf_str, af_str = build_reup_filtergraph(
                            graph_cfg,
                            has_audio=has_audio,
                            burn_srt_path=srt_override if burn_in_graph else None,
                            speech_intervals=audio_speech_intervals,
                        )
                        cmd[cmd.index("-filter_complex") + 1] = filter_complex
                        res2 = subprocess.run(cmd, capture_output=True, text=True, check=False)
                        if res2.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                            ffmpeg_success = True
            except Exception as e:
                logger.warning(f"FFmpeg execution skipped/failed ({e}), falling back to direct stream copy")
    finally:
        if subtitle_track_dir:
            shutil.rmtree(subtitle_track_dir, ignore_errors=True)
        if demucs_bgm_path and os.path.exists(demucs_bgm_path):
            try:
                os.remove(demucs_bgm_path)
            except OSError:
                pass

    if not ffmpeg_success:
        err_msg = f"FFmpeg execution failed for input '{input_path}'."
        if not ffmpeg_bin:
            err_msg = "FFmpeg binary executable not found on system PATH."
        logger.error(err_msg)
        raise RuntimeError(err_msg)

    # 4. Optional Vietnamese TTS Dubbing Pass (mix with BGM, never replace)
    dubbed_vi = False
    tts_audio_override = kwargs.get("tts_audio_override")
    if tts_audio_override and os.path.exists(tts_audio_override) and os.path.getsize(tts_audio_override) > 0:
        dubbed_vi = mix_tts_with_background(output_path, tts_audio_override, output_path)
        if not dubbed_vi:
            logger.warning("TTS mix with background audio failed; keeping original audio track")
    elif vietnamese_dubbing and text_for_dubbing:
        dubbed_vi = apply_vietnamese_dubbing(output_path, text_for_dubbing, output_path=output_path)

    # 4b. Burn Vietnamese hardsub only if it was NOT already in the master filtergraph
    burned_sub = bool(burn_in_graph)
    if (not burned_sub) and srt_override and os.path.exists(srt_override) and getattr(cfg, "burn_subtitles", True):
        burned_sub = burn_vietnamese_hardsub(
            output_path, srt_override, output_path, speed_factor=cfg.speed_factor
        )
    if srt_override and os.path.exists(srt_override) and getattr(cfg, "burn_subtitles", True):
        sidecar = os.path.splitext(output_path)[0] + ".vi.srt"
        try:
            shutil.copy2(srt_override, sidecar)
        except Exception:
            pass

    # 5. Browser-safe remux then MD5 trailer (trailer MUST come last)
    remux_faststart(output_path)
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
        # Only use text_cover_vf if explicitly provided by configuration, avoiding unsolicited delogo blurring
        if not output_path:
            base, ext = os.path.splitext(video_path)
            output_path = f"{base}_reup{ext}"

        # Trim video at the start of the pipeline so STT, Vietsub and mixing align 100% with trimmed timestamps
        trim_st = float(getattr(cfg, "trim_start_sec", 0.0) or 0.0)
        trim_en = float(getattr(cfg, "trim_end_sec", 0.0) or 0.0)
        effective_video_path, is_temp_trimmed = trim_video_input(video_path, trim_st, trim_en)
        if is_temp_trimmed:
            video_path = effective_video_path

        synced_tts_audio: Optional[str] = None
        translated_srt: Optional[str] = None
        tts_warning: Optional[str] = None

        preset_srt = getattr(cfg, "srt_path", None)
        preset_tts = getattr(cfg, "tts_audio_path", None)
        if isinstance(preset_srt, str) and os.path.exists(preset_srt):
            from app.services.pyvideotrans_service import subtitle_matches_target_language
            if subtitle_matches_target_language(preset_srt, cfg.target_lang):
                translated_srt = preset_srt
                logger.info(f"Using pre-built SRT: {translated_srt}")
            else:
                logger.warning(f"Rejected mixed/untranslated pre-built SRT: {preset_srt}")
        if isinstance(preset_tts, str) and os.path.exists(preset_tts) and os.path.getsize(preset_tts) > 2048:
            synced_tts_audio = preset_tts
            logger.info(f"Using pre-built TTS audio: {synced_tts_audio}")

        want_subs = bool(getattr(cfg, "burn_subtitles", True) or cfg.enable_tts)
        if want_subs and not translated_srt:
            try:
                from app.services.pyvideotrans_service import (
                    PyVideoTransService,
                    subtitle_matches_target_language,
                )
                from app.services.tts_service import tts_service, get_audio_duration

                pyvideotrans = PyVideoTransService()

                src_lang = cfg.source_lang or "auto"
                from app.services.xai_media_service import resolve_vietsub_style, LANG_DEFAULT_VOICE
                from app.services.tts_service import get_audio_duration
                vid_dur = get_audio_duration(video_path)
                style = resolve_vietsub_style(getattr(cfg, "vietsub_style", "auto") or "auto", vid_dur or 0)
                cfg.vietsub_style = style
                lang = (cfg.target_lang or "vi").lower()
                voice = cfg.tts_voice or ""
                if lang != "vi" and (not voice or voice.startswith("vi-")):
                    cfg.tts_voice = LANG_DEFAULT_VOICE.get(lang, voice)

                clip_title = source_clip_title(video_path, cfg)
                import re as _re
                title_cjk = "".join(_re.findall(r"[\u4e00-\u9fff]", clip_title or ""))

                stt_max = 90.0 if style == "recap" and (vid_dur or 0) > 180 else None
                stt_res = pyvideotrans.speech_to_text(
                    video_path,
                    detect_lang=src_lang,
                    model_name="base",
                    max_seconds=stt_max,
                )
                srt_path = stt_res.get("srt_path")
                is_fallback = stt_res.get("status") in ("fallback", "empty")

                # Fallback to clip title ONLY if STT was completely empty or failed
                if (not srt_path or not os.path.exists(srt_path) or is_fallback) and len(title_cjk) >= 4:
                    title_srt = os.path.splitext(video_path)[0] + ".title.srt"
                    srt_path = write_single_cue_srt(title_srt, clip_title, vid_dur or 8.0)
                    stt_res = {"status": "success", "srt_path": srt_path}
                    is_fallback = False
                    logger.info(f"STT returned empty — fallback to title: {clip_title}")

                if isinstance(srt_path, str) and os.path.exists(srt_path) and not is_fallback:
                    trans_res = pyvideotrans.translate_subtitles(
                        srt_path,
                        target_lang=cfg.target_lang,
                        style=style,
                        title=getattr(cfg, "post_title", "") or "",
                        duration=vid_dur or 0,
                    )
                    raw_trans_srt = trans_res.get("srt_path") if trans_res else None
                    if (
                        trans_res
                        and trans_res.get("status") == "success"
                        and isinstance(raw_trans_srt, str)
                        and subtitle_matches_target_language(raw_trans_srt, cfg.target_lang)
                    ):
                        translated_srt = raw_trans_srt
                        logger.info(f"Vietsub SRT ready: {translated_srt}")
                    else:
                        translated_srt = None
                        tts_warning = "Dịch phụ đề chưa hoàn tất; đã chặn bản trộn ngôn ngữ khỏi video."
                        logger.error(tts_warning)
                    if style == "recap" and translated_srt:
                        try:
                            from app.services.xai_media_service import build_recap_lines, recap_to_srt
                            from app.services.tts_service import parse_srt_segments
                            segs = parse_srt_segments(translated_srt)
                            lines = build_recap_lines(
                                title=getattr(cfg, "post_title", "") or "",
                                texts=[s.get("text") or "" for s in segs],
                                target_lang=cfg.target_lang,
                                n=8,
                            )
                            if lines:
                                recap_path = os.path.splitext(translated_srt)[0] + ".recap.srt"
                                translated_srt = recap_to_srt(lines, vid_dur or 60.0, recap_path)
                                logger.info(f"Recap narrator SRT: {translated_srt}")
                        except Exception as e:
                            logger.warning(f"Recap rewrite skipped: {e}")
                else:
                    # Sidecar next to the source (e.g. douyin_123.vi.srt) so demo clips still get hardsub
                    base_noext = os.path.splitext(video_path)[0]
                    for cand in (f"{base_noext}.vi.srt", f"{os.path.splitext(os.path.basename(video_path))[0]}.vi.srt"):
                        if os.path.exists(cand):
                            translated_srt = cand
                            logger.info(f"Using sidecar Vietsub SRT: {translated_srt}")
                            tts_warning = None
                            break
                    if not translated_srt:
                        tts_warning = "STT không nhận được lời thoại (whisper fallback). Bỏ qua vietsub/lồng tiếng."
                        logger.warning(tts_warning)

                if cfg.enable_tts and not synced_tts_audio and translated_srt and os.path.exists(translated_srt):
                    from app.config import Settings
                    settings = Settings()
                    settings.ensure_directories()
                    video_name, _ = os.path.splitext(os.path.basename(video_path))
                    tts_out_path = os.path.join(settings.TTS_OUTPUT_DIR, f"{video_name}_synced_tts.wav")
                    vid_dur = get_audio_duration(video_path)
                    import asyncio

                    async def _run_tts():
                        return await tts_service.synthesize_synchronized_tts(
                            srt_path=translated_srt,
                            output_audio_path=tts_out_path,
                            voice=cfg.tts_voice,
                            lang=cfg.target_lang,
                            engine=cfg.tts_engine,
                            total_duration=vid_dur,
                            enable_lipsync=getattr(cfg, "enable_lipsync", True) and style == "dub",
                            timeline_speed=cfg.speed_factor,
                        )

                    tts_result = None
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
                        aligned_srt = (tts_result or {}).get("aligned_srt_path")
                        if aligned_srt and os.path.exists(aligned_srt):
                            translated_srt = aligned_srt
                    else:
                        tts_warning = (tts_warning or "") + " TTS tạo file rỗng/im lặng — giữ audio gốc."
                        logger.warning(tts_warning)
            except Exception as e:
                tts_warning = f"Pipeline vietsub/TTS thất bại: {e}"
                logger.warning(tts_warning)

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
                    aligned_srt = (tts_result or {}).get("aligned_srt_path")
                    if aligned_srt and os.path.exists(aligned_srt):
                        translated_srt = aligned_srt
            except Exception as e:
                logger.warning(f"Preset-SRT TTS synthesis failed: {e}")

        speech_intervals: List[Tuple[float, float]] = []
        target_srt_for_vad = translated_srt or srt_path
        if isinstance(target_srt_for_vad, str) and os.path.exists(target_srt_for_vad):
            try:
                from app.services.tts_service import parse_srt_segments
                segs = parse_srt_segments(target_srt_for_vad)
                for s in segs:
                    st_val = float(s.get("start", 0.0) or 0.0)
                    en_val = float(s.get("end", 0.0) or 0.0)
                    if en_val > st_val:
                        speech_intervals.append((st_val, en_val))
            except Exception as e:
                logger.warning(f"Could not parse speech intervals from SRT: {e}")

        try:
            res = process_reup_video(
                input_path=video_path,
                output_path=output_path,
                cfg=cfg,
                tts_audio_override=synced_tts_audio,
                srt_override=translated_srt if getattr(cfg, "burn_subtitles", True) else None,
                speech_intervals=speech_intervals,
                **kwargs
            )
            if tts_warning:
                res["vietsub_warning"] = tts_warning
                logger.warning(f"Reup completed with vietsub warning: {tts_warning}")

            return res["output_path"]
        finally:
            if is_temp_trimmed and os.path.exists(effective_video_path):
                try:
                    os.remove(effective_video_path)
                except OSError:
                    pass
