"""
Channel branding overlays — logos and full-frame khung burned onto reup video.

Still images are 1-frame streams. overlay eof_action=repeat (repeatlast=1) keeps
the last overlay frame for the entire video duration so logos stay on-screen
from first frame to last.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


def _image_aspect(path: str) -> float:
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        return float(w) / float(h) if h else 0.0
    except Exception:
        return 0.0


def _as_dict(item: Any) -> Dict[str, Any]:
    if item is None:
        return {}
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump()
    try:
        return dict(item)
    except Exception:
        return {}


def normalize_overlays(raw: Optional[Sequence[Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not raw:
        return out
    for item in raw:
        d = _as_dict(item)
        path = (d.get("image_path") or d.get("path") or "").strip()
        if not path or not os.path.exists(path):
            continue
        kind = (d.get("kind") or "logo").lower().strip()
        if kind in ("frame", "khung", "border"):
            kind = "frame"
        elif kind == "overlay":
            kind = "overlay"
        elif kind in ("banner", "caption"):
            kind = "banner"
        else:
            kind = "logo"
        try:
            x = float(d.get("x", 0.04 if kind == "logo" else 0.0))
            y = float(d.get("y", 0.04 if kind == "logo" else 0.0))
            w = float(d.get("w", 0.18 if kind == "logo" else 1.0))
            opacity = float(d.get("opacity", 1.0))
        except (TypeError, ValueError):
            continue
        if kind == "frame":
            x, y, w = 0.0, 0.0, 1.0
        elif kind in ("banner", "caption"):
            kind = "banner"
            w = 1.0
            x = 0.0
        elif kind == "logo" and w >= 0.55 and _image_aspect(path) >= 2.2:
            # Wide caption plates must never sit as a corner/top logo.
            kind = "banner"
            w = 1.0
            x = 0.0
        elif kind == "overlay":
            w = max(0.04, min(3.0, w))
            x = max(-2.0, min(1.0, x))
            y = max(-2.0, min(1.0, y))
        else:
            w = max(0.04, min(0.80, w))
            x = max(0.0, min(max(0.0, 1.0 - w), x))
            y = max(0.0, min(0.95, y))
        band_h = 0.22
        try:
            band_h = float(d.get("band_h") or d.get("h") or 0.22)
        except (TypeError, ValueError):
            band_h = 0.22
        if kind == "banner":
            band_h = max(0.10, min(0.36, band_h))
            y = 1.0 - band_h
        out.append({
            "id": d.get("id") or os.path.basename(path),
            "image_path": os.path.abspath(path),
            "url": d.get("url") or "",
            "filename": d.get("filename") or os.path.basename(path),
            "kind": kind,
            "x": x,
            "y": y,
            "w": w,
            "band_h": band_h,
            "opacity": max(0.05, min(1.0, opacity)),
        })
    return out


def ensure_caption_cover_banner(
    overlays: Optional[Sequence[Any]],
    image_path: str,
    band_h: float,
) -> List[Dict[str, Any]]:
    """Force the caption-cover image to a bottom banner; never treat it as a corner logo."""
    items = normalize_overlays(overlays)
    path = os.path.abspath(image_path or "")
    if not path or not os.path.isfile(path):
        return items
    band = max(0.10, min(0.36, float(band_h or 0.22)))
    banner = {
        "id": os.path.basename(path),
        "image_path": path,
        "url": "",
        "filename": os.path.basename(path),
        "kind": "banner",
        "x": 0.0,
        "y": 1.0 - band,
        "w": 1.0,
        "band_h": band,
        "opacity": 1.0,
    }
    out: List[Dict[str, Any]] = []
    replaced = False
    for item in items:
        existing = os.path.abspath(item.get("image_path") or "")
        if existing == path:
            merged = {**item, **banner}
            if item.get("url"):
                merged["url"] = item["url"]
            out.append(merged)
            replaced = True
        else:
            out.append(item)
    if not replaced:
        out.append(banner)
    return out


def append_overlay_filter(
    filter_complex: str,
    overlays: Sequence[Any],
    first_overlay_index: int,
    main_size: Optional[Tuple[int, int]] = None,
) -> Tuple[str, List[str]]:
    """
    Chains overlay nodes after the existing [v_out] label.
    Stills use repeatlast (no scale2ref, no -loop) so FFmpeg 7 keeps full duration.
    """
    items = normalize_overlays(overlays)
    if not items:
        return filter_complex, []

    if "[v_out]" not in filter_complex:
        logger.warning("append_overlay_filter: [v_out] label missing, overlays skipped")
        return filter_complex, []

    mw, mh = main_size if main_size and main_size[0] > 0 and main_size[1] > 0 else (1080, 1920)
    fc = filter_complex.replace("[v_out]", "[v_base]", 1)
    prev = "v_base"
    parts: List[str] = []
    paths: List[str] = []
    persist = "format=auto:eof_action=repeat:repeatlast=1"

    for i, ov in enumerate(items):
        in_idx = first_overlay_index + i
        lg = f"lg{i}"
        nxt = "v_out" if i == len(items) - 1 else f"vov{i}"
        op = ov["opacity"]
        kind = ov["kind"]
        x, y, w = ov["x"], ov["y"], ov["w"]
        if kind == "frame":
            parts.append(
                f"[{in_idx}:v]format=rgba,colorchannelmixer=aa={op:.3f},"
                f"scale={mw}:{mh}:force_original_aspect_ratio=disable[{lg}]"
            )
            parts.append(f"[{prev}][{lg}]overlay=0:0:{persist}[{nxt}]")
        elif kind == "banner":
            bh = max(16, int(mh * float(ov.get("band_h") or 0.22)) // 2 * 2)
            parts.append(
                f"[{in_idx}:v]format=rgba,scale={mw}:{bh}:force_original_aspect_ratio=increase,"
                f"crop={mw}:{bh},colorchannelmixer=aa={op:.3f}[{lg}]"
            )
            parts.append(f"[{prev}][{lg}]overlay=0:H-h:{persist}[{nxt}]")
        else:
            wf = max(0.04, min(3.0 if kind == "overlay" else 0.80, w))
            sw = max(16, int(mw * wf) // 2 * 2)
            parts.append(
                f"[{in_idx}:v]format=rgba,colorchannelmixer=aa={op:.3f},scale={sw}:-1[{lg}]"
            )
            parts.append(
                f"[{prev}][{lg}]overlay=x='W*{x:.4f}':y='H*{y:.4f}':{persist}[{nxt}]"
            )
        prev = nxt
        paths.append(ov["image_path"])
        logger.info("overlay[%d] kind=%s band_h=%s path=%s", i, kind, ov.get("band_h"), ov["image_path"])

    return fc + ";" + ";".join(parts), paths


def overlay_input_args(paths: Sequence[str]) -> List[str]:
    """Still images as extra inputs. Overlay repeatlast keeps them on for the whole clip."""
    args: List[str] = []
    for p in paths:
        args.extend(["-i", p])
    return args
