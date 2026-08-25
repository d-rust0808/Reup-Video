"""LaMa ONNX inpainting (Carve/LaMa-ONNX, 512×512). Falls back to Telea if weights missing."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional, Tuple, cast

import numpy as np

logger = logging.getLogger(__name__)

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    cv2 = cast(Any, None)
    HAS_CV2 = False

try:
    import onnxruntime as ort
    HAS_ONNX = True
except ImportError:
    ort = cast(Any, None)
    HAS_ONNX = False

MODEL_REL = os.path.join("data", "models", "lama.onnx")
HF_REPO = "Carve/LaMa-ONNX"
HF_FILE = "lama.onnx"

_SESSION = None
_SESSION_FAILED = False


class LaMaNotAvailableError(Exception):
    pass


class LaMaInpaintError(Exception):
    pass


LamaNotAvailableError = LaMaNotAvailableError
LamaInpaintError = LaMaInpaintError


def _model_path() -> Optional[str]:
    for path in (MODEL_REL, os.path.abspath(MODEL_REL), "data/models/lama_fp32.onnx"):
        if path and os.path.isfile(path) and os.path.getsize(path) > 1_000_000:
            return os.path.abspath(path)
    return None


def ensure_lama_weights() -> Optional[str]:
    existing = _model_path()
    if existing:
        return existing
    try:
        from huggingface_hub import hf_hub_download
        os.makedirs(os.path.dirname(MODEL_REL), exist_ok=True)
        path = hf_hub_download(repo_id=HF_REPO, filename=HF_FILE, local_dir=os.path.dirname(MODEL_REL), timeout=15)
        if path and os.path.isfile(path) and os.path.getsize(path) > 1_000_000:
            dest = os.path.abspath(MODEL_REL)
            if os.path.abspath(path) != dest:
                import shutil
                shutil.copyfile(path, dest)
            return dest
    except Exception as e:
        logger.warning(f"LaMa weight download skipped: {e}")
    return _model_path()


def get_lama_session():
    global _SESSION, _SESSION_FAILED
    if _SESSION is not None:
        return _SESSION
    if _SESSION_FAILED or not HAS_ONNX:
        return None
    path = ensure_lama_weights()
    if not path:
        _SESSION_FAILED = True
        return None
    try:
        opts = ort.SessionOptions()
        try:
            from app.config import settings
            configured_threads = int(settings.ONNX_INTRA_OP_THREADS)
        except Exception:
            configured_threads = 8
        # Leave headroom for concurrent queue workers and FFmpeg processes.
        opts.intra_op_num_threads = max(1, min(16, configured_threads))
        # ORT_ENABLE_ALL / ORT_ENABLE_EXTENDED segfault (SIGSEGV) loading lama.onnx on
        # onnxruntime 1.19.x. BASIC loads and runs full inference cleanly.
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        providers = ["CPUExecutionProvider"]
        avail = ort.get_available_providers()
        if "CUDAExecutionProvider" in avail:
            providers.insert(0, "CUDAExecutionProvider")
        if "CoreMLExecutionProvider" in avail:
            providers.insert(0, "CoreMLExecutionProvider")
        _SESSION = ort.InferenceSession(path, sess_options=opts, providers=providers)
        logger.info("LaMa ONNX ready (%s)", path)
        return _SESSION
    except Exception as e:
        logger.warning(f"LaMa ONNX load failed: {e}")
        _SESSION_FAILED = True
        return None


def lama_inpaint_bgr(frame_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Fill mask (255=hole) with LaMa. Returns BGR same size. Falls back to Telea."""
    if not HAS_CV2 or frame_bgr is None or mask is None:
        return frame_bgr
    if cv2.countNonZero(mask) == 0:
        return frame_bgr
    sess = get_lama_session()
    h, w = frame_bgr.shape[:2]
    if sess is None:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        m = cv2.dilate(mask, k, 1)
        return cv2.inpaint(frame_bgr, m, 5, cv2.INPAINT_TELEA)

    img512 = cv2.resize(frame_bgr, (512, 512), interpolation=cv2.INTER_AREA)
    mask512 = cv2.resize(mask, (512, 512), interpolation=cv2.INTER_NEAREST)
    rgb = cv2.cvtColor(img512, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    img_t = np.transpose(rgb, (2, 0, 1))[None]
    mask_t = (mask512 > 0).astype(np.float32)[None, None]
    try:
        from app.services.performance import gpu_task_slot
        uses_cuda = "CUDAExecutionProvider" in sess.get_providers()
        with gpu_task_slot(enabled=uses_cuda):
            out = sess.run(None, {"l_image_": img_t, "l_mask_": mask_t})[0]
    except Exception as e:
        logger.warning(f"LaMa infer failed: {e}")
        return cv2.inpaint(frame_bgr, mask, 5, cv2.INPAINT_TELEA)
    out = np.squeeze(out, axis=0)
    if out.shape[0] == 3:
        out = np.transpose(out, (1, 2, 0))
    if out.max() <= 1.5:
        out = out * 255.0
    out = np.clip(out, 0, 255).astype(np.uint8)
    out_bgr = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
    out_bgr = cv2.resize(out_bgr, (w, h), interpolation=cv2.INTER_CUBIC)
    m = (mask > 0)[..., None]
    return np.where(m, out_bgr, frame_bgr)


def _find_default_model_path() -> Optional[str]:
    return _model_path()


def pad_to_multiple(img: np.ndarray, modulus: int = 16) -> Tuple[np.ndarray, int, int]:
    h, w = img.shape[:2]
    pad_h = (modulus - (h % modulus)) % modulus
    pad_w = (modulus - (w % modulus)) % modulus
    if pad_h > 0 or pad_w > 0:
        padded = cv2.copyMakeBorder(img, 0, pad_h, 0, pad_w, cv2.BORDER_REFLECT_101)
    else:
        padded = img
    return padded, pad_h, pad_w


class LaMaInpainter:
    def __init__(self, model_path: Optional[str] = None, strict: bool = False):
        if not HAS_CV2:
            raise LaMaNotAvailableError("OpenCV (cv2) library is not installed")
        sess = get_lama_session()
        self.backend = "onnx" if sess is not None else "fallback_inpaint"
        self.session = sess
        if sess is None and strict:
            raise LaMaNotAvailableError("LaMa ONNX weights not available")

    def inpaint_frame(self, frame_bgr: np.ndarray, roi: Tuple[int, int, int, int]) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        rx, ry, rw, rh = roi
        mask = np.zeros((h, w), dtype=np.uint8)
        if rw > 0 and rh > 0:
            x1 = max(0, min(rx, w))
            y1 = max(0, min(ry, h))
            x2 = max(0, min(rx + rw, w))
            y2 = max(0, min(ry + rh, h))
            if x2 > x1 and y2 > y1:
                from app.services.opencv_inpainter import extract_dynamic_subtitle_mask
                mask[y1:y2, x1:x2] = extract_dynamic_subtitle_mask(frame_bgr[y1:y2, x1:x2])
        else:
            from app.services.opencv_inpainter import extract_dynamic_subtitle_mask
            mask = extract_dynamic_subtitle_mask(frame_bgr)
        return lama_inpaint_bgr(frame_bgr, mask)

    def inpaint_video(self, input_path: str, output_path: str, roi: Tuple[int, int, int, int]) -> str:
        from app.services.opencv_inpainter import inpaint_video_opencv
        return inpaint_video_opencv(input_path, output_path, roi, method="hybrid", radius=5)


LamaInpainter = LaMaInpainter


def inpaint_video_lama(input_path: str, output_path: str, roi: Tuple[int, int, int, int], model_path: Optional[str] = None) -> str:
    return LaMaInpainter(model_path=model_path).inpaint_video(input_path, output_path, roi)
