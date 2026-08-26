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
        "border": "1",
    },
    "white_solid": {
        "box": "white@1",
        "primary": _BLACK,
        "outline": _WHITE,
        "back": _WHITE,
        "border": "1",
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


def cover_band_height(subtitle_bottom_crop: float, cover: str) -> float:
    if cover == "off":
        return 0.0
    height = float(subtitle_bottom_crop or 0.0)
    if height <= 0:
        height = 0.18
    return max(0.10, min(0.36, height))


def should_crop_bottom(cover: str, force_crop: bool, bottom: float) -> bool:
    """True when the filtergraph must actually cut pixels off the bottom.

    Image cover keeps 9:16 and paints a banner instead of cropping.
    """
    if float(bottom or 0.0) <= 0:
        return False
    kind = normalize_caption_cover(cover)
    if kind == "image":
        return False
    if force_crop:
        return True
    return kind == "off"


def caption_cover_drawbox(cover: str, height_frac: float) -> str:
    style = COVER_PRESETS.get(cover)
    if not style:
        return ""
    h = max(0.10, min(0.36, float(height_frac or 0.18)))
    y = 1.0 - h
    color = style["box"]
    replace = "1" if "@1" in color or color.endswith("@1.0") else "0"
    return (
        f"drawbox=x=0:y=ih*{y:.4f}:w=iw:h=ih*{h:.4f}:t=fill:color={color}:replace={replace}"
    )


def subtitle_force_style(cover: str, band_h: float = 0.18) -> str:
    if cover == "image":
        # Sit Vietsub on the picture, just above the logo banner — small type, opaque plate.
        h = max(0.10, min(0.36, float(band_h or 0.22)))
        margin_v = max(40, int(round(1080 * h)) + 16)
        return (
            "FontName=DejaVu Sans,FontSize=16,Bold=1,Alignment=2,"
            f"MarginV={margin_v},MarginL=48,MarginR=48,BorderStyle=3,Outline=2,Shadow=0,"
            f"PrimaryColour={_WHITE},OutlineColour={_BLACK},BackColour=&HA0000000"
        )
    style = COVER_PRESETS.get(cover)
    if not style:
        return (
            "FontName=DejaVu Sans,FontSize=18,Bold=1,Alignment=2,"
            "MarginV=14,MarginL=36,MarginR=36,BorderStyle=3,Outline=4,Shadow=0,"
            f"PrimaryColour={_WHITE},OutlineColour={_BLACK},BackColour=&HA0000000"
        )
    return (
        "FontName=DejaVu Sans,FontSize=18,Bold=1,Alignment=2,"
        "MarginV=18,MarginL=36,MarginR=36,BorderStyle=1,Outline=3,Shadow=0,"
        f"PrimaryColour={style['primary']},OutlineColour={style['outline']},"
        f"BackColour={style['back']}"
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
