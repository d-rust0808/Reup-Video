"""
Isochronous lip-sync for dubbed reup.
=====================================
Does not warp pixels (Wav2Lip needs a GPU). Matches dubbed speech to the
original mouth window:

1. Word-level STT cues (pause / punctuation splits)
2. Compact translation to the source duration budget
3. Edge-TTS speaking rate aimed at that window
4. Rubberband formant-preserving stretch to the exact length
5. Hard trim so a line never spills into the next mouth flap
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# Edge neural Vietnamese ~11 characters/sec at +0% (spaces included)
VI_CHARS_PER_SEC = 11.0
FILLERS = {
    "thì", "à", "ạ", "ơi", "nhé", "nhỉ", "cái", "rất", "là", "một", "chút",
    "nào", "đi", "các", "bạn", "ạ,", "nha", "á", "ừ", "ờ",
}


def estimate_tts_duration(text: str, chars_per_sec: float = VI_CHARS_PER_SEC) -> float:
    n = len(re.sub(r"\s+", " ", (text or "").strip()))
    return max(0.28, n / max(4.0, chars_per_sec))


def compact_for_duration(text: str, duration: float, chars_per_sec: float = VI_CHARS_PER_SEC) -> str:
    """Shorten a Vietnamese line so it can be spoken inside `duration` seconds."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return raw
    budget = max(8, int(round(max(0.35, duration) * 16.5)))
    if len(raw) <= budget:
        return raw
    tokens = raw.split(" ")
    kept = [t for t in tokens if t.lower().strip(" ,.?!") not in FILLERS]
    if not kept:
        kept = tokens
    compact = " ".join(kept)
    if len(compact) <= budget:
        return compact
    cut = compact[:budget]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.strip(" ,;:") or compact[:budget]


def edge_rate_tag(natural_dur: float, target_dur: float) -> str:
    """Edge-TTS rate string that aims to fit `target_dur`."""
    td = max(0.18, float(target_dur))
    nd = max(0.18, float(natural_dur))
    pct = (nd / td - 1.0) * 100.0
    pct = max(-35.0, min(85.0, pct))
    return f"{pct:+.0f}%"


def regroup_words_to_cues(
    words: Iterable[Dict[str, Any]],
    max_dur: float = 2.4,
    max_words: int = 8,
    gap: float = 0.28,
) -> List[Dict[str, Any]]:
    """Pack word-level timestamps into lip-sync cues that follow mouth flaps."""
    cues: List[Dict[str, Any]] = []
    buf: List[Dict[str, Any]] = []

    def _flush():
        if not buf:
            return
        start = float(buf[0]["start"])
        end = float(buf[-1]["end"])
        if end <= start:
            end = start + 0.35
        text = " ".join(w["text"] for w in buf).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            cues.append({
                "index": len(cues) + 1,
                "start_time": start,
                "end_time": end,
                "duration": end - start,
                "text": text,
            })
        buf.clear()

    for w in words:
        token = re.sub(r"\s+", " ", str(w.get("text") or "").strip())
        if not token:
            continue
        start = float(w.get("start") or 0.0)
        end = float(w.get("end") or (start + 0.2))
        if end <= start:
            end = start + 0.18
        item = {"text": token, "start": start, "end": end}
        if not buf:
            buf.append(item)
            continue
        prev_end = float(buf[-1]["end"])
        dur = end - float(buf[0]["start"])
        punct = bool(re.search(r"[。！？!?，,;；]$", buf[-1]["text"]))
        if (start - prev_end) >= gap or punct or dur >= max_dur or len(buf) >= max_words:
            _flush()
            buf.append(item)
        else:
            buf.append(item)
    _flush()
    return cues


def write_cues_srt(srt_path: str, cues: List[Dict[str, Any]]) -> int:
    from app.services.tts_service import format_srt_timestamp

    os.makedirs(os.path.dirname(os.path.abspath(srt_path)) or ".", exist_ok=True)
    lines = []
    n = 0
    for cue in cues:
        text = (cue.get("text") or "").strip()
        if not text:
            continue
        n += 1
        start = float(cue.get("start_time") or 0.0)
        end = float(cue.get("end_time") or (start + 0.4))
        lines.append(f"{n}\n{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n{text}\n")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    return n


def rubberband_fit(input_path: str, output_path: str, target_dur: float) -> bool:
    """Time-stretch with formants preserved so pitch stays human."""
    from app.services.audio_service import find_ffmpeg_binary
    from app.services.tts_service import get_audio_duration

    ffmpeg_bin = find_ffmpeg_binary()
    if not ffmpeg_bin or not os.path.exists(input_path):
        return False
    actual = get_audio_duration(input_path)
    td = max(0.12, float(target_dur))
    if actual <= 0.05:
        return False
    tempo = actual / td
    tempo = max(0.70, min(1.60, tempo))
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    af = (
        f"rubberband=tempo={tempo:.4f}:pitch=1:formant=preserved:transients=smooth,"
        f"atrim=0:{td:.3f},apad=pad_dur=0.02"
    )
    cmd = [
        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-i", input_path, "-af", af, "-ar", "44100", "-ac", "1",
        output_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 256:
        return True
    # Fallback: atempo chain
    from app.services.tts_service import scale_audio_speed_ffmpeg
    return scale_audio_speed_ffmpeg(input_path, output_path, tempo)


def fit_clip_to_window(input_path: str, output_path: str, target_dur: float) -> bool:
    """Rubberband then hard-trim so the clip equals the mouth window."""
    from app.services.audio_service import find_ffmpeg_binary
    from app.services.tts_service import get_audio_duration

    if rubberband_fit(input_path, output_path, target_dur):
        return True
    ffmpeg_bin = find_ffmpeg_binary()
    if not ffmpeg_bin:
        return False
    actual = get_audio_duration(input_path)
    td = max(0.12, float(target_dur))
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    if actual > td + 0.04:
        cmd = [
            ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-i", input_path, "-af", f"atrim=0:{td:.3f}", "-ar", "44100",
            output_path,
        ]
    else:
        pad = max(0.0, td - actual)
        cmd = [
            ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-i", input_path, "-af", f"apad=pad_dur={pad:.3f}", "-t", f"{td:.3f}",
            "-ar", "44100", output_path,
        ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 256


def lipsync_prepare_segment(text: str, duration: float) -> Dict[str, Any]:
    """Return compacted text + Edge rate for one mouth window."""
    spoken = compact_for_duration(text, duration)
    natural = estimate_tts_duration(spoken)
    rate = edge_rate_tag(natural, duration)
    return {
        "text": spoken,
        "rate": rate,
        "natural_dur": natural,
        "target_dur": max(0.18, float(duration)),
    }
