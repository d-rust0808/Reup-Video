"""
xAI (Grok) helpers for subtitle translation.
STT for Chinese Douyin still uses faster-whisper — Grok STT does not list zh.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import List, Optional

logger = logging.getLogger(__name__)

XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"
GROK_MODEL = "grok-4.5"


def _api_key() -> str:
    return (os.environ.get("XAI_API_KEY") or "").strip()


def is_available() -> bool:
    return bool(_api_key())


def translate_cues(texts: List[str], target_lang: str = "vi") -> Optional[List[str]]:
    """Translate subtitle cues with Grok. Returns None if the API is unavailable or fails."""
    if not texts:
        return []
    key = _api_key()
    if not key:
        return None

    lang_name = {
        "vi": "tiếng Việt",
        "en": "English",
        "zh": "中文",
        "ja": "日本語",
        "ko": "한국어",
        "th": "ภาษาไทย",
        "id": "Bahasa Indonesia",
    }.get((target_lang or "vi").lower(), target_lang)

    out: List[str] = []
    chunk_size = 40
    for start in range(0, len(texts), chunk_size):
        chunk = texts[start : start + chunk_size]
        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(chunk))
        prompt = (
            f"Dịch từng câu thoại video ngắn (Douyin/TikTok) sang {lang_name}.\n"
            "Yêu cầu:\n"
            "- Văn phong mạng xã hội, tự nhiên, đúng ngữ cảnh.\n"
            "- GIỮ NGUYÊN số thứ tự, mỗi câu 1 dòng: N. <bản dịch>\n"
            "- Dịch NGẮN GỌN bằng hoặc ngắn hơn câu gốc để khớp khẩu hình / timeline.\n"
            "- Không thêm chú thích, không gộp câu, không bỏ số thứ tự.\n\n"
            f"{numbered}"
        )
        payload = {
            "model": GROK_MODEL,
            "temperature": 0.2,
            "max_tokens": 1800,
            "messages": [
                {
                    "role": "system",
                    "content": "Bạn là biên dịch viên lồng tiếng video ngắn. Chỉ trả về các dòng đã đánh số.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        req = urllib.request.Request(
            XAI_CHAT_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            logger.warning(f"Grok translate failed: {e}")
            return None

        content = (
            (((body.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
        ).strip()
        if not content:
            return None

        parsed = _parse_numbered(content, len(chunk))
        if parsed is None:
            logger.warning("Grok translate returned unparsable numbered lines")
            return None
        out.extend(parsed)

    return out if len(out) == len(texts) else None


def _parse_numbered(content: str, expected: int) -> Optional[List[str]]:
    lines = [ln.strip() for ln in content.replace("\r\n", "\n").split("\n") if ln.strip()]
    found: dict[int, str] = {}
    for ln in lines:
        m = re.match(r"^(\d+)[\.\)\:\-]\s*(.+)$", ln)
        if m:
            found[int(m.group(1))] = m.group(2).strip()
    if len(found) == expected and all(i in found for i in range(1, expected + 1)):
        return [found[i] for i in range(1, expected + 1)]
    # Fallback: same number of non-empty lines, strip leading numbers if present
    cleaned = [re.sub(r"^\d+[\.\)\:\-]\s*", "", ln).strip() for ln in lines]
    cleaned = [c for c in cleaned if c]
    if len(cleaned) == expected:
        return cleaned
    return None
