"""Write unique Facebook Reel titles/captions via local agy CLI."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

_PHONE = re.compile(r"(0\d{8,10}|\+?84\d{8,10})")
_SRT_TEXT = re.compile(r"^\d+\s*$|^\d{2}:\d{2}:")
_SOURCE_ID = re.compile(
    r"^(douyin|kuaishou|xiaohongshu|xhs|tiktok|upload)[-_][\w.\-]+$",
    re.IGNORECASE,
)


def usable_brand_title(title: str, intent: str = "") -> str:
    """Drop auto-filled source ids like douyin_123 so agy can write a real title."""
    text = (title or "").strip()
    if not text:
        return ""
    compact = text.replace(" ", "")
    if _SOURCE_ID.match(compact):
        return ""
    if re.fullmatch(r"[\w.\-]{8,}", text) and " " not in text and not extract_contacts(text):
        return ""
    if intent and text.lower() == intent.lower():
        return ""
    return text

_POST_SCHEMA = {
    "type": "object",
    "properties": {
        "posts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "title": {"type": "string"},
                    "caption": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["index", "title", "caption"],
            },
        }
    },
    "required": ["posts"],
}

_OPENERS = (
    "Xem đoạn này xong là biết việc nhà phải làm từ đâu.",
    "Clip thực tế — ai đang tháo dỡ hay đập phá thì nên xem kỹ.",
    "Việc nhà mà làm ẩu là hỏng cả công. Đoạn này nói đúng chỗ.",
    "Ai đang cải tạo, lột gạch, phá dỡ thì lưu clip này lại.",
    "Không phải nhà nào cũng đập được như nhau — xem hết đoạn này.",
)


def extract_contacts(text: str) -> List[str]:
    found: List[str] = []
    for match in _PHONE.findall(text or ""):
        if match not in found:
            found.append(match)
    return found


def video_brief_from_srt(path: str, *, limit_chars: int = 700) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        raw = open(path, "r", encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    lines: List[str] = []
    for line in raw.splitlines():
        item = line.strip()
        if not item or item.upper() == "WEBVTT" or _SRT_TEXT.match(item):
            continue
        if "-->" in item:
            continue
        lines.append(item)
    text = " ".join(lines)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit_chars]


def find_job_transcript(job_id: str, output_dir: str = "data/outputs") -> str:
    if not job_id:
        return ""
    for name in (
        f"{job_id}.vi.srt",
        f"{job_id}_stage2_vi.srt",
        f"{job_id}.srt",
    ):
        brief = video_brief_from_srt(os.path.join(output_dir, name))
        if brief:
            return brief
    return ""


def _ensure_contacts(caption: str, intent: str) -> str:
    text = (caption or "").strip()
    intent = (intent or "").strip()
    missing = [item for item in extract_contacts(intent) if item not in text]
    if intent and intent not in text and (missing or len(text) < 40):
        text = f"{text}\n\n{intent}".strip()
    elif missing:
        text = f"{text}\n\n{' '.join(missing)}".strip()
    return text


def _fallback_post(intent: str, brand_title: str, brief: str, page_name: str, index: int) -> Dict[str, str]:
    opener = _OPENERS[index % len(_OPENERS)]
    if page_name:
        opener = f"{opener} ({page_name})"
    title_src = (brand_title or intent or "Video reup").strip()
    title = title_src[:70]
    snippet = (brief or "").strip()
    if len(snippet) > 180:
        snippet = snippet[:177].rsplit(" ", 1)[0] + "…"
    body_parts = [opener]
    if snippet:
        body_parts.append(snippet)
    if intent:
        body_parts.append(intent)
    caption = _ensure_contacts("\n\n".join(body_parts), intent)
    tags = ["dappa", "thaodo", "xaydung", "reels", "vietsub"]
    return {"title": title, "caption": caption, "hashtags": tags}


def _posts_from_envelope(envelope: Dict[str, Any], expected: int) -> List[Dict[str, str]]:
    structured = envelope.get("structured_output")
    items = None
    if isinstance(structured, dict):
        items = structured.get("posts")
    if not isinstance(items, list):
        raw = envelope.get("response") or ""
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                items = parsed.get("posts")
            elif isinstance(parsed, list):
                items = parsed
        except Exception:
            items = None
    if not isinstance(items, list):
        return []
    by_index: Dict[int, Dict[str, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index") or 0)
        except (TypeError, ValueError):
            idx = 0
        title = str(item.get("title") or "").strip()
        caption = str(item.get("caption") or "").strip()
        tags = item.get("hashtags") if isinstance(item.get("hashtags"), list) else []
        clean_tags = [str(t).strip().lstrip("#") for t in tags if str(t).strip()]
        if not title or not caption:
            continue
        key = idx if idx >= 1 else (len(by_index) + 1)
        by_index[key] = {"title": title, "caption": caption, "hashtags": clean_tags}
    out: List[Dict[str, str]] = []
    for i in range(1, expected + 1):
        if i in by_index:
            out.append(by_index[i])
    return out


def write_facebook_posts(
    *,
    intent: str,
    brand_title: str = "",
    video_brief: str = "",
    page_names: Optional[Iterable[str]] = None,
    extra_notes: str = "",
) -> List[Dict[str, str]]:
    """Return one unique {title, caption, hashtags} per page. Never raises."""
    names = [str(n or "").strip() or f"Page {i+1}" for i, n in enumerate(list(page_names or ["Page"]))]
    if not names:
        names = ["Page"]
    intent = (intent or "").strip() or usable_brand_title(brand_title)
    brand_title = usable_brand_title(brand_title, intent)
    results: List[Dict[str, str]] = []

    try:
        from app.services import agy_cli_service

        if not agy_cli_service.is_available():
            raise RuntimeError("agy unavailable")
        chunk = 4
        for start in range(0, len(names), chunk):
            batch = names[start:start + chunk]
            listed = "\n".join(f"{i}. {name}" for i, name in enumerate(batch, start=1))
            prompt = (
                "Bạn là copywriter Facebook Reels tiếng Việt. Không dùng tool, không giải thích.\n"
                "Viết BÀI MỚI, khác nhau cho từng page (câu mở đầu khác, hashtag khác).\n"
                "Bám nội dung video. Tối ưu tìm kiếm (SEO) tự nhiên, không nhồi từ khóa.\n"
                "BẮT BUỘC giữ nguyên số điện thoại, tên dịch vụ và CTA trong 'Nội dung hướng tới'.\n"
                "Tự viết title ngắn (dưới 70 ký tự) từ nội dung video + CTA. Không dùng tên file nguồn.\n"
                "Caption 2–5 câu, dễ đọc trên Reels, hashtag liên quan tự nghĩ.\n"
                "Trả JSON đúng schema posts[{index,title,caption,hashtags}].\n\n"
                f"Nội dung hướng tới (phải có trong mỗi bài):\n{intent or '(không có)'}\n"
                f"Tóm tắt video:\n{(video_brief or '(chưa có phụ đề)')[:700]}\n"
                f"Danh sách page:\n{listed}\n"
            )
            envelope = agy_cli_service.complete(
                prompt,
                json_schema=_POST_SCHEMA,
                timeout_sec=90,
            )
            parsed = _posts_from_envelope(envelope, len(batch))
            if len(parsed) != len(batch):
                raise RuntimeError(f"agy returned {len(parsed)}/{len(batch)} posts")
            for item in parsed:
                item["caption"] = _ensure_contacts(item["caption"], intent)
                results.append(item)
        if len(results) == len(names):
            return results
    except Exception as exc:
        logger.warning("agy post writer failed (%s); using unique fallback", exc)

    return [
        _fallback_post(intent, brand_title, video_brief, names[i], i)
        for i in range(len(names))
    ]
