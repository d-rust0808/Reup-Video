"""Build Vietnamese social captions + hashtags for reup posts."""

from __future__ import annotations

from typing import Iterable, Optional

PLATFORM_TAGS = {
    "douyin": ["#reup", "#douyin", "#vietsub", "#fyp", "#xuhuong"],
    "kuaishou": ["#reup", "#kuaishou", "#vietsub", "#fyp"],
    "xiaohongshu": ["#reup", "#xiaohongshu", "#vietsub"],
    "tiktok": ["#reup", "#vietsub", "#fyp", "#xuhuong", "#tiktok"],
    "youtube_shorts": ["#reup", "#vietsub", "#shorts", "#youtube"],
    "youtube": ["#reup", "#vietsub", "#youtube"],
    "facebook": ["#reup", "#vietsub", "#reels"],
    "instagram": ["#reup", "#vietsub", "#reels", "#fyp"],
}


def build_caption(
    title: Optional[str] = None,
    platform: Optional[str] = None,
    extra_tags: Optional[Iterable[str]] = None,
) -> str:
    head = (title or "Video reup").strip() or "Video reup"
    tags = list(PLATFORM_TAGS.get((platform or "").lower(), ["#reup", "#vietsub", "#fyp"]))
    for t in extra_tags or []:
        tag = str(t or "").strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag.lstrip("#")
        if tag.lower() not in {x.lower() for x in tags}:
            tags.append(tag)
    return f"{head}\n\n{' '.join(tags)}"
