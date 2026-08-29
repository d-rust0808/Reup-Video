import React, { useRef, useState, useEffect } from 'react';
import { RoiCanvas } from './RoiCanvas';
import { ReupFxControls } from './ReupFxControls';
import { getStreamUrl, submitJob, uploadVideoFile, getMediaUrl } from '../services/api';
import { loadSession, saveSession } from '../services/session';
import { Video, AlertCircle, CheckCircle2, Upload, Loader2 } from 'lucide-react';
import {
  clampSubtitleBoxH,
  platformsHaveHorizontal,
  platformsHaveVertical,
  resolveCanvasAspect,
} from '../lib/previewCanvas';

export function VideoWorkbench({ selectedMedia, onJobSubmitted }) {
  const videoRef = useRef(null);
  const fileInputRef = useRef(null);
  const [currentMedia, setCurrentMedia] = useState(selectedMedia || null);
  const [roi, setRoi] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [msg, setMsg] = useState(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [options, setOptions] = useState(() => {
    const saved = loadSession().workbenchOptions;
    const merged = {
      preset_id: 'clean_keep_bgm',
      wm_method: 'auto',
      hflip: false,
      speed_ratio: 1.03,
      pitch_shift: true,
      crop_percent: 2.0,
      subtitle_bottom_crop: 22.0,
      caption_cover: 'off',
      caption_cover_image: '',
      caption_cover_url: '',
      caption_cover_name: '',
      trim_start_sec: 0.0,
      trim_end_sec: 0.0,
      brightness: 0.01,
      contrast: 1.02,
      saturation: 1.03,
      film_grain: 3,
      modify_md5: true,
      enable_vocal_mute: false,
      vocal_mute_strategy: 'auto',
      original_vocal_volume: 0.10,
      preserve_bgm: true,
      enable_tts: false,
      enable_lipsync: true,
      vietsub_style: 'dub',
      burn_subtitles: true,
      subtitle_mode: 'hard',
      tts_voice: 'vieneu:Trúc Ly',
      tts_engine: 'vieneu',
      target_lang: 'vi',
      source_lang: 'auto',

    channel_id: null,
    channel_ids: [],
    group_ids: [],
    video_note: '',
    post_title: '',
    post_intent: '',
    agy_write_post: true,
    post_caption: '',
    post_tags: [],
    publish_status: 'READY',
    target_platforms: ['tiktok', 'youtube_shorts', 'facebook'],
    preview_aspect: '9:16',
    canvas_fill: 0,
    subtitle_y: 0,
    subtitle_box_w: 0.88,
    subtitle_box_h: 0.08,
    cover_pad: 0,
    bgm_path: '',
    bgm_id: '',
    bgm_volume: 0.85,
      ...(saved || {}),
    };
    const allowedWm = new Set([
      'auto', 'all', 'crop', 'none', 'off', 'disabled',
      'telea', 'ns', 'lama', 'delogo', 'boxblur',
      'opencv_telea', 'opencv_ns',
    ]);
    if (!allowedWm.has(String(merged.wm_method || ''))) {
      merged.wm_method = 'auto';
    }
    if (merged.caption_cover === 'white_black') {
      merged.caption_cover = 'white_solid';
    }
    // Migrate the legacy crop that left the source caption band visible.
    const colorCoverOn = ['black_soft', 'white_soft', 'black_solid', 'white_solid'].includes(merged.caption_cover);
    if (!colorCoverOn && Number(merged.subtitle_bottom_crop || 0) > 0 && Number(merged.subtitle_bottom_crop) <= 7) {
      merged.subtitle_bottom_crop = 18.0;
    }
    if (saved?.subtitle_mode === 'soft') {
      // Native CC placement differs by browser and caused the subtitle to float upward.
      merged.subtitle_mode = 'hard';
    }
    const migrateLegacyTtsAudio = saved?.enable_tts
      && saved?.preset_id === 'clean_keep_bgm'
      && saved?.original_vocal_volume == null;
    if (merged.preset_id === 'clean_mute_all') {
      merged.enable_vocal_mute = true;
      merged.preserve_bgm = false;
      merged.vocal_mute_strategy = 'mute_all';
    } else if (merged.preset_id === 'clean_duck_vocals' || migrateLegacyTtsAudio) {
      merged.preset_id = 'clean_duck_vocals';
      merged.enable_vocal_mute = true;
      merged.preserve_bgm = true;
      merged.vocal_mute_strategy = 'demucs_duck';
      merged.original_vocal_volume = merged.original_vocal_volume ?? 0.10;
    } else {
      merged.preset_id = 'clean_keep_bgm';
      merged.enable_vocal_mute = false;
      merged.preserve_bgm = true;
      merged.vocal_mute_strategy = 'auto';
    }
    const isRetiredVietnameseVoice = merged.target_lang === 'vi'
      && !/^vieneu:/i.test(merged.tts_voice || '');
    return isRetiredVietnameseVoice
      ? { ...merged, tts_voice: 'vieneu:Trúc Ly', tts_engine: 'vieneu' }
      : merged;
  });
  useEffect(() => {
    saveSession({ workbenchOptions: options });
  }, [options]);

  useEffect(() => {
    if (selectedMedia) {
      setCurrentMedia(selectedMedia);
      // Do not auto-fill post_title/caption from Douyin ids — agy writes those.
    }
  }, [selectedMedia]);

  useEffect(() => {
    if (videoRef.current && currentMedia?.video_id) {
      videoRef.current.src = getStreamUrl(currentMedia.video_id);
    }
  }, [currentMedia?.video_id]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const rate = Math.max(0.8, Math.min(1.5, Number(options.speed_ratio) || 1));
    const apply = () => {
      try {
        video.defaultPlaybackRate = rate;
        video.playbackRate = rate;
      } catch {
        /* some browsers reject mid-load */
      }
    };
    apply();
    video.addEventListener('loadedmetadata', apply);
    video.addEventListener('play', apply);
    return () => {
      video.removeEventListener('loadedmetadata', apply);
      video.removeEventListener('play', apply);
    };
  }, [options.speed_ratio, currentMedia?.video_id]);

  const handleFileUpload = async (e) => {
    const file = e.target?.files?.[0] || e;
    if (!file) return;

    setMsg(null);
    setUploading(true);

    try {
      const res = await uploadVideoFile(file);
      const newMedia = {
        video_id: res.video_id,
        platform: 'upload',
        file_path: res.file_path,
        title: res.filename,
        author: 'File Tải Lên',
      };
      setCurrentMedia(newMedia);
      // Filename is not a Facebook title; agy writes title/caption from post_intent.
    } catch (err) {
      setMsg({ type: 'error', text: err.message || 'Tải file video lên thất bại' });
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragOver(false);
    const files = Array.from(e.dataTransfer.files || []).filter((f) => f.type.startsWith('video/'));
    if (!files.length) return;
    handleFileUpload(files[0]);
  };

  const handleSubmit = async () => {
    if (!currentMedia) {
      setMsg({ type: 'error', text: 'Vui lòng nạp hoặc chọn 1 video trước khi bắt đầu xử lý.' });
      return;
    }

    setSubmitting(true);
    setMsg(null);

    let pixelRoi = [0, 0, 0, 0];
    const vw = videoRef.current?.videoWidth || 1080;
    const vh = videoRef.current?.videoHeight || 1920;
    if (roi && videoRef.current) {
      pixelRoi = [
        Math.round(roi.x * vw),
        Math.round(roi.y * vh),
        Math.round(roi.w * vw),
        Math.round(roi.h * vh),
      ];
    }

    // Slider is always percent (0–5). Never send 0.4 as 40% crop.
    const normCrop = Number(options.crop_percent || 0) / 100.0;
    const cleanMethod = options.wm_method === 'opencv_telea' ? 'telea' : (options.wm_method === 'opencv_ns' ? 'ns' : options.wm_method);
    const voice = options.tts_voice || 'vieneu:Trúc Ly';
    let ttsEngine = options.tts_engine || 'vieneu';
    if (String(voice).toLowerCase().startsWith('kokoro')) ttsEngine = 'kokoro';
    if (String(voice).toLowerCase().startsWith('vieneu:')) ttsEngine = 'vieneu';
    if (String(voice).toLowerCase().startsWith('vi-vn-')) ttsEngine = 'edge-tts';
    if (String(voice).toLowerCase().startsWith('gtts')) ttsEngine = 'gtts';

    const payload = {
      video_path: currentMedia.file_path || `data/input/raw/${currentMedia.video_id}.mp4`,
      platform: currentMedia.platform || 'douyin',
      canvas_size: [vw, vh],
      video_resolution: [vw, vh],
      channel_id: options.channel_id,
      channel_ids: options.channel_ids || (options.channel_id ? [options.channel_id] : []),
      group_ids: options.group_ids || [],
      video_note: options.video_note || '',
      post_title: '',
      post_intent: options.post_intent || '',
      agy_write_post: Boolean((options.post_intent || '').trim()),
      post_caption: '',
      post_tags: options.post_tags,
      publish_status: options.publish_status,
      watermark: {
        method: cleanMethod,
        roi: pixelRoi,
        radius: 5,
      },
      reup: {
        hflip: options.hflip,
        speed_ratio: Number(options.speed_ratio) || 1.03,
        speed_factor: Number(options.speed_ratio) || 1.03,
        pitch_shift: options.pitch_shift,
        crop_percent: normCrop,
        subtitle_bottom_crop: Number(options.subtitle_bottom_crop || 0) / 100.0,
        canvas_fill: Math.max(0, Math.min(1, Number(options.canvas_fill) || 0)),
        subtitle_y: Math.max(0, Math.min(1, Number(options.subtitle_y) || 0)),
        subtitle_box_w: Math.max(0.40, Math.min(1, Number(options.subtitle_box_w) || 0.88)),
        subtitle_box_h: clampSubtitleBoxH(options.subtitle_box_h),
        cover_pad: Math.max(0, Math.min(0.40, Number(options.cover_pad || 0) / 100)),
        caption_cover: options.caption_cover || 'off',
        caption_cover_image: options.caption_cover_image || '',
        caption_cover_url: options.caption_cover_url || '',
        overlays: [
          ...((options.caption_cover === 'image' && options.caption_cover_image)
            ? (() => {
                const bandH = Math.max(0.10, Math.min(0.36, Number(options.subtitle_bottom_crop || 0) / 100 || 0.22));
                return [{
                  image_path: options.caption_cover_image,
                  url: options.caption_cover_url || '',
                  kind: 'banner',
                  band_h: bandH,
                  x: 0,
                  y: 1 - bandH,
                  w: 1,
                  opacity: 1,
                }];
              })()
            : []),
        ],
        trim_start_sec: Number(options.trim_start_sec || 0),
        trim_end_sec: Number(options.trim_end_sec || 0),
        brightness: options.brightness,
        contrast: options.contrast,
        saturation: options.saturation,
        film_grain: options.film_grain ?? 3,
        modify_md5: options.modify_md5,
        enable_vocal_mute: options.enable_vocal_mute,
        vocal_mute_strategy: options.vocal_mute_strategy || 'auto',
        original_vocal_volume: options.original_vocal_volume ?? 0.10,
        preserve_bgm: options.preserve_bgm !== false,
        enable_tts: options.enable_tts,
        enable_lipsync: options.enable_lipsync !== false,
        vietsub_style: options.vietsub_style || 'dub',
        burn_subtitles: options.burn_subtitles !== false,
        subtitle_mode: options.burn_subtitles === false ? 'off' : (options.subtitle_mode || 'hard'),
        tts_voice: voice,
        tts_engine: ttsEngine,
        target_lang: options.target_lang || 'vi',
        source_lang: options.source_lang || (['douyin', 'kuaishou', 'xiaohongshu'].includes(currentMedia.platform) ? 'zh' : 'auto'),
        channel_id: options.channel_id,
        channel_ids: options.channel_ids || (options.channel_id ? [options.channel_id] : []),
        group_ids: options.group_ids || [],
        video_note: options.video_note || '',
        post_title: '',
        post_intent: options.post_intent || '',
        agy_write_post: Boolean((options.post_intent || '').trim()),
        post_caption: '',
        post_tags: options.post_tags,
        publish_status: options.publish_status,
        target_platforms: options.target_platforms || ['tiktok', 'youtube_shorts', 'facebook'],
        bgm_path: options.bgm_path || options.bgm_id || '',
        bgm_volume: options.bgm_volume ?? 0.85,
      },
    };


    try {
      const res = await submitJob(payload);
      if (res?.duplicate) {
        setMsg({ type: 'success', text: `Clip này đang chạy rồi (${res.job_id}) — không tạo job trùng.` });
      } else {
        setMsg({ type: 'success', text: `Tạo Job thành công! Mã Job ID: ${res.job_id}` });
      }
      onJobSubmitted?.(res.job_id);
    } catch (err) {
      setMsg({ type: 'error', text: err.message || 'Gửi job thất bại' });
    } finally {
      setSubmitting(false);
    }
  };

  // If no media is selected yet, render a beautiful Clean Empty State Dropzone
  if (!currentMedia) {
    return (
      <div className="clean-panel rounded-3xl p-8 sm:p-12 text-center max-w-3xl mx-auto space-y-6">
        <input
          ref={fileInputRef}
          type="file"
          accept="video/mp4,video/quicktime,video/x-matroska,video/webm"
          onChange={handleFileUpload}
          className="hidden"
        />

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setIsDragOver(true);
          }}
          onDragLeave={() => setIsDragOver(false)}
          onDrop={handleDrop}
          className={`p-10 sm:p-14 border-2 border-dashed rounded-3xl transition-all duration-200 cursor-pointer ${
            isDragOver
              ? 'border-blue-500 bg-blue-50/50 scale-102'
              : 'border-slate-300 hover:border-blue-400 hover:bg-slate-50/60'
          }`}
          onClick={() => fileInputRef.current?.click()}
        >
          <div className="w-16 h-16 rounded-3xl bg-blue-50 text-blue-600 border border-blue-200 flex items-center justify-center mx-auto shadow-sm">
            {uploading ? <Loader2 className="w-8 h-8 animate-spin text-blue-600" /> : <Upload className="w-8 h-8" />}
          </div>

          <div className="mt-4 space-y-2">
            <h3 className="text-lg font-black text-slate-900">
              {uploading ? 'Đang Tải Video Lên...' : 'Chưa Có Video Nào Được Chọn Trong Studio'}
            </h3>
            <p className="text-xs text-slate-500 font-medium max-w-md mx-auto leading-relaxed">
              Nhấp hoặc kéo thả MP4/MOV. Không cần khoanh logo — bấm Reup là máy tự xử lý.
            </p>
          </div>

          <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
            <button
              type="button"
              disabled={uploading}
              className="bg-blue-600 hover:bg-blue-700 text-white font-extrabold text-xs px-5 py-3 rounded-2xl transition shadow-md shadow-blue-500/20 flex items-center gap-2 cursor-pointer"
            >
              <Upload className="w-4 h-4" />
              <span>Tải Video Từ Máy Tính</span>
            </button>
          </div>
        </div>

        {msg && (
          <div
            className={`p-4 rounded-2xl border flex items-center gap-2 text-xs font-bold ${
              msg.type === 'success'
                ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
                : 'bg-rose-50 border-rose-200 text-rose-700'
            }`}
          >
            {msg.type === 'success' ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
            <span>{msg.text}</span>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Top Section: Video Player & Canvas ROI */}
      <div className="clean-panel p-6 rounded-3xl shadow-xs space-y-4 max-w-4xl mx-auto">
        <div className="flex items-center justify-between pb-3 border-b border-slate-100">
          <div className="flex items-center space-x-2.5 min-w-0">
            <div className="p-2 rounded-xl bg-blue-50 text-blue-600 border border-blue-100 shrink-0">
              <Video className="w-4 h-4" />
            </div>
            <h3 className="text-sm font-bold text-slate-900 truncate max-w-md">
              {currentMedia.title || currentMedia.video_id}
            </h3>
          </div>
          <div className="flex items-center gap-2">
            <span className="px-2.5 py-1 text-xs font-bold rounded-lg bg-blue-50 text-blue-700 border border-blue-200 uppercase">
              {currentMedia.platform}
            </span>
            <button
              onClick={() => fileInputRef.current?.click()}
              className="p-1.5 text-slate-500 hover:text-blue-600 hover:bg-slate-100 rounded-xl transition cursor-pointer"
              title="Đổi video khác từ máy"
            >
              <Upload className="w-4 h-4" />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="video/mp4,video/quicktime,video/x-matroska,video/webm"
              onChange={handleFileUpload}
              className="hidden"
            />
          </div>
        </div>

        <RoiCanvas
          videoRef={videoRef}
          onRoiChange={setRoi}
          onCanvasAspect={(aspect) => setOptions((prev) => ({ ...prev, preview_aspect: aspect }))}
          onCanvasFill={(fill) => setOptions((prev) => ({ ...prev, canvas_fill: fill }))}
          onSubtitleY={(y) => setOptions((prev) => ({ ...prev, subtitle_y: y }))}
          onSubtitleBoxW={(w) => setOptions((prev) => ({ ...prev, subtitle_box_w: w }))}
          onSubtitleBoxH={(h) => setOptions((prev) => ({ ...prev, subtitle_box_h: h }))}
          preview={{
            cropPercent: Number(options.crop_percent) || 0,
            bottomCrop: Number(options.subtitle_bottom_crop) || 0,
            captionCover: options.caption_cover || 'off',
            captionCoverUrl: options.caption_cover === 'image' && options.caption_cover_url
              ? getMediaUrl(options.caption_cover_url)
              : '',
            wmMethod: options.wm_method,
            hflip: Boolean(options.hflip),
            speedRatio: Number(options.speed_ratio) || 1,
            brightness: Number(options.brightness) || 0,
            contrast: Number(options.contrast) || 1,
            saturation: Number(options.saturation) || 1,
            canvasAspect: resolveCanvasAspect(options.target_platforms, options.preview_aspect),
            canvasFill: Number(options.canvas_fill) || 0,
            subtitleY: Number(options.subtitle_y) || 0,
            subtitleBoxW: Number(options.subtitle_box_w) || 0.88,
            subtitleBoxH: clampSubtitleBoxH(options.subtitle_box_h),
            hasVertical: platformsHaveVertical(options.target_platforms),
            hasHorizontal: platformsHaveHorizontal(options.target_platforms),
          }}
        />
      </div>

      {msg && (
        <div
          className={`p-4 rounded-2xl border flex items-center gap-2 text-xs font-bold max-w-4xl mx-auto ${
            msg.type === 'success'
              ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
              : 'bg-rose-50 border-rose-200 text-rose-700'
          }`}
        >
          {msg.type === 'success' ? <CheckCircle2 className="w-4 h-4 text-emerald-600" /> : <AlertCircle className="w-4 h-4 text-rose-600" />}
          <span>{msg.text}</span>
        </div>
      )}

      {/* Bottom Section: Processing Reup Controls */}
      <ReupFxControls
        options={options}
        onChange={setOptions}
        onSubmit={handleSubmit}
        submitting={submitting}
      />
    </div>
  );
}
