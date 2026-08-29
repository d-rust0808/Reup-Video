"""
Pydantic Data Models for Jobs, Watermark Configurations, and Reup Transformations.
===================================================================================
Target Path: app/models/job.py
"""

from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Literal, Dict, Any, Union, List


from pydantic import (
    BaseModel,
    Field,
    ConfigDict,
    AliasChoices,
    field_validator,
    model_validator
)


WatermarkAlgorithm = Literal["auto", "all", "lama", "telea", "ns", "delogo", "boxblur", "crop", "opencv_telea", "opencv_ns", "none"]
JobStatusType = Literal[
    "PENDING", "DOWNLOAD", "DOWNLOADING", "WATERMARK_REMOVAL", "REUP_TRANSFORM", "PROCESSING", "COMPLETED", "FAILED", "CANCELLED"
]


class JobAborted(BaseException):
    """Raised inside a worker thread to unwind a job on cancel or server shutdown.

    Derives from BaseException so the pipeline's broad `except Exception` handlers
    cannot swallow it and mark the job FAILED.
    """


class OverlayItem(BaseModel):
    """A channel branding logo, bottom caption banner, or full-frame khung."""
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None)
    image_path: str = Field(default="", description="Absolute path to PNG/JPG overlay")
    x: float = Field(default=0.04, ge=0.0, le=1.0, description="Left position as fraction of frame width")
    y: float = Field(default=0.04, ge=0.0, le=1.0, description="Top position as fraction of frame height")
    w: float = Field(default=0.18, ge=0.02, le=1.0, description="Overlay width as fraction of frame width")
    opacity: float = Field(default=1.0, ge=0.05, le=1.0)
    kind: str = Field(
        default="logo",
        description="logo (corner badge), banner (bottom caption plate), frame, or overlay",
    )
    band_h: float = Field(
        default=0.22,
        ge=0.0,
        le=0.5,
        description="Bottom-band height for kind=banner (fraction of frame height)",
    )
    url: str = Field(default="", description="Public URL of the overlay image")
    filename: str = Field(default="", description="Original filename")

    @field_validator("kind", mode="before")
    @classmethod
    def _norm_kind(cls, v: Any) -> str:
        s = str(v or "logo").lower().strip()
        if s in ("frame", "khung", "border"):
            return "frame"
        if s in ("banner", "caption"):
            return "banner"
        if s == "overlay":
            return "overlay"
        return "logo"

    @model_validator(mode="after")
    def _banner_geometry(self) -> "OverlayItem":
        if self.kind == "banner":
            self.x = 0.0
            self.w = 1.0
            bh = float(self.band_h or 0.22)
            self.band_h = max(0.10, min(0.36, bh))
            self.y = round(1.0 - self.band_h, 4)
        return self


class WatermarkConfig(BaseModel):
    """Configuration for ROI selection and watermark removal strategy."""
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = Field(default=True, description="Enables or disables watermark removal")
    algorithm: Union[WatermarkAlgorithm, str] = Field(
        default="auto",
        description="Inpainting algorithm / watermark filter strategy"
    )
    roi_x: int = Field(default=0, ge=0, description="X coordinate of ROI bounding box top-left corner")
    roi_y: int = Field(default=0, ge=0, description="Y coordinate of ROI bounding box top-left corner")
    roi_width: int = Field(default=100, ge=0, description="Width of ROI bounding box in pixels")
    roi_height: int = Field(default=50, ge=0, description="Height of ROI bounding box in pixels")
    radius: int = Field(default=3, ge=1, le=100, description="Inpainting boundary radius or blur kernel radius")

    @field_validator("algorithm", mode="before")
    @classmethod
    def _normalize_algorithm(cls, v: Any) -> str:
        if not v:
            return "auto"
        v_clean = str(v).lower().strip()
        if v_clean in ("opencv_telea", "telea"):
            return "telea"
        if v_clean in ("opencv_ns", "ns"):
            return "ns"
        return v_clean

    @property
    def roi_tuple(self) -> tuple[int, int, int, int]:
        """Returns ROI bounding box as (x, y, w, h) tuple."""
        return (self.roi_x, self.roi_y, self.roi_width, self.roi_height)


class ReupConfig(BaseModel):
    """Configuration for visual, audio, and binary hash anti-copyright transformations."""
    model_config = ConfigDict(populate_by_name=True)

    modify_md5: bool = Field(default=True, description="Appends trailing null-bytes & metadata to alter MD5 hash")
    hflip: bool = Field(default=True, description="Applies horizontal video flip")
    speed_factor: float = Field(
        default=1.03,
        gt=0.1,
        le=10.0,
        validation_alias=AliasChoices("speed_factor", "speed_ratio"),
        description="Video speed scaling ratio and audio tempo adjustment"
    )
    pitch_shift: bool = Field(default=True, description="Adjusts audio pitch matching speed ratio")
    pitch_factor: float = Field(default=1.0, ge=0.5, le=2.0, description="Explicit audio pitch factor if custom")
    crop_percent: float = Field(default=0.02, ge=0.0, lt=0.5, description="Border margin crop percentage (0.02 = 2%)")
    brightness: float = Field(default=0.01, ge=-1.0, le=1.0, description="FFmpeg eq filter brightness adjustment")
    contrast: float = Field(default=1.02, gt=0.0, le=3.0, description="FFmpeg eq filter contrast multiplier")
    saturation: float = Field(default=1.03, ge=0.0, le=5.0, description="FFmpeg eq filter saturation multiplier")
    gamma: float = Field(default=1.0, ge=0.1, le=5.0, description="FFmpeg eq filter gamma adjustment")
    hue_shift: float = Field(default=0.0, ge=-180.0, le=180.0, description="FFmpeg hue filter hue shift in degrees")
    sharpen: bool = Field(default=False, description="Enables FFmpeg unsharp filter")
    unsharp_amount: float = Field(default=0.5, ge=0.0, le=5.0, description="FFmpeg unsharp filter luma amount")

    @field_validator("crop_percent", mode="before")
    @classmethod
    def _normalize_crop_percent(cls, v: Any) -> float:
        if v is None:
            return 0.02
        try:
            val = float(v)
            if val > 0.5:
                val = val / 100.0
            return max(0.0, min(0.49, val))
        except (ValueError, TypeError):
            return 0.02
    color_adjust: bool = Field(default=True, description="Enables or disables color equalization filter pass")
    film_grain: float = Field(default=3.0, ge=0.0, le=20.0, description="FFmpeg noise filter amount for visual hash disruption")
    dynamic_motion: bool = Field(default=False, description="Enables dynamic micro-zoom/pan to disrupt temporal match kernels (TMK)")
    meta_compliance_mode: bool = Field(default=False, description="Enables Meta Facebook strict anti-fingerprint compliance preset")
    youtube_compliance_mode: bool = Field(default=False, description="Enables YouTube Content ID and YPP strict compliance preset")
    enable_vocal_mute: bool = Field(default=False, description="Enables original vocal extraction and muting pass")
    vocal_mute_strategy: str = Field(default="auto", description="Strategy for vocal muting: 'auto', 'demucs', 'demucs_duck', 'ffmpeg_filter', 'mute_all'")
    original_vocal_volume: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Gain retained from the separated original-language vocal stem",
    )
    preserve_bgm: bool = Field(default=True, description="Preserves background audio/music after vocal muting")
    audio_ducking: bool = Field(default=False, description="Optionally ducks BGM under TTS")
    enable_tts: bool = Field(default=False, description="Enables TTS synthesis and dubbing pass")
    enable_lipsync: bool = Field(
        default=True,
        description="Isochronous lip-sync: TTS rate + rubberband fitted to original mouth windows",
    )
    vietsub_style: str = Field(
        default="dub",
        description="dub (gốc) | narrator (kể chuyện) | funny (vui nhộn)",
    )
    text_cover_vf: str = Field(default="", description="Extra ffmpeg vf nodes to cover mid-frame source text")
    caption_cover: str = Field(
        default="off",
        description=(
            "Hide source hardsub with a color bar instead of delogo: "
            "off, black_soft, white_soft, black_solid, white_solid"
        ),
    )
    force_bottom_crop: bool = Field(
        default=False,
        description="When True (Chỉ cắt đáy), always crop the source caption band off even if a cover plate is selected",
    )
    caption_cover_image: str = Field(
        default="",
        description="Absolute path to an image that fills the bottom caption band (keeps 9:16)",
    )
    caption_cover_url: str = Field(
        default="",
        description="Public URL of the caption-cover image (used to recover the file if the path is missing)",
    )
    burn_subtitles: bool = Field(default=True, description="Includes translated Vietnamese subtitles in the output")
    subtitle_mode: str = Field(
        default="hard",
        description="Subtitle output mode: 'soft' (toggleable CC), 'hard' (burned in), or 'off'",
    )
    tts_voice: str = Field(default="vieneu:Trúc Ly", description="Voice model/role for TTS synthesis")
    target_lang: str = Field(default="vi", description="Target language code for TTS dubbing")
    tts_engine: str = Field(default="vieneu", description="TTS engine name ('vieneu', 'edge-tts', 'gtts', 'coqui-tts', 'kokoro')")
    source_lang: str = Field(default="auto", description="Source language code for STT/translation")
    srt_path: Optional[str] = Field(default=None, description="Optional pre-built SRT to burn (skips STT)")
    tts_audio_path: Optional[str] = Field(default=None, description="Optional pre-built TTS audio to mix")
    subtitle_bottom_crop: float = Field(
        default=0.0,
        ge=0.0,
        lt=0.5,
        description="Crop this fraction off the bottom to drop burned-in source subtitles (0.13 = 13%)",
    )
    canvas_fill: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="0=centered letterbox. 1=keep picture size, shift up, extra bottom pad for logo.",
    )
    subtitle_y: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="0=auto. Else Vietsub center as a fraction from the top of the picture.",
    )
    cover_y: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Unused compat field. Color cover uses cover_pad from the picture bottom.",
    )
    cover_pad: float = Field(
        default=0.0,
        ge=0.0,
        le=0.40,
        description="Color-cover gap from the picture bottom, as a fraction of min(width,height).",
    )
    subtitle_box_w: float = Field(
        default=0.88,
        ge=0.40,
        le=1.0,
        description="Vietsub background width as a fraction of the picture. Independent of cue length.",
    )
    subtitle_box_h: float = Field(
        default=0.08,
        ge=0.0,
        le=0.22,
        description="Vietsub background height as a fraction of the picture. 0 = no plate (outline text only).",
    )

    @field_validator("cover_y", "subtitle_y", mode="before")
    @classmethod
    def _normalize_subtitle_y(cls, v: Any) -> float:
        if v is None or v == "":
            return 0.0
        try:
            val = float(v)
        except (TypeError, ValueError):
            return 0.0
        if val > 1.0:
            val = val / 100.0
        if val < 0.04:
            return 0.0
        return max(0.0, min(1.0, val))

    @field_validator("subtitle_box_w", mode="before")
    @classmethod
    def _normalize_subtitle_box_w(cls, v: Any) -> float:
        if v is None or v == "":
            return 0.88
        try:
            val = float(v)
        except (TypeError, ValueError):
            return 0.88
        if val > 1.0:
            val = val / 100.0
        if val <= 0:
            return 0.88
        return max(0.40, min(1.0, val))

    @field_validator("subtitle_box_h", mode="before")
    @classmethod
    def _normalize_subtitle_box_h(cls, v: Any) -> float:
        if v is None or v == "":
            return 0.08
        try:
            val = float(v)
        except (TypeError, ValueError):
            return 0.08
        if val > 1.0:
            val = val / 100.0
        if val < 0:
            return 0.0
        return max(0.0, min(0.22, val))

    @field_validator("cover_pad", mode="before")
    @classmethod
    def _normalize_cover_pad(cls, v: Any) -> float:
        if v is None or v == "":
            return 0.0
        try:
            val = float(v)
        except (TypeError, ValueError):
            return 0.0
        if val > 1.0:
            val = val / 100.0
        return max(0.0, min(0.40, val))

    @field_validator("canvas_fill", mode="before")
    @classmethod
    def _normalize_canvas_fill(cls, v: Any) -> float:
        if v is None or v == "":
            return 0.0
        try:
            val = float(v)
        except (TypeError, ValueError):
            return 0.0
        if val > 1.0:
            val = val / 100.0
        return max(0.0, min(1.0, val))
    trim_start_sec: float = Field(
        default=0.0,
        ge=0.0,
        description="Number of seconds to trim/cut from the beginning of the video",
    )
    trim_end_sec: float = Field(
        default=0.0,
        ge=0.0,
        description="Number of seconds to trim/cut from the end of the video",
    )

    # Channel auto-distribution
    channel_id: Optional[str] = Field(default=None, description="Target distribution channel ID")
    channel_ids: List[str] = Field(
        default_factory=list,
        description="Post the same video to multiple channels / Facebook Pages",
    )
    group_ids: List[str] = Field(
        default_factory=list,
        description="Channel groups whose member Pages should all receive the video",
    )
    video_note: str = Field(default="", description="Internal note stored on every channel video from this job")
    target_platforms: List[str] = Field(
        default_factory=lambda: ["tiktok", "youtube_shorts", "facebook"],
        description="Destination platforms to export after the master reup",
    )
    post_title: Optional[str] = Field(default=None, description="Title for post upon completion")
    post_intent: str = Field(
        default="",
        description="Target CTA/copy that agy must keep (phone, service) while rewriting each post",
    )
    agy_write_post: bool = Field(
        default=True,
        description="When True, agy CLI writes a unique SEO title+caption per Fanpage",
    )
    post_caption: Optional[str] = Field(default=None, description="Caption/Hashtags for post upon completion")
    post_tags: Optional[List[str]] = Field(default_factory=list, description="Tags/Labels for channel video")
    publish_status: Optional[str] = Field(default="READY", description="Publish status: DRAFT, READY, PUBLISHED")
    overlays: List[OverlayItem] = Field(
        default_factory=list,
        description="Channel branding logos/frames burned onto every frame",
    )
    frame_enabled: bool = Field(default=False, description="Burn a custom border only when explicitly enabled")
    frame_color: str = Field(default="black", description="Outer border color name or hex")
    frame_thickness: int = Field(default=16, ge=0, le=80, description="Outer border thickness in pixels")
    bgm_path: Optional[str] = Field(default=None, description="Library BGM file to replace source music")
    bgm_volume: float = Field(default=0.85, ge=0.05, le=2.0, description="Gain of imported BGM")

    @model_validator(mode="after")

    def _apply_platform_presets(self) -> "ReupConfig":
        subtitle_mode = (self.subtitle_mode or "hard").strip().lower()
        if not self.burn_subtitles or subtitle_mode == "off":
            self.burn_subtitles = False
            self.subtitle_mode = "off"
        else:
            self.subtitle_mode = "hard" if subtitle_mode == "hard" else "soft"
        voice_lower = (self.tts_voice or "").lower()
        retired_vi_voices = {
            "vi-vn-hoaimy-fast", "vi-vn-hoaimy-warm",
            "vi-vn-namminh-fast", "vi-vn-namminh-deep",
        }
        if (self.target_lang or "vi").lower() == "vi":
            if voice_lower in retired_vi_voices or (
                voice_lower.startswith("en-us-") and "multilingual" in voice_lower
            ):
                self.tts_voice = "vieneu:Trúc Ly"
                self.tts_engine = "vieneu"
            elif voice_lower.startswith("vieneu:"):
                self.tts_engine = "vieneu"
        if self.youtube_compliance_mode:
            self.hflip = True
            if self.speed_factor == 1.03 or self.speed_factor == 1.0:
                self.speed_factor = 1.045
            self.pitch_shift = True
            if self.crop_percent == 0.02:
                self.crop_percent = 0.03
            if self.contrast == 1.02:
                self.contrast = 1.04
            if self.saturation == 1.03:
                self.saturation = 1.05
            if self.brightness == 0.01 or self.brightness == 0.0:
                self.brightness = 0.015
            if self.film_grain == 3.0 or self.film_grain == 0.0:
                self.film_grain = 4.0
            self.dynamic_motion = True
            self.sharpen = True
            if self.unsharp_amount == 0.5:
                self.unsharp_amount = 0.6
            self.modify_md5 = True
            self.enable_vocal_mute = True
        elif self.meta_compliance_mode:
            self.hflip = True
            if self.speed_factor == 1.03 or self.speed_factor == 1.0:
                self.speed_factor = 1.035
            self.pitch_shift = True
            if self.crop_percent == 0.02:
                self.crop_percent = 0.025
            if self.contrast == 1.02:
                self.contrast = 1.035
            if self.saturation == 1.03:
                self.saturation = 1.04
            if self.film_grain == 3.0 or self.film_grain == 0.0:
                self.film_grain = 3.0
            self.dynamic_motion = True
            self.modify_md5 = True
        return self

    @property
    def speed_ratio(self) -> float:
        """Alias property for backward compatibility with speed_ratio."""
        return self.speed_factor


class JobStatus(BaseModel):
    """Task status schema for tracking batch job execution and SQLite persistence."""
    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(default_factory=lambda: f"job-{uuid.uuid4().hex[:12]}", description="Unique job string ID")
    source_url: str = Field(default="", description="Source video URL or file path")
    platform: str = Field(default="auto", description="Platform identifier (douyin, kuaishou, xiaohongshu, auto)")
    status: Union[JobStatusType, str] = Field(default="PENDING", description="Current stage in task state machine")
    progress_percent: float = Field(default=0.0, ge=0.0, le=100.0, description="Task progress percentage (0-100)")
    input_file_path: Optional[str] = Field(default=None, description="Absolute file path of downloaded raw video")
    output_file_path: Optional[str] = Field(default=None, description="Absolute file path of rendered reup video")
    error_message: Optional[str] = Field(default=None, description="Failure detail if status is FAILED")
    watermark_config: WatermarkConfig = Field(default_factory=WatermarkConfig, description="Watermark removal parameters")
    reup_config: ReupConfig = Field(default_factory=ReupConfig, description="Reup transformation parameters")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Creation timestamp")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Last update timestamp")
    quality_status: str = Field(default="PENDING", description="PENDING | PASS | NEEDS_REVIEW")
    quality_report: str = Field(default="{}", description="JSON quality report without secrets")

    def to_sqlite_dict(self) -> Dict[str, Any]:
        """Converts JobStatus to SQLite flat dict format with JSON-serialized configs and ISO datetimes."""
        created_str = self.created_at.isoformat() if isinstance(self.created_at, datetime) else str(self.created_at)
        updated_str = self.updated_at.isoformat() if isinstance(self.updated_at, datetime) else str(self.updated_at)
        return {
            "job_id": self.job_id,
            "source_url": self.source_url,
            "platform": self.platform,
            "status": self.status,
            "progress_percent": self.progress_percent,
            "input_file_path": self.input_file_path,
            "output_file_path": self.output_file_path,
            "error_message": self.error_message,
            "watermark_config": self.watermark_config.model_dump_json(),
            "reup_config": self.reup_config.model_dump_json(),
            "created_at": created_str,
            "updated_at": updated_str,
            "quality_status": self.quality_status,
            "quality_report": self.quality_report,
        }

    @classmethod
    def from_sqlite_row(cls, row: Union[dict, Any]) -> JobStatus:
        """Constructs JobStatus model from a SQLite row or dictionary."""
        d = dict(row)
        
        # Handle field alias mappings if row uses test schema names
        if "input_path" in d and not d.get("input_file_path"):
            d["input_file_path"] = d["input_path"]
        if "output_path" in d and not d.get("output_file_path"):
            d["output_file_path"] = d["output_path"]
        if "progress" in d and "progress_percent" not in d:
            prog = d["progress"]
            d["progress_percent"] = prog * 100.0 if prog <= 1.0 else prog
        if "params" in d and not d.get("reup_config"):
            if isinstance(d["params"], str):
                d["reup_config"] = d["params"]
            elif isinstance(d["params"], dict):
                d["reup_config"] = json.dumps(d["params"])

        if isinstance(d.get("watermark_config"), str):
            try:
                d["watermark_config"] = WatermarkConfig.model_validate_json(d["watermark_config"])
            except Exception:
                d["watermark_config"] = WatermarkConfig()

        if isinstance(d.get("reup_config"), str):
            try:
                d["reup_config"] = ReupConfig.model_validate_json(d["reup_config"])
            except Exception:
                d["reup_config"] = ReupConfig()

        for dt_field in ("created_at", "updated_at"):
            if isinstance(d.get(dt_field), str):
                try:
                    s = d[dt_field].replace(" ", "T")
                    d[dt_field] = datetime.fromisoformat(s)
                except Exception:
                    d[dt_field] = datetime.now(timezone.utc)

        return cls.model_validate(d)


# Aliases for convenience across endpoints & queue manager
Job = JobStatus
