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
    r"(?i)(?<![A-Za-z0-9À-ỹ])#?(re-?ups?|viet-?subs?)(?![A-Za-z0-9À-ỹ])"
)
_BANNED_HASHTAG = re.compile(
    r"(?i)#(?:re-?ups?|viet-?subs?|youtubes?|facebook|tiktoks?|douyin|"
    r"kuaishou|xiaohongshu|instagram|shorts?|videos?|moi|mới)\b"
)
_LAZY_TITLE = re.compile(
    r"(?i)^(video\s*(mới|moi|re-?up|hay)|clip\s*(mới|moi|re-?up)|xem ngay)(\s*[#\d].*)?$"
)
_BANNED_TAGS = {
    "reup", "re-up", "reups", "vietsub", "viet-sub", "vietsubs",
    "youtube", "youtuber", "youtubeshorts", "facebook", "instagram",
    "tiktok", "douyin", "kuaishou", "xiaohongshu", "shorts", "short",
    "video", "videos", "clip", "moi", "mới", "ytb", "fb", "reels",
}
_VIRAL_TAGS = {
    "fyp", "viral", "xuhuong", "reviewphim", "phimhay", "phimtrung",
    "cotrang", "ngontinh", "drama", "review", "xaydung", "thaodo",
    "caitaonha", "thucung", "khoahoc",
}
_TOPIC_HINTS = (
    (("lãnh cung", "thái hậu", "thái tử", "cô nương", "tiểu thư", "công chúa",
      "thái giám", "chuộc thân", "sát chiêu", "trẫm", "hoàng thượng", "phế phi"),
     ("reviewphim", "cotrang", "phimtrung", "phimhay", "xuhuong")),
    (("ngôn tình", "kết hôn", "chồng tôi", "vợ tôi"),
     ("ngontinh", "phimhay", "reviewphim", "xuhuong")),
    (("đập phá", "tháo dỡ", "lột gạch", "bê tông", "cải tạo", "xi măng"),
     ("xaydung", "thaodo", "xuhuong")),
    (("newton", "hố đen", "ánh sáng", "trí tuệ nhân tạo", "máy chủ", "động cơ"),
     ("khoahoc", "xuhuong")),
    (("mèo", "chó", "thú cưng"),
     ("thucung", "xuhuong")),
)
_STOPWORDS = {
    "rồi", "này", "kia", "đó", "thế", "vậy", "là", "của", "và", "có",
    "một", "những", "các", "cho", "với", "trong", "không", "được", "mình",
    "bạn", "anh", "chị", "tôi", "hắn", "nàng", "người", "làm", "đi", "lại",
    "rất", "cũng", "như", "để", "khi", "nếu", "vì", "nhưng", "hay", "ra",
    "vào", "nên", "thì", "đã", "sẽ", "bị", "còn", "vẫn", "sang", "nói",
    "xem", "hết", "rõ", "đoạn", "clip", "câu", "chuyện", "chưa", "chết",
    "nương", "chải", "trốn", "phòng", "chuộc", "mau", "dậy", "đợi", "đầu",
    "kìa", "giờ", "nào", "cô", "các", "thì", "đây", "kia", "rồi", "lắm",
    "thôi", "nhé", "nha", "đi", "lại", "sang", "qua", "lên", "xuống",
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

def strip_reup_mentions(text: str) -> str:
    """Drop banned filler (reup / vietsub / platform hashtags) from published copy."""
    cleaned = _BANNED_HASHTAG.sub(" ", text or "")
    cleaned = _BANNED_WORD.sub(" ", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip(" -|,#")


def is_lazy_title(title: str) -> bool:
    text = strip_reup_mentions(title or "").strip()
    text = re.sub(
        r"(?i)\s*[·|\-–]\s*(youtube(\s*shorts)?|tiktok|facebook(\s*reels)?|instagram|douyin).*$",
        "",
        text,
    ).strip()
    text = re.sub(r"#\S+", "", text).strip()
    if not text:
        return True
    if _LAZY_TITLE.match(text):
        return True
    if re.fullmatch(r"video(\s*#?\w{0,16})?", text, flags=re.IGNORECASE):
        return True
    return False


def is_lazy_caption(caption: str) -> bool:
    text = strip_reup_mentions(caption or "")
    body = re.sub(r"#\S+", "", text).strip()
    if not body:
        return True
    if is_lazy_title(body):
        return True
    return bool(re.match(r"(?i)^video\s*(mới|moi)\b", body))


def needs_generated_copy(title: str = "", caption: str = "", brief: str = "") -> bool:
    if is_lazy_title(title or "") or is_lazy_caption(caption or ""):
        return True
    return is_raw_transcript_dump(title, caption, brief)


def is_raw_transcript_dump(title: str, caption: str, brief: str) -> bool:
    """True when title/caption is just the first spoken lines, not a recap."""
    if not (brief or "").strip():
        return False

    def _norm(value: str) -> str:
        text = strip_reup_mentions(re.sub(r"#\S+", " ", value or ""))
        return re.sub(r"\s+", " ", text).strip().lower()

    source = _norm(brief)
    if len(source) < 20:
        return False
    title_n = _norm(title)
    caption_n = _norm(caption)
    if title_n and len(title_n) >= 12 and title_n[:28] in source[:120]:
        return True
    if caption_n and len(caption_n) >= 24 and caption_n[:40] in source[:160]:
        return True
    return False


def title_from_brief(brief: str, intent: str = "", limit: int = 68) -> str:
    """One-line Vietnamese title from the transcript, never a placeholder."""
    text = re.sub(r"\s+", " ", strip_reup_mentions(brief or "")).strip(" .")
    for chunk in re.split(r"[.!?…\n]+", text):
        line = chunk.strip(" ,;:-")
        if len(line) < 12 or is_lazy_title(line):
            continue
        if len(line) <= limit:
            return line
        cut = line[:limit].rsplit(" ", 1)[0].strip()
        return cut or line[:limit]
    words = [w for w in text.split() if w and not is_lazy_title(w)]
    if len(words) >= 6:
        candidate = " ".join(words[:12])[:limit].rstrip()
        if candidate and not is_lazy_title(candidate):
            return candidate
    brand = usable_brand_title(intent)
    if brand and not is_lazy_title(brand):
        return brand[:limit]
    return "Xem hết mới rõ đoạn này"


def caption_from_brief(brief: str, intent: str = "", limit: int = 420) -> str:
    """2–4 sentence Vietnamese summary of the clip, never platform filler."""
    text = re.sub(r"\s+", " ", strip_reup_mentions(brief or "")).strip(" .")
    sentences = [
        chunk.strip(" ,;:-")
        for chunk in re.split(r"[.!?…\n]+", text)
        if len(chunk.strip(" ,;:-")) >= 8
    ]
    if not sentences:
        summary = title_from_brief(brief, intent)
    else:
        summary = ". ".join(sentences[:3]).strip()
        if summary and summary[-1] not in ".!?…":
            summary += "."
    if len(summary) > limit:
        summary = summary[:limit].rsplit(" ", 1)[0].strip() + "…"
    return _ensure_contacts(summary, intent)


def _boost_topic_tags(tags: List[str], blob: str) -> List[str]:
    text = (blob or "").lower()
    extra: List[str] = []
    for needles, hints in _TOPIC_HINTS:
        if any(needle in text for needle in needles):
            extra.extend(hints)
            break
    if not extra:
        extra = ["xuhuong"]
    out = list(tags)
    seen = {t.lower().replace("_", "-") for t in out}
    for hint in extra:
        key = hint.lower().replace("_", "-")
        if key in seen or key in _BANNED_TAGS:
            continue
        seen.add(key)
        out.append(hint)
    return out


def _is_usable_tag(slug: str) -> bool:
    key = str(slug or "").strip().lstrip("#").lower().replace("_", "-").replace(" ", "")
    if not key or key.isdigit():
        return False
    if key in _BANNED_TAGS or key in _STOPWORDS:
        return False
    if key in _VIRAL_TAGS:
        return True
    if len(key) < 6:
        return False
    return True


def hashtags_from_text(*parts: str, limit: int = 6) -> List[str]:
    """Curated viral tags from topic — never dump spoken words as hashtags."""
    blob = strip_reup_mentions(" ".join(str(p or "") for p in parts))
    tags = _boost_topic_tags([], blob)
    return _clean_hashtags(tags)[:limit]


def _clean_hashtags(tags: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in tags or []:
        slug = str(raw or "").strip().lstrip("#")
        if not slug or not _is_usable_tag(slug):
            continue
        key = slug.lower().replace("_", "-")
        if key in seen:
            continue
        seen.add(key)
        out.append(slug)
    return out


def _sanitize_post(item: Dict[str, str], intent: str = "", brief: str = "") -> Dict[str, str]:
    title = strip_reup_mentions(str(item.get("title") or ""))
    caption = strip_reup_mentions(_ensure_contacts(str(item.get("caption") or ""), intent))
    source = brief or ""
    if is_lazy_title(title):
        title = title_from_brief(source, intent)
    if is_lazy_caption(caption):
        caption = caption_from_brief(source or title, intent)
    kept = [
        t for t in _clean_hashtags(item.get("hashtags") or [])
        if t.lower().replace("_", "-") in _VIRAL_TAGS
    ]
    tags = _clean_hashtags(list(hashtags_from_text(brief, title, intent)) + kept)[:6]
    if not tags:
        tags = ["xuhuong", "fyp"]
    # Drop leftover banned hashtags from the caption body, then append clean tags.
    caption = strip_reup_mentions(re.sub(r"(?i)#(?:re-?ups?|viet-?subs?|youtubes?|facebook|tiktoks?|douyin|kuaishou|xiaohongshu|instagram|shorts?)\b", " ", caption))
    caption = re.sub(r"[ \t]{2,}", " ", caption).strip()
    hashline = " ".join(f"#{t.lstrip('#')}" for t in tags)
    body = re.sub(r"#\S+", " ", caption).strip()
    caption = f"{body}\n\n{hashline}".strip() if hashline else body
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


_TITLE_ANGLES = (
    "{}",
    "Chuyện này mới điên: {}",
    "Tình tiết căng: {}",
    "Xem đoạn này: {}",
    "Không ngờ ra nông nỗi này — {}",
    "Clip này đáng xem: {}",
    "Mới xem đã ngã ngửa: {}",
    "Phải xem hết: {}",
)
_CAPTION_HOOKS = (
    "Chuyện này xem xong mới ngã ngửa.",
    "Diễn biến đoạn này đáng xem tới cuối.",
    "Clip này tóm đúng chỗ gay cấn.",
    "Cảnh này xem xong mới rõ vì sao căng.",
    "Một đoạn ngắn nhưng tình tiết đủ đầy.",
    "Nghe xong là muốn xem tiếp ngay.",
    "Tình huống này ít ai ngờ tới.",
    "Đúng là kịch tính từ đầu tới cuối.",
)


def _caption_body(caption: str) -> str:
    text = re.sub(r"#\S+", " ", caption or "")
    return re.sub(r"\s+", " ", text).strip()


def _brief_sentences(brief: str) -> List[str]:
    return [
        chunk.strip(" ,;:-")
        for chunk in re.split(r"[.!?…\n]+", strip_reup_mentions(brief or ""))
        if len(chunk.strip(" ,;:-")) >= 12
    ]


def _varied_title(brief: str, intent: str, index: int) -> str:
    sentences = _brief_sentences(brief)
    seed = sentences[index % len(sentences)] if sentences else title_from_brief(brief, intent)
    if len(seed) > 42:
        seed = seed[:42].rsplit(" ", 1)[0]
    angle = _TITLE_ANGLES[index % len(_TITLE_ANGLES)]
    title = angle.format(seed).strip(" :")
    return title[:70]


def ensure_unique_posts(
    posts: List[Dict[str, str]],
    *,
    brief: str,
    intent: str = "",
) -> List[Dict[str, str]]:
    """Guarantee every page gets a different title and caption body."""
    seen_titles: set[str] = set()
    seen_caps: set[str] = set()
    unique: List[Dict[str, str]] = []
    for index, post in enumerate(posts):
        title = str(post.get("title") or "").strip()
        body = _caption_body(str(post.get("caption") or ""))
        tags = list(post.get("hashtags") or [])
        title_key = re.sub(r"\s+", " ", title).strip().lower()
        cap_key = re.sub(r"\s+", " ", body).strip().lower()[:96]
        if not title_key or title_key in seen_titles or is_lazy_title(title):
            title = _varied_title(brief, intent, index)
            title_key = title.strip().lower()
            spin = 2
            while title_key in seen_titles:
                title = f"{_varied_title(brief, intent, index + spin)}"[:70]
                title_key = title.strip().lower()
                spin += 1
        if not cap_key or cap_key in seen_caps or is_lazy_caption(body):
            hook = _CAPTION_HOOKS[index % len(_CAPTION_HOOKS)]
            summary = caption_from_brief(brief, intent)
            body = f"{hook} {summary}".strip()
            cap_key = re.sub(r"\s+", " ", body).strip().lower()[:96]
            spin = 2
            while cap_key in seen_caps:
                body = f"{_CAPTION_HOOKS[(index + spin) % len(_CAPTION_HOOKS)]} {summary}".strip()
                cap_key = re.sub(r"\s+", " ", body).strip().lower()[:96]
                spin += 1
        seen_titles.add(title_key)
        seen_caps.add(cap_key)
        if not tags:
            tags = hashtags_from_text(brief, title, intent)
        if tags:
            rot = index % len(tags)
            tags = tags[rot:] + tags[:rot]
        hashline = " ".join(f"#{t.lstrip('#')}" for t in tags)
        unique.append({
            "title": title[:70],
            "caption": f"{body}\n\n{hashline}".strip() if hashline else body,
            "hashtags": tags,
        })
    return unique


def _fallback_post(intent: str, brand_title: str, brief: str, page_name: str, index: int) -> Dict[str, str]:
    summary = caption_from_brief(brief, intent)
    hook = _CAPTION_HOOKS[index % len(_CAPTION_HOOKS)]
    title = _varied_title(brief, intent, index)
    if is_lazy_title(title):
        title = usable_brand_title(brand_title, intent) or title
    caption = summary if summary.lower().startswith(hook.lower()[:12]) else f"{hook} {summary}".strip()
    tags = hashtags_from_text(title, brief, intent, page_name)
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
                "NHIỆM VỤ: đọc thoại video rồi TÓM TẮT cốt truyện. Không chép nguyên câu thoại.\n"
                "Title: hook 1 ý chính (dưới 60 ký tự). CẤM 'Video mới', 'Video reup', tên file, "
                "tên nền tảng, và CẤM lấy nguyên câu thoại đầu clip.\n"
                "Caption: 2–4 câu KỂ LẠI chuyện đang xảy ra cho người chưa xem "
                "(ai làm gì, mâu thuẫn gì, đoạn gay cấn). Không dán thoại. Không liệt kê hashtag trong câu.\n"
                "Hashtag: đúng 4–6 keyword VIRAL theo thể loại "
                "(reviewphim, phimhay, cotrang, phimtrung, ngontinh, xuhuong, fyp). "
                "CẤM lấy từng từ thoại (#chưa #chết #nương #chải #trốn #phòng). "
                "CẤM: vietsub, reup, youtube, facebook, tiktok, douyin, shorts, video.\n"
                "BẮT BUỘC: mỗi page một TITLE khác và CAPTION khác (góc kể khác, câu mở khác, "
                "nhấn mạnh tình tiết khác). CẤM copy-paste cùng một bài cho nhiều page.\n"
                "Giữ nguyên số điện thoại và CTA trong 'Nội dung hướng tới' nếu có.\n"
                "Trả JSON đúng schema posts[{index,title,caption,hashtags}].\n\n"
                f"Nội dung hướng tới:\n{intent or '(không có — tóm từ thoại video)'}\n"
                f"Thoại / phụ đề video (chỉ để hiểu cốt truyện, ĐỪNG chép):\n{(video_brief or '(chưa có phụ đề)')[:900]}\n"
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
            return ensure_unique_posts(results, brief=video_brief, intent=intent)
    except Exception as exc:
        logger.warning("agy post writer failed (%s); using unique fallback", exc)

    return ensure_unique_posts(
        [
            _fallback_post(intent, brand_title, video_brief, names[i], i)
            for i in range(len(names))
        ],
        brief=video_brief,
        intent=intent,
    )
