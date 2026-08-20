"""
Multi-Engine TTS Providers Implementation.
===========================================
Unified, decoupled providers for Edge-TTS, gTTS, Coqui TTS, Melo TTS, and Piper TTS.
"""

import os
import sys
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Module directory paths for internal TTS providers
MODULE_TTS_DIR = Path(__file__).resolve().parent
COQUI_REPO_PATH = str(MODULE_TTS_DIR / "coqui")
MELO_PARENT_PATH = str(MODULE_TTS_DIR)

if COQUI_REPO_PATH not in sys.path and os.path.exists(COQUI_REPO_PATH):
    sys.path.insert(0, COQUI_REPO_PATH)
if MELO_PARENT_PATH not in sys.path and os.path.exists(MELO_PARENT_PATH):
    sys.path.insert(0, MELO_PARENT_PATH)


class BaseTTSProvider(ABC):
    """Abstract base class for all speech synthesis engine providers."""

    @abstractmethod
    async def generate(
        self,
        text: str,
        lang: str = "vi",
        voice: Optional[str] = None,
        output_path: Optional[str] = None,
        **kwargs
    ) -> str:
        """Synthesizes speech to output_path and returns destination path."""
        pass


class EdgeTTSProvider(BaseTTSProvider):
    """Microsoft Edge-TTS provider implementation."""

    async def generate(
        self,
        text: str,
        lang: str = "vi",
        voice: Optional[str] = None,
        output_path: Optional[str] = None,
        rate: str = "+0%",
        pitch: str = "+0Hz",
        volume: str = "+0%",
        **kwargs
    ) -> str:
        try:
            import edge_tts
        except ImportError as e:
            raise RuntimeError("edge-tts package is not installed.") from e

        if not voice:
            voice = "vi-VN-HoaiMyNeural" if lang == "vi" else "en-US-AvaNeural"

        # Voice style presets mapping for Vietnamese
        actual_voice = voice
        actual_rate = rate
        actual_pitch = pitch

        if actual_voice == "gtts-vi":
            gtts_prov = GTTSProvider()
            return await gtts_prov.generate(text=text, lang="vi", output_path=output_path)
        elif actual_voice == "vi-VN-HoaiMy-Fast":
            actual_voice = "vi-VN-HoaiMyNeural"
            actual_rate = "+15%"
            actual_pitch = "+1Hz"
        elif actual_voice == "vi-VN-HoaiMy-Warm":
            actual_voice = "vi-VN-HoaiMyNeural"
            actual_rate = "-5%"
            actual_pitch = "-1Hz"
        elif actual_voice == "vi-VN-NamMinh-Fast":
            actual_voice = "vi-VN-NamMinhNeural"
            actual_rate = "+14%"
            actual_pitch = "+1Hz"
        elif actual_voice == "vi-VN-NamMinh-Deep":
            actual_voice = "vi-VN-NamMinhNeural"
            actual_rate = "-8%"
            actual_pitch = "-2Hz"

        if not output_path:
            filename = f"edge_{hash(text) & 0xffffffff:08x}.mp3"
            output_path = os.path.join("data/outputs/tts", filename)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        communicate = edge_tts.Communicate(text=text, voice=actual_voice, rate=actual_rate, pitch=actual_pitch, volume=volume)
        await communicate.save(output_path)


        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError(f"EdgeTTS failed to generate audio output file: {output_path}")

        return output_path


class GTTSProvider(BaseTTSProvider):
    """Google TTS (gTTS) provider implementation."""

    async def generate(
        self,
        text: str,
        lang: str = "vi",
        voice: Optional[str] = None,
        output_path: Optional[str] = None,
        **kwargs
    ) -> str:
        try:
            from gtts import gTTS
        except ImportError as e:
            raise RuntimeError("gtts package is not installed.") from e

        if not output_path:
            filename = f"gtts_{hash(text) & 0xffffffff:08x}.mp3"
            output_path = os.path.join("data/outputs/tts", filename)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        tts = gTTS(text=text, lang=lang)
        tts.save(output_path)

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError(f"gTTS failed to write file: {output_path}")

        return output_path


class CoquiTTSProvider(BaseTTSProvider):
    """Coqui-TTS neural engine provider implementation."""

    async def generate(
        self,
        text: str,
        lang: str = "vi",
        voice: Optional[str] = None,
        output_path: Optional[str] = None,
        model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2",
        **kwargs
    ) -> str:
        try:
            if COQUI_REPO_PATH not in sys.path and os.path.exists(COQUI_REPO_PATH):
                sys.path.insert(0, COQUI_REPO_PATH)
            from TTS.api import TTS
        except ImportError as e:
            raise RuntimeError(
                "Coqui TTS is not installed or available in active environment."
            ) from e

        if not output_path:
            filename = f"coqui_{hash(text) & 0xffffffff:08x}.wav"
            output_path = os.path.join("data/outputs/tts", filename)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        tts_api = TTS(model_name=model_name, progress_bar=False)
        tts_api.tts_to_file(text=text, file_path=output_path)

        return output_path


class MeloTTSProvider(BaseTTSProvider):
    """MeloTTS neural engine provider implementation."""

    async def generate(
        self,
        text: str,
        lang: str = "EN",
        voice: Optional[str] = None,
        output_path: Optional[str] = None,
        speaker_id: Optional[str] = None,
        **kwargs
    ) -> str:
        try:
            if MELO_PARENT_PATH not in sys.path and os.path.exists(MELO_PARENT_PATH):
                sys.path.insert(0, MELO_PARENT_PATH)
            from melo.api import TTS as MeloTTS
        except ImportError as e:
            raise RuntimeError(
                "Melo TTS is not installed or available in active environment."
            ) from e

        if not output_path:
            filename = f"melo_{hash(text) & 0xffffffff:08x}.wav"
            output_path = os.path.join("data/outputs/tts", filename)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        language = lang.upper() if lang else "EN"
        model = MeloTTS(language=language)
        spkr_id = speaker_id or (list(model.hps.data.spk2id.values())[0] if hasattr(model, "hps") and hasattr(model.hps.data, "spk2id") else 0)
        model.tts_to_file(text, spkr_id, output_path)

        return output_path


class TTSProviderManager:
    """Manager for retrieving and ordering TTS engine providers."""

    def __init__(self):
        self.providers: Dict[str, BaseTTSProvider] = {
            "edge-tts": EdgeTTSProvider(),
            "gtts": GTTSProvider(),
            "coqui-tts": CoquiTTSProvider(),
            "melo-tts": MeloTTSProvider(),
            "melo": MeloTTSProvider(),
        }

    def get_provider(self, engine_name: str) -> BaseTTSProvider:
        key = engine_name.lower()
        if key not in self.providers:
            raise ValueError(f"Unsupported TTS engine: {engine_name}")
        return self.providers[key]


_provider_manager = TTSProviderManager()

def get_tts_provider(engine_name: str = "edge-tts") -> BaseTTSProvider:
    return _provider_manager.get_provider(engine_name)
