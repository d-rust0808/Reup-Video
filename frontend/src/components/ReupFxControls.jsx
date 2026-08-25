import React, { useState, useEffect } from 'react';
import {
  Wand2,
  Hash,
  FlipHorizontal,
  Volume2,
  Palette,
  Zap,
  VolumeX,
  Mic,
  Tv,
  Tag,
  CheckCircle2,
  FileText,
  Sparkles,
  Loader2,
  BookOpen,
  Smile,
  Captions,
  Share2,
  ChevronDown,
  Music,
  Scissors,
  Play,
  Square,
} from 'lucide-react';

import { fetchChannels, fetchBgmLibrary, previewVoice } from '../services/api';

const VOICE_OPTIONS = {
  vi: [
    { value: 'vieneu:Trúc Ly', label: 'Trúc Ly — nữ Bắc, tự nhiên' },
    { value: 'vieneu:Phạm Tuyên', label: 'Phạm Tuyên — nam Bắc, tự nhiên' },
    { value: 'vieneu:Xuân Vĩnh', label: 'Xuân Vĩnh — nam Nam, tự nhiên' },
    { value: 'vieneu:Đoan Trang', label: 'Đoan Trang — nữ Bắc, tự nhiên' },
    { value: 'vieneu:Ngọc Huyền', label: 'Ngọc Huyền — nữ Bắc, tự nhiên' },
    { value: 'vieneu:Adam', label: 'Adam — nam Nam, tự nhiên' },
    { value: 'vieneu:Quang Sơn', label: 'Quang Sơn — nam Trung, tự nhiên' },
    { value: 'vieneu:Ngọc Trân', label: 'Ngọc Trân — nữ Trung, tự nhiên' },
    { value: 'vieneu:Thái Sơn', label: 'Thái Sơn — nam Nam, kể chuyện' },
    { value: 'vieneu:Thanh Bình', label: 'Thanh Bình — nam Bắc, kể chuyện' },
    { value: 'vieneu:Ngọc Linh', label: 'Ngọc Linh — nữ Bắc, kể chuyện' },
    { value: 'vieneu:Thục Đoan', label: 'Thục Đoan — nữ Nam, kể chuyện' },
    { value: 'vieneu:Mỹ Duyên', label: 'Mỹ Duyên — nữ Nam, đọc truyện' },
    { value: 'vieneu:Quỳnh Anh', label: 'Quỳnh Anh — nữ Bắc, đọc truyện' },
    { value: 'vieneu:Đức Trí', label: 'Đức Trí — nam Nam, đọc truyện' },
    { value: 'vieneu:Kim Thanh', label: 'Kim Thanh — nữ Nam, đọc truyện' },
    { value: 'vieneu:Minh Đức', label: 'Minh Đức — nam Bắc, tin tức' },
    { value: 'vieneu:Mai Anh', label: 'Mai Anh — nữ Bắc, tin tức' },
    { value: 'vieneu:Minh Triết', label: 'Minh Triết — nam Nam, tin tức' },
    { value: 'vieneu:Thùy Dung', label: 'Thùy Dung — nữ Nam, tin tức' },
  ],
  en: [
    { value: 'en-US-AvaMultilingualNeural', label: 'Ava — nữ, tự nhiên' },
    { value: 'en-US-AndrewMultilingualNeural', label: 'Andrew — nam, dẫn chuyện' },
  ],
  th: [
    { value: 'th-TH-PremwadeeNeural', label: 'Premwadee — nữ Thái' },
    { value: 'th-TH-NiwatNeural', label: 'Niwat — nam Thái' },
  ],
  id: [
    { value: 'id-ID-GadisNeural', label: 'Gadis — nữ Indonesia' },
    { value: 'id-ID-ArdiNeural', label: 'Ardi — nam Indonesia' },
  ],
  ja: [
    { value: 'ja-JP-NanamiNeural', label: 'Nanami — nữ Nhật' },
    { value: 'ja-JP-KeitaNeural', label: 'Keita — nam Nhật' },
  ],
  ko: [
    { value: 'ko-KR-SunHiNeural', label: 'Sun-Hi — nữ Hàn' },
    { value: 'ko-KR-InJoonNeural', label: 'InJoon — nam Hàn' },
  ],
  pt: [
    { value: 'pt-BR-FranciscaNeural', label: 'Francisca — nữ Brazil' },
    { value: 'pt-BR-AntonioNeural', label: 'Antonio — nam Brazil' },
  ],
};

const resolveVoiceEngine = (voice) => String(voice || '').toLowerCase().startsWith('vieneu:')
  ? 'vieneu'
  : 'edge-tts';

export function ReupFxControls({ options, onChange, onSubmit, submitting }) {
  const [channels, setChannels] = useState([]);
  const [showWmAdvanced, setShowWmAdvanced] = useState(false);
  const [bgmList, setBgmList] = useState([]);
  const [previewingVoice, setPreviewingVoice] = useState(false);
  const [voicePreviewError, setVoicePreviewError] = useState('');
  const [voicePreviewAudio, setVoicePreviewAudio] = useState(null);

  useEffect(() => () => {
    if (voicePreviewAudio) {
      voicePreviewAudio.pause();
      URL.revokeObjectURL(voicePreviewAudio.src);
    }
  }, [voicePreviewAudio]);

  useEffect(() => {
    loadChannels();
    fetchBgmLibrary()
      .then((d) => setBgmList(d.items || []))
      .catch(() => setBgmList([]));

    const refreshChannels = () => loadChannels();
    window.addEventListener('reup:channels-changed', refreshChannels);
    return () => window.removeEventListener('reup:channels-changed', refreshChannels);
  }, []);

  const loadChannels = async () => {
    try {
      const data = await fetchChannels();
      const list = Array.isArray(data) ? data : (data.channels || []);
      setChannels(list);
    } catch {
      setChannels([]);
    }
  };

  const handleChange = (key, value) => {
    onChange({ ...options, [key]: value });
  };

  const handleVoicePreview = async () => {
    if (voicePreviewAudio && !voicePreviewAudio.paused) {
      voicePreviewAudio.pause();
      voicePreviewAudio.currentTime = 0;
      setPreviewingVoice(false);
      return;
    }

    setPreviewingVoice(true);
    setVoicePreviewError('');
    try {
      const blob = await previewVoice({
        voice: options.tts_voice || 'vieneu:Trúc Ly',
        lang: options.target_lang || 'vi',
        engine: options.tts_engine || 'vieneu',
      });
      if (voicePreviewAudio) {
        voicePreviewAudio.pause();
        URL.revokeObjectURL(voicePreviewAudio.src);
      }
      const audio = new Audio(URL.createObjectURL(blob));
      audio.onended = () => setPreviewingVoice(false);
      audio.onerror = () => {
        setPreviewingVoice(false);
        setVoicePreviewError('Không phát được bản nghe thử.');
      };
      setVoicePreviewAudio(audio);
      await audio.play();
    } catch (error) {
      setPreviewingVoice(false);
      setVoicePreviewError(error.message || 'Không thể tạo bản nghe thử.');
    }
  };

  const handlePresetSelect = (presetId) => {
    if (presetId === 'clean_keep_bgm') {
      onChange({
        ...options,
        preset_id: 'clean_keep_bgm',
        subtitle_bottom_crop: 7.0,
        enable_vocal_mute: false,
        preserve_bgm: true,
        vocal_mute_strategy: 'auto',
        wm_method: 'auto',
        hflip: false,
        speed_ratio: 1.03,
        crop_percent: 2.0,
      });
    } else if (presetId === 'clean_duck_vocals') {
      onChange({
        ...options,
        preset_id: 'clean_duck_vocals',
        subtitle_bottom_crop: 7.0,
        enable_vocal_mute: true,
        preserve_bgm: true,
        vocal_mute_strategy: 'demucs_duck',
        original_vocal_volume: options.original_vocal_volume ?? 0.10,
        wm_method: 'auto',
        hflip: false,
        speed_ratio: 1.03,
        crop_percent: 2.0,
      });
    } else if (presetId === 'clean_mute_all') {
      onChange({
        ...options,
        preset_id: 'clean_mute_all',
        subtitle_bottom_crop: 7.0,
        enable_vocal_mute: true,
        preserve_bgm: false,
        vocal_mute_strategy: 'mute_all',
        wm_method: 'auto',
        hflip: false,
        speed_ratio: 1.03,
        crop_percent: 2.0,
      });
    }
  };

  const handleChannelSelect = (channelId) => {
    if (!channelId || channelId === 'none') {
      onChange({
        ...options,
        channel_id: null,
      });
      return;
    }
    const selectedChan = channels.find((c) => (c.channel_id || c.id) === channelId);
    const chanTags = selectedChan?.tags || [];
    const tagString = chanTags.map((t) => (t.startsWith('#') ? t : `#${t}`)).join(' ');

    let newCaption = options.post_caption || '';
    if (tagString && !newCaption.includes(tagString)) {
      newCaption = newCaption ? `${newCaption} ${tagString}` : tagString;
    }

    onChange({
      ...options,
      channel_id: channelId,
      post_tags: chanTags,
      post_caption: newCaption,
      publish_status: options.publish_status || 'READY',
    });
  };

  const handleAppendTag = (tag) => {
    const formatted = tag.startsWith('#') ? tag : `#${tag}`;
    const currentCaption = options.post_caption || '';
    if (!currentCaption.includes(formatted)) {
      handleChange('post_caption', currentCaption ? `${currentCaption} ${formatted}` : formatted);
    }
  };

  const activeChannel = channels.find((c) => (c.channel_id || c.id) === options.channel_id);

  const hasVertical = (options.target_platforms || []).includes('tiktok');
  const hasHorizontal = (options.target_platforms || []).includes('youtube');

  return (
    <div className="clean-panel rounded-3xl p-6 sm:p-7 shadow-xs space-y-6 max-w-7xl mx-auto">
      <h3 className="text-base font-extrabold text-slate-900 flex items-center gap-2 border-b border-slate-100 pb-3">
        <Wand2 className="w-5 h-5 text-blue-600" />
        Tùy chỉnh Reup
      </h3>

      <div className="rounded-2xl border border-blue-200 bg-gradient-to-r from-blue-50 via-white to-emerald-50 p-4 space-y-3">
        <div>
          <h4 className="text-sm font-black text-slate-900">Chọn âm thanh sau khi làm sạch</h4>
          <p className="text-[11px] text-slate-600 mt-0.5">
            Cả ba chế độ đều xóa chữ/logo trước. Vietsub và lồng tiếng được chọn độc lập ở mục Âm thanh & Dịch.
          </p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
          {[
            {
              id: 'clean_keep_bgm',
              icon: Music,
              label: 'Làm sạch + Giữ âm thanh gốc',
              desc: 'Giữ nguyên thoại, nhạc, hiệu ứng và âm lượng gốc.',
            },
            {
              id: 'clean_duck_vocals',
              icon: Volume2,
              label: 'Giảm lời Trung + Giữ âm nền',
              desc: 'Tách giọng nói, giữ nhạc và hiệu ứng; lời Trung chỉ còn nghe nhỏ.',
            },
            {
              id: 'clean_mute_all',
              icon: VolumeX,
              label: 'Tắt toàn bộ âm thanh gốc',
              desc: 'Tắt cả lời, nhạc và hiệu ứng; chỉ còn lồng tiếng AI.',
            },
          ].map((mode) => {
            const active = options.preset_id === mode.id;
            const Icon = mode.icon;
            return (
              <button
                key={mode.id}
                type="button"
                onClick={() => handlePresetSelect(mode.id)}
                className={`flex items-start gap-3 text-left rounded-2xl border px-4 py-3 transition-all ${
                  active
                    ? 'bg-blue-600 border-blue-700 text-white shadow-sm'
                    : 'bg-white border-slate-200 hover:border-blue-300'
                }`}
              >
                <Icon className={`w-5 h-5 mt-0.5 shrink-0 ${active ? 'text-white' : 'text-blue-600'}`} />
                <span>
                  <span className="text-xs font-extrabold block">{mode.label}</span>
                  <span className={`text-[10px] block mt-0.5 ${active ? 'text-blue-100' : 'text-slate-500'}`}>
                    {mode.desc}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
        {options.preset_id === 'clean_duck_vocals' && (
          <label className="block rounded-xl border border-blue-100 bg-white/80 px-3 py-2 text-[11px] font-bold text-slate-700">
            <span className="flex items-center justify-between gap-3">
              <span>Âm lượng lời Trung còn lại</span>
              <span className="font-mono text-blue-700">
                {Math.round((options.original_vocal_volume ?? 0.10) * 100)}%
              </span>
            </span>
            <input
              type="range"
              min="0"
              max="0.30"
              step="0.01"
              value={options.original_vocal_volume ?? 0.10}
              onChange={(e) => handleChange('original_vocal_volume', Number(e.target.value))}
              className="mt-2 w-full accent-blue-600"
            />
            <span className="mt-1 block text-[10px] font-medium text-slate-500">
              Khuyên dùng 8-12%. Mức 0% sẽ bỏ hẳn lời gốc.
            </span>
          </label>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Cột 1: Hình ảnh & FX */}
        <div className="space-y-4 bg-slate-50/30 p-4 rounded-2xl border border-slate-100">
          <h4 className="text-xs font-black text-slate-400 uppercase tracking-wider flex items-center gap-1.5 pb-2 border-b border-slate-100">
            <Tv className="w-4 h-4 text-blue-600" />
            Hình ảnh & FX
          </h4>

          <div className="p-4 bg-emerald-50/70 rounded-2xl border border-emerald-200 space-y-2">
            <div className="flex items-center justify-between">
              <div>
                <label className="text-xs font-extrabold text-slate-800 block">Tự xoá chữ & logo</label>
                <span className="text-[11px] text-slate-500">
                  Luôn chạy trước Vietsub và lồng tiếng.
                </span>
              </div>
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-600 px-2.5 py-1 text-[10px] font-black text-white">
                <CheckCircle2 className="w-3 h-3" /> Luôn bật
              </span>
            </div>
            <button
              type="button"
              onClick={() => setShowWmAdvanced((v) => !v)}
              className="text-[10px] font-bold text-slate-500 hover:text-slate-800 inline-flex items-center gap-1"
            >
              <ChevronDown className={`w-3 h-3 transition ${showWmAdvanced ? 'rotate-180' : ''}`} />
              Nâng cao (chọn tay thuật toán)
            </button>
            {showWmAdvanced && (
              <div className="space-y-2.5 pt-1">
                <div className="grid grid-cols-2 gap-2 text-xs">
                  {[
                    { id: 'auto', label: 'Tự động nhanh (Telea)', desc: 'Mặc định; OCR thưa, xử lý nhanh hơn nhiều' },
                    { id: 'all', label: 'AI kỹ (LaMa + Telea)', desc: 'Chậm hơn; chỉ dùng cho vùng chữ rất khó' },
                    { id: 'crop', label: 'Chỉ cắt đáy', desc: 'Khi phụ đề dính cứng dưới chân' },
                  ].map((item) => {
                    const isSelected =
                      options.wm_method === item.id ||
                      (options.wm_method === 'opencv_telea' && item.id === 'auto');
                    return (
                      <label
                        key={item.id}
                        className={`flex flex-col p-3 rounded-2xl border cursor-pointer transition-all duration-150 ${
                          isSelected
                            ? 'bg-white border-emerald-400 text-slate-900 font-bold shadow-xs'
                            : 'bg-white/70 border-slate-200 text-slate-600 hover:border-slate-300'
                        }`}
                      >
                        <div className="flex items-center space-x-2">
                          <input
                            type="radio"
                            name="wm_method"
                            value={item.id}
                            checked={isSelected}
                            onChange={() => onChange({
                              ...options,
                              wm_method: item.id,
                            })}
                            className="text-emerald-600 focus:ring-emerald-500"
                          />
                          <span className="text-xs font-bold text-slate-800">{item.label}</span>
                        </div>
                        <span className="text-[10px] text-slate-500 font-normal pl-5 mt-0.5">{item.desc}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          <div className="space-y-3">
            {/* Horizontal Flip */}
            <div className="flex items-center justify-between p-3.5 bg-slate-50 rounded-2xl border border-slate-200/80">
              <div className="flex items-center space-x-2.5">
                <FlipHorizontal className="w-4 h-4 text-blue-600" />
                <label className="text-xs font-bold text-slate-700 cursor-pointer" htmlFor="hflip-toggle">
                  Lật Ngang Video (HFlip)
                </label>
              </div>
              <input
                id="hflip-toggle"
                type="checkbox"
                checked={options.hflip}
                onChange={(e) => handleChange('hflip', e.target.checked)}
                className="w-4 h-4 rounded text-blue-600 focus:ring-blue-500 border-slate-300 cursor-pointer"
              />
            </div>

            {/* Speed Ratio Slider */}
            <div className="p-3.5 bg-slate-50 rounded-2xl border border-slate-200/80 space-y-2">
              <div className="flex justify-between text-xs">
                <span className="font-bold text-slate-700">Tốc Độ Video (Speed Factor)</span>
                <span className="font-mono text-blue-600 font-bold">{options.speed_ratio}x</span>
              </div>
              <input
                type="range"
                min="0.80"
                max="1.50"
                step="0.01"
                value={options.speed_ratio}
                onChange={(e) => handleChange('speed_ratio', parseFloat(e.target.value))}
                className="w-full accent-blue-600 bg-slate-200 rounded-lg cursor-pointer"
              />
            </div>

            {/* Edge Crop Slider */}
            <div className="p-3.5 bg-slate-50 rounded-2xl border border-slate-200/80 space-y-2">
              <div className="flex justify-between text-xs">
                <span className="font-bold text-slate-700">Cắt Mép Khung Hình (Edge Crop %)</span>
                <span className="font-mono text-blue-600 font-bold">{options.crop_percent}%</span>
              </div>
              <input
                type="range"
                min="0.0"
                max="5.0"
                step="0.1"
                value={options.crop_percent}
                onChange={(e) => handleChange('crop_percent', parseFloat(e.target.value))}
                className="w-full accent-blue-600 bg-slate-200 rounded-lg cursor-pointer"
              />
            </div>

            {/* Bottom Subtitle Crop Slider */}
            <div className="p-3.5 bg-amber-50/70 rounded-2xl border border-amber-200/80 space-y-2">
              <div className="flex justify-between text-xs">
                <span className="font-bold text-amber-950">Cắt Mép Đáy Bỏ Phụ Đề Cũ (Bottom Crop)</span>
                <span className="font-mono text-amber-700 font-bold">{options.subtitle_bottom_crop || 0}%</span>
              </div>
              <input
                type="range"
                min="0.0"
                max="12.0"
                step="0.5"
                value={options.subtitle_bottom_crop || 0}
                onChange={(e) => handleChange('subtitle_bottom_crop', parseFloat(e.target.value))}
                className="w-full accent-amber-600 bg-amber-200/70 rounded-lg cursor-pointer"
              />
              <span className="text-[10px] text-amber-800 font-medium block">
                Cắt mép dưới (6-8%) để loại bỏ hoàn toàn dải chữ tiếng Trung cũ ở đáy.
              </span>
            </div>

            {/* Video Trimming (Cut Start/End Seconds) */}
            <div className="p-4 bg-indigo-50/70 rounded-2xl border border-indigo-200/80 space-y-3">
              <div className="flex items-center space-x-2 text-xs font-bold text-indigo-900 uppercase tracking-wider">
                <Scissors className="w-4 h-4 text-indigo-600" />
                <span>Cắt Đoạn Video (Trim Start / End)</span>
              </div>

              <div className="grid grid-cols-2 gap-3 text-xs">
                <div>
                  <span className="text-[11px] font-semibold text-slate-600 block mb-1">
                    Cắt đầu video (giây)
                  </span>
                  <div className="flex items-center space-x-1.5">
                    <input
                      type="number"
                      min="0"
                      max="120"
                      step="0.5"
                      value={options.trim_start_sec ?? 0}
                      onChange={(e) => handleChange('trim_start_sec', Math.max(0, parseFloat(e.target.value) || 0))}
                      className="w-full px-2.5 py-1.5 text-xs font-bold text-indigo-700 bg-white rounded-xl border border-indigo-200 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                      placeholder="0.0"
                    />
                    <span className="text-xs font-bold text-slate-400">s</span>
                  </div>
                </div>

                <div>
                  <span className="text-[11px] font-semibold text-slate-600 block mb-1">
                    Cắt đuôi video (giây)
                  </span>
                  <div className="flex items-center space-x-1.5">
                    <input
                      type="number"
                      min="0"
                      max="120"
                      step="0.5"
                      value={options.trim_end_sec ?? 0}
                      onChange={(e) => handleChange('trim_end_sec', Math.max(0, parseFloat(e.target.value) || 0))}
                      className="w-full px-2.5 py-1.5 text-xs font-bold text-indigo-700 bg-white rounded-xl border border-indigo-200 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                      placeholder="0.0"
                    />
                    <span className="text-xs font-bold text-slate-400">s</span>
                  </div>
                </div>
              </div>
              <span className="text-[10px] text-indigo-700 block">
                💡 Nhập số giây muốn cắt bỏ (ví dụ: cắt 3s đầu video intro hoặc 2s outro).
              </span>
            </div>

            {/* Color Sliders */}
            <div className="p-4 bg-slate-50 rounded-2xl border border-slate-200/80 space-y-3">
              <div className="flex items-center space-x-2 text-xs font-bold text-slate-700 uppercase tracking-wider">
                <Palette className="w-4 h-4 text-purple-600" />
                <span>Tinh Chỉnh Màu Sắc (Color Filters)</span>
              </div>

              <div className="grid grid-cols-3 gap-3 text-xs">
                <div>
                  <span className="text-[10px] font-semibold text-slate-500 block mb-1">Độ Sáng</span>
                  <input
                    type="range"
                    min="-0.1"
                    max="0.1"
                    step="0.01"
                    value={options.brightness}
                    onChange={(e) => handleChange('brightness', parseFloat(e.target.value))}
                    className="w-full accent-blue-600 bg-slate-200 rounded-lg cursor-pointer"
                  />
                  <span className="text-[10px] font-mono text-blue-600 font-bold block text-right mt-0.5">
                    {options.brightness}
                  </span>
                </div>

                <div>
                  <span className="text-[10px] font-semibold text-slate-500 block mb-1">Độ Tương Phản</span>
                  <input
                    type="range"
                    min="0.8"
                    max="1.3"
                    step="0.01"
                    value={options.contrast}
                    onChange={(e) => handleChange('contrast', parseFloat(e.target.value))}
                    className="w-full accent-blue-600 bg-slate-200 rounded-lg cursor-pointer"
                  />
                  <span className="text-[10px] font-mono text-blue-600 font-bold block text-right mt-0.5">
                    {options.contrast}
                  </span>
                </div>

                <div>
                  <span className="text-[10px] font-semibold text-slate-500 block mb-1">Độ Bão Hòa</span>
                  <input
                    type="range"
                    min="0.8"
                    max="1.4"
                    step="0.01"
                    value={options.saturation}
                    onChange={(e) => handleChange('saturation', parseFloat(e.target.value))}
                    className="w-full accent-blue-600 bg-slate-200 rounded-lg cursor-pointer"
                  />
                  <span className="text-[10px] font-mono text-blue-600 font-bold block text-right mt-0.5">
                    {options.saturation}
                  </span>
                </div>
              </div>
            </div>

            {/* MD5 Modifier Toggle */}
            <div className="flex items-center justify-between p-3.5 bg-slate-50 rounded-2xl border border-slate-200/80">
              <div className="flex items-center space-x-2.5">
                <Hash className="w-4 h-4 text-emerald-600 shrink-0" />
                <label className="text-xs font-bold text-slate-700 cursor-pointer" htmlFor="md5-toggle">
                  Đổi Mã Hash MD5 File Video
                </label>
              </div>
              <input
                id="md5-toggle"
                type="checkbox"
                checked={options.modify_md5}
                onChange={(e) => handleChange('modify_md5', e.target.checked)}
                className="w-4 h-4 rounded text-blue-600 focus:ring-blue-500 border-slate-300 shrink-0 cursor-pointer"
              />
            </div>
          </div>
        </div>

        {/* Cột 2: Âm thanh & Dịch */}
        <div className="space-y-4 bg-slate-50/30 p-4 rounded-2xl border border-slate-100">
          <h4 className="text-xs font-black text-slate-400 uppercase tracking-wider flex items-center gap-1.5 pb-2 border-b border-slate-100">
            <Mic className="w-4 h-4 text-purple-600" />
            Âm thanh & Dịch
          </h4>

          <div className="space-y-3">
            {/* Audio Pitch Shift */}
            <div className="flex items-center justify-between p-3.5 bg-slate-50 rounded-2xl border border-slate-200/80">
              <div className="flex items-center space-x-2.5">
                <Volume2 className="w-4 h-4 text-indigo-600" />
                <label className="text-xs font-bold text-slate-700 cursor-pointer" htmlFor="pitch-toggle">
                  Đổi Tone Giọng Âm Thanh (+0.5 semitones)
                </label>
              </div>
              <input
                id="pitch-toggle"
                type="checkbox"
                checked={options.pitch_shift}
                onChange={(e) => handleChange('pitch_shift', e.target.checked)}
                className="w-4 h-4 rounded text-blue-600 focus:ring-blue-500 border-slate-300 cursor-pointer"
              />
            </div>

            {/* BGM Selector */}
            <div className="p-3.5 bg-indigo-50/70 rounded-2xl border border-indigo-200 space-y-2">
              <div className="flex items-center gap-2">
                <Music className="w-4 h-4 text-indigo-600" />
                <span className="text-xs font-extrabold text-slate-800">Nhạc nền lấy từ video khác</span>
              </div>
              <select
                value={options.bgm_id || ''}
                onChange={(e) => {
                  const id = e.target.value;
                  const hit = bgmList.find((x) => x.id === id);
                  onChange({
                    ...options,
                    bgm_id: hit?.id || '',
                    bgm_path: hit?.path || hit?.id || '',
                    bgm_title: hit?.title || '',
                  });
                }}
                className="w-full bg-white border border-slate-300 rounded-xl px-3 py-2 text-xs font-bold"
              >
                <option value="">Không — giữ nhạc clip gốc</option>
                {bgmList.map((t) => (
                  <option key={t.id} value={t.id}>{t.title}</option>
                ))}
              </select>
              {(options.bgm_path || options.bgm_id) && (
                <label className="flex items-center gap-2 text-[11px] font-bold text-slate-600">
                  Âm lượng
                  <input
                    type="range"
                    min="0.2"
                    max="1.4"
                    step="0.05"
                    value={options.bgm_volume ?? 0.85}
                    onChange={(e) => handleChange('bgm_volume', Number(e.target.value))}
                    className="flex-1"
                  />
                  <span>{Math.round((options.bgm_volume ?? 0.85) * 100)}%</span>
                </label>
              )}
              <p className="text-[10px] text-slate-500">Chọn nhạc nền từ thư viện.</p>
            </div>

            {/* TTS Dubbing Control - Unified Vietnamese Voice Hub */}
            <div className="p-4 bg-gradient-to-br from-purple-50/80 via-indigo-50/50 to-slate-50 border border-purple-200/90 rounded-2xl space-y-3.5 shadow-2xs">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-2.5">
                  <Mic className="w-4 h-4 text-purple-600 shrink-0" />
                  <div>
                    <label className="text-xs font-extrabold text-purple-950 cursor-pointer block" htmlFor="tts-toggle">
                      Tự dịch + lồng tiếng khớp video gốc
                    </label>
                    <span className="text-[10px] text-purple-700 font-medium block">
                      Dịch và lồng tiếng khớp timeline.
                    </span>
                  </div>
                </div>
                <input
                  id="tts-toggle"
                  type="checkbox"
                  checked={!!options.enable_tts}
                  onChange={(e) => {
                    const enabled = e.target.checked;
                    if (enabled && options.preset_id === 'clean_keep_bgm') {
                      onChange({
                        ...options,
                        enable_tts: true,
                        preset_id: 'clean_duck_vocals',
                        enable_vocal_mute: true,
                        preserve_bgm: true,
                        vocal_mute_strategy: 'demucs_duck',
                        original_vocal_volume: options.original_vocal_volume ?? 0.10,
                      });
                    } else {
                      handleChange('enable_tts', enabled);
                    }
                  }}
                  className="w-4 h-4 rounded text-purple-600 focus:ring-purple-500 border-purple-300 shrink-0 cursor-pointer"
                />
              </div>

              <div>
                <div className="mb-1.5">
                  <label className="text-xs font-extrabold text-purple-950 block">
                    Hiển thị Vietsub
                  </label>
                  <span className="text-[10px] text-purple-700 font-medium block">
                    Chọn tắt hoàn toàn, phụ đề CC, hoặc in cố định lên hình.
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-1.5 rounded-xl border border-purple-100 bg-white/70 p-1.5">
                  {[
                    {
                      id: 'off',
                      title: 'Tắt Vietsub',
                      desc: 'Không tạo hoặc chèn phụ đề.',
                    },
                    {
                      id: 'soft',
                      title: 'CC bật / tắt',
                      desc: 'Job mới: bật CC khi xem trong Kho Video.',
                    },
                    {
                      id: 'hard',
                      title: 'In cố định',
                      desc: 'Luôn hiện trên hình, không thể tắt.',
                    },
                  ].map((mode) => {
                    const selectedMode = options.burn_subtitles === false
                      ? 'off'
                      : (options.subtitle_mode || 'soft');
                    const active = selectedMode === mode.id;
                    return (
                      <button
                        key={mode.id}
                        type="button"
                        onClick={() => onChange({
                          ...options,
                          burn_subtitles: mode.id !== 'off',
                          subtitle_mode: mode.id,
                        })}
                        className={`rounded-lg border px-2.5 py-2 text-left transition ${
                          active
                            ? 'border-purple-500 bg-purple-600 text-white'
                            : 'border-purple-100 bg-white text-purple-950 hover:border-purple-300'
                        }`}
                      >
                        <span className="block text-[11px] font-extrabold">{mode.title}</span>
                        <span className={`mt-0.5 block text-[9px] leading-snug ${active ? 'text-purple-100' : 'text-purple-700'}`}>
                          {mode.desc}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div>
                <label className="block text-[11px] font-extrabold text-purple-950 mb-1.5">
                  Chế độ Vietsub
                </label>
                <div className="grid grid-cols-3 gap-1.5">
                  {[
                    {
                      id: 'dub',
                      icon: Captions,
                      title: 'Gốc',
                      desc: 'Dịch sát nghĩa gốc.',
                    },
                    {
                      id: 'narrator',
                      icon: BookOpen,
                      title: 'Kể chuyện',
                      desc: 'Tóm tắt ngôi thứ ba.',
                    },
                    {
                      id: 'funny',
                      icon: Smile,
                      title: 'Vui nhộn',
                      desc: 'Phong cách hài hước.',
                    },
                  ].map((mode) => {
                    const active = (options.vietsub_style || 'dub') === mode.id;
                    const Icon = mode.icon;
                    return (
                      <button
                        key={mode.id}
                        type="button"
                        onClick={() => {
                          const patch = { vietsub_style: mode.id };
                          if (mode.id !== 'dub') patch.enable_lipsync = false;
                          else patch.enable_lipsync = options.enable_lipsync !== false;
                          onChange({ ...options, ...patch });
                        }}
                        className={`text-left rounded-xl border px-2 py-2.5 transition-all ${
                          active
                            ? 'bg-purple-600 border-purple-700 text-white shadow-sm'
                            : 'bg-white border-purple-200 text-purple-950 hover:border-purple-400'
                        }`}
                      >
                        <Icon className={`w-3.5 h-3.5 mb-1 ${active ? 'text-white' : 'text-purple-600'}`} />
                        <div className="text-[11px] font-extrabold leading-tight">{mode.title}</div>
                        <div className={`text-[9px] leading-snug mt-0.5 ${active ? 'text-purple-100' : 'text-purple-700'}`}>
                          {mode.desc}
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              {options.enable_tts && (
                <div className="space-y-3 pt-2.5 border-t border-purple-200/70 animate-fadeIn">
                  {(options.vietsub_style || 'dub') === 'dub' && (
                    <div className="flex items-center justify-between">
                      <div>
                        <label className="text-xs font-extrabold text-purple-950 cursor-pointer block" htmlFor="lipsync-toggle">
                          Khớp khẩu hình (Lip-sync)
                        </label>
                        <span className="text-[10px] text-purple-700 font-medium block">
                          Đồng bộ khẩu hình miệng.
                        </span>
                      </div>
                      <input
                        id="lipsync-toggle"
                        type="checkbox"
                        checked={options.enable_lipsync !== false}
                        onChange={(e) => handleChange('enable_lipsync', e.target.checked)}
                        className="w-4 h-4 rounded text-purple-600 focus:ring-purple-500 border-purple-300 shrink-0 cursor-pointer"
                      />
                    </div>
                  )}
                  <div>
                    <label className="block text-[11px] font-bold text-purple-900 mb-1.5">Ngôn ngữ đích</label>
                    <select
                      value={options.target_lang || 'vi'}
                      onChange={(e) => {
                        const lang = e.target.value;
                        const voices = {
                          vi: 'vieneu:Trúc Ly',
                          en: 'en-US-AvaMultilingualNeural',
                          th: 'th-TH-PremwadeeNeural',
                          id: 'id-ID-GadisNeural',
                          ja: 'ja-JP-NanamiNeural',
                          ko: 'ko-KR-SunHiNeural',
                          pt: 'pt-BR-FranciscaNeural',
                        };
                        onChange({
                          ...options,
                          target_lang: lang,
                          tts_voice: voices[lang] || options.tts_voice,
                          tts_engine: resolveVoiceEngine(voices[lang]),
                        });
                      }}
                      className="w-full bg-white border border-purple-200 rounded-xl px-3 py-2.5 text-xs text-slate-900 font-bold outline-none focus:ring-2 focus:ring-purple-400 cursor-pointer shadow-2xs mb-2"
                    >
                      <option value="vi">Tiếng Việt</option>
                      <option value="en">English</option>
                      <option value="th">ไทย Thai</option>
                      <option value="id">Bahasa Indonesia</option>
                      <option value="ja">日本語 Japanese</option>
                      <option value="ko">한국어 Korean</option>
                      <option value="pt">Português</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-[11px] font-bold text-purple-900 mb-1.5 flex items-center justify-between">
                      <span>Chọn giọng đọc cho ngôn ngữ đích:</span>
                    </label>
                    <select
                      value={options.tts_voice || 'vieneu:Trúc Ly'}
                      onChange={(e) => {
                        const voice = e.target.value;
                        onChange({
                          ...options,
                          tts_voice: voice,
                          tts_engine: resolveVoiceEngine(voice),
                        });
                      }}
                      className="w-full bg-white border border-purple-200 rounded-xl px-3 py-2.5 text-xs text-slate-900 font-bold outline-none focus:ring-2 focus:ring-purple-400 cursor-pointer shadow-2xs"
                    >
                      {(VOICE_OPTIONS[options.target_lang || 'vi'] || VOICE_OPTIONS.en).map((item) => (
                        <option key={item.value} value={item.value}>{item.label}</option>
                      ))}
                    </select>
                    <button
                      type="button"
                      onClick={handleVoicePreview}
                      className="mt-2 w-full flex items-center justify-center gap-2 rounded-xl border border-purple-300 bg-white px-3 py-2 text-[11px] font-extrabold text-purple-800 hover:bg-purple-50 disabled:cursor-wait disabled:opacity-70"
                    >
                      {previewingVoice && (!voicePreviewAudio || voicePreviewAudio.paused) ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : previewingVoice ? (
                        <Square className="w-3.5 h-3.5 fill-current" />
                      ) : (
                        <Play className="w-3.5 h-3.5 fill-current" />
                      )}
                      {previewingVoice && (!voicePreviewAudio || voicePreviewAudio.paused)
                        ? 'Đang tạo bản nghe thử...'
                        : previewingVoice
                          ? 'Dừng nghe thử'
                          : 'Nghe thử giọng đã chọn'}
                    </button>
                    {voicePreviewError && (
                      <p className="mt-1.5 text-[10px] font-semibold text-rose-600">{voicePreviewError}</p>
                    )}
                    {(options.tts_engine || 'vieneu') === 'vieneu' && (
                      <p className="mt-1.5 text-[10px] text-purple-600">
                        VieNeu-TTS v3 Turbo chạy local; có giọng Bắc, Nam, Trung và nhiều phong cách thật.
                      </p>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Cột 3: Đăng bài & Kênh */}
        <div className="space-y-4 bg-slate-50/30 p-4 rounded-2xl border border-slate-100">
          <h4 className="text-xs font-black text-slate-400 uppercase tracking-wider flex items-center gap-1.5 pb-2 border-b border-slate-100">
            <Share2 className="w-4 h-4 text-indigo-600" />
            Đăng bài & Kênh
          </h4>

          {/* Channel Assignment & Distribution Section */}
          <div className="p-4 bg-gradient-to-br from-blue-50/70 via-indigo-50/40 to-slate-50 rounded-2xl border border-blue-100/90 space-y-3.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Share2 className="w-4 h-4 text-blue-600" />
                <span className="text-xs font-extrabold text-slate-900">
                  Xuất đa nền tảng
                </span>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => {
                  const cur = Array.isArray(options.target_platforms) ? [...options.target_platforms] : [];
                  const verticalPlats = ['tiktok', 'youtube_shorts', 'facebook'];
                  let next;
                  if (hasVertical) {
                    next = cur.filter((x) => !verticalPlats.includes(x));
                  } else {
                    next = [...new Set([...cur, ...verticalPlats])];
                  }
                  handleChange('target_platforms', next);
                }}
                className={`text-left rounded-xl border px-3 py-2.5 transition-all cursor-pointer ${
                  hasVertical
                    ? 'bg-blue-600 border-blue-700 text-white shadow-sm'
                    : 'bg-white border-slate-200 text-slate-700 hover:border-blue-300'
                }`}
              >
                <div className="text-[11px] font-extrabold leading-tight">Dọc (9:16)</div>
                <div className={`text-[9px] font-bold mt-0.5 ${hasVertical ? 'text-blue-100' : 'text-slate-400'}`}>
                  TikTok, Shorts, Reels
                </div>
              </button>

              <button
                type="button"
                onClick={() => {
                  const cur = Array.isArray(options.target_platforms) ? [...options.target_platforms] : [];
                  const horizontalPlats = ['youtube'];
                  let next;
                  if (hasHorizontal) {
                    next = cur.filter((x) => !horizontalPlats.includes(x));
                  } else {
                    next = [...new Set([...cur, ...horizontalPlats])];
                  }
                  handleChange('target_platforms', next);
                }}
                className={`text-left rounded-xl border px-3 py-2.5 transition-all cursor-pointer ${
                  hasHorizontal
                    ? 'bg-blue-600 border-blue-700 text-white shadow-sm'
                    : 'bg-white border-slate-200 text-slate-700 hover:border-blue-300'
                }`}
              >
                <div className="text-[11px] font-extrabold leading-tight">Ngang (16:9)</div>
                <div className={`text-[9px] font-bold mt-0.5 ${hasHorizontal ? 'text-blue-100' : 'text-slate-400'}`}>
                  YouTube
                </div>
              </button>
            </div>

            {/* Channel Selector */}
            <div className="space-y-1.5">
              <label className="block text-[11px] font-bold text-slate-700">Chọn Kênh Đích:</label>
              <select
                value={options.channel_id || 'none'}
                onChange={(e) => handleChannelSelect(e.target.value)}
                onFocus={loadChannels}
                className="w-full text-xs font-semibold bg-white border border-slate-200 rounded-xl px-3 py-2.5 focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 shadow-2xs"
              >
                <option value="none">📦 Không gán kênh (chỉ lưu kho)</option>
                {channels.map((chan) => (
                  <option key={chan.channel_id || chan.id} value={chan.channel_id || chan.id}>
                    📺 [{chan.platform?.toUpperCase() || 'KHÁC'}] {chan.name} {chan.tags?.length ? `(${chan.tags.join(', ')})` : ''}
                  </option>
                ))}
              </select>
            </div>

            {/* If Channel is Selected: Metadata & Caption Form */}
            {options.channel_id && options.channel_id !== 'none' && (
              <div className="space-y-3 pt-2 border-t border-blue-100/80 animate-fadeIn">
                {/* Post Title */}
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 mb-1 flex items-center gap-1">
                    <FileText className="w-3.5 h-3.5 text-blue-600" />
                    Tiêu đề:
                  </label>
                  <input
                    type="text"
                    value={options.post_title || ''}
                    onChange={(e) => handleChange('post_title', e.target.value)}
                    placeholder="Nhập tiêu đề..."
                    className="w-full text-xs bg-white border border-slate-200 rounded-xl px-3 py-2 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 shadow-2xs"
                  />
                </div>

                {/* Post Caption & Hashtags */}
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 mb-1 flex items-center justify-between">
                    <span className="flex items-center gap-1">
                      <Tag className="w-3.5 h-3.5 text-indigo-600" />
                      Mô tả & Hashtags:
                    </span>
                  </label>
                  <textarea
                    rows={2}
                    value={options.post_caption || ''}
                    onChange={(e) => handleChange('post_caption', e.target.value)}
                    placeholder="Nhập mô tả..."
                    className="w-full text-xs bg-white border border-slate-200 rounded-xl px-3 py-2 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 shadow-2xs resize-none"
                  />
                  {/* Quick Tag Pills from Channel */}
                  {activeChannel?.tags && activeChannel.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-1.5">
                      {activeChannel.tags.map((t, idx) => (
                        <button
                          key={idx}
                          type="button"
                          onClick={() => handleAppendTag(t)}
                          className="text-[10px] font-bold bg-white text-indigo-700 hover:bg-indigo-50 border border-indigo-200/80 px-2 py-0.5 rounded-lg transition shadow-2xs flex items-center gap-1 cursor-pointer"
                        >
                          <Sparkles className="w-2.5 h-2.5 text-amber-500" />
                          {t.startsWith('#') ? t : `#${t}`}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {/* Publish Status Selector */}
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 mb-1.5">
                    Trạng thái:
                  </label>
                  <div className="grid grid-cols-3 gap-2">
                    {[
                      { id: 'READY', label: 'Sẵn sàng', color: 'border-emerald-300 text-emerald-700 bg-emerald-50/50' },
                      { id: 'DRAFT', label: 'Bản nháp', color: 'border-amber-300 text-amber-700 bg-amber-50/50' },
                      { id: 'PUBLISHED', label: 'Đã đăng', color: 'border-blue-300 text-blue-700 bg-blue-50/50' },
                    ].map((s) => {
                      const isCur = (options.publish_status || 'READY') === s.id;
                      return (
                        <button
                          key={s.id}
                          type="button"
                          onClick={() => handleChange('publish_status', s.id)}
                          className={`text-xs py-1.5 px-2 rounded-xl font-bold border transition cursor-pointer flex items-center justify-center gap-1 ${
                            isCur
                              ? `${s.color} border-2 shadow-xs font-extrabold`
                              : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50'
                          }`}
                        >
                          {isCur && <CheckCircle2 className="w-3 h-3 text-current" />}
                          {s.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Submit Button */}
      <button
        onClick={onSubmit}
        disabled={submitting}
        className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-extrabold text-sm py-4 rounded-2xl transition shadow-md shadow-blue-500/20 flex items-center justify-center gap-2 cursor-pointer"
      >
        <Zap className="w-4 h-4 fill-current text-amber-300" />
        {submitting ? 'Đang Gửi Job Xử Lý...' : 'Bắt Đầu Xử Lý Reup Video'}
      </button>
    </div>
  );
}
