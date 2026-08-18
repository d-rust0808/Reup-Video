"""
Multi-Engine TTS System Package.
=================================
Provides modular TTS providers: Edge-TTS, gTTS, Coqui TTS, Melo TTS, and Piper TTS.
"""

from app.modules.tts.providers import TTSProviderManager, get_tts_provider

__all__ = ["TTSProviderManager", "get_tts_provider"]
