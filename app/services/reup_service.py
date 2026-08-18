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
    has_audio: bool = True
) -> Tuple[str, bool, str, str]:
    """
    Constructs unified single-pass complex filtergraph string alongside individual
    video and audio filter chains.

    Returns:
        (filter_complex_str, includes_audio_stream, video_filters_str, audio_filters_str)
    """
    vf_nodes = []
    if cfg.hflip:
        vf_nodes.append("hflip")

    s_ratio = cfg.speed_factor
    if s_ratio != 1.0:
        vf_nodes.append(f"setpts=PTS/{s_ratio:.4f}")

    if cfg.crop_percent > 0:
        p = cfg.crop_percent
        vf_nodes.append(f"crop=iw*(1-2*{p:.4f}):ih*(1-2*{p:.4f})")

    if cfg.color_adjust or (cfg.brightness != 0.0 or cfg.contrast != 1.0 or cfg.saturation != 1.0):
        b, c, sat = cfg.brightness, cfg.contrast, cfg.saturation
        vf_nodes.append(f"eq=brightness={b:.4f}:contrast={c:.4f}:saturation={sat:.4f}")

    if cfg.hue_shift != 0.0:
        vf_nodes.append(f"hue=h={cfg.hue_shift}")

    if cfg.sharpen and cfg.unsharp_amount > 0:
        vf_nodes.append(f"unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount={cfg.unsharp_amount}")

    af_nodes = []
    if has_audio:
        if cfg.enable_vocal_mute:
            from app.services.audio_service import build_vocal_mute_ffmpeg_filter
            vm_filter = build_vocal_mute_ffmpeg_filter(
                preserve_bgm=cfg.preserve_bgm,
                vocal_mute_strategy=cfg.vocal_mute_strategy,
                is_stereo=True
            )
            if vm_filter:
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

    vf_graph = vf_str if vf_str else "null"
    if has_audio and af_nodes:
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
            communicator = edge_tts.Communicate(vi_text, "vi-VN-HoaiMyNeural")
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
            hflip=hflip,
            speed_factor=speed_ratio,
            pitch_shift=pitch_shift,
            crop_percent=crop_percent,
            brightness=brightness,
            contrast=contrast,
            saturation=saturation,
            modify_md5=modify_md5,
            color_adjust=(brightness != 0.0 or contrast != 1.0 or saturation != 1.0),
            enable_vocal_mute=kwargs.get("enable_vocal_mute", enable_vocal_mute),
            vocal_mute_strategy=kwargs.get("vocal_mute_strategy", vocal_mute_strategy),
            preserve_bgm=kwargs.get("preserve_bgm", preserve_bgm),
            audio_ducking=kwargs.get("audio_ducking", audio_ducking)
        )

    has_audio = detect_audio_stream(input_path)
    demucs_bgm_path: Optional[str] = None

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
        filter_complex, includes_audio, vf_str, af_str = build_reup_filtergraph(cfg, has_audio=has_audio)
        input_md5 = calculate_file_md5(input_path)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        # 3. FFmpeg Execution or Safe Fallback Copy
        ffmpeg_bin = find_ffmpeg_binary()
        ffmpeg_success = False

        if ffmpeg_bin:
            try:
                encoder_name, encoder_flags = detect_h264_encoder(ffmpeg_bin)
                if demucs_bgm_path and os.path.exists(demucs_bgm_path):
                    # Use Demucs separated BGM track as second input stream
                    # Replace [0:a] mapping in filter_complex with [1:a]
                    fc = filter_complex.replace("[0:a]", "[1:a]")
                    cmd = [ffmpeg_bin, "-y", "-i", input_path, "-i", demucs_bgm_path, "-filter_complex", fc]
                else:
                    cmd = [ffmpeg_bin, "-y", "-i", input_path, "-filter_complex", filter_complex]

                cmd.extend(["-map", "[v_out]"])
                cmd.extend(["-c:v", encoder_name] + encoder_flags)
                if includes_audio:
                    cmd.extend(["-map", "[a_out]", "-c:a", "aac", "-b:a", "128k"])
                else:
                    cmd.extend(["-an"])
                cmd.append(output_path)

                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
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

    # 4. Optional Vietnamese TTS Dubbing Pass
    dubbed_vi = False
    tts_audio_override = kwargs.get("tts_audio_override")
    if tts_audio_override and os.path.exists(tts_audio_override):
        ffmpeg_bin = find_ffmpeg_binary()
        if ffmpeg_bin:
            from app.services.tts_service import get_audio_duration
            vid_dur = get_audio_duration(output_path)
            aud_dur = get_audio_duration(tts_audio_override)
            audio_to_use = tts_audio_override
            temp_padded_audio = None
            if vid_dur > 0 and aud_dur > 0 and aud_dur < vid_dur - 0.2:
                temp_padded_audio = output_path + ".padded_tts.wav"
                pad_cmd = [
                    ffmpeg_bin, "-y",
                    "-i", tts_audio_override,
                    "-af", "apad",
                    "-t", f"{vid_dur:.3f}",
                    temp_padded_audio
                ]
                pad_res = subprocess.run(pad_cmd, capture_output=True, text=True, check=False)
                if pad_res.returncode == 0 and os.path.exists(temp_padded_audio) and os.path.getsize(temp_padded_audio) > 0:
                    audio_to_use = temp_padded_audio

            tmp_override = output_path + ".tmp_tts_merge.mp4"
            cmd_override = [
                ffmpeg_bin, "-y",
                "-i", output_path,
                "-i", audio_to_use,
                "-c:v", "copy",
                "-c:a", "aac",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
                tmp_override
            ]
            res_ov = subprocess.run(cmd_override, capture_output=True, text=True, check=False)
            if res_ov.returncode == 0 and os.path.exists(tmp_override) and os.path.getsize(tmp_override) > 0:
                os.replace(tmp_override, output_path)
                dubbed_vi = True

            if temp_padded_audio and os.path.exists(temp_padded_audio):
                try:
                    os.remove(temp_padded_audio)
                except OSError:
                    pass
    elif vietnamese_dubbing and text_for_dubbing:
        dubbed_vi = apply_vietnamese_dubbing(output_path, text_for_dubbing, output_path=output_path)

    # 5. MD5 Modification Pass
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
        if not output_path:
            base, ext = os.path.splitext(video_path)
            output_path = f"{base}_reup{ext}"

        synced_tts_audio: Optional[str] = None

        if cfg.enable_tts:
            try:
                from app.services.pyvideotrans_service import PyVideoTransService
                from app.services.tts_service import tts_service

                pyvideotrans = PyVideoTransService()

                # Step 1: STT
                stt_res = pyvideotrans.speech_to_text(video_path, detect_lang=cfg.source_lang)
                srt_path = stt_res.get("srt_path")
                is_fallback = stt_res.get("status") == "fallback"

                # Step 2: Subtitle Translation (Only for genuine transcribed speech)
                if isinstance(srt_path, str) and os.path.exists(srt_path) and not is_fallback:
                    trans_res = pyvideotrans.translate_subtitles(srt_path, target_lang=cfg.target_lang)
                    raw_trans_srt = trans_res.get("srt_path") if trans_res else None
                    translated_srt: str = (
                        raw_trans_srt if isinstance(raw_trans_srt, str) and raw_trans_srt else srt_path
                    )

                    # Step 3: Synchronized TTS Voiceover
                    from app.config import Settings
                    settings = Settings()
                    settings.ensure_directories()
                    video_name, _ = os.path.splitext(os.path.basename(video_path))
                    tts_out_path = os.path.join(settings.TTS_OUTPUT_DIR, f"{video_name}_synced_tts.wav")
                    from app.services.tts_service import get_audio_duration
                    vid_dur = get_audio_duration(video_path)
                    import asyncio
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            import threading
                            def _run():
                                asyncio.run(tts_service.synthesize_synchronized_tts(
                                    srt_path=translated_srt,
                                    output_audio_path=tts_out_path,
                                    voice=cfg.tts_voice,
                                    lang=cfg.target_lang,
                                    engine=cfg.tts_engine,
                                    total_duration=vid_dur
                                ))
                            t = threading.Thread(target=_run)
                            t.start()
                            t.join()
                        else:
                            loop.run_until_complete(tts_service.synthesize_synchronized_tts(
                                srt_path=translated_srt,
                                output_audio_path=tts_out_path,
                                voice=cfg.tts_voice,
                                lang=cfg.target_lang,
                                engine=cfg.tts_engine,
                                total_duration=vid_dur
                            ))
                    except Exception:
                        asyncio.run(tts_service.synthesize_synchronized_tts(
                            srt_path=translated_srt,
                            output_audio_path=tts_out_path,
                            voice=cfg.tts_voice,
                            lang=cfg.target_lang,
                            engine=cfg.tts_engine,
                            total_duration=vid_dur
                        ))

                    if os.path.exists(tts_out_path) and os.path.getsize(tts_out_path) > 0:
                        synced_tts_audio = tts_out_path
            except Exception as e:
                logger.warning(f"Pipeline TTS generation step skipped or failed: {e}")

        res = process_reup_video(
            input_path=video_path,
            output_path=output_path,
            cfg=cfg,
            tts_audio_override=synced_tts_audio,
            **kwargs
        )

        if synced_tts_audio and os.path.exists(synced_tts_audio):
            try:
                os.remove(synced_tts_audio)
            except OSError:
                pass

        return res["output_path"]

