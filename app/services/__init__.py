"""
Services package initialization. Exports core watermark removal engines & managers.
"""

from app.services.watermark_service import (
    remove_watermark,
    convert_roi_percentage_to_pixels,
    inpaint_video_ffmpeg,
    WatermarkService,
    WatermarkEngineManager
)
from app.services.opencv_inpainter import (
    inpaint_video_opencv,
    OpenCVInpainter
)
from app.services.lama_inpainter import (
    inpaint_video_lama,
    LaMaInpainter,
    LamaInpainter,
    LaMaNotAvailableError,
    LamaNotAvailableError,
    LaMaInpaintError,
    LamaInpaintError
)
from app.services.md5_service import (
    calculate_file_md5,
    modify_video_md5_fast,
    modify_md5,
    modify_file_md5,
    modify_video_metadata
)
from app.services.reup_service import (
    find_ffmpeg_binary,
    detect_h264_encoder,
    build_reup_filtergraph,
    process_reup_video
)
from app.services.queue_manager import (
    BatchQueueManager
)
from app.services.pyvideotrans_service import (
    PyVideoTransService,
    PyVideoTransError
)

__all__ = [
    "remove_watermark",
    "convert_roi_percentage_to_pixels",
    "inpaint_video_ffmpeg",
    "WatermarkService",
    "WatermarkEngineManager",
    "inpaint_video_opencv",
    "OpenCVInpainter",
    "inpaint_video_lama",
    "LaMaInpainter",
    "LamaInpainter",
    "LaMaNotAvailableError",
    "LamaNotAvailableError",
    "LaMaInpaintError",
    "LamaInpaintError",
    "calculate_file_md5",
    "modify_video_md5_fast",
    "modify_md5",
    "modify_file_md5",
    "modify_video_metadata",
    "find_ffmpeg_binary",
    "detect_h264_encoder",
    "build_reup_filtergraph",
    "process_reup_video",
    "BatchQueueManager",
    "PyVideoTransService",
    "PyVideoTransError",
]

