"""
Inpainting and Subtitle/Watermark Removal Core Package.
=========================================================
Integrates text detection, adaptive ROI masking, Telea/Navier-Stokes, and LaMa inpainting.
"""

from app.services.subtitle_detector import SubtitleDetector
from app.services.opencv_inpainter import OpenCVInpainter
from app.services.lama_inpainter import LaMaInpainter, LamaInpainter
from app.services.watermark_service import WatermarkService

__all__ = [
    "SubtitleDetector",
    "OpenCVInpainter",
    "LaMaInpainter",
    "LamaInpainter",
    "WatermarkService"
]
