"""
REST API Endpoint for Job Creation and ROI Canvas Processing Submission.
========================================================================
Target Path: app/api/process.py
"""

import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List, Union
from fastapi import APIRouter, HTTPException, Request, status, BackgroundTasks
from pydantic import BaseModel, Field

from app.config import settings
from app.core.ws_manager import ws_manager
from app.models.job import WatermarkConfig, ReupConfig, WatermarkAlgorithm, OverlayItem
from app.services.queue_manager import BatchQueueManager

logger = logging.getLogger(__name__)
router = APIRouter()


class WatermarkPayload(BaseModel):
    method: Optional[Union[WatermarkAlgorithm, str]] = "auto"
    algorithm: Optional[Union[WatermarkAlgorithm, str]] = None
    roi: Optional[List[int]] = Field(default_factory=lambda: [0, 0, 0, 0])
    radius: Optional[int] = 3


class ReupPayload(BaseModel):
    hflip: Optional[bool] = True
    speed_ratio: Optional[float] = 1.03
    speed_factor: Optional[float] = None
    pitch_shift: Optional[bool] = True
    crop_percent: Optional[float] = 0.02
    brightness: Optional[float] = 0.01
    contrast: Optional[float] = 1.02
    saturation: Optional[float] = 1.03
    modify_md5: Optional[bool] = True
    enable_vocal_mute: Optional[bool] = True
    enable_tts: Optional[bool] = False
    enable_lipsync: Optional[bool] = True
    vietsub_style: Optional[str] = "dub"
    burn_subtitles: Optional[bool] = True
    tts_voice: Optional[str] = "en-US-AvaMultilingualNeural"
    tts_engine: Optional[str] = "edge-tts"
    target_lang: Optional[str] = "vi"
    source_lang: Optional[str] = "auto"
    film_grain: Optional[float] = 3.0
    srt_path: Optional[str] = None
    tts_audio_path: Optional[str] = None

    # Channel distribution
    channel_id: Optional[str] = None
    post_title: Optional[str] = None
    post_caption: Optional[str] = None
    post_tags: Optional[List[str]] = None
    publish_status: Optional[str] = "READY"
    overlays: Optional[List[dict]] = None
    frame_enabled: Optional[bool] = True
    frame_color: Optional[str] = "black"
    frame_thickness: Optional[int] = 16
    target_platforms: Optional[List[str]] = None


class ProcessJobRequest(BaseModel):
    media_id: Optional[str] = None
    input_path: Optional[str] = None
    video_path: Optional[str] = None
    platform: Optional[str] = "auto"

    # Flat payload fields
    roi: Optional[List[int]] = None
    canvas_size: Optional[List[int]] = Field(default=[1920, 1080])
    video_resolution: Optional[List[int]] = Field(default=[1920, 1080])
    watermark_method: Optional[Union[WatermarkAlgorithm, str]] = "auto"
    hflip: Optional[bool] = True
    speed_ratio: Optional[float] = 1.03
    pitch_shift: Optional[bool] = True
    crop_percent: Optional[float] = 0.02
    brightness: Optional[float] = 0.01
    contrast: Optional[float] = 1.02
    saturation: Optional[float] = 1.03
    modify_md5: Optional[bool] = True
    enable_vocal_mute: Optional[bool] = True
    enable_tts: Optional[bool] = False
    enable_lipsync: Optional[bool] = True
    vietsub_style: Optional[str] = "dub"
    burn_subtitles: Optional[bool] = True
    tts_voice: Optional[str] = "en-US-AvaMultilingualNeural"
    tts_engine: Optional[str] = "edge-tts"
    target_lang: Optional[str] = "vi"
    source_lang: Optional[str] = "auto"
    film_grain: Optional[float] = 3.0
    srt_path: Optional[str] = None
    tts_audio_path: Optional[str] = None

    # Channel distribution
    channel_id: Optional[str] = None
    post_title: Optional[str] = None
    post_caption: Optional[str] = None
    post_tags: Optional[List[str]] = None
    publish_status: Optional[str] = "READY"
    overlays: Optional[List[dict]] = None
    frame_enabled: Optional[bool] = True
    frame_color: Optional[str] = "black"
    frame_thickness: Optional[int] = 16
    target_platforms: Optional[List[str]] = None

    # Nested payload fields (from React frontend)
    watermark: Optional[WatermarkPayload] = None
    reup: Optional[ReupPayload] = None


def _parse_overlays(req: "ProcessJobRequest") -> list:
    raw = None
    if req.reup and getattr(req.reup, "overlays", None):
        raw = req.reup.overlays
    elif getattr(req, "overlays", None):
        raw = req.overlays
    if not raw:
        return []
    items = []
    for item in raw:
        try:
            if isinstance(item, OverlayItem):
                items.append(item)
            elif isinstance(item, dict) and (item.get("image_path") or item.get("path")):
                if "image_path" not in item and "path" in item:
                    item = {**item, "image_path": item["path"]}
                items.append(OverlayItem(**{k: v for k, v in item.items() if k in OverlayItem.model_fields}))
        except Exception:
            continue
    return items


@router.post("/process/job", status_code=status.HTTP_201_CREATED)
async def submit_process_job(req: ProcessJobRequest, request: Request, background_tasks: BackgroundTasks):
    """
    Submits a video processing job with ROI watermark removal and Reup FX parameters into the Batch Queue.
    Executes asynchronously so HTTP requests return immediately without blocking server thread.
    """
    # 1. Resolve ROI coordinates from flat or nested payloads
    roi_raw = req.roi
    if roi_raw is None and req.watermark and req.watermark.roi is not None:
        roi_raw = req.watermark.roi
    if roi_raw is None:
        roi_raw = [0, 0, 0, 0]

    if not isinstance(roi_raw, list) or len(roi_raw) != 4:
        raise HTTPException(status_code=400, detail="ROI must be a list of 4 integers [x, y, w, h]")

    x, y, w, h = roi_raw
    if x < 0 or y < 0 or w < 0 or h < 0:
        raise HTTPException(status_code=400, detail="Invalid ROI bounds: x, y, w, h must be >= 0")

    # 2. Scale ROI coordinates from Canvas display resolution to native video resolution
    canvas_w, canvas_h = req.canvas_size if (req.canvas_size and len(req.canvas_size) == 2) else (1920, 1080)
    video_w, video_h = req.video_resolution if (req.video_resolution and len(req.video_resolution) == 2) else (1920, 1080)

    if canvas_w <= 0 or canvas_h <= 0 or video_w <= 0 or video_h <= 0:
        raise HTTPException(status_code=400, detail="Canvas and video resolution dimensions must be positive integers")

    scale_x = float(video_w) / float(canvas_w)
    scale_y = float(video_h) / float(canvas_h)

    scaled_x = int(x * scale_x)
    scaled_y = int(y * scale_y)
    scaled_w = int(w * scale_x)
    scaled_h = int(h * scale_y)

    # 3. Resolve input file path from input_path, video_path, or media_id
    input_file = req.input_path or req.video_path
    if input_file and not os.path.exists(input_file):
        fname = os.path.basename(input_file)
        raw_cand = os.path.join(settings.RAW_INPUT_DIR, fname)
        if os.path.exists(raw_cand):
            input_file = raw_cand

    if not input_file and req.media_id:
        cand1 = os.path.join(settings.RAW_INPUT_DIR, f"{req.media_id}.mp4")
        cand2 = os.path.join(settings.RAW_INPUT_DIR, req.media_id)
        if os.path.exists(cand1):
            input_file = cand1
        elif os.path.exists(cand2):
            input_file = cand2

    if not input_file:
        input_file = os.path.join(settings.RAW_INPUT_DIR, f"{req.media_id or 'sample'}.mp4")

    job_id = f"job_{uuid.uuid4().hex[:8]}"
    out_file = os.path.join(settings.OUTPUT_DIR, f"{job_id}.mp4")

    # 4. Construct WatermarkConfig & ReupConfig models
    wm_method = req.watermark_method
    if req.watermark:
        wm_method = req.watermark.method or req.watermark.algorithm or wm_method
    wm_method = (wm_method or "auto").lower()

    wm_enabled = wm_method not in ("none", "off", "disabled")
    wm_cfg = WatermarkConfig(
        enabled=wm_enabled,
        algorithm=wm_method or "auto",
        roi_x=scaled_x,
        roi_y=scaled_y,
        roi_width=scaled_w,
        roi_height=scaled_h,
        radius=req.watermark.radius if req.watermark and req.watermark.radius else 3
    )

    reup_hflip = req.reup.hflip if req.reup and req.reup.hflip is not None else req.hflip
    reup_speed = (
        req.reup.speed_ratio or req.reup.speed_factor if req.reup else req.speed_ratio
    ) or 1.03
    reup_pitch = req.reup.pitch_shift if req.reup and req.reup.pitch_shift is not None else req.pitch_shift
    
    reup_crop = req.reup.crop_percent if req.reup and req.reup.crop_percent is not None else req.crop_percent
    if reup_crop is None:
        reup_crop = 0.02
    elif reup_crop >= 0.5:
        reup_crop = reup_crop / 100.0
    if reup_crop >= 0.5:
        reup_crop = 0.49

    reup_bright = req.reup.brightness if req.reup and req.reup.brightness is not None else req.brightness
    reup_contrast = req.reup.contrast if req.reup and req.reup.contrast is not None else req.contrast
    reup_sat = req.reup.saturation if req.reup and req.reup.saturation is not None else req.saturation
    reup_md5 = req.reup.modify_md5 if req.reup and req.reup.modify_md5 is not None else req.modify_md5
    reup_vocal_mute = req.reup.enable_vocal_mute if req.reup and req.reup.enable_vocal_mute is not None else req.enable_vocal_mute
    reup_tts = req.reup.enable_tts if req.reup and req.reup.enable_tts is not None else req.enable_tts
    reup_lipsync = True
    if req.reup and getattr(req.reup, "enable_lipsync", None) is not None:
        reup_lipsync = req.reup.enable_lipsync
    elif getattr(req, "enable_lipsync", None) is not None:
        reup_lipsync = req.enable_lipsync
    reup_burn = True
    if req.reup and getattr(req.reup, "burn_subtitles", None) is not None:
        reup_burn = req.reup.burn_subtitles
    elif getattr(req, "burn_subtitles", None) is not None:
        reup_burn = req.burn_subtitles
    reup_tts_voice = (req.reup.tts_voice if req.reup and req.reup.tts_voice else req.tts_voice) or "en-US-AvaMultilingualNeural"
    reup_tts_engine = "edge-tts"
    if req.reup and getattr(req.reup, "tts_engine", None):
        reup_tts_engine = req.reup.tts_engine
    elif getattr(req, "tts_engine", None):
        reup_tts_engine = req.tts_engine
    if (reup_tts_voice or "").lower().startswith("kokoro"):
        reup_tts_engine = "kokoro"
    elif (reup_tts_voice or "").lower().startswith("gtts"):
        reup_tts_engine = "gtts"
    reup_target_lang = (req.reup.target_lang if req.reup and req.reup.target_lang else req.target_lang) or "vi"
    platform = (req.platform or "auto").lower()
    reup_source_lang = "auto"
    if req.reup and getattr(req.reup, "source_lang", None):
        reup_source_lang = req.reup.source_lang
    elif getattr(req, "source_lang", None):
        reup_source_lang = req.source_lang
    if reup_source_lang in (None, "", "auto") and platform in ("douyin", "kuaishou", "xiaohongshu"):
        reup_source_lang = "zh"
    reup_grain = 3.0
    if req.reup and getattr(req.reup, "film_grain", None) is not None:
        reup_grain = req.reup.film_grain
    elif getattr(req, "film_grain", None) is not None:
        reup_grain = req.film_grain

    reup_chan_id = req.reup.channel_id if req.reup and req.reup.channel_id else req.channel_id
    reup_post_title = req.reup.post_title if req.reup and req.reup.post_title else req.post_title
    reup_post_caption = req.reup.post_caption if req.reup and req.reup.post_caption else req.post_caption
    reup_post_tags = (req.reup.post_tags if req.reup and req.reup.post_tags is not None else req.post_tags) or []
    reup_pub_status = (req.reup.publish_status if req.reup and req.reup.publish_status else req.publish_status) or "READY"


    reup_cfg = ReupConfig(
        hflip=reup_hflip if reup_hflip is not None else True,
        speed_factor=reup_speed,
        pitch_shift=reup_pitch if reup_pitch is not None else True,
        crop_percent=reup_crop,
        brightness=reup_bright if reup_bright is not None else 0.01,
        contrast=reup_contrast if reup_contrast is not None else 1.02,
        saturation=reup_sat if reup_sat is not None else 1.03,
        modify_md5=reup_md5 if reup_md5 is not None else True,
        enable_vocal_mute=reup_vocal_mute if reup_vocal_mute is not None else True,
        enable_tts=reup_tts if reup_tts is not None else False,
        enable_lipsync=reup_lipsync if reup_lipsync is not None else True,
        vietsub_style=(
            (req.reup.vietsub_style if req.reup and getattr(req.reup, "vietsub_style", None) else getattr(req, "vietsub_style", None))
            or "auto"
        ),
        burn_subtitles=reup_burn if reup_burn is not None else True,
        tts_voice=reup_tts_voice,
        tts_engine=reup_tts_engine or "edge-tts",
        target_lang=reup_target_lang,
        source_lang=reup_source_lang or "auto",
        film_grain=reup_grain if reup_grain is not None else 3.0,
        channel_id=reup_chan_id,
        post_title=reup_post_title,
        post_caption=reup_post_caption,
        post_tags=reup_post_tags,
        publish_status=reup_pub_status,
        srt_path=(req.reup.srt_path if req.reup and getattr(req.reup, "srt_path", None) else req.srt_path),
        tts_audio_path=(req.reup.tts_audio_path if req.reup and getattr(req.reup, "tts_audio_path", None) else req.tts_audio_path),
        overlays=_parse_overlays(req),
        frame_enabled=(
            req.reup.frame_enabled if req.reup and getattr(req.reup, "frame_enabled", None) is not None
            else (req.frame_enabled if getattr(req, "frame_enabled", None) is not None else True)
        ),
        frame_color=(
            (req.reup.frame_color if req.reup and getattr(req.reup, "frame_color", None) else None)
            or getattr(req, "frame_color", None)
            or "black"
        ),
        frame_thickness=(
            req.reup.frame_thickness if req.reup and getattr(req.reup, "frame_thickness", None) is not None
            else (req.frame_thickness if getattr(req, "frame_thickness", None) is not None else 16)
        ),
        target_platforms=(
            (req.reup.target_platforms if req.reup and getattr(req.reup, "target_platforms", None) else None)
            or getattr(req, "target_platforms", None)
            or ["tiktok", "youtube_shorts", "facebook"]
        ),
    )


    # 5. Enqueue job via BatchQueueManager
    qm: Optional[BatchQueueManager] = getattr(request.app.state, "queue_manager", None)
    if qm is None:
        qm = BatchQueueManager(db_path=settings.DB_PATH, max_concurrent_jobs=settings.MAX_CONCURRENT_JOBS)
        qm.register_callback(ws_manager.on_queue_update)
        request.app.state.queue_manager = qm

    now_iso = datetime.now(timezone.utc).isoformat()
    with qm._get_conn() as conn:
        conn.execute(
            """INSERT INTO jobs (
                job_id, source_url, platform, status, progress_percent,
                input_file_path, output_file_path, watermark_config, reup_config,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'PENDING', 0.0, ?, ?, ?, ?, ?, ?)""",
            (
                job_id, input_file, req.platform or 'auto', input_file, out_file,
                wm_cfg.model_dump_json(),
                reup_cfg.model_dump_json(),
                now_iso,
                now_iso
            )
        )
        conn.commit()

    # Enqueue job for background processing asynchronously across parallel worker pool
    qm.ensure_workers()
    await qm.queue.put(job_id)

    return {
        "job_id": job_id,
        "status": "PROCESSING",
        "message": "Job submitted successfully"
    }



@router.get("/voices")
async def get_supported_voices():
    """
    Returns curated VIP Pro multi-language neural TTS voice models and dialects.
    """
    languages = [
        {"code": "vi", "name": "Tiếng Việt (Việt Nam)", "flag": "🇻🇳"},
        {"code": "en", "name": "Tiếng Anh (English Global)", "flag": "🇺🇸"},
        {"code": "zh", "name": "Tiếng Trung (Douyin Trends)", "flag": "🇨🇳"},
        {"code": "ja", "name": "Tiếng Nhật (Anime & Manga)", "flag": "🇯🇵"},
        {"code": "ko", "name": "Tiếng Hàn (K-Drama & K-Pop)", "flag": "🇰🇷"},
        {"code": "th", "name": "Tiếng Thái (Thai Drama)", "flag": "🇹🇭"},
        {"code": "fr", "name": "Tiếng Pháp (French)", "flag": "🇫🇷"},
        {"code": "es", "name": "Tiếng Tây Ban Nha (Spanish)", "flag": "🇪🇸"},
        {"code": "de", "name": "Tiếng Đức (German)", "flag": "🇩🇪"},
        {"code": "ru", "name": "Tiếng Nga (Russian)", "flag": "🇷🇺"},
        {"code": "id", "name": "Tiếng Indonesia", "flag": "🇮🇩"}
    ]

    voices = [
        # Tiếng Việt — multilingual neural (Hoài My/gTTS đã bỏ vì nghe dở)
        {"id": "en-US-AvaMultilingualNeural", "lang": "vi", "name": "Ava (nữ, tự nhiên)", "gender": "Female", "desc": "Nói tiếng Việt rõ, không ngọng — mặc định reup", "tag": "BEST"},
        {"id": "en-US-EmmaMultilingualNeural", "lang": "vi", "name": "Emma (nữ, trẻ)", "gender": "Female", "desc": "Nhẹ, vlog, review", "tag": "HOT"},
        {"id": "en-US-AndrewMultilingualNeural", "lang": "vi", "name": "Andrew (nam, dẫn chuyện)", "gender": "Male", "desc": "Trầm, tài liệu / kể lại", "tag": "PRO"},
        {"id": "en-US-BrianMultilingualNeural", "lang": "vi", "name": "Brian (nam, ấm)", "gender": "Male", "desc": "Ấm, review", "tag": "PRO"},

        # Tiếng Anh Global
        {"id": "en-US-GuyNeural", "lang": "en", "name": "Guy (Nam US)", "gender": "Male", "desc": "Giọng nam trầm cuốn hút, cực kỳ viral trên TikTok & Shorts", "tag": "HOT"},
        {"id": "en-US-JennyNeural", "lang": "en", "name": "Jenny (Nữ US)", "gender": "Female", "desc": "Giọng nữ tự nhiên, tươi vui, vlog đời sống & ẩm thực", "tag": "TREND"},
        {"id": "en-US-AriaNeural", "lang": "en", "name": "Aria (Nữ US)", "gender": "Female", "desc": "Giọng kể chuyện cảm xúc, phim tài liệu & cinematic", "tag": "VIP"},
        {"id": "en-GB-RyanNeural", "lang": "en", "name": "Ryan (Nam UK)", "gender": "Male", "desc": "Phong cách quý ông Anh Quốc, tin tức sang trọng", "tag": "PRO"},

        # Tiếng Trung Douyin
        {"id": "zh-CN-YunxiNeural", "lang": "zh", "name": "Yunxi (Nam Douyin)", "gender": "Male", "desc": "Giọng review phim kinh điển Douyin 'Chú ý xem, người đàn ông này...'", "tag": "HOT TREND"},
        {"id": "zh-CN-XiaoxiaoNeural", "lang": "zh", "name": "Xiaoxiao (Nữ Douyin)", "gender": "Female", "desc": "Dịu dàng, ngọt ngào, review du lịch & ẩm thực Trung Hoa", "tag": "VIP"},
        {"id": "zh-CN-YunjianNeural", "lang": "zh", "name": "Yunjian (Nam Kiếm Hiệp)", "gender": "Male", "desc": "Hùng tráng, uy lực, chuyên phim hành động cổ trang kiếm hiệp", "tag": "PRO"},

        # Tiếng Nhật
        {"id": "ja-JP-NanamiNeural", "lang": "ja", "name": "Nanami (Nữ Anime)", "gender": "Female", "desc": "Giọng Anime dễ thương, tươi sáng chuẩn phong cách Otaku", "tag": "ANIME"},
        {"id": "ja-JP-KeitaNeural", "lang": "ja", "name": "Keita (Nam Manga)", "gender": "Male", "desc": "Giọng nam tính, lịch lãm, review truyện tranh & game", "tag": "PRO"},

        # Tiếng Hàn
        {"id": "ko-KR-SunHiNeural", "lang": "ko", "name": "Sun-Hi (Nữ K-Drama)", "gender": "Female", "desc": "Giọng nữ chính thanh lịch, phim tình cảm lãng mạn Hàn Quốc", "tag": "K-DRAMA"},
        {"id": "ko-KR-InJoonNeural", "lang": "ko", "name": "InJoon (Nam K-Drama)", "gender": "Male", "desc": "Giọng nam thần trầm ấm, review phim Hàn Quốc", "tag": "VIP"},

        # Tiếng Thái
        {"id": "th-TH-PremwadeeNeural", "lang": "th", "name": "Premwadee (Nữ Thái)", "gender": "Female", "desc": "Nữ tính, cảm xúc, phim truyền hình Thái Lan", "tag": "POPULAR"},
        {"id": "th-TH-NiwatNeural", "lang": "th", "name": "Niwat (Nam Thái)", "gender": "Male", "desc": "Nam tính, kịch tính, review phim Thái", "tag": "PRO"},

        # Tiếng Pháp, Tây Ban Nha, Đức, Nga, Indo
        {"id": "fr-FR-DeniseNeural", "lang": "fr", "name": "Denise (Nữ Pháp)", "gender": "Female", "desc": "Quyến rũ, thời trang Paris", "tag": "GLOBAL"},
        {"id": "es-ES-ElviraNeural", "lang": "es", "name": "Elvira (Nữ TBN)", "gender": "Female", "desc": "Sôi động, phóng khoáng Tây Ban Nha", "tag": "GLOBAL"},
        {"id": "de-DE-KatjaNeural", "lang": "de", "name": "Katja (Nữ Đức)", "gender": "Female", "desc": "Chuẩn mực, khoa học & công nghệ", "tag": "GLOBAL"},
        {"id": "ru-RU-DmitryNeural", "lang": "ru", "name": "Dmitry (Nam Nga)", "gender": "Male", "desc": "Trầm mạnh mẽ, phim tài liệu lịch sử", "tag": "GLOBAL"},
        {"id": "id-ID-GadisNeural", "lang": "id", "name": "Gadis (Nữ Indo)", "gender": "Female", "desc": "Tự nhiên, thân thiện Đông Nam Á", "tag": "GLOBAL"}
    ]

    return {
        "languages": languages,
        "voices": voices,
        "default_voice": "en-US-AvaMultilingualNeural",
        "default_lang": "vi"
    }
