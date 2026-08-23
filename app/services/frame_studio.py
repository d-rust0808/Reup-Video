"""Render decorative video frames (khung) as transparent PNGs to burn onto reups."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    cv2 = None
    HAS_CV2 = False

FRAME_DIR = os.path.abspath(os.path.join("data", "frames"))

PRESETS: List[Dict[str, Any]] = [
    {"id": "cinema", "label": "Điện ảnh", "desc": "Viền đen dày + chỉ trắng", "color": "#000000", "accent": "#FFFFFF", "thickness": 28, "inner": 3, "bottom_extra": 0},
    {"id": "gold", "label": "Gold", "desc": "Viền vàng gold sang", "color": "#C9A227", "accent": "#1A1204", "thickness": 22, "inner": 3, "bottom_extra": 0},
    {"id": "neon", "label": "Neon", "desc": "Tím neon TikTok", "color": "#7C3AED", "accent": "#F5F3FF", "thickness": 18, "inner": 2, "bottom_extra": 0},
    {"id": "polaroid", "label": "Polaroid", "desc": "Viền trắng, đáy dày", "color": "#F8F5F0", "accent": "#D4D0C8", "thickness": 36, "inner": 2, "bottom_extra": 90},
    {"id": "vintage", "label": "Vintage", "desc": "Nâu cổ + kem", "color": "#3C2A1E", "accent": "#E8D5A3", "thickness": 24, "inner": 4, "bottom_extra": 0},
    {"id": "slim", "label": "Mỏng", "desc": "Viền trắng mảnh", "color": "#FFFFFF", "accent": "#111111", "thickness": 8, "inner": 2, "bottom_extra": 0},
    {"id": "rose", "label": "Hồng", "desc": "Viền hồng reels", "color": "#E11D48", "accent": "#FFF1F2", "thickness": 18, "inner": 2, "bottom_extra": 0},
]


def _hex_to_bgra(hex_color: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    s = (hex_color or "#000000").strip().lstrip("#")
    if s.startswith("0x"):
        s = s[2:]
    if len(s) == 6:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        return b, g, r, int(alpha)
    return 0, 0, 0, int(alpha)


def render_frame_png(
    preset_id: str = "cinema",
    color: str = "",
    accent: str = "",
    thickness: int = 0,
    width: int = 1080,
    height: int = 1920,
    out_name: str = "current.png",
) -> str:
    if not HAS_CV2:
        raise RuntimeError("OpenCV is required to render frames")
    preset = next((p for p in PRESETS if p["id"] == preset_id), PRESETS[0])
    col = color or preset["color"]
    acc = accent or preset["accent"]
    thick = int(thickness or preset["thickness"] or 20)
    thick = max(4, min(80, thick))
    inner = int(preset.get("inner") or 2)
    extra_b = int(preset.get("bottom_extra") or 0)

    os.makedirs(FRAME_DIR, exist_ok=True)
    img = np.zeros((height, width, 4), dtype=np.uint8)
    outer = _hex_to_bgra(col, 255)
    inner_c = _hex_to_bgra(acc, 255)

    # Full-bleed outer border (including extra polaroid bottom)
    cv2.rectangle(img, (0, 0), (width - 1, height - 1), outer, thickness=thick)
    if extra_b > 0:
        img[height - extra_b : height, :, :] = outer

    inset = thick
    cv2.rectangle(
        img,
        (inset, inset),
        (width - 1 - inset, height - 1 - inset - extra_b),
        inner_c,
        thickness=max(1, inner),
    )
    # punch the interior back to transparent (keep only the ring)
    x1, y1 = inset + inner, inset + inner
    x2, y2 = width - inset - inner, height - inset - inner - extra_b
    if x2 > x1 + 8 and y2 > y1 + 8:
        img[y1:y2, x1:x2] = (0, 0, 0, 0)

    dest = os.path.join(FRAME_DIR, out_name)
    cv2.imwrite(dest, img)
    if not os.path.exists(dest) or os.path.getsize(dest) == 0:
        raise RuntimeError("Failed to write frame PNG")
    return dest


def overlay_payload(path: str, preset_id: str = "cinema") -> Dict[str, Any]:
    abs_p = os.path.abspath(path)
    name = os.path.basename(abs_p)
    return {
        "id": f"frame_{preset_id}",
        "image_path": abs_p,
        "url": f"/api/v1/frames/file/{name}",
        "filename": name,
        "kind": "frame",
        "x": 0,
        "y": 0,
        "w": 1,
        "opacity": 1,
    }
