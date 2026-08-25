"""
Multi-Engine TTS Providers Implementation.
===========================================
Unified, decoupled providers for Edge-TTS, gTTS, Coqui TTS, Melo TTS, and Piper TTS.
"""

import os
import sys
import logging
import asyncio
import threading
import shutil
import subprocess
import tempfile
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

        actual_voice = voice
        actual_rate = rate
        actual_pitch = pitch
        vlow = str(actual_voice or "").lower()
        if actual_voice == "gtts-vi" or vlow.startswith("kokoro"):
            actual_voice = "vi-VN-HoaiMyNeural"

        if not output_path:
            filename = f"edge_{hash(text) & 0xffffffff:08x}.mp3"
            output_path = os.path.join("data/outputs/tts", filename)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        try:
            communicate = edge_tts.Communicate(text=text, voice=actual_voice, rate=actual_rate, pitch=actual_pitch, volume=volume)
            await communicate.save(output_path)
        except Exception:
            # Fallback to pure standard voice if custom pitch/rate encounters rate limiting or formatting mismatch
            communicate = edge_tts.Communicate(text=text, voice=actual_voice, rate="+0%", pitch="+0Hz", volume="+0%")
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


class VieNeuTTSProvider(BaseTTSProvider):
    """Local Vietnamese TTS using VieNeu-TTS v3 Turbo (ONNX on CPU/macOS)."""

    _model = None
    _lock = threading.Lock()

    async def generate(
        self,
        text: str,
        lang: str = "vi",
        voice: Optional[str] = None,
        output_path: Optional[str] = None,
        **kwargs
    ) -> str:
        if (lang or "vi").lower() not in ("vi", "en"):
            raise RuntimeError("VieNeu-TTS currently supports Vietnamese and English text only")
        try:
            from vieneu import Vieneu
        except ImportError as e:
            raise RuntimeError(
                "VieNeu-TTS is not installed. Install vieneu==3.3.0 to enable the local Vietnamese engine."
            ) from e

        if not output_path:
            output_path = os.path.join("data/outputs/tts", f"vieneu_{hash(text) & 0xffffffff:08x}.wav")
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        selected_voice = (voice or "Trúc Ly").strip()
        if selected_voice.lower().startswith("vieneu:"):
            selected_voice = selected_voice.split(":", 1)[1].strip() or "Trúc Ly"
        if "-" in selected_voice or selected_voice.lower() in {"female", "male", "neutral"}:
            selected_voice = "Adam"

        def _synthesize() -> str:
            # The model is expensive to initialize and its inference state is not
            # guaranteed to be thread-safe, so share one instance per worker process.
            model_cls = type(self)
            needs_convert = not output_path.lower().endswith(".wav")
            wav_path = output_path
            if needs_convert:
                fd, wav_path = tempfile.mkstemp(prefix="vieneu_", suffix=".wav")
                os.close(fd)
            try:
                with model_cls._lock:
                    if model_cls._model is None:
                        model_cls._model = Vieneu(mode="v3turbo", backend="onnx")
                    audio = model_cls._model.infer(text, voice=selected_voice)
                    model_cls._model.save(audio, wav_path)
                if needs_convert:
                    ffmpeg_bin = shutil.which("ffmpeg")
                    if not ffmpeg_bin:
                        raise RuntimeError("FFmpeg is required to convert VieNeu-TTS WAV output")
                    res = subprocess.run(
                        [ffmpeg_bin, "-y", "-i", wav_path, output_path],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if res.returncode != 0:
                        raise RuntimeError(f"VieNeu-TTS audio conversion failed: {(res.stderr or '')[-400:]}")
            finally:
                if needs_convert and os.path.exists(wav_path):
                    os.remove(wav_path)
            return output_path

        result = await asyncio.to_thread(_synthesize)
        if not os.path.exists(result) or os.path.getsize(result) < 256:
            raise RuntimeError(f"VieNeu-TTS failed to write audio output: {result}")
        return result


class KokoroTTSProvider(BaseTTSProvider):
    """
    Kokoro-82M Next-Gen Open-Source Neural TTS Provider.
    Lightweight 82M parameter model delivering high-fidelity 24kHz audio synthesis.
    """
    _pipeline = None

    async def generate(
        self,
        text: str,
        lang: str = "vi",
        voice: Optional[str] = None,
        output_path: Optional[str] = None,
        speed: float = 1.0,
        **kwargs
    ) -> str:
        try:
            from kokoro import KPipeline
            import soundfile as sf
        except ImportError as e:
            logger.warning(f"Kokoro package not available ({e}). Falling back to Edge-TTS.")
            edge_prov = EdgeTTSProvider()
            return await edge_prov.generate(text=text, lang="vi", voice="vi-VN-HoaiMyNeural", output_path=output_path)

        if not output_path:
            filename = f"kokoro_{hash(text) & 0xffffffff:08x}.wav"
            output_path = os.path.join("data/outputs/tts", filename)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        try:
            if KokoroTTSProvider._pipeline is None:
                KokoroTTSProvider._pipeline = KPipeline(lang_code='a')

            pipeline = KokoroTTSProvider._pipeline
            kokoro_voice = voice or "af_heart"
            if kokoro_voice.startswith("kokoro-") or kokoro_voice.startswith("vi-"):
                kokoro_voice = "af_heart"

            generator = pipeline(text, voice=kokoro_voice, speed=speed, split_pattern=r'\n+')
            audio_segments = []
            for _, _, audio in generator:
                if audio is not None and len(audio) > 0:
                    audio_segments.append(audio)

            if audio_segments:
                import numpy as np
                full_audio = np.concatenate(audio_segments)
                sf.write(output_path, full_audio, 24000)
            else:
                raise RuntimeError("Kokoro produced empty audio stream")

        except Exception as e:
            logger.warning(f"Kokoro-82M synthesis error ({e}). Falling back to Edge-TTS.")
            edge_prov = EdgeTTSProvider()
            return await edge_prov.generate(text=text, lang="vi", voice="vi-VN-HoaiMyNeural", output_path=output_path)

        return output_path


class TTSProviderManager:
    """Manager for retrieving and ordering TTS engine providers."""

    def __init__(self):
        self.providers: Dict[str, BaseTTSProvider] = {
            "edge-tts": EdgeTTSProvider(),
            "gtts": GTTSProvider(),
            "kokoro": KokoroTTSProvider(),
            "kokoro-tts": KokoroTTSProvider(),
            "kokoro-82m": KokoroTTSProvider(),
            "coqui-tts": CoquiTTSProvider(),
            "melo-tts": MeloTTSProvider(),
            "melo": MeloTTSProvider(),
            "vieneu": VieNeuTTSProvider(),
            "vieneu-tts": VieNeuTTSProvider(),
        }

    def get_provider(self, engine_name: str) -> BaseTTSProvider:
        key = engine_name.lower()
        if key in self.providers:
            return self.providers[key]
        raise ValueError(f"Unsupported TTS engine: {engine_name}")


_provider_manager = TTSProviderManager()

def get_tts_provider(engine_name: str = "edge-tts") -> BaseTTSProvider:
    return _provider_manager.get_provider(engine_name)
