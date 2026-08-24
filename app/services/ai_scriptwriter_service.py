"""
DeepSeek AI Scriptwriter & Dubbing Director Engine.
===================================================
Translates, localizes, diarizes, and assigns character personas to video dialogue transcripts,
producing cinematic, context-aware, pacing-constrained Vietnamese dubbing scripts with emotion tags.
"""

import os
import re
import json
import logging
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)


# Standard Pronoun / Context Clues for Vietnamese Speaker Gender & Role Detection
MALE_INDICATORS = {
    "anh", "chàng", "huynh", "đại ca", "tiểu đệ", "lão gia", "lão phu", "bổn tọa", "bổn vương",
    "thiếu gia", "công tử", "sư phụ", "sư huynh", "sư đệ", "ông", "chú", "bác", "bố", "cha", "hắn", "nam"
}
FEMALE_INDICATORS = {
    "em", "nàng", "muội", "tỷ tỷ", "tiểu thư", "cô nương", "nương tử", "nương nương", "mẫu thân",
    "mẹ", "bà", "cô", "chị", "tiểu muội", "sư tỷ", "sư muội", "nữ"
}
ELDER_INDICATORS = {
    "lão phu", "lão gia", "lão đầu", "tiền bối", "sư phụ", "chưởng môn", "bà lão", "ông lão", "lão bản"
}
CHILD_INDICATORS = {
    "tiểu hài tử", "hài nhi", "bé", "cháu", "con nít", "tiểu quỷ", "tiểu muội muội"
}


class AIScriptwriterService:
    """DeepSeek-powered film localization, speaker diarization, and dubbing director."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.api_key = api_key or settings.DEEPSEEK_API_KEY or os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = (base_url or settings.DEEPSEEK_BASE_URL or "https://api.deepseek.com").rstrip("/")
        self.model = model or settings.DEEPSEEK_MODEL or "deepseek-chat"

    def is_available(self) -> bool:
        """Returns True if DeepSeek API credentials are configured."""
        self.api_key = self.api_key or settings.DEEPSEEK_API_KEY or os.getenv("DEEPSEEK_API_KEY", "")
        return bool(self.api_key and self.api_key.strip())

    def translate_text(self, text: str, target_lang: str = "vi") -> str:
        """Translates a single text string to target language using DeepSeek."""
        if not text or not text.strip():
            return ""
        if not self.is_available():
            return text

        lang_name = {
            "vi": "Tiếng Việt",
            "en": "English",
            "zh": "中文",
            "ja": "日本語",
            "ko": "한국어",
        }.get((target_lang or "vi").lower(), target_lang)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": f"Bạn là chuyên gia dịch thuật đa ngôn ngữ. Hãy dịch đoạn văn bản được cung cấp sang {lang_name} tự nhiên, chuẩn ngữ cảnh, ngắn gọn súc tích. Chỉ trả về duy nhất nội dung dịch bằng {lang_name}, không lặp lại ngôn ngữ gốc nếu khác ngôn ngữ đích, không giải thích thêm, không để trong dấu ngoặc kép."
                },
                {"role": "user", "content": f"Văn bản cần dịch:\n{text.strip()}"}
            ],
            "temperature": 0.3
        }
        try:
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                ans = data["choices"][0]["message"]["content"].strip()
                if (ans.startswith('"') and ans.endswith('"')) or (ans.startswith("'") and ans.endswith("'")):
                    ans = ans[1:-1].strip()
                return ans or text
        except Exception as e:
            logger.warning(f"DeepSeek translate_text failed: {e}")
            return text

    def translate_cues(
        self,
        texts: List[str],
        target_lang: str = "vi",
        style: str = "dub",
        title: str = "",
    ) -> Optional[List[str]]:
        """
        Translates a list of subtitle cues into target language using DeepSeek.
        Preserves exact cue count and numbers. Returns None on failure.
        """
        if not texts:
            return []
        if not self.is_available():
            return None

        lang_name = {
            "vi": "Tiếng Việt",
            "en": "English",
            "zh": "中文",
            "ja": "日本語",
            "ko": "한국어",
        }.get((target_lang or "vi").lower(), target_lang)

        style_n = (style or "dub").lower()
        if style_n == "narrator":
            vibe = (
                "- CHẾ ĐỘ KỂ CHUYỆN: viết lời người dẫn chuyện ngôi 3, diễn đạt lại ý câu đó.\n"
                "- Mỗi câu 1 dòng, ngắn (tối đa ~12 từ) để không đè hình.\n"
            )
        elif style_n == "funny":
            vibe = (
                "- CHẾ ĐỘ VUI NHỘN: dí dỏm, văn mạng Việt, mặn mà — hài trên đúng cảnh.\n"
                "- Mỗi câu 1 dòng, ngắn gọn, không tục.\n"
            )
        else:
            vibe = (
                "- CHẾ ĐỘ TỰ NHIÊN: Dịch sát ý, giữ đại từ xưng hô phù hợp ngữ cảnh.\n"
                "- Ngắn gọn, súc tích để người xem dễ đọc trên video ngắn.\n"
            )

        extra = f"Ngữ cảnh video: {title}\n" if title else ""
        out: List[str] = []
        chunk_size = 20

        for start in range(0, len(texts), chunk_size):
            chunk = [t.strip() for t in texts[start : start + chunk_size]]
            numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(chunk))
            prompt = (
                f"Dịch các câu thoại sau sang {lang_name}.\n"
                f"{extra}"
                "YÊU CẦU BẮT BUỘC:\n"
                f"{vibe}"
                f"- BẮT BUỘC trả về đúng {len(chunk)} dòng được đánh số từ 1 đến {len(chunk)} theo định dạng: 1. <bản dịch>\n"
                "- Không gộp dòng, không bỏ bớt câu nào, không thêm lời chào hay giải thích.\n\n"
                f"{numbered}"
            )
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Bạn là biên dịch viên phụ đề phim và video ngắn chuyên nghiệp. Chỉ trả về đúng danh sách câu đã đánh số theo định dạng 1. <câu dịch>, mỗi câu trên 1 dòng."
                    },
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3
            }
            try:
                req = urllib.request.Request(
                    f"{self.base_url}/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    content = data["choices"][0]["message"]["content"].strip()

                parsed = self._parse_numbered_response(content, len(chunk))
                if parsed and len(parsed) == len(chunk):
                    out.extend(parsed)
                else:
                    logger.warning(f"DeepSeek chunk format mismatch, falling back per-line for chunk {start}")
                    for t in chunk:
                        out.append(self.translate_text(t, target_lang=target_lang))
            except Exception as e:
                logger.warning(f"DeepSeek translate_cues chunk failed: {e}")
                return None

        return out

    def _parse_numbered_response(self, content: str, expected_count: int) -> Optional[List[str]]:
        lines = [line.strip() for line in content.split("\n") if line.strip()]
        result = {}
        for line in lines:
            m = re.match(r"^(\d+)[\.\:\-\)\s]+(.*)$", line)
            if m:
                idx = int(m.group(1))
                text = m.group(2).strip()
                text = re.sub(r"^[\*\"']+|[\*\"']+$", "", text).strip()
                result[idx] = text

        if len(result) == expected_count and all(i in result for i in range(1, expected_count + 1)):
            return [result[i] for i in range(1, expected_count + 1)]
        return None

    def heuristic_diarize_and_localize(
        self,
        segments: List[Dict[str, Any]],
        target_lang: str = "vi"
    ) -> List[Dict[str, Any]]:
        """
        Offline intelligent heuristic fallback for speaker diarization and lip-sync pacing.
        Detects speaker turns, infers gender/character role, and compacts translated text
        to prevent speech overflowing segment duration.
        """
        if not segments:
            return []

        enhanced_segments = []
        current_speaker_idx = 1
        last_end_time = 0.0

        for i, s in enumerate(segments):
            dur = max(0.5, float(s.get("duration", 2.0)))
            # Max words allowed based on speech duration (~3.0 syllables / sec for natural Vietnamese speech)
            max_words = max(2, int(round(dur * 3.0)))

            raw_text = (s.get("translated_text") or s.get("text") or "").strip()

            # 1. Speaker Turn Detection: Gap between dialogue turns or punctuation alternation
            start_t = float(s.get("start_time", 0.0))
            if i > 0 and (start_t - last_end_time > 0.6 or "?" in segments[i - 1].get("text", "")):
                current_speaker_idx = 2 if current_speaker_idx == 1 else 1

            last_end_time = float(s.get("end_time", start_t + dur))

            # 2. Gender and Role Inference from Text Tokens
            lower_text = raw_text.lower()
            tokens = set(re.findall(r"\w+", lower_text))

            gender = "male" if current_speaker_idx == 1 else "female"
            character_name = f"Nhân vật {current_speaker_idx}"
            speaker_id = f"speaker_{current_speaker_idx}"

            if tokens.intersection(CHILD_INDICATORS):
                gender = "child"
                character_name = "Trẻ em"
                speaker_id = "speaker_child"
            elif tokens.intersection(ELDER_INDICATORS):
                gender = "elder_male"
                character_name = "Lão nhân"
                speaker_id = "speaker_elder"
            elif tokens.intersection(FEMALE_INDICATORS) and not tokens.intersection(MALE_INDICATORS):
                gender = "female"
                character_name = "Nữ nhân vật"
                speaker_id = "speaker_female"
            elif tokens.intersection(MALE_INDICATORS) and not tokens.intersection(FEMALE_INDICATORS):
                gender = "male"
                character_name = "Nam nhân vật"
                speaker_id = "speaker_male"

            # 3. Emotion Detection from Context
            emotion = "neutral"
            if any(w in lower_text for w in ["hộc", "chết tiệt", "ngươi dám", "khốn kiếp", "tức chết"]):
                emotion = "angry"
            elif any(w in lower_text for w in ["cứu", "sợ", "nguy rồi", "trời ơi", "á"]):
                emotion = "terrified"
            elif any(w in lower_text for w in ["ha ha", "tuyệt", "vui", "thích quá"]):
                emotion = "cheerful"
            elif any(w in lower_text for w in ["hu hu", "đau", "buồn", "tiếc", "tha thứ"]):
                emotion = "sad"
            elif any(w in lower_text for w in ["suy nghĩ", "ngươi nghe đây", "cẩn thận"]):
                emotion = "dramatic"

            # 4. Pacing Compaction: Trim excessively long translated sentences using smart compaction
            from app.services.lipsync_service import compact_for_duration
            compacted_text = compact_for_duration(raw_text, dur)

            copied = dict(s)
            copied["translated_text"] = compacted_text
            copied["speaker_id"] = speaker_id
            copied["character_name"] = character_name
            copied["gender"] = gender
            copied["emotion"] = emotion
            copied["max_words"] = max_words
            enhanced_segments.append(copied)

        return enhanced_segments

    def localize_script(
        self,
        segments: List[Dict[str, Any]],
        target_lang: str = "vi",
        genre: str = "anime_drama"
    ) -> List[Dict[str, Any]]:
        """
        Localizes a list of timed dialogue segments using DeepSeek LLM with speaker diarization
        and strict pacing constraints.
        
        Args:
            segments: List of dicts with keys 'index', 'start_time', 'end_time', 'duration', 'text'
            target_lang: Output target language (default 'vi')
            genre: Film genre hint ('anime_drama', 'action_movie', 'comedy', 'documentary')
            
        Returns:
            Updated segments with 'speaker_id', 'character_name', 'gender', 'emotion', 'translated_text'
        """
        if not segments:
            return []

        if not self.is_available():
            logger.info("DeepSeek API key not configured. Using heuristic speaker diarization and pacing.")
            return self.heuristic_diarize_and_localize(segments, target_lang=target_lang)

        # Prepare compact payload for DeepSeek
        dialogue_items = []
        for s in segments:
            dur = round(float(s.get("duration", 2.0)), 2)
            max_words = max(2, int(round(dur * 3.0)))
            dialogue_items.append({
                "index": s["index"],
                "start_time": s.get("start_time", 0.0),
                "end_time": s.get("end_time", 0.0),
                "duration_sec": dur,
                "max_words": max_words,
                "text": s.get("translated_text") or s.get("text", "")
            })

        system_prompt = (
            "Bạn là một Đạo Diễn Lồng Tiếng & Biên Kịch Phim Điện Ảnh / Hoạt Hình / Kiếm Hiệp hàng đầu. "
            "Nhiệm vụ của bạn là phân tích kịch bản đối thoại, nhận diện phân vai từng nhân vật (Speaker Diarization), "
            "và chuyển thể kịch bản sang Tiếng Việt cực kỳ tự nhiên, giàu cảm xúc, khớp khẩu hình.\n\n"
            "QUY TẮC BẮT BUỘC:\n"
            "1. PHÂN VAI & GIỮ TÍNH NHẤT QUÁN CỦA NHÂN VẬT:\n"
            "   - Gán 'speaker_id' cố định cho mỗi nhân vật (ví dụ: 'spk_1', 'spk_2', 'spk_3', 'spk_narrator'). Cùng một nhân vật nói ở nhiều phân đoạn khác nhau PHẢI dùng chung một 'speaker_id'.\n"
            "   - Gán 'character_name' (ví dụ: 'Nam chính', 'Nữ chính', 'Sư phụ', 'Tiểu muội', 'Người dẫn').\n"
            "   - Gán 'gender' chính xác: 'male' (nam trẻ), 'female' (nữ trẻ), 'elder_male' (nam già/trung niên), 'elder_female' (nữ già/trung niên), 'child' (trẻ em), 'narrator' (dẫn chuyện).\n"
            "2. TỐI ƯU THỜI LƯỢNG & KHỚP KHẨU HÌNH (PACING):\n"
            "   - Số từ Tiếng Việt của từng câu trong 'translated_text' TUYỆT ĐỐI KHÔNG ĐƯỢC VƯỢT QUÁ giá trị 'max_words' được cung cấp cho câu đó.\n"
            "   - Nếu câu dịch quá dài so với 'max_words', bạn phải chủ động viết lại câu dịch ngắn gọn hơn, lược bỏ các từ đệm không cần thiết, sử dụng các từ ngắn gọn nhưng vẫn giữ nguyên ý nghĩa cốt lõi của câu thoại gốc.\n"
            "3. XƯNG HÔ ĐÚNG NGỮ CẢNH:\n"
            "   - Xưng hô nhất quán xuyên suốt (Huynh - Muội, Ta - Nàng, Sư phụ - Đồ nhi, Anh - Em, Tôi - Cậu).\n"
            "4. GÁN CẢM XÚC (EMOTION):\n"
            "   - Gán 1 trong các nhãn: 'cheerful', 'angry', 'sad', 'whisper', 'terrified', 'dramatic', 'gentle', 'neutral'.\n"
            "5. ĐỊNH DẠNG ĐẦU RA:\n"
            "   - Trả về duy nhất 1 JSON object có key 'dialogues' chứa danh sách object:\n"
            "     { 'index': 1, 'speaker_id': 'spk_1', 'character_name': 'Nam chính', 'gender': 'male', 'emotion': 'dramatic', 'translated_text': '...' }"
        )

        user_content = json.dumps({
            "genre": genre,
            "target_language": target_lang,
            "dialogues": dialogue_items
        }, ensure_ascii=False)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            "temperature": 0.4,
            "response_format": {"type": "json_object"}
        }

        try:
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers=headers
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result_json = json.loads(resp.read().decode("utf-8"))
                content_str = result_json["choices"][0]["message"]["content"]
                parsed = json.loads(content_str)
                localized_list = parsed.get("dialogues", [])

                lookup = {item["index"]: item for item in localized_list if "index" in item}

                # Merge back into segments
                enhanced_segments = []
                for s in segments:
                    idx = s["index"]
                    copied = dict(s)
                    if idx in lookup:
                        matched = lookup[idx]
                        copied["translated_text"] = matched.get("translated_text", s.get("translated_text", s.get("text", "")))
                        copied["speaker_id"] = matched.get("speaker_id", "speaker_1")
                        copied["character_name"] = matched.get("character_name", "Nhân vật")
                        copied["gender"] = matched.get("gender", "male")
                        copied["emotion"] = matched.get("emotion", "neutral")
                    else:
                        copied["translated_text"] = s.get("translated_text", s.get("text", ""))
                        copied["speaker_id"] = "speaker_1"
                        copied["character_name"] = "Nhân vật"
                        copied["gender"] = "male"
                        copied["emotion"] = "neutral"
                    enhanced_segments.append(copied)

                logger.info(f"Successfully localized & diarized {len(enhanced_segments)} dialogue segments with DeepSeek LLM")
                return enhanced_segments

        except Exception as e:
            logger.error(f"DeepSeek script localization failed: {e}. Falling back to heuristic diarization.")
            return self.heuristic_diarize_and_localize(segments, target_lang=target_lang)


# Singleton Instance
ai_scriptwriter_service = AIScriptwriterService()
