"""Libass-free Vietnamese hardsub: render each SRT cue to a transparent PNG with
Pillow and composite via FFmpeg `overlay`. Used when the ffmpeg binary was built
without libass (no `subtitles=`/`ass=` filter) — common on slim Homebrew builds.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
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
    srt_path: str,
    video_w: int,
    video_h: int,
    out_dir: str,
    *,
    cover_band: float = 0.0,
    cover_kind: str = "off",
    subtitle_y: float = 0.0,
    cover_pad: float = 0.0,
    subtitle_box_w: float = 0.88,
    subtitle_box_h: float = 0.08,
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
    from app.services.vietsub_rules import split_cues_for_display

    segs = split_cues_for_display(segs)

    os.makedirs(out_dir, exist_ok=True)
    band = max(0.0, min(0.36, float(cover_band or 0.0)))
    cover = (cover_kind or "off").strip().lower()
    from app.services.caption_cover import (
        caption_layout,
        clamp_subtitle_box_h,
        clamp_subtitle_box_w,
        clamp_subtitle_y,
        cover_cue_rgba,
        normalize_caption_cover,
        resolve_cue_y,
    )

    cover = normalize_caption_cover(cover)
    has_band = band > 0 and cover not in ("", "off")
    cue_y = clamp_subtitle_y(subtitle_y)
    text_rgba, box_rgba, stroke_rgba = cover_cue_rgba(cover)
    margin_v, font_size = caption_layout(
        video_w, video_h, cover, band if has_band else 0.0,
        subtitle_y=cue_y, cover_pad=cover_pad,
    )
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception as e:
        logger.warning(f"Font load failed ({e})")
        return []

    box_w = clamp_subtitle_box_w(subtitle_box_w)
    box_h = clamp_subtitle_box_h(subtitle_box_h)
    cue_center = resolve_cue_y(
        cue_y, cover, band if has_band else 0.0, video_w, video_h,
    )
    margin_x = int(video_w * (1.0 - box_w) / 2)
    max_text_w = max(32, int(video_w * box_w) - 2 * int(font_size * 0.45))
    line_gap = int(font_size * 0.28)
    pad_x, pad_y = int(font_size * 0.45), int(font_size * 0.28)
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
        plate_w = max(int(video_w * box_w), block_w + 2 * pad_x)
        plate_h = max(int(video_h * box_h), block_h + 2 * pad_y) if box_h > 0 else (block_h + 2 * pad_y)
        plate_x = (video_w - plate_w) // 2
        plate_y = int(round(video_h * cue_center - plate_h / 2))
        plate_y = max(6, min(plate_y, video_h - plate_h - 2))
        if box_h > 0:
            radius = max(6, int(font_size * 0.35))
            d.rounded_rectangle(
                [plate_x, plate_y, plate_x + plate_w, plate_y + plate_h],
                radius=radius, fill=box_rgba,
            )

        y = plate_y + pad_y
        for i, ln in enumerate(lines):
            lw = widths[i]
            x = (video_w - lw) // 2
            d.text(
                (x, y), ln, font=font, fill=text_rgba,
                stroke_width=stroke_w, stroke_fill=stroke_rgba,
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


def write_overlay_concat_list(
    overlays: List[Dict[str, Any]],
    concat_path: str,
    transparent_path: str,
) -> Optional[str]:
    """Write a concat demuxer list that holds each cue PNG for its on-screen duration."""
    if not overlays:
        return None
    entries: List[Tuple[str, float]] = []
    cursor = 0.0
    for overlay in overlays:
        start = max(cursor, float(overlay["start"]))
        end = max(start + 0.04, float(overlay["end"]))
        if start > cursor:
            entries.append((transparent_path, start - cursor))
        entries.append((overlay["png"], end - start))
        cursor = end
    entries.append((transparent_path, 0.04))
    os.makedirs(os.path.dirname(os.path.abspath(concat_path)) or ".", exist_ok=True)
    with open(concat_path, "w", encoding="utf-8") as concat_file:
        for path, duration in entries:
            escaped = os.path.abspath(path).replace("'", "'\\''")
            concat_file.write(f"file '{escaped}'\n")
            concat_file.write(f"duration {duration:.3f}\n")
        escaped = os.path.abspath(entries[-1][0]).replace("'", "'\\''")
        concat_file.write(f"file '{escaped}'\n")
    return concat_path


def render_srt_to_concat_track(
    srt_path: str,
    video_w: int,
    video_h: int,
    concat_path: str,
    *,
    cover_band: float = 0.0,
    cover_kind: str = "off",
    subtitle_y: float = 0.0,
    cover_pad: float = 0.0,
    subtitle_box_w: float = 0.88,
    subtitle_box_h: float = 0.08,
) -> Optional[str]:
    """Render cues to PNGs and a concat list. Keeps stills on disk for the encode."""
    from PIL import Image

    frame_dir = os.path.dirname(os.path.abspath(concat_path)) or "."
    overlays = render_srt_to_overlays(
        srt_path,
        video_w,
        video_h,
        frame_dir,
        cover_band=cover_band,
        cover_kind=cover_kind,
        subtitle_y=subtitle_y,
        cover_pad=cover_pad,
        subtitle_box_w=subtitle_box_w,
        subtitle_box_h=subtitle_box_h,
    )
    if not overlays:
        return None
    transparent_path = os.path.join(frame_dir, "transparent.png")
    Image.new("RGBA", (video_w, video_h), (0, 0, 0, 0)).save(transparent_path)
    return write_overlay_concat_list(overlays, concat_path, transparent_path)


def render_srt_to_apng(
    srt_path: str,
    video_w: int,
    video_h: int,
    output_path: str,
    *,
    cover_band: float = 0.0,
    cover_kind: str = "off",
    subtitle_y: float = 0.0,
    cover_pad: float = 0.0,
    subtitle_box_w: float = 0.88,
    subtitle_box_h: float = 0.08,
) -> Optional[str]:
    """Render all timed cues into one transparent APNG subtitle track."""
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="visub_frames_") as frame_dir:
        overlays = render_srt_to_overlays(
            srt_path,
            video_w,
            video_h,
            frame_dir,
            cover_band=cover_band,
            cover_kind=cover_kind,
            subtitle_y=subtitle_y,
            cover_pad=cover_pad,
            subtitle_box_w=subtitle_box_w,
            subtitle_box_h=subtitle_box_h,
        )
        if not overlays:
            return None

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None
        transparent_path = os.path.join(frame_dir, "transparent.png")
        Image.new("RGBA", (video_w, video_h), (0, 0, 0, 0)).save(transparent_path)
        entries: List[Tuple[str, float]] = []
        cursor = 0.0
        for overlay in overlays:
            start = max(cursor, float(overlay["start"]))
            end = max(start + 0.04, float(overlay["end"]))
            if start > cursor:
                entries.append((transparent_path, start - cursor))
            entries.append((overlay["png"], end - start))
            cursor = end
        entries.append((transparent_path, 0.04))

        concat_path = os.path.join(frame_dir, "frames.txt")
        with open(concat_path, "w", encoding="utf-8") as concat_file:
            for path, duration in entries:
                escaped = os.path.abspath(path).replace("'", "'\\''")
                concat_file.write(f"file '{escaped}'\n")
                concat_file.write(f"duration {duration:.3f}\n")
            escaped = os.path.abspath(entries[-1][0]).replace("'", "'\\''")
            concat_file.write(f"file '{escaped}'\n")

        os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
        result = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_path,
                "-vf",
                "format=rgba",
                "-plays",
                "1",
                "-f",
                "apng",
                output_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.warning("Timed subtitle APNG failed: %s", (result.stderr or "")[-400:])
            return None
    return output_path if os.path.exists(output_path) and os.path.getsize(output_path) > 0 else None


def append_timed_subtitle_filter(
    filter_complex: str,
    input_index: int,
) -> str:
    """Overlay a single timed APNG track after the existing video filters."""
    if "[v_out]" not in filter_complex:
        return filter_complex
    base = filter_complex.replace("[v_out]", "[v_sub_base]", 1)
    return (
        f"{base};[{input_index}:v]format=rgba[v_sub_track];"
        "[v_sub_base][v_sub_track]overlay=0:0:eof_action=pass:repeatlast=0[v_out]"
    )


def inject_timed_overlay_before_speed(
    filter_complex: str,
    input_index: int,
) -> str:
    """Composite the timed APNG on the source clock, then speed the picture+subs together.

    Scaling the SRT and overlaying *after* setpts makes captions race ahead when the
    encoder (often VideoToolbox) ignores setpts and keeps the original frame timing.
    """
    if "[v_out]" not in filter_complex:
        return filter_complex
    marker = ",setpts=PTS/"
    split_at = filter_complex.find(marker)
    if split_at < 0:
        return append_timed_subtitle_filter(filter_complex, input_index)
    v_out_at = filter_complex.find("[v_out]", split_at)
    if v_out_at < 0:
        return append_timed_subtitle_filter(filter_complex, input_index)
    pre = filter_complex[:split_at]
    sped = filter_complex[split_at + 1:v_out_at]  # setpts=...,fps=...
    rest = filter_complex[v_out_at + len("[v_out]"):]
    return (
        f"{pre}[v_pre];"
        f"[{input_index}:v]format=rgba[v_sub_track];"
        "[v_pre][v_sub_track]overlay=0:0:eof_action=pass:repeatlast=0[v_mid];"
        f"[v_mid]{sped}[v_out]{rest}"
    )
