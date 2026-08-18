import React, { useState, useRef } from 'react';
import {
  Download,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Link2,
  Upload,
  ArrowRight,
  Sparkles,
  ClipboardPaste,
  Trash2,
  Video,
  UserCheck,
  Search,
} from 'lucide-react';
import { extractUrls, uploadVideoFile } from '../services/api';

export function UrlExtractor({ onMediaExtracted, onSelectForWorkbench }) {
  const [mode, setMode] = useState('video'); // 'video' | 'channel'
  const [inputUrl, setInputUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [extractedList, setExtractedList] = useState([]);
  const [pasteTip, setPasteTip] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const inputRef = useRef(null);
  const fileInputRef = useRef(null);

  // Focus input and show in-app paste shortcut tip WITHOUT calling restricted navigator.clipboard.readText()
  const handleFocusForPaste = () => {
    if (inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
    setPasteTip(true);
    setTimeout(() => setPasteTip(false), 3000);
  };

  const handleExtract = async (e) => {
    e?.preventDefault();
    if (!inputUrl.trim()) {
      setError(
        mode === 'video'
          ? 'Vui lòng nhập đường link hoặc văn bản chia sẻ của 1 video.'
          : 'Vui lòng nhập đường link trang cá nhân / kênh để quét.'
      );
      return;
    }
    setError(null);
    setLoading(true);

    try {
      const result = await extractUrls([inputUrl.trim()]);
      const items = result.items || [];
      setExtractedList(items);
      onMediaExtracted?.(items);

      // If single video extracted, directly open it in Studio Workbench
      if (items.length === 1) {
        onSelectForWorkbench?.(items[0]);
      }
    } catch (err) {
      setError(err.message || 'Lỗi bóc tách đường dẫn URL');
    } finally {
      setLoading(false);
    }
  };

  const processFile = async (file) => {
    if (!file) return;
    setError(null);
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
      const updated = [newMedia, ...extractedList];
      setExtractedList(updated);
      onMediaExtracted?.(updated);
      onSelectForWorkbench?.(newMedia);
    } catch (err) {
      setError(err.message || 'Tải file video lên thất bại');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleFileUpload = (e) => {
    const file = e.target.files?.[0];
    processFile(file);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file && file.type.startsWith('video/')) {
      processFile(file);
    }
  };

  const getPlatformBadge = (platform) => {
    switch (platform?.toLowerCase()) {
      case 'douyin':
        return (
          <span className="px-2.5 py-1 text-xs font-extrabold rounded-lg bg-pink-50 text-pink-700 border border-pink-200">
            Douyin (抖音)
          </span>
        );
      case 'kuaishou':
        return (
          <span className="px-2.5 py-1 text-xs font-extrabold rounded-lg bg-amber-50 text-amber-800 border border-amber-200">
            Kuaishou (快手)
          </span>
        );
      case 'xiaohongshu':
        return (
          <span className="px-2.5 py-1 text-xs font-extrabold rounded-lg bg-red-50 text-red-700 border border-red-200">
            Xiaohongshu (小红书)
          </span>
        );
      case 'upload':
        return (
          <span className="px-2.5 py-1 text-xs font-extrabold rounded-lg bg-purple-50 text-purple-700 border border-purple-200">
            File Từ Máy
          </span>
        );
      default:
        return (
          <span className="px-2.5 py-1 text-xs font-extrabold rounded-lg bg-slate-100 text-slate-700 border border-slate-200">
            {platform}
          </span>
        );
    }
  };

  return (
    <div className="space-y-6">
      {/* Hero Banner with Solid High-Contrast Gradient & Crystal Clear 3D Showcase */}
      <div className="hero-gradient-card rounded-3xl p-6 sm:p-8 shadow-lg relative overflow-hidden">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-center">
          {/* Left Text Column */}
          <div className="lg:col-span-7 space-y-4">
            <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-white/20 text-xs font-bold text-white border border-white/30 backdrop-blur-xs">
              <Sparkles className="w-3.5 h-3.5 text-amber-300" />
              AI Video Reup Engine Pro V2.0
            </div>
            <h2 className="text-2xl sm:text-3xl font-black tracking-tight text-white leading-tight">
              Xoá Sạch Logo, Text Cũ & Biến Đổi Bản Quyền Video AI
            </h2>
            <p className="text-sm text-blue-50 font-medium leading-relaxed">
              Bóc tách tự động link Douyin, Kuaishou, Xiaohongshu hoặc tải video từ máy tính với công nghệ AI Inpainting FFC LaMa và OpenCV Navier-Stokes.
            </p>

            <div className="flex flex-wrap items-center gap-2 pt-1">
              <span className="px-2.5 py-1 text-xs font-bold rounded-lg bg-white/15 text-white border border-white/20">
                🛡️ Biến Đổi MD5 Anti-Ban
              </span>
              <span className="px-2.5 py-1 text-xs font-bold rounded-lg bg-white/15 text-white border border-white/20">
                🎙️ Khử Giọng & TTS Lồng Tiếng
              </span>
              <span className="px-2.5 py-1 text-xs font-bold rounded-lg bg-white/15 text-white border border-white/20">
                ⚡ Xuất File H.264 / AAC 60fps
              </span>
            </div>
          </div>

          {/* Right Image Showcase Column (100% Sharp & Clear) */}
          <div className="lg:col-span-5 flex justify-center">
            <div className="w-full max-w-sm rounded-2xl overflow-hidden shadow-2xl border-2 border-white/40 bg-white/10 backdrop-blur-xs transform hover:scale-102 transition-transform duration-300">
              <img
                src="/hero_banner.jpg"
                alt="AI Video Editor Workspace"
                className="w-full h-auto object-cover block"
              />
            </div>
          </div>
        </div>
      </div>

      {/* Streamlined Clean Link Ingestion Box (Reup Theo Video / Reup Theo Kênh) */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragOver(true);
        }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
        className={`clean-panel rounded-3xl p-6 sm:p-7 space-y-5 transition-all duration-200 ${
          isDragOver ? 'border-blue-500 ring-4 ring-blue-500/10 bg-blue-50/20' : ''
        }`}
      >
        {/* Mode Selector Tabs (Reup Theo Video vs Reup Theo Kênh) */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-slate-100">
          <div className="flex items-center p-1 bg-slate-100/90 rounded-2xl border border-slate-200 w-fit">
            <button
              onClick={() => {
                setMode('video');
                setInputUrl('');
                setError(null);
              }}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black transition-all cursor-pointer ${
                mode === 'video'
                  ? 'bg-white text-blue-600 shadow-sm'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <Video className="w-4 h-4" />
              <span>1. Reup Theo Video</span>
            </button>

            <button
              onClick={() => {
                setMode('channel');
                setInputUrl('');
                setError(null);
              }}
              className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black transition-all cursor-pointer ${
                mode === 'channel'
                  ? 'bg-white text-blue-600 shadow-sm'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <UserCheck className="w-4 h-4" />
              <span>2. Reup Theo Kênh</span>
            </button>
          </div>

          {/* Quick Paste & Clear Actions */}
          <div className="flex items-center gap-2 relative">
            <button
              type="button"
              onClick={handleFocusForPaste}
              className="px-3.5 py-2 bg-slate-100 hover:bg-slate-200 text-slate-800 text-xs font-bold rounded-xl transition flex items-center gap-1.5 border border-slate-200 cursor-pointer shadow-xs"
              title="Dán nhanh bằng phím tắt Ctrl + V"
            >
              <ClipboardPaste className="w-3.5 h-3.5 text-blue-600" />
              <span>Dán (Ctrl+V)</span>
            </button>

            {inputUrl && (
              <button
                type="button"
                onClick={() => setInputUrl('')}
                className="p-2 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-xl transition cursor-pointer"
                title="Xóa ô nhập"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            )}

            {/* In-app non-blocking Tooltip */}
            {pasteTip && (
              <div className="absolute top-10 left-0 bg-slate-900 text-white text-[11px] font-bold px-3 py-1.5 rounded-xl shadow-xl z-20 whitespace-nowrap animate-in fade-in zoom-in-95 duration-150">
                👉 Nhấn <kbd className="bg-slate-800 px-1.5 py-0.5 rounded border border-slate-700 font-mono">Ctrl + V</kbd> hoặc <kbd className="bg-slate-800 px-1.5 py-0.5 rounded border border-slate-700 font-mono">Cmd + V</kbd> để dán link
              </div>
            )}
          </div>
        </div>

        {/* Single Line Streamlined Input Form */}
        <form onSubmit={handleExtract} className="space-y-4">
          <div className="relative flex items-center">
            <div className="absolute left-4 text-slate-400 pointer-events-none">
              {mode === 'video' ? <Link2 className="w-5 h-5 text-blue-600" /> : <Search className="w-5 h-5 text-purple-600" />}
            </div>

            <input
              ref={inputRef}
              type="text"
              value={inputUrl}
              onChange={(e) => setInputUrl(e.target.value)}
              className="w-full bg-slate-50 border border-slate-300 focus:border-blue-500 focus:bg-white rounded-2xl pl-12 pr-4 py-3.5 text-sm text-slate-900 placeholder-slate-400 focus:ring-3 focus:ring-blue-500/10 outline-none font-mono transition shadow-inner"
              placeholder={
                mode === 'video'
                  ? 'Dán 1 link video tại đây (ví dụ: https://v.douyin.com/xyz123/ hoặc đoạn chia sẻ)...'
                  : 'Dán 1 link kênh/profile tại đây (ví dụ: https://www.douyin.com/user/MS4wLjAB...)...'
              }
            />
          </div>

          {error && (
            <div className="p-3.5 bg-rose-50 border border-rose-200 rounded-2xl flex items-center gap-2.5 text-rose-700 text-xs font-bold">
              <AlertCircle className="w-4 h-4 shrink-0 text-rose-600" />
              <span>{error}</span>
            </div>
          )}

          {/* Action Footer */}
          <div className="flex flex-wrap items-center justify-between gap-4 pt-1">
            <div className="flex items-center space-x-2">
              <span className="text-xs font-bold text-slate-500">Nền tảng hỗ trợ:</span>
              <span className="px-2.5 py-1 text-xs font-extrabold rounded-lg bg-pink-50 text-pink-700 border border-pink-200 flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-pink-500"></span> Douyin
              </span>
              <span className="px-2.5 py-1 text-xs font-extrabold rounded-lg bg-amber-50 text-amber-800 border border-amber-200 flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500"></span> Kuaishou
              </span>
              <span className="px-2.5 py-1 text-xs font-extrabold rounded-lg bg-red-50 text-red-700 border border-red-200 flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-red-500"></span> Xiaohongshu
              </span>
            </div>

            <div className="flex items-center gap-3">
              <input
                ref={fileInputRef}
                type="file"
                accept="video/mp4,video/quicktime,video/x-matroska,video/webm"
                onChange={handleFileUpload}
                className="hidden"
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                className="bg-slate-100 hover:bg-slate-200 text-slate-800 font-bold text-xs px-4 py-2.5 rounded-xl border border-slate-300 transition flex items-center gap-2 cursor-pointer shadow-xs"
              >
                {uploading ? (
                  <Loader2 className="w-4 h-4 animate-spin text-purple-600" />
                ) : (
                  <Upload className="w-4 h-4 text-purple-600" />
                )}
                <span>{uploading ? 'Đang Nạp File...' : 'Tải File Từ Máy'}</span>
              </button>

              <button
                type="submit"
                disabled={loading}
                className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-extrabold text-xs px-6 py-2.5 rounded-xl transition shadow-md shadow-blue-500/20 flex items-center gap-2 cursor-pointer"
              >
                {loading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" /> Đang bóc tách...
                  </>
                ) : mode === 'video' ? (
                  <>
                    <Download className="w-4 h-4" /> Bóc Tách & Tải Video
                  </>
                ) : (
                  <>
                    <Search className="w-4 h-4" /> Quét & Tải Toàn Bộ Kênh
                  </>
                )}
              </button>
            </div>
          </div>
        </form>
      </div>

      {/* Extracted Cards Results (Only displays when multiple videos are extracted from a channel) */}
      {extractedList.length > 1 && (
        <div className="clean-panel rounded-3xl p-6 sm:p-8 space-y-4 animate-in fade-in duration-300">
          <div className="flex items-center justify-between pb-2 border-b border-slate-100">
            <h4 className="text-xs font-black text-slate-500 uppercase tracking-wider">
              Danh Sách Video Đã Quét Từ Kênh ({extractedList.length})
            </h4>
            <span className="text-xs font-semibold text-slate-500">Chọn video để chuyển sang Studio</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {extractedList.map((item, idx) => (
              <div
                key={item.video_id || idx}
                className="clean-card clean-card-hover p-5 rounded-2xl flex flex-col justify-between space-y-4 border border-slate-200"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1.5 min-w-0">
                    {getPlatformBadge(item.platform)}
                    <h5 className="text-sm font-bold text-slate-900 line-clamp-2 mt-1 leading-snug">
                      {item.title || 'Video Không Tiêu Đề'}
                    </h5>
                    <p className="text-xs text-slate-500 font-medium">
                      Tác giả: <span className="font-semibold text-slate-700">{item.author || 'Chưa xác định'}</span>
                    </p>
                  </div>
                  {item.watermark_free && (
                    <span className="px-2.5 py-1 text-xs font-bold bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-lg flex items-center gap-1 shrink-0">
                      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" /> Gốc Sạch
                    </span>
                  )}
                </div>

                <div className="pt-3 border-t border-slate-100 flex items-center justify-between">
                  <span className="text-xs font-mono text-slate-500 font-medium truncate max-w-[140px]">
                    ID: {item.video_id}
                  </span>
                  <button
                    onClick={() => onSelectForWorkbench(item)}
                    className="bg-blue-600 hover:bg-blue-700 text-white font-bold text-xs px-4 py-2 rounded-xl transition shadow-sm flex items-center gap-1.5 cursor-pointer"
                  >
                    <span>Mở Trong Studio</span>
                    <ArrowRight className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
