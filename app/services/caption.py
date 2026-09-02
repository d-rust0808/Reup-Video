"""Build Vietnamese social captions + hashtags for reup posts."""

from __future__ import annotations

from typing import Iterable, Optional

PLATFORM_TAGS = {
    "douyin": ["#fyp", "#xuhuong"],
    "kuaishou": ["#fyp"],
    "xiaohongshu": ["#fyp"],
    "tiktok": ["#fyp", "#xuhuong"],
    "youtube_shorts": ["#shorts"],
    "youtube": ["#shorts"],
    "facebook": ["#reels"],
    "instagram": ["#reels", "#fyp"],
}

_BANNED_TAGS = {
    "reup", "re-up", "reups", "vietsub", "viet-sub", "vietsubs",
    "youtube", "facebook", "instagram", "douyin", "kuaishou", "xiaohongshu",
}


def build_caption(
    title: Optional[str] = None,
    platform: Optional[str] = None,
    extra_tags: Optional[Iterable[str]] = None,
) -> str:
    from app.services.post_writer import is_lazy_title, strip_reup_mentions, title_from_brief

    head = strip_reup_mentions((title or "").strip())
    if is_lazy_title(head):
        head = title_from_brief(head)
    tags = list(PLATFORM_TAGS.get((platform or "").lower(), ["#fyp"]))
    for t in extra_tags or []:
        tag = str(t or "").strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag.lstrip("#")
        slug = tag.lstrip("#").lower().replace("_", "-")
        if slug in _BANNED_TAGS:
            continue
        if tag.lower() not in {x.lower() for x in tags}:
            tags.append(tag)
    return f"{head}\n\n{' '.join(tags)}".strip()
