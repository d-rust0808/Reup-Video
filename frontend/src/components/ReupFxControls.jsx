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
  Music,
  Scissors,
  Play,
  Square,
  ImagePlus,
  X,
} from 'lucide-react';

import { fetchChannels, fetchChannelGroups, fetchBgmLibrary, previewVoice, uploadStudioOverlay, getMediaUrl } from '../services/api';
import {
  HORIZONTAL_TOGGLE,
  VERTICAL_TOGGLE,
  platformsHaveHorizontal,
  platformsHaveVertical,
  previewSubtitleY,
  clampCoverHeight,
  clampSubtitleBoxW,
  clampSubtitleBoxH,
} from '../lib/previewCanvas';

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
  const [groups, setGroups] = useState([]);
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
    fetchChannelGroups()
      .then((d) => setGroups(d.groups || []))
      .catch(() => setGroups([]));
    fetchBgmLibrary()
      .then((d) => setBgmList(d.items || []))
      .catch(() => setBgmList([]));

    const refreshChannels = () => {
      loadChannels();
      fetchChannelGroups()
        .then((d) => setGroups(d.groups || []))
        .catch(() => setGroups([]));
    };
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
        subtitle_bottom_crop: 22.0,
        enable_vocal_mute: false,
        preserve_bgm: true,
        vocal_mute_strategy: 'auto',
        hflip: false,
        crop_percent: 2.0,
      });
    } else if (presetId === 'clean_duck_vocals') {
      onChange({
        ...options,
        preset_id: 'clean_duck_vocals',
        subtitle_bottom_crop: 22.0,
        enable_vocal_mute: true,
        preserve_bgm: true,
        vocal_mute_strategy: 'auto',
        original_vocal_volume: options.original_vocal_volume ?? 0.10,
        hflip: false,
        crop_percent: 2.0,
      });
    } else if (presetId === 'clean_mute_all') {
      onChange({
        ...options,
        preset_id: 'clean_mute_all',
        subtitle_bottom_crop: 22.0,
        enable_vocal_mute: true,
        preserve_bgm: false,
        vocal_mute_strategy: 'mute_all',
        hflip: false,
        crop_percent: 2.0,
      });
    }
  };

  const selectedIds = (() => {
    const ids = Array.isArray(options.channel_ids) ? options.channel_ids.filter(Boolean) : [];
    if (options.channel_id && options.channel_id !== 'none' && !ids.includes(options.channel_id)) {
      ids.unshift(options.channel_id);
    }
    return ids;
  })();

  const applyChannelIds = (nextIds, extra = {}) => {
    onChange({
      ...options,
      channel_ids: nextIds,
      channel_id: nextIds[0] || null,
      publish_status: options.publish_status || 'READY',
      ...extra,
    });
  };

  const handleChannelSelect = (channelId) => {
    if (!channelId || channelId === 'none') {
      applyChannelIds([]);
      return;
    }
    const selectedChan = channels.find((c) => (c.channel_id || c.id) === channelId);
    const chanTags = selectedChan?.tags || [];
    const tagString = chanTags.map((t) => (t.startsWith('#') ? t : `#${t}`)).join(' ');
    let newCaption = options.post_caption || '';
    if (tagString && !newCaption.includes(tagString)) {
      newCaption = newCaption ? `${newCaption} ${tagString}` : tagString;
    }
    applyChannelIds([channelId], { post_tags: chanTags, post_caption: newCaption });
  };

  const toggleChannelId = (channelId) => {
    const on = selectedIds.includes(channelId);
    const next = on ? selectedIds.filter((id) => id !== channelId) : [...selectedIds, channelId];
    const nextGroups = (options.group_ids || []).filter((gid) => {
      const members = (groups.find((g) => g.group_id === gid) || {}).channel_ids || [];
      return members.length > 0 && members.every((id) => next.includes(id));
    });
    applyChannelIds(next, { group_ids: nextGroups });
  };

  const handleAppendTag = (tag) => {
    const formatted = tag.startsWith('#') ? tag : `#${tag}`;
    const currentCaption = options.post_caption || '';
    if (!currentCaption.includes(formatted)) {
      handleChange('post_caption', currentCaption ? `${currentCaption} ${formatted}` : formatted);
    }
  };

  const facebookChannels = channels
    .filter((c) => String(c.platform || '').toLowerCase() === 'facebook')
    .slice()
    .sort((a, b) => {
      const aid = a.channel_id || a.id;
      const bid = b.channel_id || b.id;
      const aOn = selectedIds.includes(aid) ? 0 : 1;
      const bOn = selectedIds.includes(bid) ? 0 : 1;
      if (aOn !== bOn) return aOn - bOn;
      return String(a.name || '').localeCompare(String(b.name || ''), 'vi');
    });
  const tiktokChannels = channels
    .filter((c) => String(c.platform || '').toLowerCase() === 'tiktok')
    .slice()
    .sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'vi'));
  const otherChannels = channels.filter((c) => {
    const platform = String(c.platform || '').toLowerCase();
    return platform !== 'facebook' && platform !== 'tiktok';
  });
  const activeChannel = channels.find((c) => selectedIds.includes(c.channel_id || c.id));

  const hasVertical = platformsHaveVertical(options.target_platforms);
  const hasHorizontal = platformsHaveHorizontal(options.target_platforms);

  return (
    <div className="clean-panel rounded-3xl p-4 sm:p-6 shadow-xs space-y-6 w-full min-w-0">
      <h3 className="text-base font-extrabold text-slate-900 flex items-center gap-2 border-b border-slate-100 pb-3">
        <Wand2 className="w-5 h-5 text-blue-600" />
        Tùy chỉnh Reup
      </h3>

      <div className="rounded-2xl border border-blue-200 bg-gradient-to-r from-blue-50 via-white to-emerald-50 p-4 space-y-3">
        <div>
          <h4 className="text-sm font-black text-slate-900">Chọn âm thanh sau khi làm sạch</h4>
          <p className="text-[11px] text-slate-600 mt-0.5">
            Âm thanh gốc chọn ở đây. Cách xoá chữ/logo nằm ở mục Hình ảnh — có thể tắt hoặc chỉ cắt đáy để khỏi nhoè.
          </p>
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-2.5">
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
              desc: 'Giữ nhạc, tiếng hành động và động vật; chỉ hạ lời thoại gốc khi có giọng Việt.',
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
              Chỉ chỉnh audio gốc (lời Trung + nhạc). Giọng Việt lồng tiếng mix lớp riêng ở bước cuối, không dính slider này.
            </span>
          </label>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3 gap-4 lg:gap-6 min-w-0 items-start">
        {/* Cột 1: Hình ảnh & FX */}
        <div className="space-y-4 bg-slate-50/30 p-4 rounded-2xl border border-slate-100 min-w-0">
          <h4 className="text-xs font-black text-slate-400 uppercase tracking-wider flex items-center gap-1.5 pb-2 border-b border-slate-100">
            <Tv className="w-4 h-4 text-blue-600" />
            Hình ảnh & FX
          </h4>

          <div className="p-3.5 sm:p-4 bg-emerald-50/70 rounded-2xl border border-emerald-200 space-y-2">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <label className="text-xs font-extrabold text-slate-800 block">Xoá chữ & logo</label>
                <span className="text-[11px] text-slate-500 leading-snug block mt-0.5">
                  AI dễ nhoè nền. Cắt đáy hoặc tắt để giữ ảnh.
                </span>
              </div>
              {(['none', 'off', 'disabled'].includes(options.wm_method) ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-slate-600 px-2.5 py-1 text-[10px] font-black text-white shrink-0 self-start">
                  Đã tắt
                </span>
              ) : options.wm_method === 'crop' ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-amber-500 px-2.5 py-1 text-[10px] font-black text-white shrink-0 self-start">
                  Chỉ cắt đáy
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-600 px-2.5 py-1 text-[10px] font-black text-white shrink-0 self-start">
                  <CheckCircle2 className="w-3 h-3" /> AI
                </span>
              ))}
            </div>
            <div className="grid grid-cols-1 min-[480px]:grid-cols-2 gap-2 text-xs pt-1">
              {[
                { id: 'auto', label: 'Tự động (Telea)', desc: 'AI — có thể nhoè nền' },
                { id: 'all', label: 'AI kỹ (LaMa)', desc: 'Chậm, chữ rất khó' },
                { id: 'crop', label: 'Chỉ cắt đáy', desc: 'Cắt phụ đề cứng, không nhoè' },
                { id: 'none', label: 'Không xoá', desc: 'Giữ nguyên hình' },
              ].map((item) => {
                const isSelected =
                  options.wm_method === item.id ||
                  (options.wm_method === 'opencv_telea' && item.id === 'auto') ||
                  (['off', 'disabled'].includes(options.wm_method) && item.id === 'none');
                return (
                  <label
                    key={item.id}
                    className={`flex flex-col p-2.5 sm:p-3 rounded-2xl border cursor-pointer transition-all duration-150 min-w-0 ${
                      isSelected
                        ? 'bg-white border-emerald-400 text-slate-900 font-bold shadow-xs'
                        : 'bg-white/70 border-slate-200 text-slate-600 hover:border-slate-300'
                    }`}
                  >
                    <div className="flex items-start gap-2">
                      <input
                        type="radio"
                        name="wm_method"
                        value={item.id}
                        checked={isSelected}
                        onChange={() => onChange({
                          ...options,
                          wm_method: item.id,
                        })}
                        className="mt-0.5 shrink-0 text-emerald-600 focus:ring-emerald-500"
                      />
                      <span className="min-w-0">
                        <span className="block text-xs font-bold text-slate-800 leading-snug">{item.label}</span>
                        <span className="block text-[10px] text-slate-500 font-normal leading-snug mt-0.5">{item.desc}</span>
                      </span>
                    </div>
                  </label>
                );
              })}
            </div>
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
                <span className="font-mono text-blue-600 font-bold">{Number(options.speed_ratio || 1).toFixed(2)}x</span>
              </div>
              <input
                type="range"
                min="0.80"
                max="1.50"
                step="0.01"
                value={Number(options.speed_ratio || 1.03)}
                onChange={(e) => handleChange('speed_ratio', parseFloat(e.target.value))}
                className="w-full accent-blue-600 bg-slate-200 rounded-lg cursor-pointer"
              />
              <p className="text-[10px] text-slate-500 font-medium">
                Bấm phát trên preview để nghe đúng hệ số. File encode ngắn/dài theo tốc độ (1.30x ≈ ngắn 23%).
              </p>
            </div>

            <div className="p-3.5 bg-slate-50 rounded-2xl border border-slate-200/80 space-y-2">
              <div className="flex justify-between text-xs">
                <span className="font-bold text-slate-700">Kéo dài ô logo 9:16</span>
                <span className="font-mono text-blue-600 font-bold">
                  {Math.round((Number(options.canvas_fill) || 0) * 100)}%
                </span>
              </div>
              <input
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={Math.max(0, Math.min(1, Number(options.canvas_fill) || 0))}
                onChange={(e) => handleChange('canvas_fill', parseFloat(e.target.value))}
                className="w-full accent-blue-600 bg-slate-200 rounded-lg cursor-pointer"
              />
              <p className="text-[10px] text-slate-500 font-medium">
                0% = ảnh 16:9 như cũ. 100% = hiện đủ logo dưới video, khít theo tỉ lệ ảnh — không zoom, không đè, không dư đen.
              </p>
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

            {/* Cover source captions with a color plate or custom image (keeps 9:16) */}
            <div className="p-3.5 bg-slate-900/5 rounded-2xl border border-slate-200 space-y-2">
              <div>
                <span className="text-xs font-extrabold text-slate-800 block">Phủ chữ gốc</span>
                <span className="text-[10px] text-slate-500">
                  Phủ màu dính đáy hình. Chỉ kéo Vietsub để đè chữ gốc. Ảnh logo vẫn nằm dưới video 9:16.
                </span>
              </div>
              <div className="grid grid-cols-2 gap-1.5 min-w-0">
                {[
                  { id: 'off', label: 'Không phủ', swatch: 'transparent' },
                  { id: 'black_soft', label: 'Đen trong suốt', swatch: 'rgba(0,0,0,0.55)' },
                  { id: 'white_soft', label: 'Trắng trong suốt', swatch: 'rgba(255,255,255,0.7)' },
                  { id: 'black_solid', label: 'Đen đậm', swatch: '#111' },
                  { id: 'white_solid', label: 'Trắng đậm', swatch: '#f4f4f4' },
                ].map((item) => {
                  const cover = options.caption_cover === 'white_black' ? 'white_solid' : (options.caption_cover || 'off');
                  const selected = cover === item.id;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => handleChange('caption_cover', item.id)}
                      className={`flex items-center gap-2 rounded-xl border px-2 py-2 text-left transition min-w-0 ${
                        selected
                          ? 'border-slate-900 bg-white shadow-xs'
                          : 'border-slate-200 bg-white/70 hover:border-slate-300'
                      }`}
                    >
                      <span
                        className="w-5 h-5 rounded-md border border-slate-300 shrink-0"
                        style={{
                          background: item.swatch === 'transparent'
                            ? 'repeating-conic-gradient(#ddd 0% 25%, #fff 0% 50%) 50% / 8px 8px'
                            : item.swatch,
                          color: item.id.startsWith('white') ? '#111' : '#fff',
                          fontSize: 9,
                          fontWeight: 800,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                        }}
                      >
                        {item.id !== 'off' ? 'A' : ''}
                      </span>
                      <span className="text-[10px] font-bold text-slate-800 leading-snug min-w-0">{item.label}</span>
                    </button>
                  );
                })}
              </div>
              <div className={`flex items-center gap-1 rounded-xl border min-w-0 ${
                (options.caption_cover || 'off') === 'image'
                  ? 'border-slate-900 bg-white shadow-xs'
                  : 'border-slate-200 bg-white/70 hover:border-slate-300'
              }`}>
                <label className="flex flex-1 items-center gap-2 px-2.5 py-2 cursor-pointer min-w-0">
                  <span className="w-8 h-8 rounded-md border border-slate-300 shrink-0 overflow-hidden bg-slate-100 flex items-center justify-center">
                    {options.caption_cover_url ? (
                      <img src={getMediaUrl(options.caption_cover_url)} alt="" className="w-full h-full object-cover" />
                    ) : (
                      <ImagePlus className="w-4 h-4 text-slate-500" />
                    )}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-[10px] font-bold text-slate-800 leading-snug">Ảnh tuỳ chỉnh</span>
                    <span className="block text-[9px] text-slate-500 leading-snug">
                      {options.caption_cover_name || 'Chọn PNG/JPG logo dưới video'}
                    </span>
                  </span>
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    className="hidden"
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      e.target.value = '';
                      if (!file) return;
                      try {
                        const res = await uploadStudioOverlay(file, { kind: 'logo', x: 0, y: 0.78, w: 1 });
                        onChange({
                          ...options,
                          caption_cover: 'image',
                          caption_cover_image: res.image_path,
                          caption_cover_url: res.url,
                          caption_cover_name: res.filename || file.name,
                        });
                      } catch (err) {
                        console.error(err);
                      }
                    }}
                  />
                </label>
                {Boolean(options.caption_cover_url || options.caption_cover_image) && (
                  <button
                    type="button"
                    aria-label="Xoá ảnh logo"
                    title="Xoá ảnh logo"
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      onChange({
                        ...options,
                        caption_cover: options.caption_cover === 'image' ? 'off' : options.caption_cover,
                        caption_cover_image: '',
                        caption_cover_url: '',
                        caption_cover_name: '',
                      });
                    }}
                    className="shrink-0 mr-1.5 w-7 h-7 rounded-lg border border-slate-200 bg-white hover:bg-rose-50 hover:border-rose-200 hover:text-rose-600 text-slate-500 flex items-center justify-center"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>

            {/* Bottom Subtitle Crop / cover height */}
            <div className="p-3.5 bg-amber-50/70 rounded-2xl border border-amber-200/80 space-y-2">
              <div className="flex justify-between text-xs">
                <span className="font-bold text-amber-950">
                  {options.caption_cover === 'image'
                    ? 'Cắt mép đáy (bỏ chữ gốc)'
                    : options.caption_cover && options.caption_cover !== 'off'
                      ? 'Độ cao dải phủ đáy'
                      : 'Cắt mép đáy (bỏ chữ gốc)'}
                </span>
                <span className="font-mono text-amber-700 font-bold">
                  {options.caption_cover && options.caption_cover !== 'off' && options.caption_cover !== 'image'
                    && Number(options.subtitle_bottom_crop || 0) <= 0
                    ? 'Tắt'
                    : `${options.subtitle_bottom_crop || 0}%`}
                </span>
              </div>
              <input
                type="range"
                min="0"
                max="36"
                step="0.5"
                value={
                  options.caption_cover && options.caption_cover !== 'off' && options.caption_cover !== 'image'
                    ? clampCoverHeight((Number(options.subtitle_bottom_crop) || 0) / 100) * 100
                    : (options.subtitle_bottom_crop || 0)
                }
                onChange={(e) => handleChange('subtitle_bottom_crop', parseFloat(e.target.value))}
                className="w-full accent-amber-600 bg-amber-200/70 rounded-lg cursor-pointer"
              />
              <span className="text-[10px] text-amber-800 font-medium block">
                {options.caption_cover === 'image'
                  ? 'Cắt dải chữ gốc ở đáy video. Logo nằm dưới khung 9:16, không đè hình.'
                  : options.caption_cover && options.caption_cover !== 'off'
                    ? '0% = tắt khoảng trắng. Kéo 6–36% chiều cao hình (22% = mặc định). Vietsub kéo riêng.'
                    : 'Cắt hẳn dải dưới. Logo: chọn Ảnh tuỳ chỉnh rồi kéo dài ô logo 9:16.'}
              </span>
              {options.caption_cover && options.caption_cover !== 'off' && options.caption_cover !== 'image' && (
                <button
                  type="button"
                  onClick={() => handleChange(
                    'subtitle_bottom_crop',
                    Number(options.subtitle_bottom_crop || 0) <= 0 ? 22 : 0,
                  )}
                  className={`w-full rounded-xl border px-2.5 py-1.5 text-[11px] font-extrabold transition ${
                    Number(options.subtitle_bottom_crop || 0) <= 0
                      ? 'border-amber-600 bg-amber-600 text-white'
                      : 'border-amber-200 bg-white text-amber-950 hover:border-amber-400'
                  }`}
                >
                  {Number(options.subtitle_bottom_crop || 0) <= 0 ? 'Bật lại dải phủ đáy' : 'Tắt dải phủ đáy'}
                </button>
              )}
            </div>

            <div className="p-3.5 bg-sky-50/80 rounded-2xl border border-sky-200/80 space-y-2">
              <div className="flex justify-between text-xs">
                <span className="font-bold text-sky-950">Vị trí Vietsub trên video</span>
                <span className="font-mono text-sky-700 font-bold">
                  {Number(options.subtitle_y) > 0.04
                    ? `${Math.round(previewSubtitleY(options.subtitle_y) * 100)}%`
                    : 'Tự động đáy'}
                </span>
              </div>
              <input
                type="range"
                min="0.08"
                max="0.94"
                step="0.01"
                value={previewSubtitleY(
                  options.subtitle_y,
                  clampCoverHeight((Number(options.subtitle_bottom_crop) || 0) / 100),
                  Boolean(options.caption_cover && options.caption_cover !== 'off' && options.caption_cover !== 'image'),
                )}
                onChange={(e) => handleChange('subtitle_y', parseFloat(e.target.value))}
                className="w-full accent-sky-600 bg-sky-200/70 rounded-lg cursor-pointer"
              />
              <div className="flex justify-between text-[10px] font-bold text-sky-800">
                <span>Đỉnh hình</span>
                <span>Giữa</span>
                <span>Đáy hình</span>
              </div>
              <span className="text-[10px] text-sky-800 font-medium block">
                Kéo hộp Vietsub để đè chữ gốc. Cao nền 0% = tắt khoảng trắng (chỉ chữ + viền).
              </span>
              <div className="flex justify-between text-xs pt-1">
                <span className="font-bold text-sky-950">Rộng nền Vietsub</span>
                <span className="font-mono text-sky-700 font-bold">
                  {Math.round(clampSubtitleBoxW(options.subtitle_box_w) * 100)}%
                </span>
              </div>
              <input
                type="range"
                min="0.40"
                max="1"
                step="0.01"
                value={clampSubtitleBoxW(options.subtitle_box_w)}
                onChange={(e) => handleChange('subtitle_box_w', parseFloat(e.target.value))}
                className="w-full accent-sky-600 bg-sky-200/70 rounded-lg cursor-pointer"
              />
              <div className="flex justify-between text-xs">
                <span className="font-bold text-sky-950">Cao nền Vietsub</span>
                <span className="font-mono text-sky-700 font-bold">
                  {clampSubtitleBoxH(options.subtitle_box_h) <= 0
                    ? 'Tắt'
                    : `${Math.round(clampSubtitleBoxH(options.subtitle_box_h) * 100)}%`}
                </span>
              </div>
              <input
                type="range"
                min="0"
                max="0.22"
                step="0.005"
                value={clampSubtitleBoxH(options.subtitle_box_h)}
                onChange={(e) => handleChange('subtitle_box_h', parseFloat(e.target.value))}
                className="w-full accent-sky-600 bg-sky-200/70 rounded-lg cursor-pointer"
              />
              <button
                type="button"
                onClick={() => handleChange(
                  'subtitle_box_h',
                  clampSubtitleBoxH(options.subtitle_box_h) <= 0 ? 0.08 : 0,
                )}
                className={`w-full rounded-xl border px-2.5 py-1.5 text-[11px] font-extrabold transition ${
                  clampSubtitleBoxH(options.subtitle_box_h) <= 0
                    ? 'border-sky-600 bg-sky-600 text-white'
                    : 'border-sky-200 bg-white text-sky-950 hover:border-sky-400'
                }`}
              >
                {clampSubtitleBoxH(options.subtitle_box_h) <= 0 ? 'Bật lại nền Vietsub' : 'Tắt khoảng trắng'}
              </button>
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

              <div className="grid grid-cols-1 min-[420px]:grid-cols-3 gap-3 text-xs">
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

        <div className="flex flex-col gap-4 lg:gap-6 min-w-0 2xl:contents">
        {/* Cột 2: Âm thanh & Dịch */}
        <div className="space-y-4 bg-slate-50/30 p-4 rounded-2xl border border-slate-100 min-w-0">
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
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-start gap-2.5 min-w-0">
                  <Mic className="w-4 h-4 text-purple-600 shrink-0 mt-0.5" />
                  <div className="min-w-0">
                    <label className="text-xs font-extrabold text-purple-950 cursor-pointer block leading-snug" htmlFor="tts-toggle">
                      Tự dịch + lồng tiếng
                    </label>
                    <span className="text-[10px] text-purple-700 font-medium block leading-snug">
                      Dịch và lồng tiếng khớp timeline gốc.
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
                        vocal_mute_strategy: 'auto',
                        subtitle_mode: 'hard',
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
                <div className="grid grid-cols-1 min-[520px]:grid-cols-3 gap-1.5 rounded-xl border border-purple-100 bg-white/70 p-1.5">
                  {[
                    {
                      id: 'off',
                      title: 'Tắt Vietsub',
                      desc: 'Không chèn phụ đề.',
                    },
                    {
                      id: 'soft',
                      title: 'CC bật / tắt',
                      desc: 'Bật CC khi xem trong Kho.',
                    },
                    {
                      id: 'hard',
                      title: 'In cố định',
                      desc: 'Luôn hiện trên hình.',
                    },
                  ].map((mode) => {
                    const selectedMode = options.burn_subtitles === false
                      ? 'off'
                      : (options.subtitle_mode || 'hard');
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
                        className={`rounded-lg border px-2 py-2 text-left transition min-w-0 ${
                          active
                            ? 'border-purple-500 bg-purple-600 text-white'
                            : 'border-purple-100 bg-white text-purple-950 hover:border-purple-300'
                        }`}
                      >
                        <span className="block text-[11px] font-extrabold leading-snug">{mode.title}</span>
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
                <div className="grid grid-cols-1 min-[520px]:grid-cols-3 gap-1.5">
                  {[
                    {
                      id: 'dub',
                      icon: Captions,
                      title: 'Gốc',
                      desc: 'Tiếng Việt nói, sát ý, sửa lỗi nghe.',
                    },
                    {
                      id: 'narrator',
                      icon: BookOpen,
                      title: 'Kể chuyện',
                      desc: 'Ngôi 3, kể lại đúng cảnh đang xảy ra.',
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
                        className={`text-left rounded-xl border px-2 py-2.5 transition-all min-w-0 ${
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
        <div className="space-y-4 bg-slate-50/30 p-4 rounded-2xl border border-slate-100 min-w-0">
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
                  let next;
                  let preview_aspect = options.preview_aspect;
                  if (hasVertical) {
                    next = cur.filter((x) => !VERTICAL_TOGGLE.includes(x));
                    if (hasHorizontal) preview_aspect = '16:9';
                  } else {
                    next = [...new Set([...cur, ...VERTICAL_TOGGLE])];
                    preview_aspect = '9:16';
                  }
                  onChange({ ...options, target_platforms: next, preview_aspect });
                }}
                className={`text-left rounded-xl border px-3 py-2.5 transition-all cursor-pointer ${
                  hasVertical
                    ? 'bg-blue-600 border-blue-700 text-white shadow-sm'
                    : 'bg-white border-slate-200 text-slate-700 hover:border-blue-300'
                }`}
              >
                <div className="text-[11px] font-extrabold leading-tight">Dọc · 9:16</div>
                <div className={`text-[9px] font-bold mt-0.5 ${hasVertical ? 'text-blue-100' : 'text-slate-400'}`}>
                  TikTok, Shorts, Reels
                </div>
              </button>

              <button
                type="button"
                onClick={() => {
                  const cur = Array.isArray(options.target_platforms) ? [...options.target_platforms] : [];
                  let next;
                  let preview_aspect = options.preview_aspect;
                  if (hasHorizontal) {
                    next = cur.filter((x) => !HORIZONTAL_TOGGLE.includes(x));
                    if (hasVertical) preview_aspect = '9:16';
                  } else {
                    next = [...new Set([...cur, ...HORIZONTAL_TOGGLE])];
                    preview_aspect = '16:9';
                  }
                  onChange({ ...options, target_platforms: next, preview_aspect });
                }}
                className={`text-left rounded-xl border px-3 py-2.5 transition-all cursor-pointer ${
                  hasHorizontal
                    ? 'bg-blue-600 border-blue-700 text-white shadow-sm'
                    : 'bg-white border-slate-200 text-slate-700 hover:border-blue-300'
                }`}
              >
                <div className="text-[11px] font-extrabold leading-tight">Ngang · 16:9</div>
                <div className={`text-[9px] font-bold mt-0.5 ${hasHorizontal ? 'text-blue-100' : 'text-slate-400'}`}>
                  Khung 16:9 · YouTube ngang
                </div>
              </button>
            </div>
            <p className="text-[10px] font-semibold text-slate-500 leading-snug">
              {hasVertical && hasHorizontal
                ? 'Đang chọn cả hai. Khung xem trước (Sau crop / config) theo tỉ lệ vừa bấm — có nút 9:16 / 16:9 trên preview.'
                : hasHorizontal
                  ? 'Xem trước khung 16:9 ngay trên video Sau crop / config.'
                  : 'Xem trước khung 9:16 ngay trên video Sau crop / config.'}
            </p>

            {/* Channel Selector — Facebook pages are multi-select */}
            <div className="space-y-1.5">
              {groups.length > 0 && (
                <div className="space-y-1.5">
                  <label className="block text-[11px] font-bold text-slate-700">Chọn theo nhóm:</label>
                  <div className="flex flex-wrap gap-1.5">
                    {groups.map((g) => {
                      const memberIds = g.channel_ids || [];
                      const on = memberIds.length > 0 && memberIds.every((id) => selectedIds.includes(id));
                      return (
                        <button
                          key={g.group_id}
                          type="button"
                          title={g.notes || ''}
                          onClick={() => {
                            if (on) {
                              applyChannelIds(selectedIds.filter((id) => !memberIds.includes(id)), {
                                group_ids: (options.group_ids || []).filter((id) => id !== g.group_id),
                              });
                            } else {
                              applyChannelIds(
                                [...new Set([...selectedIds, ...memberIds])],
                                { group_ids: [...new Set([...(options.group_ids || []), g.group_id])] },
                              );
                            }
                          }}
                          className={`px-2.5 py-1 rounded-lg text-[10px] font-extrabold border ${
                            on ? 'bg-blue-600 text-white border-blue-700' : 'bg-white text-slate-700 border-slate-200'
                          }`}
                        >
                          {g.name} ({memberIds.length})
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
              <div className="flex items-center justify-between gap-2">
                <label className="block text-[11px] font-bold text-slate-700">Đăng lên Fanpage:</label>
                {facebookChannels.length > 0 && (
                  <button
                    type="button"
                    onClick={() => {
                      const all = facebookChannels.map((c) => c.channel_id || c.id);
                      applyChannelIds(selectedIds.length === all.length ? [] : all, { group_ids: selectedIds.length === all.length ? [] : (options.group_ids || []) });
                    }}
                    className="text-[10px] font-extrabold text-blue-700 hover:text-blue-900"
                  >
                    {selectedIds.length === facebookChannels.length ? 'Bỏ chọn hết' : 'Chọn tất cả Page'}
                  </button>
                )}
              </div>
              {tiktokChannels.length > 0 && (
                <div className="space-y-1.5 pt-1">
                  <label className="block text-[11px] font-bold text-slate-700">Đăng lên TikTok:</label>
                  <div className="max-h-40 overflow-y-auto rounded-xl border border-rose-200 bg-white divide-y divide-slate-100">
                    {tiktokChannels.map((chan) => {
                      const id = chan.channel_id || chan.id;
                      const on = selectedIds.includes(id);
                      return (
                        <label
                          key={id}
                          className={`flex items-center gap-2.5 px-2.5 py-2 cursor-pointer ${on ? 'bg-rose-50' : 'hover:bg-slate-50'}`}
                        >
                          <input
                            type="checkbox"
                            checked={on}
                            onChange={() => toggleChannelId(id)}
                            className="accent-rose-600 shrink-0"
                          />
                          {chan.tiktok_avatar_url ? (
                            <img src={chan.tiktok_avatar_url} alt="" className="w-8 h-8 rounded-lg object-cover shrink-0 bg-slate-200" />
                          ) : (
                            <span className="w-8 h-8 rounded-lg bg-rose-600 text-white text-[10px] font-black flex items-center justify-center shrink-0">
                              {String(chan.name || '?').slice(0, 2).toUpperCase()}
                            </span>
                          )}
                          <span className="min-w-0">
                            <span className="block text-[11px] font-bold text-slate-900 leading-snug line-clamp-2">{chan.name}</span>
                            <span className="block text-[10px] text-slate-500 truncate">
                              {chan.tiktok_username ? `@${chan.tiktok_username}` : 'TikTok Direct Post'}
                              {chan.tiktok_auto_publish ? ' · tự đăng' : ''}
                            </span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              )}
              {facebookChannels.length > 0 ? (
                <div className="max-h-52 overflow-y-auto rounded-xl border border-slate-200 bg-white divide-y divide-slate-100">
                  {facebookChannels.map((chan) => {
                    const id = chan.channel_id || chan.id;
                    const on = selectedIds.includes(id);
                    const pic = chan.facebook_page_id
                      ? getMediaUrl(`/api/v1/facebook/pages/${chan.facebook_page_id}/picture`)
                      : '';
                    return (
                      <label
                        key={id}
                        className={`flex items-center gap-2.5 px-2.5 py-2 cursor-pointer ${on ? 'bg-blue-50' : 'hover:bg-slate-50'}`}
                      >
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={() => toggleChannelId(id)}
                          className="accent-blue-600 shrink-0"
                        />
                        {pic ? (
                          <img src={pic} alt="" className="w-8 h-8 rounded-lg object-cover shrink-0 bg-slate-200" />
                        ) : (
                          <span className="w-8 h-8 rounded-lg bg-blue-600 text-white text-[10px] font-black flex items-center justify-center shrink-0">
                            {String(chan.name || '?').slice(0, 2).toUpperCase()}
                          </span>
                        )}
                        <span className="min-w-0">
                          <span className="block text-[11px] font-bold text-slate-900 leading-snug line-clamp-2">{chan.name}</span>
                          <span className="block text-[10px] text-slate-500 truncate">
                            {chan.facebook_category || 'Facebook Reels'}
                          </span>
                        </span>
                      </label>
                    );
                  })}
                </div>
              ) : (
                <p className="text-[11px] text-slate-500 font-medium px-1">
                  Chưa có Fanpage. Vào tab Kênh → kết nối Facebook rồi đồng bộ Page.
                </p>
              )}
              {otherChannels.length > 0 && (
                <select
                  value={selectedIds.find((id) => otherChannels.some((c) => (c.channel_id || c.id) === id)) || 'none'}
                  onChange={(e) => handleChannelSelect(e.target.value)}
                  onFocus={loadChannels}
                  className="w-full text-xs font-semibold bg-white border border-slate-200 rounded-xl px-3 py-2.5 focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 shadow-2xs"
                >
                  <option value="none">Kênh khác (TikTok/YouTube)…</option>
                  {otherChannels.map((chan) => (
                    <option key={chan.channel_id || chan.id} value={chan.channel_id || chan.id}>
                      [{chan.platform?.toUpperCase() || 'KHÁC'}] {chan.name}
                    </option>
                  ))}
                </select>
              )}
              {selectedIds.length > 0 && (
                <p className="text-[10px] font-bold text-blue-700">
                  Sẽ đăng {selectedIds.length} page khi reup xong (trạng thái Sẵn sàng).
                </p>
              )}
            </div>

            {/* If Channel is Selected: Metadata & Caption Form */}
            {selectedIds.length > 0 && (
              <div className="space-y-3 pt-2 border-t border-blue-100/80 animate-fadeIn">
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 mb-1">
                    Nội dung hướng tới — chỉ cần điền ô này:
                  </label>
                  <textarea
                    rows={4}
                    value={options.post_intent || ''}
                    onChange={(e) => handleChange('post_intent', e.target.value)}
                    placeholder="Cần đập phá tháo dỡ nhà, lột gạch liên hệ 0777704099"
                    className="w-full text-xs bg-white border border-slate-200 rounded-xl px-3 py-2 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 shadow-2xs resize-none"
                  />
                  <p className="mt-1.5 text-[10px] font-medium text-slate-500 leading-snug">
                    agy tự viết title + caption + hashtag cho từng Fanpage, bám nội dung video, giữ nguyên SĐT/CTA. Mỗi bài khác nhau.
                  </p>
                </div>
                <div>
                  <label className="block text-[11px] font-bold text-slate-700 mb-1">Note video (nội bộ):</label>
                  <textarea
                    rows={2}
                    value={options.video_note || ''}
                    onChange={(e) => handleChange('video_note', e.target.value)}
                    placeholder="Ghi lại clip này đăng nhóm nào, mục đích gì..."
                    className="w-full text-xs bg-amber-50/60 border border-amber-200 rounded-xl px-3 py-2 resize-none"
                  />
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
