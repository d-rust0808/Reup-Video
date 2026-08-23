import React, { useRef, useState, useEffect } from 'react';
import { RoiCanvas } from './RoiCanvas';
import { ReupFxControls } from './ReupFxControls';
import { getStreamUrl, submitJob, uploadVideoFile, fetchChannels } from '../services/api';
import { loadSession, saveSession } from '../services/session';
import { Video, AlertCircle, CheckCircle2, Upload, Loader2 } from 'lucide-react';

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
    return {
    wm_method: 'auto',
    hflip: true,
    speed_ratio: 1.03,
    pitch_shift: true,
    crop_percent: 2.0,
    brightness: 0.01,
    contrast: 1.02,
    saturation: 1.03,
    film_grain: 3,
    modify_md5: true,
    enable_vocal_mute: true,
    enable_tts: true,
    enable_lipsync: true,
    vietsub_style: 'dub',
    burn_subtitles: true,
    tts_voice: 'en-US-AvaMultilingualNeural',
    tts_engine: 'edge-tts',
    target_lang: 'vi',
    source_lang: 'auto',

    channel_id: null,
    post_title: '',
    post_caption: '',
    post_tags: [],
    publish_status: 'READY',
    frame_enabled: true,
    frame_color: 'black',
    frame_thickness: 16,
    overlays: [],
    target_platforms: ['tiktok', 'youtube_shorts', 'facebook'],
    bgm_path: '',
    bgm_id: '',
    bgm_volume: 0.85,
    ...(saved || {}),
    };
  });
  const [channelOverlays, setChannelOverlays] = useState([]);

  useEffect(() => {
    const sess = loadSession();
    const sessionOvs = sess.workbenchOptions?.overlays || [];
    const localOvs = options.overlays || [];
    saveSession({
      workbenchOptions: {
        ...options,
        overlays: localOvs.length ? localOvs : sessionOvs,
      },
    });
  }, [options]);

  useEffect(() => {
    if (selectedMedia) {
      setCurrentMedia(selectedMedia);
      if (selectedMedia.title) {
        setOptions((prev) => ({
          ...prev,
          post_title: prev.post_title || selectedMedia.title,
          post_caption: prev.post_caption || `${selectedMedia.title}\n\n#reup #trending #viral`,
        }));
      }
    }
  }, [selectedMedia]);

  useEffect(() => {
    if (videoRef.current && currentMedia?.video_id) {
      videoRef.current.src = getStreamUrl(currentMedia.video_id);
    }
  }, [currentMedia?.video_id]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!options.channel_id) {
        setChannelOverlays([]);
        return;
      }
      try {
        const data = await fetchChannels();
        const list = data.channels || [];
        const ch = list.find((c) => (c.channel_id || c.id) === options.channel_id);
        if (!cancelled) setChannelOverlays(ch?.overlays || []);
      } catch {
        if (!cancelled) setChannelOverlays([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [options.channel_id]);

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
      if (res.filename) {
        setOptions((prev) => ({
          ...prev,
          post_title: prev.post_title || res.filename,
          post_caption: prev.post_caption || `${res.filename}\n\n#reup #trending #viral`,
        }));
      }
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
    const voice = options.tts_voice || 'en-US-AvaMultilingualNeural';
    let ttsEngine = options.tts_engine || 'edge-tts';
    if (String(voice).toLowerCase().startsWith('kokoro')) ttsEngine = 'kokoro';
    if (String(voice).toLowerCase().startsWith('gtts')) ttsEngine = 'gtts';

    const sess = loadSession();
    const frameOvs = (sess.workbenchOptions?.overlays || options.overlays || []).filter(
      (o) => o && (o.kind === 'frame' ? o.image_path || o.url : true)
    );
    const payload = {
      video_path: currentMedia.file_path || `data/input/raw/${currentMedia.video_id}.mp4`,
      platform: currentMedia.platform || 'douyin',
      canvas_size: [vw, vh],
      video_resolution: [vw, vh],
      channel_id: options.channel_id,
      post_title: options.post_title || currentMedia.title,
      post_caption: options.post_caption,
      post_tags: options.post_tags,
      publish_status: options.publish_status,
      watermark: {
        method: cleanMethod,
        roi: pixelRoi,
        radius: 5,
      },
      reup: {
        hflip: options.hflip,
        speed_ratio: options.speed_ratio,
        pitch_shift: options.pitch_shift,
        crop_percent: normCrop,
        brightness: options.brightness,
        contrast: options.contrast,
        saturation: options.saturation,
        film_grain: options.film_grain ?? 3,
        modify_md5: options.modify_md5,
        enable_vocal_mute: options.enable_vocal_mute,
        enable_tts: options.enable_tts,
        enable_lipsync: options.enable_lipsync !== false,
        vietsub_style: options.vietsub_style || 'auto',
        burn_subtitles: options.burn_subtitles !== false,
        tts_voice: voice,
        tts_engine: ttsEngine,
        target_lang: options.target_lang || 'vi',
        source_lang: options.source_lang || (['douyin', 'kuaishou', 'xiaohongshu'].includes(currentMedia.platform) ? 'zh' : 'auto'),
        channel_id: options.channel_id,
        post_title: options.post_title || currentMedia.title,
        post_caption: options.post_caption,
        post_tags: options.post_tags,
        publish_status: options.publish_status,
        frame_enabled: options.frame_enabled !== false && !frameOvs.some((o) => o.kind === 'frame'),
        frame_color: options.frame_color || 'black',
        frame_thickness: options.frame_thickness || 16,
        overlays: frameOvs,
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
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
      {/* Left Column: Video Player & Canvas ROI */}
      <div className="lg:col-span-7 space-y-4">
        <div className="clean-panel p-6 rounded-3xl shadow-xs space-y-4">
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

          <RoiCanvas videoRef={videoRef} onRoiChange={setRoi} overlays={channelOverlays} />
        </div>

        {msg && (
          <div
            className={`p-4 rounded-2xl border flex items-center gap-2 text-xs font-bold ${
              msg.type === 'success'
                ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
                : 'bg-rose-50 border-rose-200 text-rose-700'
            }`}
          >
            {msg.type === 'success' ? <CheckCircle2 className="w-4 h-4 text-emerald-600" /> : <AlertCircle className="w-4 h-4 text-rose-600" />}
            <span>{msg.text}</span>
          </div>
        )}
      </div>

      {/* Right Column: Processing Reup Controls */}
      <div className="lg:col-span-5">
        <ReupFxControls
          options={options}
          onChange={setOptions}
          onSubmit={handleSubmit}
          submitting={submitting}
        />
      </div>
    </div>
  );
}
