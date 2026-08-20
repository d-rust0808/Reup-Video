"""
Subtitle & Text Region Auto-Detector Engine.
=============================================
Uses OpenCV morphological analysis, edge gradients, dynamic color contrast,
and contour clustering to locate hardcoded subtitles and text overlays in video frames.
Supports dynamic frame-by-frame and timestamp-segmented subtitle region mask generation.

Target Path: app/services/subtitle_detector.py
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
        if req.results():
            for face in req.results():
                bbox = face.boundingBox()
                fx = int(bbox.origin.x * w)
                fw = int(bbox.size.width * w)
                fh = int(bbox.size.height * h)
                fy = int((1.0 - bbox.origin.y - bbox.size.height) * h)

                x1 = max(0, fx - padding)
                y1 = max(0, fy - padding)
                x2 = min(w, fx + fw + padding)
                y2 = min(h, fy + fh + padding)
                faces.append((x1, y1, x2 - x1, y2 - y1))
        return faces
    except Exception as e:
        logger.debug(f"Apple Vision face detection exception: {e}")
        return []


def detect_text_boxes_neural(
    frame: np.ndarray,
    min_confidence: float = 0.3,
    padding: int = 4
) -> List[Tuple[int, int, int, int]]:
    """
    Hardware-accelerated Neural Text & Subtitle Region Detector.
    Uses Apple Vision Framework (Apple Neural Engine) on macOS to detect all text, Chinese characters,
    brackets, labels, and subtitles with pixel-level bounding boxes.
    Strictly filters out non-text imagery to prevent false-positive blurring on scenery or objects.
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
    try:
        _, buf = cv2.imencode('.png', frame)
        ns_data = NSData.dataWithBytes_length_(buf.tobytes(), len(buf))
        handler = VNImageRequestHandler.alloc().initWithData_options_(ns_data, {})

        boxes = []

        def on_complete(req, err):
            if not err and req.results():
                for obs in req.results():
                    cands = obs.topCandidates_(1)
                    if not cands:
                        continue
                    cand = cands[0]
                    text_str = cand.string()
                    conf = cand.confidence() if hasattr(cand, "confidence") else 1.0

                    # Strictly ignore non-text/empty patterns or low-confidence noise
                    if not text_str or not text_str.strip() or conf < min_confidence:
                        continue

                    bbox = obs.boundingBox()
                    bx = int(bbox.origin.x * w)
                    bw = int(bbox.size.width * w)
                    bh = int(bbox.size.height * h)
                    by = int((1.0 - bbox.origin.y - bbox.size.height) * h)

                    # Discard oversized background regions (anything taller than 35% frame height is scenery/person)
                    if bh > int(h * 0.35) or bw > int(w * 0.98):
                        continue

                    x1 = max(0, bx - padding)
                    y1 = max(0, by - padding)
                    x2 = min(w, bx + bw + padding)
                    y2 = min(h, by + bh + padding)
                    boxes.append((x1, y1, x2 - x1, y2 - y1))

        req = VNRecognizeTextRequest.alloc().initWithCompletionHandler_(on_complete)
        req.setRecognitionLanguages_(['zh-Hans', 'zh-Hant', 'en-US', 'vi-VN'])
        req.setRecognitionLevel_(VNRequestTextRecognitionLevelAccurate)
        req.setUsesLanguageCorrection_(False)
        handler.performRequests_error_([req], None)
        return boxes
    except Exception as e:
        logger.warning(f"Apple Vision text detection exception: {e}")
        return []




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

        # 1. Morphological Gradient for text edge extraction
        kernel_grad = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 3))
        grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, kernel_grad)

        # 2. Otsu thresholding on gradient
        _, thresh_grad = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # 3. Dynamic color contrast across BGR channels
        c_max = np.max(crop_zone, axis=2)
        c_min = np.min(crop_zone, axis=2)
        c_diff = (c_max - c_min).astype(np.uint8)
        if np.max(c_diff) > 20:
            _, thresh_color = cv2.threshold(c_diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        else:
            thresh_color = np.zeros_like(gray)

        # Combine edge & color features
        combined_features = cv2.bitwise_or(thresh_grad, thresh_color)

        # 4. Horizontal morphological closing to connect adjacent characters into text lines
        close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 5))
        closed = cv2.morphologyEx(combined_features, cv2.MORPH_CLOSE, close_kernel)

        # Find contours
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidate_boxes = []
        for cnt in contours:
            x, y, w, h_cnt = cv2.boundingRect(cnt)
            aspect_ratio = w / float(h_cnt) if h_cnt > 0 else 0

            # Filter candidates based on typical text line aspect ratio & size
            if w > frame_width * 0.05 and h_cnt >= 4 and h_cnt <= crop_h * 0.7 and (aspect_ratio >= 0.8 and aspect_ratio <= 35.0):
                real_y = min_y_offset + y
                candidate_boxes.append((x, real_y, w, h_cnt))

        return candidate_boxes

    def detect_frame_subtitle_roi(
        self,
        frame: np.ndarray,
        padding: int = 8
    ) -> Optional[Tuple[int, int, int, int]]:
        """
        Detects subtitle bounding box (x, y, w, h) for a single frame.

        Args:
            frame: Input BGR numpy image array.
            padding: Additional pixel padding around detected bounding box.

        Returns:
            Tuple of (x, y, w, h) in pixels, or None if no subtitle detected.
        """
        if not HAS_OPENCV or frame is None or frame.size == 0:
            return None

        height, width = frame.shape[:2]
        min_y, max_y = self._get_search_bounds(height)
        crop_zone = frame[min_y:max_y, :]

        boxes = self._detect_contours_in_crop(crop_zone, min_y, width, max_y - min_y)
        if not boxes:
            return None

        # Merge boxes for this frame only
        fx1 = min(b[0] for b in boxes)
        fy1 = min(b[1] for b in boxes)
        fx2 = max(b[0] + b[2] for b in boxes)
        fy2 = max(b[1] + b[3] for b in boxes)

        pad_x = max(0, fx1 - padding)
        pad_y = max(0, fy1 - padding)
        pad_w = min(width - pad_x, (fx2 - fx1) + 2 * padding)
        pad_h = min(height - pad_y, (fy2 - fy1) + 2 * padding)

        return (pad_x, pad_y, pad_w, pad_h)

    def generate_frame_mask(
        self,
        frame: np.ndarray,
        padding: int = 8
    ) -> np.ndarray:
        """
        Generates a dynamic 2D binary uint8 mask (0 or 255) for subtitles in a single frame.

        Args:
            frame: Input BGR numpy image array.
            padding: Additional pixel padding around detected bounding box.

        Returns:
            2D uint8 numpy array of shape (height, width).
        """
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

        # Extract character stroke detail inside ROI slice
        gray_slice = cv2.cvtColor(roi_slice, cv2.COLOR_BGR2GRAY)
        kernel_grad = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        grad = cv2.morphologyEx(gray_slice, cv2.MORPH_GRADIENT, kernel_grad)
        _, thresh_grad = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        kernel_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        stroke_mask = cv2.dilate(thresh_grad, kernel_dilate, iterations=1)

        mask[y:y+h, x:x+w] = stroke_mask
        return mask

    def detect_subtitle_roi(
        self,
        video_path: str,
        padding: int = 8
    ) -> Tuple[int, int, int, int]:
        """
        Detects consolidated bounding box (x, y, w, h) of hardcoded subtitles across sampled video frames.
        Eliminates the global static union box bug by using statistical consensus clustering across frames.

        Args:
            video_path: Path to input video file.
            padding: Additional pixel padding added around detected bounding box.

        Returns:
            Tuple of (x, y, w, h) in pixels.
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

        # Eliminate global static union box bug:
        # Filter outlier frame ROIs whose horizontal center deviates significantly from median subtitle center
        centers_x = [r[0] + r[2] / 2.0 for r in frame_rois]
        median_center_x = float(np.median(centers_x))
        max_allowed_dev = width * 0.35

        filtered_rois = [
            r for r in frame_rois
            if abs((r[0] + r[2] / 2.0) - median_center_x) <= max_allowed_dev
        ]

        if not filtered_rois:
            filtered_rois = frame_rois

        # Use 10th and 90th percentiles for X boundaries to avoid single outlier expansion
        min_x = int(np.percentile([r[0] for r in filtered_rois], 10))
        max_x = int(np.percentile([r[0] + r[2] for r in filtered_rois], 90))
        min_y = int(min(r[1] for r in filtered_rois))
        max_y = int(max(r[1] + r[3] for r in filtered_rois))

        # Ensure valid positive dimensions
        if max_x <= min_x:
            min_x = min(r[0] for r in filtered_rois)
            max_x = max(r[0] + r[2] for r in filtered_rois)
        if max_y <= min_y:
            min_y = min(r[1] for r in filtered_rois)
            max_y = max(r[1] + r[3] for r in filtered_rois)

        # Add safety padding
        pad_x = max(0, min_x - padding)
        pad_y = max(0, min_y - padding)
        pad_w = min(width - pad_x, (max_x - min_x) + 2 * padding)
        pad_h = min(height - pad_y, (max_y - min_y) + 2 * padding)

        # Safety cap: Subtitle ROI must never exceed 30% of video height
        if pad_h > int(height * 0.30):
            pad_h = int(height * 0.22)
            pad_y = max(0, height - pad_h - padding)

        merged_roi = (pad_x, pad_y, pad_w, pad_h)
        logger.info(f"Auto-detected subtitle ROI: {merged_roi} (video size: {width}x{height})")
        return merged_roi

    def detect_timestamp_segments(
        self,
        video_path: str,
        padding: int = 8,
        min_duration_sec: float = 0.2
    ) -> List[Dict[str, Any]]:
        """
        Scans video and detects timestamp segments where subtitles appear.

        Returns:
            List of dictionaries containing segment details:
            [{"start_sec": float, "end_sec": float, "start_frame": int, "end_frame": int, "roi": (x, y, w, h)}]
        """
        if not HAS_OPENCV or not os.path.exists(video_path):
            return []

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0 or np.isnan(fps):
            fps = 30.0

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            total_frames = 100

        segments: List[Dict[str, Any]] = []
        current_segment: Optional[Dict[str, Any]] = None

        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            roi = self.detect_frame_subtitle_roi(frame, padding=padding)
            if roi is not None:
                if current_segment is None:
                    current_segment = {
                        "start_frame": frame_idx,
                        "end_frame": frame_idx,
                        "start_sec": round(frame_idx / fps, 3),
                        "end_sec": round((frame_idx + 1) / fps, 3),
                        "rois": [roi]
                    }
                else:
                    current_segment["end_frame"] = frame_idx
                    current_segment["end_sec"] = round((frame_idx + 1) / fps, 3)
                    current_segment["rois"].append(roi)
            else:
                if current_segment is not None:
                    duration = current_segment["end_sec"] - current_segment["start_sec"]
                    if duration >= min_duration_sec:
                        # Consolidate ROI for this segment
                        s_rois = current_segment["rois"]
                        s_x = int(min(r[0] for r in s_rois))
                        s_y = int(min(r[1] for r in s_rois))
                        s_w = int(max(r[0] + r[2] for r in s_rois) - s_x)
                        s_h = int(max(r[1] + r[3] for r in s_rois) - s_y)
                        current_segment["roi"] = (s_x, s_y, s_w, s_h)
                        del current_segment["rois"]
                        segments.append(current_segment)
                    current_segment = None

            frame_idx += 1

        if current_segment is not None:
            duration = current_segment["end_sec"] - current_segment["start_sec"]
            if duration >= min_duration_sec:
                s_rois = current_segment["rois"]
                s_x = int(min(r[0] for r in s_rois))
                s_y = int(min(r[1] for r in s_rois))
                s_w = int(max(r[0] + r[2] for r in s_rois) - s_x)
                s_h = int(max(r[1] + r[3] for r in s_rois) - s_y)
                current_segment["roi"] = (s_x, s_y, s_w, s_h)
                del current_segment["rois"]
                segments.append(current_segment)

        cap.release()
        return segments


def detect_subtitle_roi(
    video_path: str,
    sample_frames: int = 10,
    position: str = "all",
    padding: int = 8
) -> Tuple[int, int, int, int]:
    """Helper function to auto-detect video subtitle ROI."""
    detector = SubtitleDetector(sample_frames=sample_frames, position=position)
    return detector.detect_subtitle_roi(video_path=video_path, padding=padding)


def detect_frame_subtitle_roi(
    frame: np.ndarray,
    position: str = "all",
    padding: int = 8
) -> Optional[Tuple[int, int, int, int]]:
    """Helper function to detect subtitle ROI for a single frame."""
    detector = SubtitleDetector(position=position)
    return detector.detect_frame_subtitle_roi(frame, padding=padding)


def generate_frame_mask(
    frame: np.ndarray,
    position: str = "all",
    padding: int = 8
) -> np.ndarray:
    """Helper function to generate a 2D subtitle mask for a single frame."""
    detector = SubtitleDetector(position=position)
    return detector.generate_frame_mask(frame, padding=padding)


def detect_timestamp_segments(
    video_path: str,
    sample_frames: int = 10,
    position: str = "bottom",
    padding: int = 8,
    min_duration_sec: float = 0.2
) -> List[Dict[str, Any]]:
    """Helper function to detect timestamp segments where subtitles appear in video."""
    detector = SubtitleDetector(sample_frames=sample_frames, position=position)
    return detector.detect_timestamp_segments(video_path=video_path, padding=padding, min_duration_sec=min_duration_sec)
