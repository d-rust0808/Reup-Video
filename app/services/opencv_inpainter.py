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
from typing import Tuple, Optional, List, Dict, Any, cast

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


def _inpaint_radius(radius: int) -> int:
    """Clamp Telea radius. 2px leaves CJK stroke halos; 3–6 fills interiors cleanly."""
    try:
        r = int(radius)
    except (TypeError, ValueError):
        r = 3
    return max(3, min(r, 7))


class TemporalTextTracker:
    """
    Temporal Subtitle & Watermark Consistency Tracker.
    Maintains detected text bounding boxes across consecutive video frames (hysteresis window)
    to eliminate frame flickering, motion blur dropouts, and fade-in/fade-out detection misses.
    """

    def __init__(self, persistence_frames: int = 15, iou_threshold: float = 0.25):
        self.persistence_frames = max(3, persistence_frames)
        self.iou_threshold = iou_threshold
        self.active_tracks: List[Dict[str, Any]] = []

    def _compute_iou(self, b1: Tuple[int, int, int, int], b2: Tuple[int, int, int, int]) -> float:
        x1 = max(b1[0], b2[0])
        y1 = max(b1[1], b2[1])
        x2 = min(b1[0] + b1[2], b2[0] + b2[2])
        y2 = min(b1[1] + b1[3], b2[1] + b2[3])
        if x2 <= x1 or y2 <= y1:
            return 0.0
        inter = (x2 - x1) * (y2 - y1)
        union = (b1[2] * b1[3]) + (b2[2] * b2[3]) - inter
        return inter / union if union > 0 else 0.0

    def update(self, detected_boxes: List[Tuple[int, int, int, int]]) -> List[Tuple[int, int, int, int]]:
        # Decrement persistence for active tracks
        for track in self.active_tracks:
            track["remaining"] -= 1

        # Match new detections with existing tracks
        for dbox in detected_boxes:
            matched = False
            for track in self.active_tracks:
                if self._compute_iou(track["box"], dbox) > self.iou_threshold:
                    track["box"] = dbox
                    track["remaining"] = self.persistence_frames
                    matched = True
                    break
            if not matched:
                self.active_tracks.append({"box": dbox, "remaining": self.persistence_frames})

        # Keep alive active tracks within persistence window
        self.active_tracks = [t for t in self.active_tracks if t["remaining"] > 0]
        return [t["box"] for t in self.active_tracks]

    def get_active_boxes(self) -> List[Tuple[int, int, int, int]]:
        for track in self.active_tracks:
            track["remaining"] -= 1
        self.active_tracks = [t for t in self.active_tracks if t["remaining"] > 0]
        return [t["box"] for t in self.active_tracks]


def extract_adaptive_text_mask(roi_slice: np.ndarray) -> np.ndarray:
    """
    Extracts ultra-sharp character stroke mask from BGR ROI slice without smudging background textures.
    Uses multi-scale morphological gradient, local luminance contrast, hole-fill for CJK interiors,
    and anti-halo dilation.
    """
    if roi_slice is None or roi_slice.size == 0:
        return np.zeros((0, 0), dtype=np.uint8)

    h, w = roi_slice.shape[:2]
    if h == 0 or w == 0:
        return np.zeros((h, w), dtype=np.uint8)

    gray = cv2.cvtColor(roi_slice, cv2.COLOR_BGR2GRAY)

    # 1. Bilateral smoothing to suppress texture noise while preserving sharp text boundaries
    smooth = cv2.bilateralFilter(gray, 5, 40, 40)

    # 2. Morphological Gradient on text edges
    kernel_grad = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    grad = cv2.morphologyEx(smooth, cv2.MORPH_GRADIENT, kernel_grad)

    # 3. Local Contrast Difference
    mean_local = cv2.blur(smooth, (9, 9))
    diff_local = cv2.absdiff(smooth, mean_local)

    feat = cv2.addWeighted(grad, 0.6, diff_local, 0.4, 0)
    _, binary = cv2.threshold(feat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Edge/contrast features vanish on a near-uniform solid blob (a filled caption
    # plate or a solid-fill glyph region), so Otsu returns an all-zero mask. Recover
    # by thresholding on absolute luminance: a bright block on dark video (or the
    # reverse) is almost always burned-in text/plate and must be inpainted.
    gmin, gmax = int(smooth.min()), int(smooth.max())
    gmean = float(smooth.mean())
    if (gmax - gmin) < 25:
        if gmean >= 180 or gmean <= 70:
            binary = np.full_like(gray, 255)
    else:
        # Contrasty region: OR in the extreme luminance band Otsu on gradients missed
        # (solid white/near-white strokes whose interiors carry no gradient).
        if gmean < 128:
            _, hi = cv2.threshold(smooth, max(200, gmax - 30), 255, cv2.THRESH_BINARY)
        else:
            _, hi = cv2.threshold(smooth, min(60, gmin + 30), 255, cv2.THRESH_BINARY_INV)
        dens = cv2.countNonZero(hi) / float(hi.size)
        if 0.0 < dens < 0.6:
            binary = cv2.bitwise_or(binary, hi)

    # Fill CJK character interiors (口/国/回 leftover if we only keep strokes)
    close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, close_k)

    kernel_d = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    text_mask = cv2.dilate(binary, kernel_d, iterations=1)

    return text_mask


def _or_band_mask(text_mask: np.ndarray, sub_zone: np.ndarray, y1: int, y2: int) -> np.ndarray:
    """OR morphological stroke mask from a horizontal band (top logo / bottom hardsub)."""
    zh, zw = sub_zone.shape[:2]
    y1 = max(0, min(y1, zh))
    y2 = max(y1 + 1, min(y2, zh))
    band = sub_zone[y1:y2, :]
    if band.size == 0:
        return text_mask
    stroke = extract_adaptive_text_mask(band)
    if stroke.size == 0 or cv2.countNonZero(stroke) == 0:
        return text_mask
    density = cv2.countNonZero(stroke) / float(stroke.size)
    # Text overlays are sparse (few %). High density is texture/pattern, not glyphs.
    if density < 0.008 or density > 0.22:
        return text_mask
    text_mask[y1:y2, :] = cv2.bitwise_or(text_mask[y1:y2, :], stroke)
    return text_mask


def extract_dynamic_subtitle_mask(
    sub_zone: np.ndarray,
    tracker: Optional[TemporalTextTracker] = None,
    run_ocr: bool = True,
    roi_fallback: bool = False
) -> np.ndarray:
    """
    Dynamically detects and extracts masks for active text & subtitles.

    CJK hardsubs have outlined strokes + filled interiors. Stroke-only masks leave
    ghost characters, so detected bounding boxes are FILLED (plus a dilated stroke
    mask). If OCR/MSER finds nothing and roi_fallback is True (manual ROI), the
    whole adaptive stroke mask of the ROI is used so user-drawn boxes still erase.
    """
    if sub_zone is None or sub_zone.size == 0:
        return np.zeros((0, 0), dtype=np.uint8)

    zh, zw = sub_zone.shape[:2]
    if zh == 0 or zw == 0:
        return np.zeros((zh, zw), dtype=np.uint8)

    gray = cv2.cvtColor(sub_zone, cv2.COLOR_BGR2GRAY)
    text_mask = np.zeros_like(gray)

    try:
        from app.services.subtitle_detector import detect_text_boxes, detect_faces

        if run_ocr:
            raw_boxes = detect_text_boxes(sub_zone, min_confidence=0.25, padding=8)
            active_boxes = tracker.update(raw_boxes) if tracker is not None else raw_boxes
        else:
            active_boxes = tracker.get_active_boxes() if tracker is not None else []

        for bx, by, bw, bh in active_boxes:
            cbx = max(0, min(bx, zw - 1))
            cby = max(0, min(by, zh - 1))
            cbw = max(1, min(bw, zw - cbx))
            cbh = max(1, min(bh, zh - cby))

            # Fill the whole text box — required for Chinese character interiors
            cv2.rectangle(text_mask, (cbx, cby), (cbx + cbw, cby + cbh), 255, -1)

            box_slice = sub_zone[cby : cby + cbh, cbx : cbx + cbw]
            box_mask = extract_adaptive_text_mask(box_slice)
            if box_mask.size > 0 and cv2.countNonZero(box_mask) > 0:
                text_mask[cby : cby + cbh, cbx : cbx + cbw] = cv2.bitwise_or(
                    text_mask[cby : cby + cbh, cbx : cbx + cbw], box_mask
                )

        # Douyin captions sit mid-lower as well as the classic bottom band
        text_mask = _or_band_mask(text_mask, sub_zone, 0, max(12, int(zh * 0.14)))
        text_mask = _or_band_mask(text_mask, sub_zone, int(zh * 0.48), int(zh * 0.84))
        text_mask = _or_band_mask(text_mask, sub_zone, int(zh * 0.78), zh)

        if cv2.countNonZero(text_mask) == 0 and roi_fallback:
            stroke = extract_adaptive_text_mask(sub_zone)
            if stroke.size > 0:
                text_mask = stroke

        if cv2.countNonZero(text_mask) > 0:
            kernel_d = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
            text_mask = cv2.dilate(text_mask, kernel_d, iterations=1)

        # Face shield
        if run_ocr:
            face_boxes = detect_faces(sub_zone, padding=18)
            for fx, fy, fw, fh in face_boxes:
                cfx = max(0, min(fx, zw - 1))
                cfy = max(0, min(fy, zh - 1))
                cfw = max(1, min(fw, zw - cfx))
                cfh = max(1, min(fh, zh - cfy))
                text_mask[cfy : cfy + cfh, cfx : cfx + cfw] = 0

    except Exception as e:
        logger.debug(f"Neural OCR text extraction skipped: {e}")
        if roi_fallback:
            try:
                text_mask = extract_adaptive_text_mask(sub_zone)
            except Exception:
                pass

    return text_mask


def _resolve_scan_region(
    roi: Tuple[int, int, int, int],
    width: int,
    height: int
) -> Tuple[int, int, int, int, bool]:
    """
    Returns (x1, y1, x2, y2, is_dynamic_auto).
    Auto mode scans the FULL frame (including Douyin logos in the top 10%).
    """
    x, y, w, h = roi
    is_dynamic_auto = (roi == (0, 0, 0, 0) or (x == 0 and y == 0 and (w == 0 or w == width)))
    if is_dynamic_auto:
        return 0, 0, width, height, True
    x1 = max(0, min(x, width))
    y1 = max(0, min(y, height))
    x2 = max(0, min(x + w, width))
    y2 = max(0, min(y + h, height))
    return x1, y1, x2, y2, False


def hybrid_inpaint_frame(
    frame: np.ndarray,
    mask: np.ndarray,
    radius: int = 5,
    use_lama: bool = True,
    frame_index: int = 0,
) -> np.ndarray:
    """Telea fill; LaMa refine every 5th frame when the hole is large."""
    if frame is None or mask is None or mask.size == 0:
        return frame
    if cv2.countNonZero(mask) == 0:
        return frame
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    m = cv2.dilate(mask, k, iterations=1)
    r = _inpaint_radius(radius)
    out = cv2.inpaint(frame, m, r, cv2.INPAINT_TELEA)
    h, w = out.shape[:2]
    hole = cv2.countNonZero(m) / float(max(1, w * h))
    if use_lama and hole >= 0.004 and (frame_index % 5 == 0):
        try:
            from app.services.lama_inpainter import lama_inpaint_bgr, get_lama_session
            if get_lama_session() is not None:
                out = lama_inpaint_bgr(out, m)
        except Exception:
            pass
    return out


def _inpaint_frame_region(
    frame: np.ndarray,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    tracker: TemporalTextTracker,
    frames_processed: int,
    radius: int,
    flag: int,
    is_manual_roi: bool,
    use_lama: bool = True,
) -> int:
    """Inpaint one frame region. Returns number of inpainted pixels."""
    rw = x2 - x1
    rh = y2 - y1
    if rw <= 0 or rh <= 0:
        return 0

    height, width = frame.shape[:2]
    run_ocr = (frames_processed % 3 == 0)

    if is_manual_roi:
        pad = max(radius * 2, 10)
        px1 = max(0, x1 - pad)
        py1 = max(0, y1 - pad)
        px2 = min(width, x2 + pad)
        py2 = min(height, y2 + pad)
        sub_frame = frame[py1:py2, px1:px2]
        roi_slice = sub_frame[y1 - py1 : y2 - py1, x1 - px1 : x2 - px1]
        text_mask = extract_dynamic_subtitle_mask(
            roi_slice, tracker=tracker, run_ocr=run_ocr, roi_fallback=True
        )
        if cv2.countNonZero(text_mask) == 0:
            return 0
        sub_mask = np.zeros((py2 - py1, px2 - px1), dtype=np.uint8)
        sub_mask[y1 - py1 : y2 - py1, x1 - px1 : x2 - px1] = text_mask
        inpainted_sub = hybrid_inpaint_frame(
            sub_frame, sub_mask, radius, use_lama=use_lama, frame_index=frames_processed
        )
        frame[py1:py2, px1:px2] = inpainted_sub
        return int(cv2.countNonZero(text_mask))

    sub_zone = frame[y1:y2, x1:x2]
    text_mask = extract_dynamic_subtitle_mask(
        sub_zone, tracker=tracker, run_ocr=run_ocr, roi_fallback=False
    )
    if cv2.countNonZero(text_mask) == 0:
        return 0
    inpainted_zone = hybrid_inpaint_frame(
        sub_zone, text_mask, radius, use_lama=use_lama, frame_index=frames_processed
    )
    frame[y1:y2, x1:x2] = inpainted_zone
    return int(cv2.countNonZero(text_mask))


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
    radius: int = 3,
    progress_callback: Optional[Any] = None
) -> str:
    """
    Inpaints video ROI using OpenCV Telea or Navier-Stokes algorithm.
    Supports dynamic per-frame subtitle tracking when roi=(0, 0, 0, 0) or explicit manual ROI.
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
    use_lama = method_clean in ("hybrid", "auto", "all", "lama")
    if method_clean in ("opencv_telea", "telea", "hybrid", "auto", "all", "lama"):
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
    total_frames_est = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames_est <= 0:
        total_frames_est = 300
    if fps <= 0 or np.isnan(fps):
        fps = 30.0

    if width <= 0 or height <= 0:
        cap.release()
        raise RuntimeError(f"Invalid video dimensions probed from {input_path}: {width}x{height}")

    ffmpeg_bin = _find_ffmpeg()
    x1, y1, x2, y2, is_dynamic_auto = _resolve_scan_region((int(x), int(y), int(w), int(h)), width, height)
    is_manual_roi = not is_dynamic_auto

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
            ffmpeg_bin, "-loglevel", "warning", "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo", "-s", f"{width}x{height}",
            "-pix_fmt", "bgr24", "-r", f"{fps:.3f}", "-i", "-",
            "-i", input_path, "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", encoder, *encoder_flags, "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-shortest", output_path
        ]

        try:
            decoder = subprocess.Popen(decoder_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            encoder_proc = subprocess.Popen(encoder_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

            tracker = TemporalTextTracker(persistence_frames=18)
            frames_processed = 0
            pixels_inpainted = 0
            while True:
                raw_frame = _read_exact(decoder.stdout, frame_size)
                if len(raw_frame) < frame_size:
                    break

                frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((height, width, 3)).copy()
                pixels_inpainted += _inpaint_frame_region(
                    frame, x1, y1, x2, y2, tracker, frames_processed, radius, flag, is_manual_roi, use_lama
                )

                if encoder_proc.stdin:
                    encoder_proc.stdin.write(frame.tobytes())
                frames_processed += 1

                if progress_callback and frames_processed % 15 == 0:
                    cur_prog = min(0.65, 0.35 + 0.30 * (frames_processed / max(1, total_frames_est)))
                    try:
                        progress_callback(cur_prog)
                    except Exception:
                        pass

            if decoder.stdout:
                decoder.stdout.close()
            if encoder_proc.stdin:
                encoder_proc.stdin.close()

            decoder.wait()
            encoder_proc.wait()

            if encoder_proc.returncode not in (0, None):
                logger.warning(
                    f"FFmpeg encoder exited {encoder_proc.returncode}"
                )

            logger.info(
                f"OpenCV inpaint: {frames_processed} frames, {pixels_inpainted} mask pixels, "
                f"roi={'auto-fullframe' if is_dynamic_auto else (x1, y1, x2 - x1, y2 - y1)}"
            )

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
        tracker_b = TemporalTextTracker(persistence_frames=18)
        frames_processed = 0
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break
            _inpaint_frame_region(
                frame, x1, y1, x2, y2, tracker_b, frames_processed, radius, flag, is_manual_roi, use_lama
            )
            writer.write(frame)
            frames_processed += 1
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
        method: Optional[str] = None,
        progress_callback: Optional[Any] = None
    ) -> str:
        r = radius if radius is not None else self.radius
        m = method if method is not None else self.method
        return inpaint_video_opencv(input_path, output_path, roi, method=m, radius=r, progress_callback=progress_callback)

    def inpaint(
        self,
        input_path: str,
        output_path: str,
        roi: Tuple[int, int, int, int],
        radius: Optional[int] = None,
        method: Optional[str] = None,
        progress_callback: Optional[Any] = None
    ) -> str:
        return self.inpaint_video(input_path, output_path, roi, radius=radius, method=method, progress_callback=progress_callback)
