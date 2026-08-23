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
        else:
            w = max(0.04, min(0.80, w))
            x = max(0.0, min(max(0.0, 1.0 - w), x))
            y = max(0.0, min(0.95, y))
        out.append({
            "id": d.get("id") or os.path.basename(path),
            "image_path": os.path.abspath(path),
            "url": d.get("url") or "",
            "filename": d.get("filename") or os.path.basename(path),
            "kind": kind,
            "x": x,
            "y": y,
            "w": w,
            "opacity": max(0.05, min(1.0, opacity)),
        })
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
        if kind == "frame" or w >= 0.97:
            parts.append(
                f"[{in_idx}:v]format=rgba,colorchannelmixer=aa={op:.3f},"
                f"scale={mw}:{mh}:force_original_aspect_ratio=disable[{lg}]"
            )
            parts.append(f"[{prev}][{lg}]overlay=0:0:{persist}[{nxt}]")
        else:
            wf = max(0.04, min(0.80, w))
            sw = max(16, int(mw * wf) // 2 * 2)
            parts.append(
                f"[{in_idx}:v]format=rgba,colorchannelmixer=aa={op:.3f},scale={sw}:-1[{lg}]"
            )
            parts.append(
                f"[{prev}][{lg}]overlay=x='W*{x:.4f}':y='H*{y:.4f}':{persist}[{nxt}]"
            )
        prev = nxt
        paths.append(ov["image_path"])

    return fc + ";" + ";".join(parts), paths


def overlay_input_args(paths: Sequence[str]) -> List[str]:
    """Still images as extra inputs. Overlay repeatlast keeps them on for the whole clip."""
    args: List[str] = []
    for p in paths:
        args.extend(["-i", p])
    return args
