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
        # Tight plate behind Vietnamese text (not a full-width bar)
        style = (
            "FontName=DejaVu Sans,FontSize=17,Bold=1,Alignment=2,"
            "MarginV=22,MarginL=48,MarginR=48,BorderStyle=3,Outline=5,Shadow=0,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&HB2000000,BackColour=&HB2000000"
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
        if cfg.enable_vocal_mute:
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
        from deep_translator import GoogleTranslator
        import edge_tts

        vi_text = GoogleTranslator(source="auto", target="vi").translate(text_to_translate)
        tts_file = video_path + ".vi_voice.mp3"
        
        async def _gen_tts():
            communicator = edge_tts.Communicate(vi_text, "en-US-AvaMultilingualNeural")
            await communicator.save(tts_file)
            
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import threading
                def _run_in_thread():
                    asyncio.run(_gen_tts())
                t = threading.Thread(target=_run_in_thread)
                t.start()
                t.join()
            else:
                loop.run_until_complete(_gen_tts())
        except Exception:
            asyncio.run(_gen_tts())
        
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


def burn_vietnamese_hardsub(video_path: str, srt_path: str, output_path: str, speed_factor: float = 1.0) -> bool:
    """Burns a Vietnamese SRT onto video as hardsub. Returns True on success."""
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

    fontfile = _find_subtitle_font()
    style = (
        "FontName=DejaVu Sans,FontSize=17,Bold=1,Alignment=2,"
        "MarginV=22,MarginL=48,MarginR=48,BorderStyle=3,Outline=5,Shadow=0,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&HB2000000,BackColour=&HB2000000"
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
    """Keep BGM body; duck it under Vietnamese TTS. Do not lowpass BGM again (already muted)."""
    return (
        "[1:a]aresample=44100,aformat=channel_layouts=stereo,volume=1.12,highpass=f=80,lowpass=f=12000,"
        "equalizer=f=2500:t=q:w=1.0:g=2.5,"
        "acompressor=threshold=-22dB:ratio=2.6:attack=8:release=90:makeup=2.5[voice];"
        "[voice]asplit=2[sc][vox];"
        "[0:a]aresample=44100,aformat=channel_layouts=stereo,volume=1.00[bgraw];"
        "[bgraw][sc]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=240:makeup=2:knee=4[bg];"
        "[bg][vox]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[amixed];"
        "[amixed]dynaudnorm=f=120:g=10:p=0.95,alimiter=limit=0.94[aout]"
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
    srt_override = kwargs.get("srt_override")
    burn_in_graph = bool(
        srt_override and os.path.exists(srt_override) and getattr(cfg, "burn_subtitles", True)
    )

    if has_audio and cfg.enable_vocal_mute and cfg.vocal_mute_strategy in ("auto", "demucs"):
        from app.services.audio_service import check_demucs_available, extract_audio_stream, process_vocal_muting
        if not check_demucs_available():
            if cfg.vocal_mute_strategy == "demucs":
                raise RuntimeError("Demucs strategy requested but demucs is not installed")
        else:
            import tempfile
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
        filter_complex, includes_audio, vf_str, af_str = build_reup_filtergraph(
            cfg, has_audio=has_audio, burn_srt_path=srt_override if burn_in_graph else None
        )
        from app.services.overlay_service import append_overlay_filter, normalize_overlays, overlay_input_args
        overlay_items = normalize_overlays(getattr(cfg, "overlays", None))
        overlay_paths: List[str] = []
        demucs_used = bool(demucs_bgm_path and os.path.exists(demucs_bgm_path))
        if overlay_items:
            first_ov = 2 if demucs_used else 1
            filter_complex, overlay_paths = append_overlay_filter(
                filter_complex, overlay_items, first_overlay_index=first_ov
            )
        input_md5 = calculate_file_md5(input_path)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        # 3. FFmpeg Execution or Safe Fallback Copy
        ffmpeg_bin = find_ffmpeg_binary()
        ffmpeg_success = False

        if ffmpeg_bin:
            try:
                encode_args = browser_safe_encode_args(ffmpeg_bin)
                cmd = [ffmpeg_bin, "-y", "-threads", "0", "-i", input_path]
                if demucs_bgm_path and os.path.exists(demucs_bgm_path):
                    fc = filter_complex.replace("[0:a]", "[1:a]")
                    cmd.extend(["-i", demucs_bgm_path])
                else:
                    fc = filter_complex
                cmd.extend(overlay_input_args(overlay_paths))
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
                    if "delogo" in fc and "Logo area is outside" in err:
                        logger.warning("Retrying encode without mid-text delogo")
                        cfg.text_cover_vf = ""
                        filter_complex, includes_audio, vf_str, af_str = build_reup_filtergraph(
                            cfg, has_audio=has_audio, burn_srt_path=srt_override if burn_in_graph else None
                        )
                        cmd[cmd.index("-filter_complex") + 1] = filter_complex
                        res2 = subprocess.run(cmd, capture_output=True, text=True, check=False)
                        if res2.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                            ffmpeg_success = True
            except Exception as e:
                logger.warning(f"FFmpeg execution skipped/failed ({e}), falling back to direct stream copy")
    finally:
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
        1. Subtitle STT / extraction
        2. Subtitle translation to target language
        3. TTS voice synthesis with SRT segment speed alignment
        4. Vocal muting / background audio preservation
        5. Audio-video single-pass FFmpeg merging
        6. Hash modification
        """
        cfg = config or ReupConfig()
        if not getattr(cfg, "text_cover_vf", None):
            try:
                from app.services.subtitle_detector import persistent_text_cover_filters
                covers = persistent_text_cover_filters(video_path)
                if covers:
                    cfg.text_cover_vf = ",".join(covers)
            except Exception as e:
                logger.warning(f"mid-text cover detect skipped: {e}")
        if not output_path:
            base, ext = os.path.splitext(video_path)
            output_path = f"{base}_reup{ext}"

        synced_tts_audio: Optional[str] = None
        translated_srt: Optional[str] = None
        tts_warning: Optional[str] = None

        preset_srt = getattr(cfg, "srt_path", None)
        preset_tts = getattr(cfg, "tts_audio_path", None)
        if isinstance(preset_srt, str) and os.path.exists(preset_srt):
            translated_srt = preset_srt
            logger.info(f"Using pre-built SRT: {translated_srt}")
        if isinstance(preset_tts, str) and os.path.exists(preset_tts) and os.path.getsize(preset_tts) > 2048:
            synced_tts_audio = preset_tts
            logger.info(f"Using pre-built TTS audio: {synced_tts_audio}")

        want_subs = bool(getattr(cfg, "burn_subtitles", True) or cfg.enable_tts)
        if want_subs and not translated_srt:
            try:
                from app.services.pyvideotrans_service import PyVideoTransService
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
                use_title_cue = bool((vid_dur or 0) < 22 and len(title_cjk) >= 4)

                stt_max = 90.0 if style == "recap" and (vid_dur or 0) > 180 else None
                if use_title_cue:
                    title_srt = os.path.splitext(video_path)[0] + ".title.srt"
                    srt_path = write_single_cue_srt(title_srt, clip_title, vid_dur or 8.0)
                    stt_res = {"status": "success", "srt_path": srt_path}
                    logger.info(f"Short meme clip — vietsub from title, skip BGM STT: {clip_title}")
                else:
                    stt_res = pyvideotrans.speech_to_text(
                        video_path,
                        detect_lang=src_lang,
                        model_name="base",
                        max_seconds=stt_max,
                    )
                    srt_path = stt_res.get("srt_path")
                    if (
                        isinstance(srt_path, str)
                        and os.path.exists(srt_path)
                        and srt_looks_like_bgm_lyrics(srt_path, clip_title)
                        and len(title_cjk) >= 4
                    ):
                        title_srt = os.path.splitext(video_path)[0] + ".title.srt"
                        srt_path = write_single_cue_srt(title_srt, clip_title, vid_dur or 8.0)
                        stt_res = {"status": "success", "srt_path": srt_path}
                        logger.info(f"STT looked like BGM lyrics — using title: {clip_title}")

                srt_path = stt_res.get("srt_path")
                is_fallback = stt_res.get("status") in ("fallback", "empty")

                if isinstance(srt_path, str) and os.path.exists(srt_path) and not is_fallback:
                    trans_res = pyvideotrans.translate_subtitles(
                        srt_path,
                        target_lang=cfg.target_lang,
                        style=style,
                        title=getattr(cfg, "post_title", "") or "",
                        duration=vid_dur or 0,
                    )
                    raw_trans_srt = trans_res.get("srt_path") if trans_res else None
                    translated_srt = (
                        raw_trans_srt if isinstance(raw_trans_srt, str) and raw_trans_srt else srt_path
                    )
                    logger.info(f"Vietsub SRT ready: {translated_srt}")
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
                        )

                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            import threading
                            def _run():
                                asyncio.run(_run_tts())
                            t = threading.Thread(target=_run)
                            t.start()
                            t.join()
                        else:
                            loop.run_until_complete(_run_tts())
                    except Exception:
                        asyncio.run(_run_tts())

                    if os.path.exists(tts_out_path) and os.path.getsize(tts_out_path) > 2048:
                        synced_tts_audio = tts_out_path
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
                    )

                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        import threading

                        def _run():
                            asyncio.run(_run_preset_tts())

                        t = threading.Thread(target=_run)
                        t.start()
                        t.join()
                    else:
                        loop.run_until_complete(_run_preset_tts())
                except Exception:
                    asyncio.run(_run_preset_tts())

                if os.path.exists(tts_out_path) and os.path.getsize(tts_out_path) > 2048:
                    synced_tts_audio = tts_out_path
            except Exception as e:
                logger.warning(f"Preset-SRT TTS synthesis failed: {e}")

        res = process_reup_video(
            input_path=video_path,
            output_path=output_path,
            cfg=cfg,
            tts_audio_override=synced_tts_audio,
            srt_override=translated_srt if getattr(cfg, "burn_subtitles", True) else None,
            **kwargs
        )
        if tts_warning:
            res["vietsub_warning"] = tts_warning
            logger.warning(f"Reup completed with vietsub warning: {tts_warning}")

        return res["output_path"]

