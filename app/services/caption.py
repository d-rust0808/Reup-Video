"""Build Vietnamese social captions + hashtags for reup posts."""

from __future__ import annotations

from typing import Iterable, Optional


def build_caption(
    title: Optional[str] = None,
    platform: Optional[str] = None,
    extra_tags: Optional[Iterable[str]] = None,
) -> str:
    from app.services.post_writer import (
        _sanitize_post,
        is_lazy_caption,
        is_lazy_title,
        strip_reup_mentions,
    )

    head = strip_reup_mentions((title or "").strip())
    tags = [str(t or "").strip() for t in (extra_tags or []) if str(t or "").strip()]
    cleaned = _sanitize_post(
        {"title": head, "caption": head, "hashtags": tags},
        brief=head,
    )
    caption = cleaned["caption"]
    if is_lazy_title(cleaned["title"]) and is_lazy_caption(caption):
        return caption
    return caption
