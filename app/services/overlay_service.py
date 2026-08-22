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
) -> Tuple[str, List[str]]:
    """
    Chains overlay nodes after the existing [v_out] label.
    first_overlay_index is the ffmpeg input index of the first overlay image.
    Returns (new_filter_complex, overlay_file_paths).
    """
    items = normalize_overlays(overlays)
    if not items:
        return filter_complex, []

    if "[v_out]" not in filter_complex:
        logger.warning("append_overlay_filter: [v_out] label missing, overlays skipped")
        return filter_complex, []

    fc = filter_complex.replace("[v_out]", "[v_base]", 1)
    prev = "v_base"
    parts: List[str] = []
    paths: List[str] = []

    # eof_action=repeat + repeatlast=1: still PNG/JPG lasts the whole video.
    persist = "format=auto:eof_action=repeat:repeatlast=1:shortest=1"

    for i, ov in enumerate(items):
        in_idx = first_overlay_index + i
        lg = f"lg{i}"
        lgs = f"lgs{i}"
        dump = f"vdump{i}"
        spa = f"vsa{i}"
        spb = f"vsb{i}"
        nxt = "v_out" if i == len(items) - 1 else f"vov{i}"
        op = ov["opacity"]
        kind = ov["kind"]
        x, y, w = ov["x"], ov["y"], ov["w"]
        parts.append(f"[{in_idx}:v]format=rgba,colorchannelmixer=aa={op:.3f}[{lg}]")
        # Labelled pads can be consumed only once. Split so scale2ref cannot
        # truncate the video; overlay the looping still onto a full-length copy.
        parts.append(f"[{prev}]split=2[{spa}][{spb}]")
        if kind == "frame" or w >= 0.97:
            parts.append(f"[{lg}][{spa}]scale2ref=w=iw:h=ih[{lgs}][{dump}]")
            parts.append(f"[{dump}]nullsink")
            parts.append(f"[{spb}][{lgs}]overlay=0:0:{persist}[{nxt}]")
        else:
            wf = max(0.04, min(0.80, w))
            parts.append(
                f"[{lg}][{spa}]scale2ref=w='iw*{wf:.4f}':h='ow/mdar'[{lgs}][{dump}]"
            )
            parts.append(f"[{dump}]nullsink")
            parts.append(
                f"[{spb}][{lgs}]overlay=x='W*{x:.4f}':y='H*{y:.4f}':{persist}[{nxt}]"
            )
        prev = nxt
        paths.append(ov["image_path"])

    return fc + ";" + ";".join(parts), paths


def overlay_input_args(paths: Sequence[str]) -> List[str]:
    """FFmpeg args that loop still images so they last the whole video."""
    args: List[str] = []
    for p in paths:
        args.extend(["-loop", "1", "-i", p])
    return args
