"""Libass-free Vietnamese hardsub: render each SRT cue to a transparent PNG with
Pillow and composite via FFmpeg `overlay`. Used when the ffmpeg binary was built
without libass (no `subtitles=`/`ass=` filter) — common on slim Homebrew builds.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
]


def find_overlay_font() -> Optional[str]:
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def _wrap_lines(draw, text: str, font, max_w: int) -> List[str]:
    words = text.split()
    if not words:
        return []
    lines: List[str] = []
    cur = words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def render_srt_to_overlays(
    srt_path: str, video_w: int, video_h: int, out_dir: str
) -> List[Dict[str, Any]]:
    """Render each cue to a full-frame transparent PNG. Returns [{png,start,end}]."""
    from PIL import Image, ImageDraw, ImageFont
    from app.services.tts_service import parse_srt_segments

    font_path = find_overlay_font()
    if not font_path:
        logger.warning("No Unicode font found for subtitle overlay")
        return []

    segs = parse_srt_segments(srt_path)
    if not segs:
        return []

    os.makedirs(out_dir, exist_ok=True)
    font_size = max(20, int(round(video_h * 0.042)))
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception as e:
        logger.warning(f"Font load failed ({e})")
        return []

    margin_x = int(video_w * 0.05)
    margin_v = int(video_h * 0.05)
    max_text_w = video_w - 2 * margin_x
    line_gap = int(font_size * 0.28)
    pad_x, pad_y = int(font_size * 0.5), int(font_size * 0.32)
    stroke_w = max(2, font_size // 10)

    scratch = Image.new("RGBA", (8, 8))
    measure = ImageDraw.Draw(scratch)
    overlays: List[Dict[str, Any]] = []

    for idx, seg in enumerate(segs):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines = _wrap_lines(measure, text, font, max_text_w)
        if not lines:
            continue

        heights, widths = [], []
        for ln in lines:
            box = measure.textbbox((0, 0), ln, font=font, stroke_width=stroke_w)
            widths.append(box[2] - box[0])
            heights.append(box[3] - box[1])
        block_w = max(widths)
        line_h = max(heights)
        block_h = line_h * len(lines) + line_gap * (len(lines) - 1)

        img = Image.new("RGBA", (video_w, video_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        plate_w = block_w + 2 * pad_x
        plate_h = block_h + 2 * pad_y
        plate_x = (video_w - plate_w) // 2
        plate_y = video_h - margin_v - plate_h
        radius = max(6, int(font_size * 0.35))
        d.rounded_rectangle(
            [plate_x, plate_y, plate_x + plate_w, plate_y + plate_h],
            radius=radius, fill=(0, 0, 0, 170),
        )

        y = plate_y + pad_y
        for i, ln in enumerate(lines):
            lw = widths[i]
            x = (video_w - lw) // 2
            d.text(
                (x, y), ln, font=font, fill=(255, 255, 255, 255),
                stroke_width=stroke_w, stroke_fill=(0, 0, 0, 220),
            )
            y += line_h + line_gap

        png = os.path.join(out_dir, f"sub_{idx:05d}.png")
        img.save(png)
        overlays.append({
            "png": png,
            "start": float(seg["start_time"]),
            "end": float(seg["end_time"]),
        })

    return overlays


def build_overlay_filter(overlays: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """Chain one `overlay` per cue, gated by enable=between(t,start,end).
    Returns (filter_complex, extra_input_args)."""
    inputs: List[str] = []
    parts: List[str] = []
    cur = "[0:v]"
    for i, ov in enumerate(overlays):
        inputs.extend(["-i", ov["png"]])
        in_idx = i + 1  # 0 is the base video
        out_lbl = "[v_out]" if i == len(overlays) - 1 else f"[ov{i}]"
        parts.append(
            f"{cur}[{in_idx}:v]overlay=0:0:enable='between(t,{ov['start']:.3f},{ov['end']:.3f})'{out_lbl}"
        )
        cur = f"[ov{i}]"
    return ";".join(parts), inputs
