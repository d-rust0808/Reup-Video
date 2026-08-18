"""
OpenCV Video Inpainting Engine (Telea & Navier-Stokes).
======================================================
Uses FFmpeg rawvideo memory pipe streaming or cv2 VideoWriter fallback to perform
in-memory spatial inpainting on designated video region of interest (ROI).
Includes adaptive thresholding (Otsu + morphological gradient + dynamic contrast) for text stroke extraction.
"""

import os
import sys
import shutil
import subprocess
import logging
from typing import Tuple, Optional, Any, cast

import numpy as np

logger = logging.getLogger(__name__)

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    cv2 = cast(Any, None)
    HAS_OPENCV = False


def _find_ffmpeg() -> Optional[str]:
    """Locates ffmpeg binary on system or environment PATH."""
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
    return shutil.which("ffmpeg")


def _find_ffprobe() -> Optional[str]:
    """Locates ffprobe binary on system or environment PATH."""
    env_path = os.environ.get("FFPROBE_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
    return shutil.which("ffprobe")


def get_best_h264_encoder(ffmpeg_bin: str) -> str:
    """Probes system to return optimal H.264 encoder."""
    if not ffmpeg_bin:
        return "libx264"
    try:
        res = subprocess.run([ffmpeg_bin, "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
        if "h264_videotoolbox" in res.stdout:
            return "h264_videotoolbox"
        elif "h264_nvenc" in res.stdout:
            return "h264_nvenc"
    except Exception:
        pass
    return "libx264"


def _read_exact(stream, n_bytes: int) -> bytes:
    """Reads exactly n_bytes from stream or returns remaining bytes on EOF."""
    data = bytearray()
    while len(data) < n_bytes:
        chunk = stream.read(n_bytes - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def _get_fourcc(codec: str = "mp4v") -> int:
    """
    Safely resolves FOURCC integer codec identifier across OpenCV Python bindings and static type stubs.
    Checks cv2.VideoWriter_fourcc, cv2.VideoWriter.fourcc, or computes manual 4-byte ASCII bit-shift calculation.
    """
    fourcc_func = getattr(cv2, "VideoWriter_fourcc", None) or (getattr(cv2.VideoWriter, "fourcc", None) if hasattr(cv2, "VideoWriter") else None)
    if fourcc_func is not None:
        return fourcc_func(*codec)
    return int(sum(ord(c) << (i * 8) for i, c in enumerate(codec[:4])))



def extract_adaptive_text_mask(roi_slice: np.ndarray) -> np.ndarray:
    """
    Extracts high-precision text character stroke mask from BGR ROI image slice.
    Combines bilateral smoothing, local contrast difference, adaptive Sobel gradient,
    and connected component geometric analysis to detect bright, dark, colored, and
    semi-transparent text strokes while cleanly suppressing background noise.

    Args:
        roi_slice: BGR numpy array image slice of ROI.

    Returns:
        2D uint8 numpy binary mask (0 or 255) of shape (height, width).
    """
    if roi_slice is None or roi_slice.size == 0:
        return np.zeros((0, 0), dtype=np.uint8)

    h, w = roi_slice.shape[:2]
    if h == 0 or w == 0:
        return np.zeros((h, w), dtype=np.uint8)

    gray = cv2.cvtColor(roi_slice, cv2.COLOR_BGR2GRAY)

    # 1. Bilateral smoothing to suppress texture noise (grass, film grain) while preserving sharp text edges
    smooth = cv2.bilateralFilter(gray, 5, 40, 40)

    # 2. High-frequency Sobel & Morphological Gradient
    kernel_grad = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    grad = cv2.morphologyEx(smooth, cv2.MORPH_GRADIENT, kernel_grad)

    if np.max(grad) < 14:
        return np.zeros_like(gray)

    # 3. Local Contrast Difference (Text stands out from local average background)
    mean_local = cv2.blur(gray, (17, 17))
    diff_bright = cv2.subtract(gray, mean_local)
    diff_dark = cv2.subtract(mean_local, gray)
    diff_contrast = cv2.max(diff_bright, diff_dark)

    # Dynamic contrast threshold
    otsu_c, _ = cv2.threshold(diff_contrast, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    c_thresh = max(14, min(28, int(otsu_c)))
    thresh_c = (diff_contrast >= c_thresh).astype(np.uint8) * 255

    # Gradient edge threshold
    otsu_g, _ = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    g_thresh = max(16, min(32, int(otsu_g)))
    thresh_g = (grad >= g_thresh).astype(np.uint8) * 255

    # Combine text edge & contrast features
    text_cands = cv2.bitwise_and(thresh_c, thresh_g)

    # 4. Text stroke closure to join broken character segments
    kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    closed = cv2.morphologyEx(text_cands, cv2.MORPH_CLOSE, kernel_close)

    # 5. Connected Component Analysis (CCA)
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(closed, connectivity=8)
    char_mask = np.zeros_like(gray)

    min_area = 8
    for i in range(1, num_labels):
        comp_w = stats[i, cv2.CC_STAT_WIDTH]
        comp_h = stats[i, cv2.CC_STAT_HEIGHT]
        comp_area = stats[i, cv2.CC_STAT_AREA]

        if 4 <= comp_h <= int(h * 0.85) and 3 <= comp_w <= int(w * 0.95) and comp_area >= min_area:
            aspect = comp_w / float(comp_h)
            if 0.10 <= aspect <= 18.0:
                char_mask[labels == i] = 255

    if cv2.countNonZero(char_mask) == 0:
        return np.zeros_like(gray)

    # 6. Anti-Halo Morphological Dilation (Engulfs 2-4px anti-aliased font edges to eliminate chalk smears)
    kernel_d = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    text_mask = cv2.dilate(char_mask, kernel_d, iterations=1)

    # Safety cap: if mask covers > 45% of ROI, suppress
    if cv2.countNonZero(text_mask) > 0.45 * (h * w):
        return np.zeros_like(gray)

    return text_mask


def extract_dynamic_subtitle_mask(sub_zone: np.ndarray) -> np.ndarray:
    """
    Dynamically detects all active hardcoded subtitles inside a vertical frame zone.
    Matches centered high-luminance text strokes with sharp gradients and horizontal
    alignment, and applies Anti-Halo dilation to completely engulf drop shadows.
    """
    if sub_zone is None or sub_zone.size == 0:
        return np.zeros((0, 0), dtype=np.uint8)

    zh, zw = sub_zone.shape[:2]
    gray = cv2.cvtColor(sub_zone, cv2.COLOR_BGR2GRAY)

    # 1. Bilateral smoothing to suppress clothing texture (plaid, knit, fabric)
    smooth = cv2.bilateralFilter(gray, 5, 30, 30)

    # 2. Text edge gradient + luminance core
    kernel_grad = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    grad = cv2.morphologyEx(smooth, cv2.MORPH_GRADIENT, kernel_grad)

    bright = (gray >= 165).astype(np.uint8) * 255
    text_cand = cv2.bitwise_and(bright, (grad >= 10).astype(np.uint8) * 255)

    # 3. Horizontal text stroke connection (joins characters into sentence lines)
    kernel_conn = cv2.getStructuringElement(cv2.MORPH_RECT, (16, 3))
    connected = cv2.morphologyEx(text_cand, cv2.MORPH_CLOSE, kernel_conn)

    # 4. Find subtitle contours
    contours, _ = cv2.findContours(connected, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    text_mask = np.zeros_like(gray)
    kernel_d = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))

    for cnt in contours:
        cx, cy, cw, ch = cv2.boundingRect(cnt)
        aspect = cw / float(ch) if ch > 0 else 0
        center_x = cx + cw / 2.0
        dist_from_center = abs(center_x - zw / 2.0)

        # Precise subtitle line criteria:
        # - Width >= 40px
        # - Height 12px to 75px
        # - Aspect ratio >= 1.05
        # - Centered horizontally (within 40% of video center)
        if cw >= 40 and 12 <= ch <= 75 and aspect >= 1.05 and dist_from_center <= zw * 0.40:
            box_slice = text_cand[cy:cy+ch, cx:cx+cw]
            box_dilated = cv2.dilate(box_slice, kernel_d, iterations=1)
            text_mask[cy:cy+ch, cx:cx+cw] = box_dilated

    return text_mask


def remux_audio_if_available(input_path: str, temp_video_path: str, output_path: str) -> bool:
    """
    Remuxes/re-encodes video from temp_video_path and audio from input_path to standard H.264/AAC MP4.
    Returns True if successfully remuxed, False otherwise.
    """
    ffmpeg_bin = _find_ffmpeg()
    if not ffmpeg_bin:
        return False

    cmd = [
        ffmpeg_bin, "-loglevel", "error", "-y",
        "-i", temp_video_path,
        "-i", input_path,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v:0",
        "-map", "1:a:0?",
        "-shortest",
        output_path
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        logger.warning(f"remux_audio_if_available error: {e}")
        return False


def inpaint_video_opencv(
    input_path: str,
    output_path: str,
    roi: Tuple[int, int, int, int],
    method: str = "telea",
    radius: int = 3
) -> str:
    """
    Inpaints video ROI using OpenCV Telea or Navier-Stokes algorithm.
    Supports dynamic per-frame subtitle tracking when roi=(0, 0, 0, 0) or explicit manual ROI.

    Args:
        input_path: Absolute or relative path to input video.
        output_path: Target path for output video.
        roi: 4-element tuple (x, y, w, h) specifying pixel ROI, or (0, 0, 0, 0) for dynamic subtitle mode.
        method: "telea" (Fast Marching) or "ns" (Navier-Stokes).
        radius: Inpainting search radius in pixels.

    Returns:
        Path to processed output video.
    """
    if not HAS_OPENCV:
        raise RuntimeError("OpenCV library (cv2) is not installed")

    # 1. Validate inputs
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input video file not found: {input_path}")
    if os.path.getsize(input_path) == 0:
        raise ValueError("Input file is empty (0 bytes)")

    if not isinstance(roi, (tuple, list)) or len(roi) != 4:
        raise ValueError("ROI must be a 4-element tuple (x, y, w, h)")
    x, y, w, h = roi
    if x < 0 or y < 0:
        raise ValueError("ROI coordinates (x, y) must be non-negative")

    if radius <= 0:
        raise ValueError("Inpainting radius must be strictly greater than 0")

    method_clean = method.lower()
    if method_clean in ("opencv_telea", "telea"):
        flag = cv2.INPAINT_TELEA
    elif method_clean in ("opencv_ns", "ns"):
        flag = cv2.INPAINT_NS
    else:
        raise ValueError(f"Unsupported OpenCV inpainting method: '{method}'. Must be 'telea' or 'ns'")

    # Ensure output directory exists
    out_dir = os.path.dirname(os.path.abspath(output_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # 2. Inspect video stream metadata via cv2.VideoCapture
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV VideoCapture failed to open video file: {input_path}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or np.isnan(fps):
        fps = 30.0

    if width <= 0 or height <= 0:
        cap.release()
        raise RuntimeError(f"Invalid video dimensions probed from {input_path}: {width}x{height}")

    ffmpeg_bin = _find_ffmpeg()

    # Strategy A: FFmpeg subprocess piping (preferred high performance, retains audio)
    if ffmpeg_bin:
        cap.release()
        encoder = get_best_h264_encoder(ffmpeg_bin)
        encoder_flags = ["-b:v", "4M"] if encoder == "h264_videotoolbox" else ["-preset", "ultrafast", "-crf", "23"]
        frame_size = width * height * 3

        decoder_cmd = [
            ffmpeg_bin, "-loglevel", "error", "-i", input_path,
            "-f", "image2pipe", "-pix_fmt", "bgr24", "-vcodec", "rawvideo", "-"
        ]
        encoder_cmd = [
            ffmpeg_bin, "-loglevel", "error", "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo", "-s", f"{width}x{height}",
            "-pix_fmt", "bgr24", "-r", f"{fps:.3f}", "-i", "-",
            "-i", input_path, "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", encoder, *encoder_flags, "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p",
            "-shortest", output_path
        ]

        try:
            decoder = subprocess.Popen(decoder_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            encoder_proc = subprocess.Popen(encoder_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

            is_dynamic_auto = (roi == (0, 0, 0, 0) or (x == 0 and y == 0 and (w == 0 or w == width)))

            if is_dynamic_auto:
                y1 = int(height * 0.45)
                y2 = int(height * 0.96)
                x1 = 0
                x2 = width
                rw = width
                rh = y2 - y1
            else:
                x1 = max(0, min(x, width))
                y1 = max(0, min(y, height))
                x2 = max(0, min(x + w, width))
                y2 = max(0, min(y + h, height))
                rw = x2 - x1
                rh = y2 - y1

            frames_processed = 0
            while True:
                raw_frame = _read_exact(decoder.stdout, frame_size)
                if len(raw_frame) < frame_size:
                    break

                frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((height, width, 3)).copy()

                if rw > 0 and rh > 0:
                    if is_dynamic_auto:
                        sub_zone = frame[y1:y2, x1:x2]
                        text_mask = extract_dynamic_subtitle_mask(sub_zone)
                        if cv2.countNonZero(text_mask) > 0:
                            inpaint_r = max(1, min(radius, 3))
                            inpainted_zone = cv2.inpaint(sub_zone, text_mask, inpaintRadius=inpaint_r, flags=flag)
                            frame[y1:y2, x1:x2] = inpainted_zone
                    else:
                        pad = max(radius * 2, 10)
                        px1 = max(0, x1 - pad)
                        py1 = max(0, y1 - pad)
                        px2 = min(width, x2 + pad)
                        py2 = min(height, y2 + pad)

                        sub_frame = frame[py1:py2, px1:px2]
                        roi_slice = sub_frame[y1 - py1 : y2 - py1, x1 - px1 : x2 - px1]
                        text_mask = extract_adaptive_text_mask(roi_slice)

                        if cv2.countNonZero(text_mask) > 0:
                            sub_mask = np.zeros((py2 - py1, px2 - px1), dtype=np.uint8)
                            sub_mask[y1 - py1 : y2 - py1, x1 - px1 : x2 - px1] = text_mask
                            inpaint_r = max(1, min(radius, 3))
                            inpainted_sub = cv2.inpaint(sub_frame, sub_mask, inpaintRadius=inpaint_r, flags=flag)
                            frame[py1:py2, px1:px2] = inpainted_sub

                if encoder_proc.stdin:
                    encoder_proc.stdin.write(frame.tobytes())
                frames_processed += 1

            if decoder.stdout:
                decoder.stdout.close()
            if encoder_proc.stdin:
                encoder_proc.stdin.close()

            decoder.wait()
            encoder_proc.wait()

            if frames_processed > 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return output_path
        except Exception as e:
            logger.warning(f"FFmpeg rawvideo pipe failed: {e}. Falling back to OpenCV VideoWriter.")

    # Strategy B: Pure OpenCV VideoWriter fallback (used if ffmpeg binary absent or pipe fails)
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV VideoCapture failed to open video file: {input_path}")

    # Use a temporary file for VideoWriter if we intend to remux audio later
    temp_writer_out = output_path + ".temp.mp4" if ffmpeg_bin else output_path

    fourcc = _get_fourcc("mp4v")
    writer = cv2.VideoWriter(temp_writer_out, fourcc, fps, (width, height))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"OpenCV VideoWriter failed to create output file: {temp_writer_out}")

    try:
        is_dynamic_auto = (roi == (0, 0, 0, 0) or (x == 0 and y == 0 and (w == 0 or w == width)))

        if is_dynamic_auto:
            y1 = int(height * 0.45)
            y2 = int(height * 0.96)
            x1 = 0
            x2 = width
            rw = width
            rh = y2 - y1
        else:
            x1 = max(0, min(x, width))
            y1 = max(0, min(y, height))
            x2 = max(0, min(x + w, width))
            y2 = max(0, min(y + h, height))
            rw = x2 - x1
            rh = y2 - y1

        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            if rw > 0 and rh > 0:
                if is_dynamic_auto:
                    sub_zone = frame[y1:y2, x1:x2]
                    text_mask = extract_dynamic_subtitle_mask(sub_zone)
                    if cv2.countNonZero(text_mask) > 0:
                        inpaint_r = max(1, min(radius, 3))
                        inpainted_zone = cv2.inpaint(sub_zone, text_mask, inpaintRadius=inpaint_r, flags=flag)
                        frame[y1:y2, x1:x2] = inpainted_zone
                else:
                    pad = max(radius * 2, 10)
                    px1 = max(0, x1 - pad)
                    py1 = max(0, y1 - pad)
                    px2 = min(width, x2 + pad)
                    py2 = min(height, y2 + pad)

                    sub_frame = frame[py1:py2, px1:px2]
                    roi_slice = sub_frame[y1 - py1 : y2 - py1, x1 - px1 : x2 - px1]
                    text_mask = extract_adaptive_text_mask(roi_slice)

                    if cv2.countNonZero(text_mask) > 0:
                        sub_mask = np.zeros((py2 - py1, px2 - px1), dtype=np.uint8)
                        sub_mask[y1 - py1 : y2 - py1, x1 - px1 : x2 - px1] = text_mask
                        inpaint_r = max(1, min(radius, 3))
                        inpainted_sub = cv2.inpaint(sub_frame, sub_mask, inpaintRadius=inpaint_r, flags=flag)
                        frame[py1:py2, px1:px2] = inpainted_sub

            writer.write(frame)
    finally:
        cap.release()
        writer.release()

    # Remux audio and convert to standard H.264 if temp file was created and FFmpeg is available
    if temp_writer_out != output_path and os.path.exists(temp_writer_out):
        remuxed = remux_audio_if_available(input_path, temp_writer_out, output_path)
        if remuxed:
            if os.path.exists(temp_writer_out):
                os.remove(temp_writer_out)
        else:
            # If remux failed, replace output_path with temp file
            if os.path.exists(output_path):
                os.remove(output_path)
            os.rename(temp_writer_out, output_path)

    return output_path


class OpenCVInpainter:
    """Class wrapper for OpenCV Inpainter engine."""

    def __init__(self, radius: int = 3, method: str = "telea"):
        self.radius = radius
        self.method = method

    def inpaint_video(
        self,
        input_path: str,
        output_path: str,
        roi: Tuple[int, int, int, int],
        radius: Optional[int] = None,
        method: Optional[str] = None
    ) -> str:
        r = radius if radius is not None else self.radius
        m = method if method is not None else self.method
        return inpaint_video_opencv(input_path, output_path, roi, method=m, radius=r)

    def inpaint(
        self,
        input_path: str,
        output_path: str,
        roi: Tuple[int, int, int, int],
        radius: Optional[int] = None,
        method: Optional[str] = None
    ) -> str:
        return self.inpaint_video(input_path, output_path, roi, radius=radius, method=method)
