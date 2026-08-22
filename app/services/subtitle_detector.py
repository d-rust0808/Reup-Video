"""
Subtitle & Text Region Auto-Detector Engine.
=============================================
Uses OpenCV morphological analysis, MSER, edge gradients, and (on macOS)
Apple Vision to locate hardcoded subtitles, Chinese overlay text, and watermarks.
"""

import os
import logging
from typing import Tuple, List, Optional, Dict, Any, cast

import numpy as np

logger = logging.getLogger(__name__)

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    cv2 = cast(Any, None)
    HAS_OPENCV = False

Vision: Any = None
NSData: Any = None
VNImageRequestHandler: Any = None
VNRecognizeTextRequest: Any = None
VNRequestTextRecognitionLevelAccurate: Any = 0
HAS_APPLE_VISION = False

try:
    import Vision as _Vision  # type: ignore[import-untyped, import-not-found]
    import Foundation as _Foundation  # type: ignore[import-untyped, import-not-found]
    Vision = _Vision
    NSData = getattr(_Foundation, "NSData", None)
    VNImageRequestHandler = getattr(_Vision, "VNImageRequestHandler", None)
    VNRecognizeTextRequest = getattr(_Vision, "VNRecognizeTextRequest", None)
    VNRequestTextRecognitionLevelAccurate = getattr(_Vision, "VNRequestTextRecognitionLevelAccurate", 0)
    HAS_APPLE_VISION = (
        Vision is not None
        and NSData is not None
        and VNImageRequestHandler is not None
        and VNRecognizeTextRequest is not None
    )
except Exception:
    Vision = None
    NSData = None
    VNImageRequestHandler = None
    VNRecognizeTextRequest = None
    HAS_APPLE_VISION = False


def _clamp_box(x: int, y: int, w: int, h: int, fw: int, fh: int, padding: int = 0) -> Tuple[int, int, int, int]:
    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(fw, x + w + padding)
    y2 = min(fh, y + h + padding)
    return x1, y1, max(1, x2 - x1), max(1, y2 - y1)


def _merge_boxes(boxes: List[Tuple[int, int, int, int]], gap: int = 12) -> List[Tuple[int, int, int, int]]:
    """Merge overlapping / nearby boxes into text-line regions."""
    if not boxes:
        return []
    items = sorted(boxes, key=lambda b: (b[1], b[0]))
    merged: List[List[int]] = []
    for x, y, w, h in items:
        x2, y2 = x + w, y + h
        attached = False
        for m in merged:
            mx, my, mx2, my2 = m
            if x <= mx2 + gap and x2 >= mx - gap and y <= my2 + gap and y2 >= my - gap:
                m[0] = min(mx, x)
                m[1] = min(my, y)
                m[2] = max(mx2, x2)
                m[3] = max(my2, y2)
                attached = True
                break
        if not attached:
            merged.append([x, y, x2, y2])
    return [(a, b, c - a, d - b) for a, b, c, d in merged]


def detect_faces_haar(frame: np.ndarray, padding: int = 15) -> List[Tuple[int, int, int, int]]:
    """OpenCV Haar cascade face detector (cross-platform fallback)."""
    if not HAS_OPENCV or frame is None or frame.size == 0:
        return []
    try:
        cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        if not os.path.exists(cascade_path):
            return []
        detector = cv2.CascadeClassifier(cascade_path)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(24, 24))
        out = []
        for fx, fy, fw, fh in faces:
            out.append(_clamp_box(int(fx), int(fy), int(fw), int(fh), w, h, padding))
        return out
    except Exception as e:
        logger.debug(f"Haar face detection skipped: {e}")
        return []


def detect_faces_neural(frame: np.ndarray, padding: int = 15) -> List[Tuple[int, int, int, int]]:
    """
    Hardware-accelerated Neural Face Detector using Apple Vision Framework.
    Detects all human faces in a video frame to construct a Zero-Mask Face Shield
    preventing any inpainting, blur, or erasure from touching human subjects.
    """
    if (
        not HAS_APPLE_VISION
        or NSData is None
        or VNImageRequestHandler is None
        or frame is None
        or frame.size == 0
    ):
        return []

    h, w = frame.shape[:2]
    try:
        _, buf = cv2.imencode('.png', frame)
        ns_data = NSData.dataWithBytes_length_(buf.tobytes(), len(buf))
        handler = VNImageRequestHandler.alloc().initWithData_options_(ns_data, {})
        req = Vision.VNDetectFaceRectanglesRequest.alloc().init()
        handler.performRequests_error_([req], None)

        faces = []
        results = req.results() if hasattr(req, "results") else None
        if results:
            for face in results:
                bbox = face.boundingBox()
                fx = int(bbox.origin.x * w)
                fw = int(bbox.size.width * w)
                fh = int(bbox.size.height * h)
                fy = int((1.0 - bbox.origin.y - bbox.size.height) * h)
                faces.append(_clamp_box(fx, fy, fw, fh, w, h, padding))
        return faces
    except Exception as e:
        logger.debug(f"Apple Vision face detection exception: {e}")
        return []


def detect_faces(frame: np.ndarray, padding: int = 15) -> List[Tuple[int, int, int, int]]:
    """Unified face detector: Apple Vision first, Haar cascade fallback."""
    faces = detect_faces_neural(frame, padding=padding)
    if faces:
        return faces
    return detect_faces_haar(frame, padding=padding)


def detect_text_boxes_neural(
    frame: np.ndarray,
    min_confidence: float = 0.3,
    padding: int = 4
) -> List[Tuple[int, int, int, int]]:
    """
    Hardware-accelerated Neural Text & Subtitle Region Detector.
    Uses Apple Vision Framework on macOS. Collects results both from the
    completion handler AND req.results() after performRequests (PyObjC
    completion handlers are not always invoked synchronously).
    """
    if (
        not HAS_APPLE_VISION
        or NSData is None
        or VNImageRequestHandler is None
        or VNRecognizeTextRequest is None
        or frame is None
        or frame.size == 0
    ):
        return []

    h, w = frame.shape[:2]
    boxes: List[Tuple[int, int, int, int]] = []

    def _collect_obs(obs) -> None:
        try:
            cands = obs.topCandidates_(1)
            if not cands:
                return
            cand = cands[0]
            text_str = cand.string() if hasattr(cand, "string") else ""
            conf = cand.confidence() if hasattr(cand, "confidence") else 1.0
            if not text_str or not str(text_str).strip() or conf < min_confidence:
                return
            bbox = obs.boundingBox()
            bx = int(bbox.origin.x * w)
            bw = int(bbox.size.width * w)
            bh = int(bbox.size.height * h)
            by = int((1.0 - bbox.origin.y - bbox.size.height) * h)
            if bh > int(h * 0.40) or bw > int(w * 0.98):
                return
            boxes.append(_clamp_box(bx, by, bw, bh, w, h, padding))
        except Exception:
            return

    try:
        _, buf = cv2.imencode('.png', frame)
        ns_data = NSData.dataWithBytes_length_(buf.tobytes(), len(buf))
        handler = VNImageRequestHandler.alloc().initWithData_options_(ns_data, {})

        def on_complete(req, err):
            if err or not req.results():
                return
            for obs in req.results():
                _collect_obs(obs)

        req = VNRecognizeTextRequest.alloc().initWithCompletionHandler_(on_complete)
        try:
            req.setRecognitionLanguages_(['zh-Hans', 'zh-Hant', 'en-US'])
        except Exception:
            try:
                req.setRecognitionLanguages_(['zh-Hans', 'en-US'])
            except Exception:
                pass
        try:
            req.setRecognitionLevel_(VNRequestTextRecognitionLevelAccurate)
            req.setUsesLanguageCorrection_(False)
        except Exception:
            pass
        handler.performRequests_error_([req], None)

        # Always drain req.results() — completion handler is racy in PyObjC
        results = req.results() if hasattr(req, "results") else None
        if results:
            for obs in results:
                _collect_obs(obs)

        return _merge_boxes(boxes, gap=8)
    except Exception as e:
        logger.warning(f"Apple Vision text detection exception: {e}")
        return []


def detect_text_boxes_opencv(
    frame: np.ndarray,
    padding: int = 6
) -> List[Tuple[int, int, int, int]]:
    """
    Cross-platform CJK/Latin text detector.
    Combines MSER character blobs with morphological subtitle-line extraction.
    Tuned for Douyin/Kuaishou hardcoded Chinese subtitles and corner watermarks.
    """
    if not HAS_OPENCV or frame is None or frame.size == 0:
        return []

    h, w = frame.shape[:2]
    if h < 16 or w < 16:
        return []

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    try:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
    except Exception:
        pass

    candidate: List[Tuple[int, int, int, int]] = []
    min_area = max(24, int(w * h * 0.00012))
    max_area = int(w * h * 0.18)
    max_box_h = int(h * 0.32)
    max_box_w = int(w * 0.95)

    # --- MSER character-like regions (works well on CJK strokes) ---
    try:
        mser = cv2.MSER_create()
        try:
            mser.setDelta(5)
            mser.setMinArea(max(20, min_area // 4))
            mser.setMaxArea(max_area)
        except Exception:
            pass
        regions, _ = mser.detectRegions(gray)
        for pts in regions:
            x, y, bw, bh = cv2.boundingRect(pts.reshape(-1, 1, 2))
            area = bw * bh
            if area < min_area or area > max_area:
                continue
            if bh < 8 or bw < 8 or bh > max_box_h or bw > max_box_w:
                continue
            ar = bw / float(bh)
            if ar < 0.15 or ar > 18.0:
                continue
            candidate.append((x, y, bw, bh))
    except Exception as e:
        logger.debug(f"MSER text detect skipped: {e}")

    # --- Morphological subtitle lines (bottom band + full-frame) ---
    try:
        kernel_grad = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3))
        grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, kernel_grad)
        _, th = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 5))
        closed = cv2.morphologyEx(th, cv2.MORPH_CLOSE, close_k)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            x, y, bw, bh = cv2.boundingRect(cnt)
            area = bw * bh
            if area < min_area or bh < 8 or bh > max_box_h or bw < int(w * 0.06):
                continue
            ar = bw / float(bh) if bh else 0
            if ar < 0.8 or ar > 35.0:
                continue
            candidate.append((x, y, bw, bh))
    except Exception as e:
        logger.debug(f"Morph text detect skipped: {e}")

    # --- High-contrast white/yellow hardsub (typical Chinese burn-in) ---
    try:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        # White / light-gray text
        white = cv2.inRange(hsv, (0, 0, 180), (180, 60, 255))
        # Yellow hardsub
        yellow = cv2.inRange(hsv, (18, 80, 160), (40, 255, 255))
        hi = cv2.bitwise_or(white, yellow)
        hi = cv2.morphologyEx(hi, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3)))
        contours, _ = cv2.findContours(hi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            x, y, bw, bh = cv2.boundingRect(cnt)
            if bw * bh < min_area or bh < 8 or bh > max_box_h:
                continue
            if bw < 12:
                continue
            candidate.append((x, y, bw, bh))
    except Exception:
        pass

    padded = [_clamp_box(x, y, bw, bh, w, h, padding) for x, y, bw, bh in candidate]
    return _merge_boxes(padded, gap=10)


def detect_text_boxes(
    frame: np.ndarray,
    min_confidence: float = 0.25,
    padding: int = 6
) -> List[Tuple[int, int, int, int]]:
    """
    Unified text detector. Apple Vision first; OpenCV MSER/morph fallback so
    Chinese overlay text is still found on Linux / when Vision misses stylized fonts.
    """
    boxes = detect_text_boxes_neural(frame, min_confidence=min_confidence, padding=padding)
    if boxes:
        return boxes
    return detect_text_boxes_opencv(frame, padding=padding)


class SubtitleDetectorError(Exception):
    """Raised when subtitle detection fails."""
    pass


class SubtitleDetector:
    """Automated Subtitle & Text Region Detector."""

    def __init__(self, sample_frames: int = 10, position: str = "bottom", search_margin: float = 0.25):
        """
        Args:
            sample_frames: Number of evenly spaced keyframes to sample across video.
            position: Search region target ("bottom", "middle", "top", "all").
            search_margin: Percentage of frame height to scan (default 0.25 = bottom 25%).
        """
        self.sample_frames = max(3, sample_frames)
        self.position = position.lower()
        self.search_margin = min(1.0, max(0.1, search_margin))

    def _get_search_bounds(self, height: int) -> Tuple[int, int]:
        """Calculates vertical crop bounds (min_y, max_y) based on target position."""
        if self.position == "bottom":
            min_y = int(height * (1.0 - self.search_margin))
            max_y = height
        elif self.position == "top":
            min_y = 0
            max_y = int(height * self.search_margin)
        elif self.position == "middle":
            min_y = int(height * 0.3)
            max_y = int(height * 0.7)
        else:
            min_y = 0
            max_y = height
        return min_y, max_y

    def _detect_contours_in_crop(self, crop_zone: np.ndarray, min_y_offset: int, frame_width: int, crop_h: int) -> List[Tuple[int, int, int, int]]:
        """
        Detects candidate text contours inside cropped frame region.
        Uses morphological gradient + adaptive thresholding + dynamic color contrast.
        """
        gray = cv2.cvtColor(crop_zone, cv2.COLOR_BGR2GRAY)

        kernel_grad = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3))
        grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, kernel_grad)
        _, thresh_grad = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        c_max = np.max(crop_zone, axis=2)
        c_min = np.min(crop_zone, axis=2)
        c_diff = (c_max - c_min).astype(np.uint8)
        if np.max(c_diff) > 20:
            _, thresh_color = cv2.threshold(c_diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        else:
            thresh_color = np.zeros_like(gray)

        combined_features = cv2.bitwise_or(thresh_grad, thresh_color)
        close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 5))
        closed = cv2.morphologyEx(combined_features, cv2.MORPH_CLOSE, close_kernel)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidate_boxes = []
        for cnt in contours:
            x, y, w, h_cnt = cv2.boundingRect(cnt)
            aspect_ratio = w / float(h_cnt) if h_cnt > 0 else 0
            if w > frame_width * 0.05 and h_cnt >= 4 and h_cnt <= crop_h * 0.7 and (aspect_ratio >= 0.8 and aspect_ratio <= 35.0):
                real_y = min_y_offset + y
                candidate_boxes.append((x, real_y, w, h_cnt))
        return candidate_boxes

    def detect_frame_subtitle_roi(
        self,
        frame: np.ndarray,
        padding: int = 8
    ) -> Optional[Tuple[int, int, int, int]]:
        """Detects subtitle bounding box (x, y, w, h) for a single frame."""
        if not HAS_OPENCV or frame is None or frame.size == 0:
            return None

        height, width = frame.shape[:2]
        neural = detect_text_boxes(frame, padding=padding)
        if neural:
            # Prefer boxes in the configured search band (usually bottom subtitles)
            min_y, max_y = self._get_search_bounds(height)
            band = [b for b in neural if (b[1] + b[3] / 2) >= min_y and (b[1] + b[3] / 2) <= max_y]
            use = band or neural
            fx1 = min(b[0] for b in use)
            fy1 = min(b[1] for b in use)
            fx2 = max(b[0] + b[2] for b in use)
            fy2 = max(b[1] + b[3] for b in use)
            return _clamp_box(fx1, fy1, fx2 - fx1, fy2 - fy1, width, height, 0)

        min_y, max_y = self._get_search_bounds(height)
        crop_zone = frame[min_y:max_y, :]
        boxes = self._detect_contours_in_crop(crop_zone, min_y, width, max_y - min_y)
        if not boxes:
            return None

        fx1 = min(b[0] for b in boxes)
        fy1 = min(b[1] for b in boxes)
        fx2 = max(b[0] + b[2] for b in boxes)
        fy2 = max(b[1] + b[3] for b in boxes)
        return _clamp_box(fx1, fy1, fx2 - fx1, fy2 - fy1, width, height, padding)

    def generate_frame_mask(
        self,
        frame: np.ndarray,
        padding: int = 8
    ) -> np.ndarray:
        """Generates a dynamic 2D binary uint8 mask (0 or 255) for subtitles in a single frame."""
        if not HAS_OPENCV or frame is None or frame.size == 0:
            return np.zeros((0, 0), dtype=np.uint8)

        height, width = frame.shape[:2]
        mask = np.zeros((height, width), dtype=np.uint8)
        roi = self.detect_frame_subtitle_roi(frame, padding=padding)
        if roi is None:
            return mask

        x, y, w, h = roi
        roi_slice = frame[y:y+h, x:x+w]
        if roi_slice.size == 0:
            return mask

        gray_slice = cv2.cvtColor(roi_slice, cv2.COLOR_BGR2GRAY)
        kernel_grad = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        grad = cv2.morphologyEx(gray_slice, cv2.MORPH_GRADIENT, kernel_grad)
        _, thresh_grad = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kernel_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        stroke_mask = cv2.dilate(thresh_grad, kernel_dilate, iterations=1)
        close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        stroke_mask = cv2.morphologyEx(stroke_mask, cv2.MORPH_CLOSE, close_k)
        mask[y:y+h, x:x+w] = stroke_mask
        return mask

    def detect_subtitle_roi(
        self,
        video_path: str,
        padding: int = 8
    ) -> Tuple[int, int, int, int]:
        """
        Detects consolidated bounding box (x, y, w, h) of hardcoded subtitles across sampled video frames.
        """
        if not HAS_OPENCV:
            raise SubtitleDetectorError("OpenCV library (cv2) is not installed.")

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise SubtitleDetectorError(f"Failed to open video file: {video_path}")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        if width <= 0 or height <= 0:
            cap.release()
            raise SubtitleDetectorError(f"Invalid video dimensions: {width}x{height}")

        if total_frames <= 0:
            total_frames = 100

        step = max(1, total_frames // self.sample_frames)
        sample_indices = [i * step for i in range(self.sample_frames) if i * step < total_frames]

        frame_rois: List[Tuple[int, int, int, int]] = []

        for frame_idx in sample_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            roi = self.detect_frame_subtitle_roi(frame, padding=0)
            if roi is not None:
                frame_rois.append(roi)

        cap.release()

        if not frame_rois:
            logger.info("No subtitle text bounding box detected across sampled frames.")
            return (0, 0, 0, 0)

        centers_x = [r[0] + r[2] / 2.0 for r in frame_rois]
        median_center_x = float(np.median(centers_x))
        max_allowed_dev = width * 0.35
        filtered_rois = [
            r for r in frame_rois
            if abs((r[0] + r[2] / 2.0) - median_center_x) <= max_allowed_dev
        ]
        if not filtered_rois:
            filtered_rois = frame_rois

        min_x = int(np.percentile([r[0] for r in filtered_rois], 10))
        max_x = int(np.percentile([r[0] + r[2] for r in filtered_rois], 90))
        min_y = int(min(r[1] for r in filtered_rois))
        max_y = int(max(r[1] + r[3] for r in filtered_rois))

        if max_x <= min_x:
            min_x = min(r[0] for r in filtered_rois)
            max_x = max(r[0] + r[2] for r in filtered_rois)
        if max_y <= min_y:
            min_y = min(r[1] for r in filtered_rois)
            max_y = max(r[1] + r[3] for r in filtered_rois)

        pad_x = max(0, min_x - padding)
        pad_y = max(0, min_y - padding)
        pad_w = min(width - pad_x, (max_x - min_x) + 2 * padding)
        pad_h = min(height - pad_y, (max_y - min_y) + 2 * padding)

        if pad_h > int(height * 0.30):
            pad_h = int(height * 0.30)
            pad_y = min(pad_y, height - pad_h)

        return (pad_x, pad_y, pad_w, pad_h)


def detect_subtitle_roi(video_path: str, padding: int = 8) -> Tuple[int, int, int, int]:
    """Module-level helper used by WatermarkService."""
    return SubtitleDetector().detect_subtitle_roi(video_path, padding=padding)


def persistent_text_cover_filters(video_path: str, max_boxes: int = 4) -> List[str]:
    """
    Sample a few frames, find caption-like boxes in the MID of the frame
    (not the bottom band we crop), and return ffmpeg delogo filters.
    """
    if not HAS_OPENCV or not video_path or not os.path.exists(video_path):
        return []
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if w < 32 or h < 32:
        cap.release()
        return []
    indices = [max(0, int(n * f)) for f in (0.12, 0.28, 0.45, 0.62, 0.80)] if n > 10 else [0]
    raw_boxes: List[Tuple[int, int, int, int]] = []
    y_lo, y_hi = int(h * 0.10), int(h * 0.78)
    try:
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            for x, y, bw, bh in detect_text_boxes_opencv(frame, padding=4):
                if bh <= 0 or bw <= 0:
                    continue
                cy = y + bh / 2.0
                if cy < y_lo or cy > y_hi:
                    continue
                ar = bw / float(bh)
                if ar < 1.6 or bw < w * 0.18:
                    continue
                if bw * bh > w * h * 0.16:
                    continue
                raw_boxes.append((x, y, bw, bh))
    finally:
        cap.release()
    if not raw_boxes:
        return []

    # Cluster similar boxes across samples
    clusters: List[List[int]] = []  # [x,y,x2,y2,count]
    for x, y, bw, bh in raw_boxes:
        x2, y2 = x + bw, y + bh
        hit = False
        for c in clusters:
            cx, cy, cx2, cy2, cnt = c
            if abs((x + x2) / 2 - (cx + cx2) / 2) < w * 0.12 and abs((y + y2) / 2 - (cy + cy2) / 2) < h * 0.08:
                c[0] = min(cx, x)
                c[1] = min(cy, y)
                c[2] = max(cx2, x2)
                c[3] = max(cy2, y2)
                c[4] = cnt + 1
                hit = True
                break
        if not hit:
            clusters.append([x, y, x2, y2, 1])
    clusters = [c for c in clusters if c[4] >= 2]
    clusters.sort(key=lambda c: c[4], reverse=True)
    filters: List[str] = []
    for x, y, x2, y2, _ in clusters[:max_boxes]:
        x = max(8, x - 4)
        y = max(8, y - 4)
        x2 = min(w - 8, x2 + 4)
        y2 = min(h - 8, y2 + 4)
        bw = (x2 - x) // 2 * 2
        bh = (y2 - y) // 2 * 2
        x = x // 2 * 2
        y = y // 2 * 2
        if bw < 24 or bh < 12:
            continue
        filters.append(f"delogo=x={x}:y={y}:w={bw}:h={bh}:show=0")
    return filters

