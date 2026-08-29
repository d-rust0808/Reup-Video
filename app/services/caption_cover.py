"""Caption-cover plates: hide source hardsub with a color bar instead of smeary delogo."""

from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

# ASS colours are &HAABBGGRR
_WHITE = "&H00FFFFFF"
_BLACK = "&H00000000"

COVER_PRESETS: Dict[str, Dict[str, str]] = {
    "black_soft": {
        "box": "black@0.62",
        "primary": _WHITE,
        "outline": _BLACK,
        "back": "&HA0000000",
        "border": "1",
    },
    "white_soft": {
        "box": "white@0.70",
        "primary": _BLACK,
        "outline": _WHITE,
        "back": "&H00FFFFFF",
        "border": "1",
    },
    "black_solid": {
        "box": "black@1",
        "primary": _WHITE,
        "outline": _BLACK,
        "back": _BLACK,
        "border": "3",
    },
    "white_solid": {
        "box": "white@1",
        "primary": _BLACK,
        "outline": _WHITE,
        "back": _WHITE,
        "border": "3",
    },
}

_ALIASES = {
    "": "off",
    "none": "off",
    "off": "off",
    "black": "black_solid",
    "white": "white_solid",
    "đen trong suốt": "black_soft",
    "trắng trong suốt": "white_soft",
    "đen đậm": "black_solid",
    "trắng đậm": "white_solid",
    "white_black": "white_solid",
    "trắng thì chữ đen": "white_solid",
    "white_text_black": "white_solid",
}


def normalize_caption_cover(raw: Optional[str]) -> str:
    key = str(raw or "off").strip().lower()
    key = _ALIASES.get(key, key)
    if key in COVER_PRESETS or key in ("off", "image"):
        return key
    return "off"


def _as_frac(raw: Optional[float], default: float = 0.0) -> float:
    try:
        val = float(raw or 0.0)
    except (TypeError, ValueError):
        return default
    if val > 1.0:
        val = val / 100.0
    return val


def cover_band_height(subtitle_bottom_crop: float, cover: str) -> float:
    if cover == "off":
        return 0.0
    height = _as_frac(subtitle_bottom_crop, 0.0)
    if height <= 0:
        height = 0.18
    return max(0.10, min(0.36, height))


def clamp_color_cover_height(raw: Optional[float], default: float = 0.22) -> float:
    """Color-bar thickness as a fraction of picture height. 0 = off."""
    if raw is None or raw == "":
        return default
    height = _as_frac(raw, 0.0)
    if height < 0:
        return 0.0
    return max(0.0, min(0.36, height))


def clamp_cover_pad(raw: Optional[float]) -> float:
    """Gap from the picture bottom to the color bar, as a fraction of min(width, height)."""
    return max(0.0, min(0.40, _as_frac(raw, 0.0)))


def should_crop_bottom(cover: str, force_crop: bool, bottom: float) -> bool:
    """True when the filtergraph must actually cut pixels off the bottom.

    Color plates paint over the band instead of cutting. A custom logo image
    still crops the source hardsub strip — the logo itself sits under the
    picture on the 9:16 canvas, not on top of it.
    """
    if float(bottom or 0.0) <= 0:
        return False
    kind = normalize_caption_cover(cover)
    if force_crop:
        return True
    if kind == "image":
        return True
    return kind == "off"


def caption_cover_drawbox(
    cover: str,
    height_frac: float,
    cover_y: float = 0.0,
    cover_pad: float = 0.0,
    video_w: Optional[int] = None,
    video_h: Optional[int] = None,
) -> str:
    """Paint a color plate pinned to the picture bottom.

    Height is a fraction of the frame (0.22 = 22% đáy). ``cover_y`` /
    ``cover_pad`` are ignored — only Vietsub moves.
    """
    style = COVER_PRESETS.get(cover)
    if not style:
        return ""
    _ = cover_y
    _ = cover_pad
    h = clamp_color_cover_height(height_frac)
    if h <= 0:
        return ""
    color = style["box"]
    replace = "1" if "@1" in color or color.endswith("@1.0") else "0"
    if video_w and video_h:
        h_px = max(36, int(round(int(video_h) * h)))
        h_px = min(h_px, max(36, int(video_h) - 2))
        y_px = max(0, int(video_h) - h_px)
        return (
            f"drawbox=x=0:y={y_px}:w=iw:h={h_px}:t=fill:color={color}:replace={replace}"
        )
    y = 1.0 - h
    return (
        f"drawbox=x=0:y=ih*{y:.4f}:w=iw:h=ih*{h:.4f}:t=fill:color={color}:replace={replace}"
    )


def clamp_subtitle_y(raw: Optional[float]) -> float:
    """0 = auto (bottom). (0, 1] = cue center as a fraction from the top of the picture."""
    try:
        y = float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if y > 1.0:
        y = y / 100.0
    if y < 0.04:
        return 0.0
    return min(0.96, y)


def caption_layout(
    video_w: int = 1920,
    video_h: int = 1080,
    cover: str = "off",
    band_h: float = 0.18,
    subtitle_y: float = 0.0,
    cover_pad: float = 0.0,
) -> Tuple[int, int]:
    """Pixel (margin_v, font_size) for bottom-centered Vietnamese captions.

    Landscape (and any video without a portrait logo banner) pins to the frame
    bottom. A 9:16 image banner still lifts the cue just above the logo so the
    text is not printed on the plate — that lift is skipped on 16:9, where the
    same 18–30% band would park the cue in the middle of the picture.
    Color covers sit on min(width,height) with optional bottom padding; auto
    Vietsub then rests on that bar.
    """
    width = max(2, int(video_w or 1920))
    height = max(2, int(video_h or 1080))
    pad = max(16, min(36, int(round(height * 0.02))))
    kind = normalize_caption_cover(cover)
    is_portrait = height >= int(width * 1.15)
    band = max(0.0, float(band_h or 0.0))
    if kind == "image" and is_portrait and band > 0:
        band = max(0.10, min(0.36, band))
        margin_v = int(round(height * band)) + pad
    elif kind in COVER_PRESETS:
        h = clamp_color_cover_height(band)
        _ = cover_pad
        if h <= 0:
            margin_v = pad
        else:
            h_px = max(36, int(round(height * h)))
            margin_v = max(8, int(h_px * 0.22))
    else:
        margin_v = pad
    short = min(width, height)
    font_size = max(20, min(40, int(round(short * 0.028))))
    if kind == "image" and is_portrait:
        font_size = max(16, min(28, int(round(short * 0.020))))
    y = clamp_subtitle_y(subtitle_y)
    if y > 0:
        # Alignment=8 (top): MarginV is from the top of the frame to the cue.
        margin_v = int(round(height * y - font_size * 0.55))
        margin_v = max(4, min(height - font_size - 8, margin_v))
    else:
        margin_v = max(4, min(height - font_size - 8, margin_v))
    return margin_v, font_size


def subtitle_force_style(
    cover: str,
    band_h: float = 0.18,
    video_w: Optional[int] = None,
    video_h: Optional[int] = None,
    subtitle_y: float = 0.0,
    cover_pad: float = 0.0,
    subtitle_box_h: float = 0.08,
) -> str:
    width = int(video_w) if video_w else 1920
    height = int(video_h) if video_h else 1080
    y = clamp_subtitle_y(subtitle_y)
    margin_v, font_size = caption_layout(
        width, height, cover, band_h, subtitle_y=y, cover_pad=cover_pad,
    )
    align = 8 if y > 0 else 2
    kind = normalize_caption_cover(cover)
    no_plate = clamp_subtitle_box_h(subtitle_box_h) <= 0
    if no_plate:
        if kind in COVER_PRESETS:
            style = COVER_PRESETS[kind]
            return (
                f"FontName=DejaVu Sans,FontSize={font_size},Bold=1,Alignment={align},"
                f"MarginV={margin_v},MarginL=36,MarginR=36,BorderStyle=1,"
                f"Outline=4,Shadow=0,"
                f"PrimaryColour={style['primary']},OutlineColour={style['outline']},"
                f"BackColour=&HFF000000"
            )
        return (
            f"FontName=DejaVu Sans,FontSize={font_size},Bold=1,Alignment={align},"
            f"MarginV={margin_v},MarginL=36,MarginR=36,BorderStyle=1,"
            f"Outline=4,Shadow=0,"
            f"PrimaryColour={_WHITE},OutlineColour={_BLACK},BackColour=&HFF000000"
        )
    if kind == "image":
        return (
            f"FontName=DejaVu Sans,FontSize={font_size},Bold=1,Alignment={align},"
            f"MarginV={margin_v},MarginL=48,MarginR=48,BorderStyle=3,Outline=2,Shadow=0,"
            f"PrimaryColour={_WHITE},OutlineColour={_BLACK},BackColour=&HA0000000"
        )
    style = COVER_PRESETS.get(kind)
    if not style:
        return (
            f"FontName=DejaVu Sans,FontSize={font_size},Bold=1,Alignment={align},"
            f"MarginV={margin_v},MarginL=36,MarginR=36,BorderStyle=3,Outline=4,Shadow=0,"
            f"PrimaryColour={_WHITE},OutlineColour={_BLACK},BackColour=&HA0000000"
        )
    # BorderStyle=1: the covering plate is a separate drawbox the user sized.
    # BorderStyle=3 would paint a tight pill around the glyphs and hide the plate.
    return (
        f"FontName=DejaVu Sans,FontSize={font_size},Bold=1,Alignment={align},"
        f"MarginV={margin_v},MarginL=36,MarginR=36,BorderStyle=1,"
        f"Outline=3,Shadow=0,"
        f"PrimaryColour={style['primary']},OutlineColour={style['outline']},"
        f"BackColour={style['back']}"
    )


def cover_cue_rgba(cover: str) -> Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int], Tuple[int, int, int, int]]:
    """(text, box, stroke) RGBA so rendered Vietsub matches the caption cover."""
    kind = normalize_caption_cover(cover)
    if kind in ("white_solid", "white_soft"):
        alpha = 255 if kind == "white_solid" else 178
        return (17, 17, 17, 255), (244, 244, 244, alpha), (255, 255, 255, 220)
    if kind in ("black_solid", "black_soft"):
        alpha = 255 if kind == "black_solid" else 158
        return (255, 255, 255, 255), (17, 17, 17, alpha), (0, 0, 0, 220)
    return (255, 255, 255, 255), (0, 0, 0, 170), (0, 0, 0, 220)


def clamp_subtitle_box_w(raw: Optional[float], default: float = 0.88) -> float:
    width = _as_frac(raw, 0.0)
    if width <= 0:
        width = default
    return max(0.40, min(1.0, width))


def clamp_subtitle_box_h(raw: Optional[float], default: float = 0.08) -> float:
    if raw is None or raw == "":
        return default
    height = _as_frac(raw, 0.0)
    if height < 0:
        return 0.0
    return max(0.0, min(0.22, height))


def resolve_cue_y(
    subtitle_y: float = 0.0,
    cover: str = "off",
    band_h: float = 0.22,
    video_w: int = 1920,
    video_h: int = 1080,
) -> float:
    """Cue-center Y from the top of the picture. 0 = auto on the bottom cover."""
    y = clamp_subtitle_y(subtitle_y)
    if y > 0:
        return y
    kind = normalize_caption_cover(cover)
    width = max(2, int(video_w or 1920))
    height = max(2, int(video_h or 1080))
    if kind == "image" and height >= int(width * 1.15) and float(band_h or 0) > 0:
        band = max(0.10, min(0.36, float(band_h)))
        return max(0.08, min(0.94, 1.0 - band - 0.05))
    if kind in COVER_PRESETS:
        h = clamp_color_cover_height(band_h)
        return max(0.08, min(0.94, 1.0 - h * 0.45))
    return 0.90


def subtitle_plate_drawbox(
    cover: str,
    box_w: float,
    box_h: float,
    subtitle_y: float = 0.0,
    band_h: float = 0.22,
    video_w: Optional[int] = None,
    video_h: Optional[int] = None,
) -> str:
    """Independent Vietsub background so a short cue can still cover source hardsubs."""
    kind = normalize_caption_cover(cover)
    style = COVER_PRESETS.get(kind)
    color = style["box"] if style else "black@0.82"
    replace = "1" if "@1" in color or color.endswith("@1.0") else "0"
    bw = clamp_subtitle_box_w(box_w)
    bh = clamp_subtitle_box_h(box_h)
    if bh <= 0:
        return ""
    cy = resolve_cue_y(
        subtitle_y, kind, band_h,
        int(video_w or 1920), int(video_h or 1080),
    )
    if video_w and video_h:
        w_px = max(8, int(round(int(video_w) * bw)))
        h_px = max(8, int(round(int(video_h) * bh)))
        w_px = min(w_px, int(video_w))
        h_px = min(h_px, int(video_h))
        x_px = max(0, (int(video_w) - w_px) // 2)
        y_px = int(round(int(video_h) * cy - h_px / 2))
        y_px = max(0, min(int(video_h) - h_px, y_px))
        return (
            f"drawbox=x={x_px}:y={y_px}:w={w_px}:h={h_px}:t=fill:color={color}:replace={replace}"
        )
    return (
        f"drawbox=x='(iw-iw*{bw:.4f})/2':y='ih*{cy:.4f}-ih*{bh:.4f}/2'"
        f":w='iw*{bw:.4f}':h='ih*{bh:.4f}':t=fill:color={color}:replace={replace}"
    )


def uses_caption_cover(cover: str) -> bool:
    return normalize_caption_cover(cover) != "off"


def resolve_caption_cover_image(image_path: Optional[str] = None, image_url: Optional[str] = None) -> str:
    """Find the banner file from a stored path or /api/v1/studio/overlay/<file> URL."""
    for raw in (image_path, image_url):
        text = str(raw or "").strip()
        if not text:
            continue
        if os.path.isfile(text):
            return os.path.abspath(text)
        name = os.path.basename(text.split("?", 1)[0])
        if not name:
            continue
        try:
            from app.config import settings
            studio = os.path.join(str(settings.CHANNELS_DIR), "studio")
        except Exception:
            studio = os.path.join("data", "channels", "studio")
        for folder in (studio, os.path.abspath(studio)):
            cand = os.path.join(folder, name)
            if os.path.isfile(cand):
                return os.path.abspath(cand)
    return ""


def drop_delogo_nodes(vf_chunk: str) -> Tuple[str, ...]:
    """Never let ffmpeg delogo into the master graph — it smears scenery."""
    parts = []
    for node in (vf_chunk or "").split(","):
        item = node.strip()
        if not item:
            continue
        if item.startswith("delogo="):
            continue
        parts.append(item)
    return tuple(parts)
