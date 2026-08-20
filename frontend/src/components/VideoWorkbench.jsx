import React, { useRef, useState, useEffect } from 'react';
import { RoiCanvas } from './RoiCanvas';
import { ReupFxControls } from './ReupFxControls';
import { getStreamUrl, submitJob, uploadVideoFile } from '../services/api';
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

  const [options, setOptions] = useState({
    wm_method: 'all',
    hflip: false,
    speed_ratio: 1.03,
    pitch_shift: true,
    crop_percent: 1.5,
    brightness: 0.01,
    contrast: 1.02,
    saturation: 1.03,
    modify_md5: true,
    enable_vocal_mute: true,
    enable_tts: false,
    tts_voice: 'vi-VN-HoaiMyNeural',
    target_lang: 'vi',
  });

  useEffect(() => {
    if (selectedMedia) {
      setCurrentMedia(selectedMedia);
    }
  }, [selectedMedia]);

  useEffect(() => {
    if (videoRef.current && currentMedia?.video_id) {
      videoRef.current.src = getStreamUrl(currentMedia.video_id);
    }
  }, [currentMedia?.video_id]);

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
    const file = e.dataTransfer.files?.[0];
    if (file && file.type.startsWith('video/')) {
      handleFileUpload(file);
    }
  };

  const handleSubmit = async () => {
    if (!currentMedia) {
      setMsg({ type: 'error', text: 'Vui lòng nạp hoặc chọn 1 video trước khi bắt đầu xử lý.' });
      return;
    }

    setSubmitting(true);
    setMsg(null);

    let pixelRoi = [0, 0, 0, 0];
    if (roi && videoRef.current) {
      const vw = videoRef.current.videoWidth || 1080;
      const vh = videoRef.current.videoHeight || 1920;
      pixelRoi = [
        Math.round(roi.x * vw),
        Math.round(roi.y * vh),
        Math.round(roi.w * vw),
        Math.round(roi.h * vh),
      ];
    }

    const normCrop = options.crop_percent >= 0.5 ? options.crop_percent / 100.0 : options.crop_percent;
    const cleanMethod = options.wm_method === 'opencv_telea' ? 'telea' : (options.wm_method === 'opencv_ns' ? 'ns' : options.wm_method);

    const payload = {
      video_path: currentMedia.file_path || `data/input/${currentMedia.video_id}.mp4`,
      platform: currentMedia.platform || 'douyin',
      watermark: {
        method: cleanMethod,
        roi: pixelRoi,
        radius: 3,
      },
      reup: {
        hflip: options.hflip,
        speed_ratio: options.speed_ratio,
        pitch_shift: options.pitch_shift,
        crop_percent: normCrop,
        brightness: options.brightness,
        contrast: options.contrast,
        saturation: options.saturation,
        modify_md5: options.modify_md5,
        enable_vocal_mute: options.enable_vocal_mute,
        enable_tts: options.enable_tts,
        tts_voice: options.tts_voice || 'vi-VN-HoaiMyNeural',
        target_lang: options.target_lang || 'vi',
      },
    };

    try {
      const res = await submitJob(payload);
      setMsg({ type: 'success', text: `Tạo Job thành công! Mã Job ID: ${res.job_id}` });
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
              Nhấp vào đây hoặc kéo thả file video (MP4, MOV) từ máy tính của bạn vào đây để bắt đầu khoanh vùng xoá logo & chỉnh sửa Reup.
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

          <RoiCanvas videoRef={videoRef} onRoiChange={setRoi} />
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
