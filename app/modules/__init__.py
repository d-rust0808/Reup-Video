"""
Reup-Video Core Modules Architecture.
=====================================
Unified internal system modules for Video Processing, AI Subtitle Translation,
TTS Synthesis, Watermark Removal, Audio Processing, Scrapers, and Queue Pipeline.
"""

import os
import sys
from pathlib import Path

MODULES_DIR = Path(__file__).resolve().parent

paths_to_register = [
    str(MODULES_DIR / "videotrans"),
    str(MODULES_DIR / "tts"),
    str(MODULES_DIR / "tts" / "coqui"),
]

for p in paths_to_register:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)
