"""Fit one master reup into destination-platform canvases (TikTok / Shorts / Reels / YouTube)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional

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


def fit_vf(width: int, height: int) -> str:
    w, h = int(width), int(height)
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"setsar=1,fps=30"
    )


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

    for plat in wanted:
        preset = PRESETS[plat]
        w, h = int(preset["w"]), int(preset["h"])
        dest = os.path.join(out_dir, f"{job_id}.{plat}.mp4")
        key = (w, h)
        ok = False
        if key in by_size:
            ok = _copy(by_size[key], dest)
        elif ff:
            vf = fit_vf(w, h)
            cmd = [
                ff, "-y", "-i", src_path,
                "-vf", vf,
                "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                "-c:s", "mov_text",
                dest,
            ]
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
