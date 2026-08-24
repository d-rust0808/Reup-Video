"""Reusable BGM library: pull instrumental from a video URL/file and reuse on reups."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.audio_service import (
    apply_ffmpeg_vocal_mute,
    check_demucs_available,
    extract_audio_stream,
    extract_vocals_demucs,
    find_ffmpeg_binary,
)

logger = logging.getLogger(__name__)

CATALOG_NAME = "catalog.json"


def bgm_dir() -> str:
    path = os.path.join(str(settings.BASE_DIR), getattr(settings, "BGM_DIR", "data/bgm"))
    os.makedirs(path, exist_ok=True)
    return path


def catalog_path() -> str:
    return os.path.join(bgm_dir(), CATALOG_NAME)


def load_catalog() -> List[Dict[str, Any]]:
    p = catalog_path()
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items") if isinstance(data, dict) else data
        return [x for x in (items or []) if isinstance(x, dict) and x.get("id")]
    except Exception:
        return []


def save_catalog(items: List[Dict[str, Any]]) -> None:
    with open(catalog_path(), "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=2)


def _duration_sec(path: str) -> float:
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        res = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, check=False,
        )
        try:
            return float((res.stdout or "0").strip() or 0)
        except ValueError:
            return 0.0
    ffmpeg = find_ffmpeg_binary()
    if not ffmpeg:
        return 0.0
    res = subprocess.run([ffmpeg, "-i", path], capture_output=True, text=True, check=False)
    info = (res.stderr or "") + (res.stdout or "")
    import re
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if not m:
        return 0.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def _encode_library_audio(src: str, dest: str) -> bool:
    ffmpeg = find_ffmpeg_binary()
    if not ffmpeg:
        return False
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", src, "-vn",
        "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        dest,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 800:
        return True
    dest_m4a = os.path.splitext(dest)[0] + ".m4a"
    res = subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-vn",
         "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", dest_m4a],
        capture_output=True, text=True, check=False,
    )
    if res.returncode == 0 and os.path.isfile(dest_m4a) and os.path.getsize(dest_m4a) > 800:
        shutil.move(dest_m4a, dest)
        return True
    return False


def harvest_bgm(
    video_path: str,
    title: Optional[str] = None,
    source_url: Optional[str] = None,
) -> Dict[str, Any]:
    if not video_path or not os.path.isfile(video_path):
        raise FileNotFoundError("Không tìm thấy video nguồn để tách nhạc")

    track_id = f"bgm_{uuid.uuid4().hex[:10]}"
    work = os.path.join(bgm_dir(), "_work", track_id)
    os.makedirs(work, exist_ok=True)
    raw_wav = os.path.join(work, "raw.wav")
    inst_wav = os.path.join(work, "inst.wav")
    method = "ffmpeg"

    try:
        if not extract_audio_stream(video_path, raw_wav):
            raise RuntimeError("Video này không có tiếng / không tách được audio")

        if check_demucs_available():
            try:
                _voc, bgm = extract_vocals_demucs(raw_wav, work)
                if bgm and os.path.isfile(bgm) and os.path.getsize(bgm) > 800:
                    inst_wav = bgm
                    method = "demucs"
            except Exception as e:
                logger.warning("Demucs BGM harvest failed, ffmpeg fallback: %s", e)

        if method != "demucs":
            ok = apply_ffmpeg_vocal_mute(raw_wav, inst_wav, preserve_bgm=True, vocal_mute_strategy="auto")
            if not ok:
                inst_wav = raw_wav
                method = "copy"

        dest = os.path.join(bgm_dir(), f"{track_id}.mp3")
        if not _encode_library_audio(inst_wav, dest):
            raise RuntimeError("Không encode được file nhạc nền")

        item = {
            "id": track_id,
            "title": (title or os.path.splitext(os.path.basename(video_path))[0] or "Nhạc nền").strip(),
            "source_url": source_url or "",
            "file": os.path.basename(dest),
            "path": dest,
            "duration": round(_duration_sec(dest), 2),
            "bytes": os.path.getsize(dest),
            "method": method,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        items = load_catalog()
        items.insert(0, item)
        save_catalog(items)
        return item
    finally:
        shutil.rmtree(work, ignore_errors=True)


def import_audio_file(
    src_path: str,
    title: Optional[str] = None,
    *,
    method: str = "upload",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not src_path or not os.path.isfile(src_path):
        raise FileNotFoundError("Không tìm thấy file nhạc")
    track_id = f"bgm_{uuid.uuid4().hex[:10]}"
    dest = os.path.join(bgm_dir(), f"{track_id}.mp3")
    if not _encode_library_audio(src_path, dest):
        raise RuntimeError("Không nhận được file audio")
    item = {
        "id": track_id,
        "title": (title or os.path.splitext(os.path.basename(src_path))[0] or "Nhạc nền").strip(),
        "source_url": "",
        "file": os.path.basename(dest),
        "path": dest,
        "duration": round(_duration_sec(dest), 2),
        "bytes": os.path.getsize(dest),
        "method": method,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        item.update({k: v for k, v in extra.items() if k not in ("id", "file", "path", "bytes")})
    items = load_catalog()
    items.insert(0, item)
    save_catalog(items)
    return item


def import_remote_track(track: Dict[str, Any]) -> Dict[str, Any]:
    """Download a track found by app.services.bgm_providers and add it to the library.

    `track` is one normalised provider row (needs at least audio_url).
    License metadata is persisted so credits can be exported later.
    """
    from app.services.bgm_providers import download_track_audio  # local import: avoids httpx at import time

    audio_url = (track or {}).get("audio_url") or ""
    if not audio_url:
        raise ValueError("Thiếu link nhạc để tải")

    provider = (track.get("provider") or "online").strip().lower()
    title = (track.get("title") or "Nhạc nền").strip()
    artist = (track.get("artist") or "").strip()

    work = os.path.join(bgm_dir(), "_work", f"dl_{uuid.uuid4().hex[:8]}")
    os.makedirs(work, exist_ok=True)
    tmp_audio = os.path.join(work, "download.bin")
    try:
        download_track_audio(audio_url, tmp_audio)
        return import_audio_file(
            tmp_audio,
            title=f"{title} - {artist}" if artist else title,
            method=provider,
            extra={
                "source_url": track.get("page_url") or audio_url,
                "artist": artist,
                "license": track.get("license") or "",
                "license_url": track.get("license_url") or "",
                "attribution": track.get("attribution") or "",
                "provider": provider,
                "external_id": str(track.get("external_id") or ""),
            },
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def resolve_bgm(track_id: str) -> Optional[str]:
    if not track_id:
        return None
    clean = os.path.basename(str(track_id).strip())
    for item in load_catalog():
        if item.get("id") == clean or item.get("file") == clean:
            path = item.get("path") or os.path.join(bgm_dir(), item.get("file") or "")
            if path and os.path.isfile(path):
                return path
    direct = os.path.join(bgm_dir(), clean)
    if os.path.isfile(direct):
        return direct
    if os.path.isfile(track_id) and os.path.abspath(track_id).startswith(os.path.abspath(bgm_dir())):
        return track_id
    return None


def delete_bgm(track_id: str) -> bool:
    items = load_catalog()
    keep = []
    found = False
    for item in items:
        if item.get("id") == track_id:
            found = True
            path = item.get("path") or os.path.join(bgm_dir(), item.get("file") or "")
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        else:
            keep.append(item)
    if found:
        save_catalog(keep)
    return found
