import React, { useState, useRef, useEffect, useCallback } from 'react';
import heroBanner from '../assets/hero_banner.jpg';
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
  ListVideo,
  Clapperboard,
} from 'lucide-react';
import {
  extractUrls,
  extractChannel,
  uploadVideoFile,
  fetchSampleVideos,
  fetchLibrary,
  deleteLibraryVideo,
  getStreamUrl,
  submitJob,
} from '../services/api';
import { loadSession, saveSession } from '../services/session';

function sameMediaIds(a, b) {
  if (a.length !== b.length) return false;
  return a.every((item, index) => item?.video_id === b[index]?.video_id);
}

export function UrlExtractor({ initialMedia, onMediaExtracted, onSelectForWorkbench, onJobsQueued }) {
  const boot = loadSession();
  const [mode, setMode] = useState(boot.extractMode === 'channel' ? 'channel' : 'video'); // 'video' | 'channel'
  const [inputUrl, setInputUrl] = useState(boot.extractUrl || '');
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [extractedList, setExtractedList] = useState(initialMedia || []);
  const [pasteTip, setPasteTip] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const [samples, setSamples] = useState([]);
  const [maxVideos, setMaxVideos] = useState(boot.maxVideos || 8);
  const [autoReup, setAutoReup] = useState(boot.autoReup === true);
  const [channelProfile, setChannelProfile] = useState(null);
  const [channelMessage, setChannelMessage] = useState('');
  const [queuedJobs, setQueuedJobs] = useState([]);
  const [batchBusy, setBatchBusy] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const inputRef = useRef(null);
  const fileInputRef = useRef(null);
  const onMediaExtractedRef = useRef(onMediaExtracted);
  onMediaExtractedRef.current = onMediaExtracted;

  useEffect(() => {
    onMediaExtractedRef.current?.(extractedList);
  }, [extractedList]);

  useEffect(() => {
    saveSession({
      extractMode: mode,
      extractUrl: inputUrl,
      maxVideos,
      autoReup,
    });
  }, [mode, inputUrl, maxVideos, autoReup]);

  useEffect(() => {
    if (Array.isArray(initialMedia) && initialMedia.length) {
      setExtractedList((prev) => {
        const map = new Map();
        [...initialMedia, ...prev].forEach((item) => {
          if (item?.video_id && !map.has(item.video_id)) map.set(item.video_id, item);
        });
        const next = Array.from(map.values());
        return sameMediaIds(prev, next) ? prev : next;
      });
    }
  }, [initialMedia]);

  // The strip shows the most recent sources, so it has to be refetched whenever
  // the library changes (extract, upload, delete) instead of only on mount.
  const refreshRecent = useCallback(() => {
    fetchSampleVideos()
      .then((data) => setSamples(data.items || []))
      .catch(() => setSamples([]));
  }, []);

  useEffect(() => {
    refreshRecent();
    fetchLibrary()
      .then((data) => {
        const items = data.items || [];
        if (!items.length) return;
        setExtractedList((prev) => {
          const map = new Map();
          [...items, ...prev].forEach((item) => {
            if (item?.video_id && !map.has(item.video_id)) map.set(item.video_id, item);
          });
          const next = Array.from(map.values());
          return sameMediaIds(prev, next) ? prev : next;
        });
      })
      .catch(() => {});
  }, [refreshRecent]);

  // Focus input and show in-app paste shortcut tip WITHOUT calling restricted navigator.clipboard.readText()
  const handleFocusForPaste = () => {
    if (inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
    setPasteTip(true);
    setTimeout(() => setPasteTip(false), 3000);
  };

  const handleDeleteLibraryVideo = async (item) => {
    const videoId = item?.video_id;
    if (!videoId || deletingId) return;
    setDeletingId(videoId);
    setError(null);
    try {
      await deleteLibraryVideo(videoId);
      setExtractedList((prev) => {
        return prev.filter((v) => v.video_id !== videoId);
      });
      refreshRecent();
    } catch (e) {
      setError(e.message || 'Không thể xóa video');
    } finally {
      setDeletingId(null);
    }
  };

  const handleExtract = async (e) => {
    e?.preventDefault();
    if (!inputUrl.trim()) {
      setError(
        mode === 'video'
          ? 'Vui lòng nhập đường link hoặc văn bản chia sẻ của 1 video.'
          : 'Dán URL kênh Douyin/Kuaishou, hoặc dán nhiều link video (mỗi dòng một link).'
      );
      return;
    }
    setError(null);
    setChannelMessage('');
    setLoading(true);

    try {
      if (mode === 'channel') {
        const opts = loadSession().workbenchOptions || {};
        const wantsTts = opts.enable_tts !== false;
        const hasSavedAudioMode = typeof opts.enable_vocal_mute === 'boolean';
        const enableVocalMute = hasSavedAudioMode ? opts.enable_vocal_mute : wantsTts;
        const vocalMuteStrategy = hasSavedAudioMode
          ? (opts.vocal_mute_strategy || 'auto')
          : (wantsTts ? 'demucs_duck' : 'auto');
        const result = await extractChannel({
          url: inputUrl.trim(),
          max_videos: Number(maxVideos) || 8,
          auto_reup: autoReup,
          reup: {
            vietsub_style: opts.vietsub_style || 'dub',
            tts_voice: opts.tts_voice,
            tts_engine: opts.tts_engine,
            enable_tts: wantsTts,
            enable_vocal_mute: enableVocalMute,
            preserve_bgm: opts.preserve_bgm !== false,
            vocal_mute_strategy: vocalMuteStrategy,
            original_vocal_volume: opts.original_vocal_volume ?? 0.10,
            enable_lipsync: opts.enable_lipsync !== false,
            burn_subtitles: opts.burn_subtitles !== false,
            subtitle_mode: opts.burn_subtitles === false ? 'off' : (opts.subtitle_mode || 'soft'),
            target_platforms: opts.target_platforms || ['tiktok', 'youtube_shorts', 'facebook'],
            bgm_path: opts.bgm_path || opts.bgm_id,
            bgm_volume: opts.bgm_volume ?? 0.85,
            channel_id: opts.channel_id,
            wm_method: opts.wm_method,
          },
        });
        const items = result.items || [];
        setChannelProfile(result.profile || null);
        setChannelMessage(result.message || '');
        setQueuedJobs(result.jobs || []);
        if (items.length) {
          setExtractedList((prev) => {
            const map = new Map();
            [...items, ...prev].forEach((item) => {
              if (item?.video_id && !map.has(item.video_id)) map.set(item.video_id, item);
            });
            const next = Array.from(map.values());
            onMediaExtracted?.(next);
            return next;
          });
        }
        if (!items.length && result.hint) {
          setError(result.hint);
        }
        if ((result.jobs || []).length) {
          onJobsQueued?.(result.jobs);
        }
        refreshRecent();
        return;
      }

      const urls = inputUrl
        .split(/[\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      const result = await extractUrls(urls);
      const items = result.items || [];
      if (items.length === 0) {
        setError(
          'Không tìm thấy hoặc không tải được video từ đường link này. Hệ thống hiện hỗ trợ bóc tách Douyin, Kuaishou, Xiaohongshu hoặc bạn có thể bấm "Tải File Từ Máy" để nạp video trực tiếp.'
        );
        return;
      }
      setExtractedList((prev) => {
        const map = new Map();
        [...items, ...prev].forEach((item) => {
          if (item?.video_id && !map.has(item.video_id)) map.set(item.video_id, item);
        });
        const next = Array.from(map.values());
        onMediaExtracted?.(next);
        return next;
      });

      refreshRecent();

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
      setExtractedList((prev) => {
        const next = [newMedia, ...prev.filter((x) => x.video_id !== newMedia.video_id)];
        onMediaExtracted?.(next);
        return next;
      });
      refreshRecent();
      onSelectForWorkbench?.(newMedia);
    } catch (err) {
      setError(err.message || 'Tải file video lên thất bại');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleFileUpload = (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    (async () => {
      for (const f of files) {
        await processFile(f);
      }
    })();
  };

  const handleBatchReup = async () => {
    if (!extractedList.length) return;
    setBatchBusy(true);
    setError(null);
    const opts = loadSession().workbenchOptions || {};
    const wantsTts = opts.enable_tts !== false;
    const hasSavedAudioMode = typeof opts.enable_vocal_mute === 'boolean';
    const enableVocalMute = hasSavedAudioMode ? opts.enable_vocal_mute : wantsTts;
    const vocalMuteStrategy = hasSavedAudioMode
      ? (opts.vocal_mute_strategy || 'auto')
      : (wantsTts ? 'demucs_duck' : 'auto');
    let ok = 0;
    let skipped = 0;
    try {
      for (const item of extractedList) {
        const res = await submitJob({
          video_path: item.file_path || `data/input/raw/${item.video_id}.mp4`,
          platform: item.platform || 'douyin',
          canvas_size: [1080, 1920],
          video_resolution: [1080, 1920],
          watermark: { method: opts.wm_method || 'auto', roi: [0, 0, 0, 0], radius: 5 },
          reup: {
            hflip: opts.hflip !== false,
            speed_ratio: opts.speed_ratio || 1.03,
            pitch_shift: opts.pitch_shift !== false,
            crop_percent: (Number(opts.crop_percent) || 2) / 100,
            film_grain: opts.film_grain ?? 3,
            modify_md5: opts.modify_md5 !== false,
            enable_vocal_mute: enableVocalMute,
            preserve_bgm: opts.preserve_bgm !== false,
            vocal_mute_strategy: vocalMuteStrategy,
            original_vocal_volume: opts.original_vocal_volume ?? 0.10,
            enable_tts: wantsTts,
            enable_lipsync: opts.enable_lipsync !== false,
            vietsub_style: opts.vietsub_style || 'dub',
            burn_subtitles: opts.burn_subtitles !== false,
            subtitle_mode: opts.burn_subtitles === false ? 'off' : (opts.subtitle_mode || 'soft'),
            tts_voice: opts.tts_voice || 'vieneu:Trúc Ly',
            tts_engine: opts.tts_engine || 'vieneu',
            target_lang: 'vi',
            channel_id: opts.channel_id,
            post_title: item.title,
            target_platforms: opts.target_platforms || ['tiktok', 'youtube_shorts', 'facebook'],
            bgm_path: opts.bgm_path || opts.bgm_id,
            bgm_volume: opts.bgm_volume ?? 0.85,
          },
        });
        if (res?.duplicate) skipped += 1;
        else ok += 1;
      }
      setChannelMessage(
        skipped
          ? `Đã xếp ${ok} job mới, bỏ qua ${skipped} clip đang chạy.`
          : `Đã xếp ${ok} job reup. Mở tab Hàng chờ để theo dõi.`
      );
      onJobsQueued?.();
    } catch (err) {
      setError(err.message || 'Reup hàng loạt thất bại');
    } finally {
      setBatchBusy(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragOver(false);
    const files = Array.from(e.dataTransfer.files || []).filter((f) => f.type.startsWith('video/'));
    if (!files.length) return;
    (async () => {
      for (const f of files) {
        await processFile(f);
      }
    })();
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
                src={heroBanner}
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
                setChannelMessage('');
                setChannelProfile(null);
                setQueuedJobs([]);
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
          <div className="relative flex items-start">
            <div className="absolute left-4 top-4 text-slate-400 pointer-events-none">
              {mode === 'video' ? <Link2 className="w-5 h-5 text-blue-600" /> : <Search className="w-5 h-5 text-purple-600" />}
            </div>

            {mode === 'channel' ? (
              <textarea
                ref={inputRef}
                value={inputUrl}
                onChange={(e) => setInputUrl(e.target.value)}
                rows={5}
                className="w-full bg-slate-50 border border-slate-300 focus:border-blue-500 focus:bg-white rounded-2xl pl-12 pr-4 py-3.5 text-sm text-slate-900 placeholder-slate-400 focus:ring-3 focus:ring-blue-500/10 outline-none font-mono transition shadow-inner resize-y min-h-[120px]"
                placeholder={'Dán URL kênh + (nếu cần) vài link video, mỗi dòng một link:\nhttps://www.douyin.com/user/MS4wLjAB...\nhttps://www.douyin.com/jingxuan?modal_id=7671...\nhttps://v.douyin.com/xxxx/'}
              />
            ) : (
              <input
                ref={inputRef}
                type="text"
                value={inputUrl}
                onChange={(e) => setInputUrl(e.target.value)}
                className="w-full bg-slate-50 border border-slate-300 focus:border-blue-500 focus:bg-white rounded-2xl pl-12 pr-4 py-3.5 text-sm text-slate-900 placeholder-slate-400 focus:ring-3 focus:ring-blue-500/10 outline-none font-mono transition shadow-inner"
                placeholder="Dán 1 link video tại đây (ví dụ: https://v.douyin.com/xyz123/ hoặc đoạn chia sẻ)..."
              />
            )}
          </div>

          {mode === 'channel' && (
            <div className="space-y-2.5 p-3 rounded-2xl bg-purple-50/70 border border-purple-100">
              <div className={`rounded-xl border px-3 py-2 text-xs font-bold ${
                autoReup
                  ? 'border-amber-200 bg-amber-50 text-amber-800'
                  : 'border-emerald-200 bg-emerald-50 text-emerald-800'
              }`}>
                {autoReup
                  ? 'Đang bật xử lý AI: tải xong sẽ xóa chữ/logo và reup từng video.'
                  : 'Chỉ tải video gốc: không xóa watermark, không Vietsub, không lồng tiếng.'}
              </div>
              <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-xs font-bold text-slate-700">
                <ListVideo className="w-4 h-4 text-purple-600" />
                Tối đa
                <select
                  value={maxVideos}
                  onChange={(e) => setMaxVideos(Number(e.target.value))}
                  className="bg-white border border-slate-300 rounded-lg px-2 py-1 text-xs font-bold text-slate-800"
                >
                  <option value={4}>4 video</option>
                  <option value={8}>8 video</option>
                  <option value={12}>12 video</option>
                  <option value={20}>20 video</option>
                </select>
              </label>
              <label className="flex items-center gap-2 text-xs font-bold text-slate-700 cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoReup}
                  onChange={(e) => setAutoReup(e.target.checked)}
                  className="w-4 h-4 accent-blue-600"
                />
                <Clapperboard className="w-4 h-4 text-blue-600" />
                Sau khi tải, tự đưa vào hàng xử lý AI
              </label>
              </div>
            </div>
          )}

          {error && (
            <div className="p-3.5 bg-rose-50 border border-rose-200 rounded-2xl flex items-center gap-2.5 text-rose-700 text-xs font-bold">
              <AlertCircle className="w-4 h-4 shrink-0 text-rose-600" />
              <span>{error}</span>
            </div>
          )}

          {channelMessage && !error && (
            <div className="p-3.5 bg-emerald-50 border border-emerald-200 rounded-2xl flex items-center gap-2.5 text-emerald-800 text-xs font-bold">
              <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-600" />
              <span>{channelMessage}</span>
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
                multiple
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
                    <Loader2 className="w-4 h-4 animate-spin" />
                    {mode === 'channel' ? 'Đang quét kênh...' : 'Đang bóc tách...'}
                  </>
                ) : mode === 'video' ? (
                  <>
                    <Download className="w-4 h-4" /> Bóc Tách & Tải Video
                  </>
                ) : (
                  <>
                    <Search className="w-4 h-4" /> {autoReup ? 'Quét & Reup Cả Kênh' : 'Quét & Tải Kênh'}
                  </>
                )}
              </button>
            </div>
          </div>
        </form>
      </div>

      {channelProfile && (channelProfile.nickname || channelProfile.sec_user_id) && (
        <div className="clean-panel rounded-3xl p-5 sm:p-6 flex flex-col sm:flex-row sm:items-center gap-4 border border-purple-100">
          <div className="w-16 h-16 rounded-2xl overflow-hidden bg-slate-200 shrink-0">
            {channelProfile.avatar ? (
              <img src={channelProfile.avatar} alt="" className="w-full h-full object-cover" />
            ) : (
              <div className="w-full h-full flex items-center justify-center text-slate-400">
                <UserCheck className="w-7 h-7" />
              </div>
            )}
          </div>
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-base font-black text-slate-900 truncate">
                {channelProfile.nickname || 'Kênh Douyin'}
              </h3>
              {getPlatformBadge(channelProfile.platform || 'douyin')}
            </div>
            <p className="text-xs text-slate-500 font-medium line-clamp-2 break-words [overflow-wrap:anywhere]">
              {channelProfile.signature || channelProfile.url}
            </p>
            <p className="text-[11px] font-bold text-slate-600">
              {channelProfile.aweme_count ? `${channelProfile.aweme_count} video trên kênh` : 'Đã nhận kênh'}
              {queuedJobs.length ? ` · đã xếp ${queuedJobs.length} job reup` : ''}
            </p>
          </div>
        </div>
      )}

      {samples.length > 0 && (
        <div className="clean-panel rounded-3xl p-6 sm:p-8 space-y-4">
          <div className="flex items-center justify-between pb-2 border-b border-slate-100">
            <h4 className="text-xs font-black text-slate-500 uppercase tracking-wider">
              Video gần nhất — bấm để mở Studio & reup ngay
            </h4>
            <span className="text-xs font-semibold text-slate-500">{samples.length} clip sẵn sàng</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {samples.map((item) => (
              <div key={item.video_id} className="clean-card p-3 rounded-2xl border border-slate-200 space-y-3 min-w-0">
                <div className="rounded-xl overflow-hidden bg-slate-950 aspect-video">
                  <video
                    src={getStreamUrl(item.video_id)}
                    muted
                    playsInline
                    preload="metadata"
                    controls
                    className="w-full h-full object-contain"
                  />
                </div>
                <div className="px-1 space-y-1 min-w-0">
                  <h5 className="text-sm font-bold text-slate-900 line-clamp-2 break-words [overflow-wrap:anywhere]">{item.title}</h5>
                  <p className="text-[11px] text-slate-500 font-medium truncate">
                    {(item.file_size / (1024 * 1024)).toFixed(2)} MB · {item.platform}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => onSelectForWorkbench(item)}
                  className="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold text-xs px-4 py-2.5 rounded-xl transition shadow-sm flex items-center justify-center gap-1.5 cursor-pointer"
                >
                  <span>Mở Trong Studio</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Extracted Cards Results (channel clone or multi-video extract) */}
      {(extractedList.length > 1 || (mode === 'channel' && extractedList.length > 0)) && (
        <div className="clean-panel rounded-3xl p-6 sm:p-8 space-y-4 animate-in fade-in duration-300">
          <div className="flex items-center justify-between pb-2 border-b border-slate-100">
            <h4 className="text-xs font-black text-slate-500 uppercase tracking-wider">
              Danh Sách Video Đã Quét ({extractedList.length})
            </h4>
            <button
              type="button"
              disabled={batchBusy || !extractedList.length}
              onClick={handleBatchReup}
              className="text-xs font-extrabold bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-3 py-2 rounded-xl"
            >
              {batchBusy ? 'Đang xếp job…' : `Reup tất cả (${extractedList.length})`}
            </button>
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
                    <h5 className="text-sm font-bold text-slate-900 line-clamp-2 break-words [overflow-wrap:anywhere] mt-1 leading-snug">
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
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => handleDeleteLibraryVideo(item)}
                      disabled={deletingId === item.video_id}
                      className="p-2 text-rose-600 hover:bg-rose-50 rounded-xl border border-rose-200 disabled:opacity-50"
                      title="Xóa video khỏi thư viện"
                    >
                      {deletingId === item.video_id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => onSelectForWorkbench(item)}
                      className="bg-blue-600 hover:bg-blue-700 text-white font-bold text-xs px-4 py-2 rounded-xl transition shadow-sm flex items-center gap-1.5 cursor-pointer"
                    >
                      <span>Mở Trong Studio</span>
                      <ArrowRight className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
