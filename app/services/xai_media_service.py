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


def _clean_cue(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def compact_vi_cue(text: str, max_chars: int = 42) -> str:
    """Wrap burned-in lines cleanly so they sit at the bottom without cutting text."""
    t = _clean_cue(text)
    if len(t) <= max_chars:
        return t
    words = t.split()
    if not words:
        return t
    lines = []
    current_line = []
    budget = max(18, max_chars // 2)
    for w in words:
        if current_line and (sum(len(x) for x in current_line) + len(current_line) + len(w) > budget):
            lines.append(" ".join(current_line))
            current_line = [w]
        else:
            current_line.append(w)
    if current_line:
        lines.append(" ".join(current_line))
    return "\n".join(lines).strip()


def translate_cues(
    texts: List[str],
    target_lang: str = "vi",
    style: str = "dub",
    title: str = "",
) -> Optional[List[str]]:
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
        "pt": "Português",
    }.get((target_lang or "vi").lower(), target_lang)

    style_n = (style or "dub").lower()
    style_n = resolve_vietsub_style(style_n, 0)
    if style_n == "narrator":
        vibe = (
            "- CHẾ ĐỘ KỂ CHUYỆN: viết lời người dẫn chuyện ngôi 3, diễn đạt LẠI ý câu đó.\n"
            "- Câu i phải nói đúng khoảnh khắc câu i gốc (khớp video, không nhảy cảnh).\n"
            "- Nghe như kể: 'Lúc này…', 'Rồi nó…'. Không dịch word-by-word, không bịa thêm tình tiết.\n"
            "- Mỗi câu 1 dòng, ngắn (tối đa ~12 từ) để không đè hình.\n"
        )
    elif style_n == "funny":
        vibe = (
            "- CHẾ ĐỘ VUI NHỘN: dí dỏm, văn mạng Việt, mặn vừa — hài trên ĐÚNG cảnh đang xảy ra.\n"
            "- Câu i phải khớp nội dung câu i gốc, không lạc đề, không tục.\n"
            "- Được thêm 1 nhịp hài ngắn, không kéo dài, không bịa tình tiết mới.\n"
            "- Mỗi câu 1 dòng, ngắn (tối đa ~12 từ).\n"
        )
    elif style_n == "recap":
        vibe = (
            "- Đây là thoại gốc để nắm cốt truyện, vẫn dịch N câu ngắn, đúng nghĩa.\n"
        )
    else:
        vibe = (
            "- CHẾ ĐỘ GỐC: tuân theo ĐÚNG lời thoại / chữ trên video.\n"
            "- Dịch sát ý, giữ ngôi gốc (tôi/mày nếu là hội thoại). Không kể lại, không thêm hài, không bịa.\n"
            "- Ngắn bằng hoặc ngắn hơn câu gốc để khớp khẩu hình / timeline, không đè hình.\n"
        )

    extra = f"Ngữ cảnh video: {title}\n" if title else ""

    out: List[str] = []
    chunk_size = 24
    for start in range(0, len(texts), chunk_size):
        chunk = [_clean_cue(t) for t in texts[start : start + chunk_size]]
        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(chunk))
        prompt = (
            f"Dịch thoại video ngắn sang {lang_name} — tiếng Việt đời thường, đúng nghĩa.\n"
            f"{extra}"
            "Yêu cầu:\n"
            f"{vibe}"
            "- GIỮ NGUYÊN số thứ tự, mỗi câu 1 dòng: N. <bản dịch>\n"
            "- Dịch Ý, không dịch word-by-word. Nói như người Việt đang xem clip.\n"
            "- CẤM câu vô nghĩa, CẤM bịa thêm (y tế, tôn giáo, nội dung không có trong gốc).\n"
            "- Không chú thích, không gộp câu, không bỏ số.\n\n"
            f"{numbered}"
        )
        payload = {
            "model": GROK_MODEL,
            "temperature": 0.35,
            "max_tokens": 1800,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Bạn là biên dịch viên TikTok Việt. Chỉ trả về các dòng đã đánh số. "
                        "Văn nói tự nhiên, không giọng máy."
                    ),
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
            with urllib.request.urlopen(req, timeout=90) as resp:
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
        out.extend(compact_vi_cue(p) for p in parsed)

    return out if len(out) == len(texts) else None


def build_recap_lines(
    title: str,
    texts: List[str],
    target_lang: str = "vi",
    n: int = 8,
) -> Optional[List[str]]:
    """Ask Grok for an N-sentence narrator recap."""
    key = _api_key()
    if not key:
        return None
    lang_name = {
        "vi": "tiếng Việt",
        "en": "English",
        "th": "ภาษาไทย",
        "id": "Bahasa Indonesia",
        "ja": "日本語",
        "ko": "한국어",
        "pt": "Português",
    }.get((target_lang or "vi").lower(), target_lang)
    blob = " ".join((t or "").strip() for t in texts[:40])[:1800]
    prompt = (
        f"Viết {n} câu LỜI KỂ LẠI (voice-over) bằng {lang_name} cho video ngắn/dài.\n"
        f"Tiêu đề: {title or '(không có)'}\n"
        f"Thoại/nội dung gốc (rút gọn): {blob or '(chỉ có tiêu đề)'}\n"
        "Yêu cầu: ngôi kể chuyện, dễ nghe, mỗi câu 1 dòng số thứ tự N. <câu>. "
        "Không hashtag, không emoji, không chú thích."
    )
    payload = {
        "model": GROK_MODEL,
        "temperature": 0.4,
        "max_tokens": 900,
        "messages": [
            {"role": "system", "content": "Bạn là người dẫn chuyện video. Chỉ trả về các dòng đã đánh số."},
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        XAI_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Grok recap failed: {e}")
        return None
    content = ((((body.get("choices") or [{}])[0]).get("message") or {}).get("content") or "").strip()
    parsed = _parse_numbered(content, n)
    if parsed:
        return parsed
    cleaned = [re.sub(r"^\d+[\.\)\:\-]\s*", "", ln).strip() for ln in content.split("\n") if ln.strip()]
    cleaned = [c for c in cleaned if c]
    return cleaned[:n] if cleaned else None


def recap_to_srt(lines: List[str], duration: float, srt_path: str) -> str:
    from app.services.tts_service import format_srt_timestamp

    n = max(1, len(lines))
    span = max(8.0, float(duration) * 0.82)
    slot = span / n
    os.makedirs(os.path.dirname(os.path.abspath(srt_path)) or ".", exist_ok=True)
    chunks = []
    t = 1.0
    for i, line in enumerate(lines, start=1):
        start = t
        end = min(float(duration) - 0.2, t + max(3.5, slot * 0.85))
        if end <= start:
            end = start + 3.0
        chunks.append(f"{i}\n{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n{line.strip()}\n")
        t += slot
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(chunks) + "\n")
    return srt_path


def resolve_vietsub_style(style: str, duration: float = 0.0) -> str:
    from app.services.vietsub_rules import resolve_vietsub_style as _resolve

    return _resolve(style, duration)


LANG_DEFAULT_VOICE = {
    "vi": "vieneu:Trúc Ly",
    "en": "en-US-AriaNeural",
    "th": "th-TH-PremwadeeNeural",
    "id": "id-ID-GadisNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "pt": "pt-BR-FranciscaNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
}



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
