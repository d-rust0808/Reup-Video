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
_BANNED_WORD = re.compile(
    r"(?i)(?<![A-Za-z0-9À-ỹ])#?(re-?ups?|vietsubs?)(?![A-Za-z0-9À-ỹ])"
)
_LAZY_TITLE = re.compile(
    r"(?i)^(video\s*(mới|moi|re-?up)|clip\s*re-?up)(\s*[#\d].*)?$"
)
_BANNED_TAGS = {
    "reup", "re-up", "reups", "vietsub", "viet-sub", "vietsubs",
    "youtube", "facebook", "instagram", "douyin", "kuaishou", "xiaohongshu",
}
_STOPWORDS = {
    "rồi", "rồi", "này", "kia", "đó", "thế", "vậy", "là", "của", "và", "có",
    "một", "những", "các", "cho", "với", "trong", "không", "được", "mình",
    "bạn", "anh", "chị", "tôi", "hắn", "nàng", "người", "làm", "đi", "lại",
    "rất", "cũng", "như", "để", "khi", "nếu", "vì", "nhưng", "hay", "ra",
    "vào", "nên", "thì", "đã", "sẽ", "bị", "còn", "vẫn", "sang", "nói",
}


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


def strip_reup_mentions(text: str) -> str:
    """Drop banned filler (reup / vietsub) from published copy."""
    cleaned = _BANNED_WORD.sub(" ", text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    return cleaned.strip(" -|,#")


def is_lazy_title(title: str) -> bool:
    text = strip_reup_mentions(title or "").strip()
    if not text:
        return True
    if _LAZY_TITLE.match(text):
        return True
    if re.fullmatch(r"video\s*#?\w{0,12}", text, flags=re.IGNORECASE):
        return True
    return False


def title_from_brief(brief: str, intent: str = "", limit: int = 68) -> str:
    """One-line Vietnamese title from the transcript, never a placeholder."""
    text = re.sub(r"\s+", " ", strip_reup_mentions(brief or "")).strip(" .")
    for chunk in re.split(r"[.!?…\n]+", text):
        line = chunk.strip(" ,;:-")
        if len(line) < 12:
            continue
        if len(line) <= limit:
            return line
        cut = line[:limit].rsplit(" ", 1)[0].strip()
        return cut or line[:limit]
    words = [w for w in text.split() if w]
    if len(words) >= 6:
        return " ".join(words[:12])[:limit].rstrip()
    brand = usable_brand_title(intent)
    if brand and not is_lazy_title(brand):
        return brand[:limit]
    return "Xem hết mới rõ đoạn này"


def hashtags_from_text(*parts: str, limit: int = 8) -> List[str]:
    blob = strip_reup_mentions(" ".join(str(p or "") for p in parts)).lower()
    found: List[str] = []
    seen = set()
    for raw in re.findall(r"[A-Za-zÀ-ỹ0-9]{4,}", blob):
        key = raw.lower().replace("_", "-")
        if key in _STOPWORDS or key in _BANNED_TAGS or key in seen:
            continue
        if raw.isdigit():
            continue
        seen.add(key)
        found.append(raw)
        if len(found) >= limit:
            break
    return found


def _clean_hashtags(tags: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in tags or []:
        slug = str(raw or "").strip().lstrip("#")
        if not slug:
            continue
        key = slug.lower().replace("_", "-")
        if key in _BANNED_TAGS or key in seen:
            continue
        seen.add(key)
        out.append(slug)
    return out


def _sanitize_post(item: Dict[str, str], intent: str = "", brief: str = "") -> Dict[str, str]:
    title = strip_reup_mentions(str(item.get("title") or ""))
    caption = strip_reup_mentions(_ensure_contacts(str(item.get("caption") or ""), intent))
    tags = _clean_hashtags(item.get("hashtags") or [])
    if is_lazy_title(title):
        title = title_from_brief(brief or caption, intent)
    if not caption.strip():
        caption = strip_reup_mentions(_ensure_contacts(brief[:400], intent)) or title
    if not tags:
        tags = hashtags_from_text(title, caption, brief, intent) or ["fyp", "reels"]
    tags = tags[:10]
    hashline = " ".join(f"#{t.lstrip('#')}" for t in tags)
    if hashline and hashline.lower() not in caption.lower():
        caption = f"{caption}\n\n{hashline}".strip()
    return {"title": title[:70], "caption": caption, "hashtags": tags}


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
    summary = re.sub(r"\s+", " ", strip_reup_mentions(brief or "")).strip()
    if len(summary) > 320:
        summary = summary[:317].rsplit(" ", 1)[0] + "…"
    if not summary:
        summary = _OPENERS[index % len(_OPENERS)]
    hooks = (
        "Xem đoạn này là nắm hết chuyện.",
        "Diễn biến đoạn này đáng xem tới cuối.",
        "Clip này tóm đúng chỗ gay cấn.",
        "Cảnh này xem xong mới rõ vì sao căng.",
        "Một đoạn ngắn nhưng tình tiết đủ đầy.",
    )
    hook = hooks[index % len(hooks)]
    title = usable_brand_title(brand_title, intent) or title_from_brief(brief, intent)
    caption = _ensure_contacts(f"{hook} {summary}".strip(), intent)
    tags = hashtags_from_text(title, brief, intent)
    return _sanitize_post(
        {"title": title, "caption": caption, "hashtags": tags},
        intent,
        brief=brief,
    )


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
                "Bạn là editor mạng xã hội tiếng Việt (Facebook Reels / YouTube / TikTok).\n"
                "Không dùng tool, không giải thích. Viết như người Việt thật, không máy móc.\n"
                "Title: tóm 1 ý chính của clip, dưới 60 ký tự. CẤM title generic "
                "('Video mới', 'Video reup', tên file, mã job).\n"
                "Caption: 2–4 câu tóm nội dung/diễn biến, dễ đọc. Có thể CTA nhẹ nếu có SĐT.\n"
                "Hashtag: 5–8 tag đúng chủ đề (nhân vật, thể loại, tình tiết). "
                "CẤM: vietsub, reup, #youtube, #facebook, #tiktok, #douyin.\n"
                "Mỗi page một bài khác nhau (câu mở khác, hashtag khác).\n"
                "Giữ nguyên số điện thoại và CTA trong 'Nội dung hướng tới' nếu có.\n"
                "Trả JSON đúng schema posts[{index,title,caption,hashtags}].\n\n"
                f"Nội dung hướng tới:\n{intent or '(không có)'}\n"
                f"Tóm tắt / thoại video:\n{(video_brief or '(chưa có phụ đề)')[:900]}\n"
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
                results.append(_sanitize_post(item, intent, brief=video_brief))
        if len(results) == len(names):
            return results
    except Exception as exc:
        logger.warning("agy post writer failed (%s); using unique fallback", exc)

    return [
        _fallback_post(intent, brand_title, video_brief, names[i], i)
        for i in range(len(names))
    ]
