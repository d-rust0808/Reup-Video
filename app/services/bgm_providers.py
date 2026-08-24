"""Online BGM providers: search royalty-free music catalogs via public APIs.

Providers verified against live endpoints:
  - Openverse  GET https://api.openverse.org/v1/audio/   (no key, rate limited 20/min, 200/day)
  - Jamendo    GET https://api.jamendo.com/v3.0/tracks/  (needs client_id from devportal.jamendo.com)

Every provider normalises results to the same dict schema so the API layer and the
UI stay provider-agnostic:
    {provider, external_id, title, artist, duration, license, license_url,
     attribution, audio_url, page_url, tags, instrumental}
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

OPENVERSE_URL = "https://api.openverse.org/v1/audio/"
JAMENDO_URL = "https://api.jamendo.com/v3.0/tracks/"

HTTP_TIMEOUT = 20.0
USER_AGENT = "ReupVideoStudio/1.0 (+bgm-library)"

# CC licenses safe for commercial reup work (no NC, no ND).
# Openverse license codes are lowercase, e.g. "by", "by-sa", "cc0", "pdm".
COMMERCIAL_SAFE_LICENSES = {"cc0", "pdm", "by", "by-sa"}

# Openverse tags coming from Jamendo include an explicit vocal/instrumental marker.
_INSTRUMENTAL_TAGS = {"instrumental"}
_VOCAL_TAGS = {"vocal", "vocals", "voice", "singing"}


def _is_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def is_commercial_safe(license_code: str) -> bool:
    return (license_code or "").strip().lower() in COMMERCIAL_SAFE_LICENSES


def available_providers() -> List[Dict[str, Any]]:
    """Providers usable right now (Jamendo hides itself when no client_id is set)."""
    providers = [
        {
            "id": "openverse",
            "label": "Openverse (CC)",
            "ready": True,
            "needs_key": False,
            "note": "Không cần API key. Giới hạn 20 request/phút.",
        },
        {
            "id": "jamendo",
            "label": "Jamendo",
            "ready": bool(getattr(settings, "JAMENDO_CLIENT_ID", "")),
            "needs_key": True,
            "note": "Cần JAMENDO_CLIENT_ID trong .env (devportal.jamendo.com).",
        },
    ]
    return providers


def _tag_names(tags: Any) -> List[str]:
    names: List[str] = []
    for tag in tags or []:
        if isinstance(tag, dict):
            name = tag.get("name")
        else:
            name = tag
        if name:
            names.append(str(name).strip().lower())
    return names


def _guess_instrumental(tags: List[str]) -> Optional[bool]:
    if any(t in _INSTRUMENTAL_TAGS for t in tags):
        return True
    if any(t in _VOCAL_TAGS for t in tags):
        return False
    return None


def _openverse_request(
    query: str,
    *,
    limit: int,
    page: int,
    commercial_only: bool,
) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {
        "q": query,
        "page_size": max(1, min(int(limit), 50)),
        "page": max(1, int(page)),
        "category": "music",
    }
    if commercial_only:
        # Verified: this filter drops the by-nc-nd results the default query returns.
        params["license_type"] = "commercial,modification"

    with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        res = client.get(OPENVERSE_URL, params=params)
        res.raise_for_status()
        payload = res.json()
    return list(payload.get("results") or [])


def search_openverse(
    query: str,
    *,
    limit: int = 20,
    page: int = 1,
    instrumental: bool = False,
    commercial_only: bool = True,
) -> List[Dict[str, Any]]:
    rows = _openverse_request(query, limit=limit, page=page, commercial_only=commercial_only)

    # Openverse joins multi-word queries with AND, so "lofi chill" plus the license
    # filter often yields nothing. Fall back to per-term queries and merge.
    terms = [t for t in query.split() if len(t) > 1]
    if not rows and len(terms) > 1:
        seen_ids = set()
        merged: List[Dict[str, Any]] = []
        for term in terms[:3]:
            try:
                for row in _openverse_request(
                    term, limit=limit, page=page, commercial_only=commercial_only
                ):
                    rid = row.get("id")
                    if rid and rid not in seen_ids:
                        seen_ids.add(rid)
                        merged.append(row)
            except httpx.HTTPError as e:
                logger.warning("Openverse term fallback %r failed: %s", term, e)
        rows = merged[: max(1, min(int(limit), 50))]

    out: List[Dict[str, Any]] = []
    for row in rows:
        audio_url = row.get("url") or ""
        if not _is_http_url(audio_url):
            continue
        license_code = (row.get("license") or "").lower()
        if commercial_only and not is_commercial_safe(license_code):
            continue
        tags = _tag_names(row.get("tags"))
        is_instr = _guess_instrumental(tags)
        if instrumental and is_instr is False:
            continue
        # Openverse reports duration in milliseconds.
        duration_ms = row.get("duration") or 0
        out.append(
            {
                "provider": "openverse",
                "external_id": str(row.get("id") or ""),
                "title": (row.get("title") or "Untitled").strip(),
                "artist": (row.get("creator") or "").strip(),
                "duration": round(float(duration_ms) / 1000.0, 2) if duration_ms else 0.0,
                "license": license_code,
                "license_url": row.get("license_url") or "",
                "attribution": row.get("attribution") or "",
                "audio_url": audio_url,
                "page_url": row.get("foreign_landing_url") or "",
                "tags": tags,
                "instrumental": is_instr,
                "source_platform": row.get("provider") or "openverse",
            }
        )
    return out


def search_jamendo(
    query: str,
    *,
    limit: int = 20,
    page: int = 1,
    instrumental: bool = False,
    commercial_only: bool = True,
) -> List[Dict[str, Any]]:
    client_id = getattr(settings, "JAMENDO_CLIENT_ID", "")
    if not client_id:
        raise RuntimeError("Chưa cấu hình JAMENDO_CLIENT_ID trong .env")

    limit = max(1, min(int(limit), 200))
    params: Dict[str, Any] = {
        "client_id": client_id,
        "format": "json",
        "limit": limit,
        "offset": max(0, (max(1, int(page)) - 1) * limit),
        "search": query,
        "audioformat": "mp32",
        "include": "licenses musicinfo",
        "groupby": "artist_id",
    }
    if instrumental:
        params["vocalinstrumental"] = "instrumental"
    if commercial_only:
        # Exclude the NonCommercial and NoDerivs buckets.
        params["ccnc"] = "false"
        params["ccnd"] = "false"

    with httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        res = client.get(JAMENDO_URL, params=params)
        res.raise_for_status()
        payload = res.json()

    headers = payload.get("headers") or {}
    if headers.get("status") not in (None, "success"):
        raise RuntimeError(headers.get("error_message") or "Jamendo API lỗi")

    out: List[Dict[str, Any]] = []
    for row in payload.get("results") or []:
        # Prefer the download URL, but only when the artist allows downloads.
        audio_url = ""
        if row.get("audiodownload_allowed") and _is_http_url(row.get("audiodownload") or ""):
            audio_url = row["audiodownload"]
        elif _is_http_url(row.get("audio") or ""):
            audio_url = row["audio"]
        if not audio_url:
            continue

        musicinfo = row.get("musicinfo") or {}
        tags_block = musicinfo.get("tags") or {}
        tags = _tag_names(
            list(tags_block.get("genres") or [])
            + list(tags_block.get("instruments") or [])
            + list(tags_block.get("vartags") or [])
        )
        vocal_flag = (musicinfo.get("vocalinstrumental") or "").lower()
        is_instr = True if vocal_flag == "instrumental" else (False if vocal_flag == "vocal" else _guess_instrumental(tags))
        if instrumental and is_instr is False:
            continue

        licenses = row.get("licenses") or {}
        license_url = row.get("license_ccurl") or licenses.get("ccurl") or ""
        artist = (row.get("artist_name") or "").strip()
        title = (row.get("name") or "Untitled").strip()
        out.append(
            {
                "provider": "jamendo",
                "external_id": str(row.get("id") or ""),
                "title": title,
                "artist": artist,
                "duration": round(float(row.get("duration") or 0), 2),
                "license": _license_code_from_url(license_url),
                "license_url": license_url,
                "attribution": f'"{title}" by {artist} (Jamendo)' if artist else f'"{title}" (Jamendo)',
                "audio_url": audio_url,
                "page_url": row.get("shareurl") or "",
                "tags": tags,
                "instrumental": is_instr,
                "source_platform": "jamendo",
            }
        )
    return out


def _license_code_from_url(url: str) -> str:
    """Map a creativecommons.org URL to its short code, e.g. .../licenses/by-sa/3.0/ -> by-sa."""
    if not url:
        return ""
    parts = [p for p in urlparse(url).path.split("/") if p]
    if "publicdomain" in parts:
        return "cc0"
    if "licenses" in parts:
        idx = parts.index("licenses")
        if idx + 1 < len(parts):
            return parts[idx + 1].lower()
    return ""


PROVIDER_FUNCS = {
    "openverse": search_openverse,
    "jamendo": search_jamendo,
}


def search_tracks(
    query: str,
    *,
    provider: str = "openverse",
    limit: int = 20,
    page: int = 1,
    instrumental: bool = False,
    commercial_only: bool = True,
    min_duration: float = 0.0,
    max_duration: float = 0.0,
) -> Dict[str, Any]:
    """Search one provider (or every ready provider with provider='all')."""
    query = (query or "").strip()
    if not query:
        raise ValueError("Cần nhập từ khóa tìm nhạc")

    provider = (provider or "openverse").strip().lower()
    if provider == "all":
        targets = [p["id"] for p in available_providers() if p["ready"]]
    elif provider in PROVIDER_FUNCS:
        targets = [provider]
    else:
        raise ValueError(f"Provider không hỗ trợ: {provider}")

    items: List[Dict[str, Any]] = []
    errors: Dict[str, str] = {}
    for name in targets:
        try:
            found = PROVIDER_FUNCS[name](
                query,
                limit=limit,
                page=page,
                instrumental=instrumental,
                commercial_only=commercial_only,
            )
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            errors[name] = "Bị giới hạn tốc độ, thử lại sau ít phút" if status == 429 else f"HTTP {status}"
            logger.warning("BGM provider %s HTTP %s", name, status)
            continue
        except Exception as e:  # network error, bad payload, missing key
            errors[name] = str(e)
            logger.warning("BGM provider %s failed: %s", name, e)
            continue
        items.extend(found)

    if min_duration > 0:
        items = [x for x in items if x["duration"] <= 0 or x["duration"] >= min_duration]
    if max_duration > 0:
        items = [x for x in items if x["duration"] <= 0 or x["duration"] <= max_duration]

    return {"items": items, "count": len(items), "providers": targets, "errors": errors}


def download_track_audio(audio_url: str, dest_path: str, max_bytes: int = 60 * 1024 * 1024) -> int:
    """Stream a remote track to disk. Returns bytes written; raises on empty/oversized files."""
    if not _is_http_url(audio_url):
        raise ValueError("Link nhạc không hợp lệ")

    written = 0
    with httpx.Client(
        timeout=HTTP_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        with client.stream("GET", audio_url) as res:
            res.raise_for_status()
            with open(dest_path, "wb") as fh:
                for chunk in res.iter_bytes(64 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise RuntimeError("File nhạc quá lớn (>60MB)")
                    fh.write(chunk)

    if written < 2048:
        raise RuntimeError("Tải về rỗng, nguồn có thể đã chặn")
    return written
