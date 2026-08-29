"""Fit one master reup into destination-platform canvases (TikTok / Shorts / Reels / YouTube)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

PRESETS: Dict[str, Dict[str, Any]] = {
    "tiktok": {"w": 1080, "h": 1920, "label": "TikTok", "ratio": "9:16"},
    "youtube_shorts": {"w": 1080, "h": 1920, "label": "YouTube Shorts", "ratio": "9:16"},
    "youtube": {"w": 1920, "h": 1080, "label": "YouTube", "ratio": "16:9"},
    "facebook": {"w": 1080, "h": 1920, "label": "Facebook Reels", "ratio": "9:16"},
    "instagram": {"w": 1080, "h": 1920, "label": "Instagram Reels", "ratio": "9:16"},
    "douyin": {"w": 1080, "h": 1920, "label": "Douyin", "ratio": "9:16"},
}

ALIASES = {
    "yt": "youtube_shorts",
    "shorts": "youtube_shorts",
    "youtube-shorts": "youtube_shorts",
    "fb": "facebook",
    "facebook_reels": "facebook",
    "ig": "instagram",
    "reels": "instagram",
    "instagram_reels": "instagram",
    "ytb": "youtube",
}


def normalize_platforms(raw: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in raw or []:
        key = str(item or "").strip().lower().replace(" ", "_")
        key = ALIASES.get(key, key)
        if key not in PRESETS or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _clamp_fill(raw: Any) -> float:
    try:
        t = float(raw or 0.0)
    except (TypeError, ValueError):
        t = 0.0
    if t > 1.0:
        t = t / 100.0
    return max(0.0, min(1.0, t))


def canvas_layout(
    src_w: int,
    src_h: int,
    canvas_w: int,
    canvas_h: int,
    fill: float = 0.0,
    logo_aspect: float = 0.0,
) -> Dict[str, float]:
    """Match frontend fitKeepIntoCanvas: contain picture, optional bottom plate.

    When logo_aspect is known, the plate is capped at the image's natural height
    at canvas width so leftover 9:16 space is letterbox around the group.
    """
    sw = max(1.0, float(src_w or 1))
    sh = max(1.0, float(src_h or 1))
    cw = max(1.0, float(canvas_w or 1))
    ch = max(1.0, float(canvas_h or 1))
    contain = min(cw / sw, ch / sh)
    fitted_w = min(cw, sw * contain)
    fitted_h = min(ch, sh * contain)
    rest = max(0.0, ch - fitted_h)
    t = _clamp_fill(fill)
    try:
        aspect = float(logo_aspect or 0.0)
    except (TypeError, ValueError):
        aspect = 0.0
    natural = min(rest, fitted_w / aspect) if aspect > 0.05 else rest
    plate_h = min(rest * t, natural)
    pad_x = (cw - fitted_w) / 2.0
    pad_y = (rest - plate_h) / 2.0
    bottom_pad = max(0.0, rest - pad_y - plate_h)
    return {
        "fitted_w": fitted_w,
        "fitted_h": fitted_h,
        "pad_x": pad_x,
        "pad_y": pad_y,
        "plate_h": plate_h,
        "natural_plate_h": natural,
        "bottom_pad": bottom_pad,
        "fill": t,
    }


def fit_vf(width: int, height: int, fill: float = 0.0, pad_y: Optional[float] = None) -> str:
    w, h = int(width), int(height)
    t = _clamp_fill(fill)
    if pad_y is not None:
        y = max(0, int(round(float(pad_y))) // 2 * 2)
        return (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:{y}:black,"
            f"setsar=1,fps=30"
        )
    if t < 1e-4:
        return (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"setsar=1,fps=30"
        )
    # Keep contain size. Shift the picture up and leave extra pad at the bottom
    # for the logo plate — do not stretch or zoom the video.
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:'trunc((oh-ih)/2*(1-{t:.4f})/2)*2',"
        f"setsar=1,fps=30"
    )


def build_variant_filters(
    src_w: int,
    src_h: int,
    canvas_w: int,
    canvas_h: int,
    fill: float = 0.0,
    plate_banners: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """Simple -vf letterbox, or filter_complex that cover-fits banners full-width into the plate."""
    banners = list(plate_banners or [])
    logo_aspect = 0.0
    if banners:
        from app.services.overlay_service import _image_aspect
        for item in banners:
            path = ""
            if isinstance(item, dict):
                path = str(item.get("image_path") or "")
            if path:
                logo_aspect = _image_aspect(path)
                if logo_aspect > 0.05:
                    break
    layout = canvas_layout(src_w, src_h, canvas_w, canvas_h, fill, logo_aspect=logo_aspect)
    vf = fit_vf(canvas_w, canvas_h, fill, pad_y=layout["pad_y"] if _clamp_fill(fill) >= 1e-4 else None)
    if not banners:
        return {"mode": "vf", "vf": vf, "filter_complex": "", "paths": []}
    portrait = int(canvas_h) > int(canvas_w)
    plate_h = float(layout["plate_h"] or 0)
    if portrait and banners and plate_h < 8:
        plate_h = float(layout.get("bottom_pad") or layout["pad_y"] or 0)
    if portrait and plate_h >= 8:
        from app.services.overlay_service import append_plate_banner_filter
        fc = f"[0:v]{vf}[v_out]"
        plate_y = int(layout["pad_y"] + layout["fitted_h"])
        fc, paths = append_plate_banner_filter(
            fc,
            banners,
            1,
            canvas_w,
            canvas_h,
            plate_y=plate_y,
            plate_h=int(plate_h),
        )
        if paths:
            return {"mode": "complex", "vf": "", "filter_complex": fc, "paths": paths}
    if not portrait:
        from app.services.overlay_service import append_overlay_filter
        fc = f"[0:v]{vf}[v_out]"
        fc, paths = append_overlay_filter(
            fc, banners, 1, main_size=(int(canvas_w), int(canvas_h))
        )
        if paths:
            return {"mode": "complex", "vf": "", "filter_complex": fc, "paths": paths}
    return {"mode": "vf", "vf": vf, "filter_complex": "", "paths": []}


def _probe_wh(path: str) -> Tuple[int, int]:
    try:
        from app.services.reup_service import _probe_video_size
        return _probe_video_size(path)
    except Exception:
        return 1080, 1920


def _ffmpeg() -> Optional[str]:
    path = shutil.which("ffmpeg")
    if path:
        return path
    for candidate in ("/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if os.path.exists(candidate):
            return candidate
    return None


def _copy(src: str, dest: str) -> bool:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        if os.path.abspath(src) == os.path.abspath(dest):
            return True
        shutil.copy2(src, dest)
        return os.path.exists(dest) and os.path.getsize(dest) > 0
    except OSError as e:
        logger.warning("copy variant failed: %s", e)
        return False


def export_for_platforms(
    src_path: str,
    job_id: str,
    platforms: Optional[List[str]] = None,
    out_dir: str = "data/outputs",
    canvas_fill: float = 0.0,
    plate_banners: Optional[Sequence[Any]] = None,
) -> List[Dict[str, Any]]:
    """Create one file per destination. Same canvas is copied, not re-encoded twice."""
    if not src_path or not os.path.exists(src_path):
        return []
    wanted = normalize_platforms(platforms)
    if not wanted:
        return []

    ff = _ffmpeg()
    os.makedirs(out_dir, exist_ok=True)
    by_size: Dict[tuple, str] = {}
    results: List[Dict[str, Any]] = []
    src_w, src_h = _probe_wh(src_path)
    banners = list(plate_banners or [])

    for plat in wanted:
        preset = PRESETS[plat]
        w, h = int(preset["w"]), int(preset["h"])
        dest = os.path.join(out_dir, f"{job_id}.{plat}.mp4")
        key = (w, h)
        ok = False
        if key in by_size:
            ok = _copy(by_size[key], dest)
        elif ff:
            spec = build_variant_filters(src_w, src_h, w, h, canvas_fill, banners)
            cmd = [ff, "-y", "-i", src_path]
            if spec["mode"] == "complex":
                from app.services.overlay_service import overlay_input_args
                cmd.extend(overlay_input_args(spec["paths"]))
                cmd.extend([
                    "-filter_complex", spec["filter_complex"],
                    "-map", "[v_out]", "-map", "0:a?", "-map", "0:s?",
                ])
            else:
                cmd.extend([
                    "-vf", spec["vf"],
                    "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
                ])
            cmd.extend([
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                "-c:a", "aac", "-b:a", "192k", "-ac", "2",
                "-c:s", "mov_text",
                dest,
            ])
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            ok = res.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 0
            if not ok:
                logger.warning("platform export %s failed: %s", plat, (res.stderr or "")[-400:])
                ok = _copy(src_path, dest)
        else:
            ok = _copy(src_path, dest)

        if ok:
            by_size.setdefault(key, dest)
            results.append({
                "platform": plat,
                "label": preset["label"],
                "path": dest,
                "width": w,
                "height": h,
                "ratio": preset["ratio"],
            })
    return results
