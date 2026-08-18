"""
Audio Processing, Vocal Separation & Alignment Package.
========================================================
Integrates Demucs vocal separation, FFmpeg vocal muting filtergraphs,
audio track mixing, pitch/speed scaling, and video rendering.
"""

from app.services.audio_service import (
    find_ffmpeg_binary,
    find_ffprobe_binary,
    extract_audio_stream,
    apply_ffmpeg_vocal_mute,
    process_vocal_muting,
    mix_audio_tracks
)
from app.services.reup_service import ReupService

__all__ = [
    "find_ffmpeg_binary",
    "find_ffprobe_binary",
    "extract_audio_stream",
    "apply_ffmpeg_vocal_mute",
    "process_vocal_muting",
    "mix_audio_tracks",
    "ReupService"
]
