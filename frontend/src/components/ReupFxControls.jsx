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
  Layers
} from 'lucide-react';

import { fetchChannels, getMediaUrl } from '../services/api';

export function ReupFxControls({ options, onChange, onSubmit, submitting }) {
  const [channels, setChannels] = useState([]);
  const [loadingChannels, setLoadingChannels] = useState(false);


  useEffect(() => {
    loadChannels();
  }, []);

  const loadChannels = async () => {
    setLoadingChannels(true);
    try {
      const data = await fetchChannels();
      const list = Array.isArray(data) ? data : (data.channels || []);
      setChannels(list);
    } catch {
      setChannels([]);
    } finally {
      setLoadingChannels(false);
    }
  };

  const handleChange = (key, value) => {
    onChange({ ...options, [key]: value });
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

  return (
    <div className="clean-panel rounded-3xl p-6 sm:p-7 shadow-xs space-y-6">
      <h3 className="text-base font-extrabold text-slate-900 flex items-center gap-2 border-b border-slate-100 pb-3">
        <Wand2 className="w-5 h-5 text-blue-600" />
        Tùy Chỉnh Thuật Toán & Reup FX
      </h3>

      {/* Watermark Method Selector */}
      <div className="space-y-2.5">
        <label className="block text-xs font-bold text-slate-700">
          Thuật Toán Xoá Text Cũ & Logo Watermark
        </label>
        <div className="grid grid-cols-2 gap-2 text-xs">
          {[
            { id: 'all', label: '🚀 Siêu Cấp Toàn Năng (All-In-One)', desc: 'Vừa cắt sạch 100% phụ đề đáy vừa inpaint quét xóa sạch logo/text ở giữa và đỉnh (Khuyên dùng)' },
            { id: 'auto', label: '🔮 Inpaint Nét Chữ AI + OpenCV', desc: 'Tự động quét & xóa sạch chữ/logo trên mọi vị trí (Giữ nguyên 100% khung hình)' },
            { id: 'crop', label: '🌟 Cắt Bỏ Phụ Đề Đáy (Crop 6%)', desc: 'Chỉ cắt mỏng đáy nếu phụ đề gốc dính cứng — mặc định không cắt' },
            { id: 'boxblur', label: '🎬 Dải Mờ Điện Ảnh (Blur Bar)', desc: 'Làm mờ mịn dải phụ đề phong cách điện ảnh' },
            { id: 'telea', label: '⚡ OpenCV Telea (Nhanh)', desc: 'Xóa mượt mà theo vùng ROI đã chọn' },
            { id: 'none', label: '🚫 Giữ Nguyên Khung Hình', desc: 'Không can thiệp phụ đề/watermark' },
          ].map((item) => {
            const isSelected =
              options.wm_method === item.id ||
              (options.wm_method === 'opencv_telea' && item.id === 'telea') ||
              (options.wm_method === 'opencv_ns' && item.id === 'ns');
            return (
              <label
                key={item.id}
                className={`flex flex-col p-3 rounded-2xl border cursor-pointer transition-all duration-150 ${
                  isSelected
                    ? 'bg-blue-50/80 border-blue-400 text-blue-900 font-bold shadow-xs'
                    : 'bg-slate-50/80 border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-100/80'
                }`}
              >
                <div className="flex items-center space-x-2">
                  <input
                    type="radio"
                    name="wm_method"
                    value={item.id}
                    checked={isSelected}
                    onChange={() => handleChange('wm_method', item.id)}
                    className="text-blue-600 focus:ring-blue-500"
                  />
                  <span className="text-xs font-bold text-slate-800">{item.label}</span>
                </div>
                <span className="text-[10px] text-slate-500 font-normal pl-5 mt-0.5">{item.desc}</span>
              </label>
            );
          })}
        </div>
      </div>

      {/* Video & Audio Controls */}
      <div className="space-y-3 pt-2 border-t border-slate-100">
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

        {/* Audio Vocal Mute */}
        <div className="flex items-center justify-between p-3.5 bg-slate-50 rounded-2xl border border-slate-200/80">
          <div className="flex items-center space-x-2.5">
            <VolumeX className="w-4 h-4 text-rose-600 shrink-0" />
            <div>
              <label className="text-xs font-bold text-slate-700 cursor-pointer block" htmlFor="vocal-mute-toggle">
                Tắt tiếng gốc (khử thoại, giữ BGM)
              </label>
              <span className="text-[10px] text-slate-500 block">Cắt hẳn thoại gốc (chỉ giữ bass nhạc) — hết chồng giọng Việt + Trung</span>
            </div>
          </div>
          <input
            id="vocal-mute-toggle"
            type="checkbox"
            checked={options.enable_vocal_mute !== false}
            onChange={(e) => handleChange('enable_vocal_mute', e.target.checked)}
            className="w-4 h-4 rounded text-blue-600 focus:ring-blue-500 border-slate-300 shrink-0 cursor-pointer"
          />
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
                  STT lời thoại → dịch Việt → TTS ghép đúng timestamp, giữ nhạc nền BGM
                </span>
              </div>
            </div>
            <input
              id="tts-toggle"
              type="checkbox"
              checked={!!options.enable_tts}
              onChange={(e) => handleChange('enable_tts', e.target.checked)}
              className="w-4 h-4 rounded text-purple-600 focus:ring-purple-500 border-purple-300 shrink-0 cursor-pointer"
            />
          </div>

          <div className="flex items-center justify-between pt-1">
            <div>
              <label className="text-xs font-extrabold text-purple-950 cursor-pointer block" htmlFor="burnsub-toggle">
                Cháy phụ đề Vietsub lên video (Hardsub)
              </label>
              <span className="text-[10px] text-purple-700 font-medium block">
                Nhận lời thoại → dịch tiếng Việt → đốt chữ xuống đáy khung hình
              </span>
            </div>
            <input
              id="burnsub-toggle"
              type="checkbox"
              checked={options.burn_subtitles !== false}
              onChange={(e) => handleChange('burn_subtitles', e.target.checked)}
              className="w-4 h-4 rounded text-purple-600 focus:ring-purple-500 border-purple-300 shrink-0 cursor-pointer"
            />
          </div>

          {options.enable_tts && (
            <div className="space-y-3 pt-2.5 border-t border-purple-200/70 animate-fadeIn">
              <div className="flex items-center justify-between">
                <div>
                  <label className="text-xs font-extrabold text-purple-950 cursor-pointer block" htmlFor="lipsync-toggle">
                    Khớp khẩu hình (Lip-sync)
                  </label>
                  <span className="text-[10px] text-purple-700 font-medium block">
                    Canh từng câu đúng cửa sổ miệng gốc: rút câu → chỉnh tốc TTS → rubberband giữ formant
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
              <div>
                <label className="block text-[11px] font-bold text-purple-900 mb-1.5">Kiểu Vietsub / lồng tiếng</label>
                <select
                  value={options.vietsub_style || 'auto'}
                  onChange={(e) => handleChange('vietsub_style', e.target.value)}
                  className="w-full bg-white border border-purple-200 rounded-xl px-3 py-2.5 text-xs text-slate-900 font-bold outline-none focus:ring-2 focus:ring-purple-400 cursor-pointer shadow-2xs"
                >
                  <option value="auto">Tự chọn — clip ngắn lồng tiếng, clip dài kể lại</option>
                  <option value="dub">Lồng tiếng khớp khẩu hình (từng câu)</option>
                  <option value="narrator">Kể lại (người dẫn chuyện, ngôi 3)</option>
                  <option value="recap">Tóm tắt voice-over (clip dài 10–45 phút)</option>
                  <option value="funny">Bản hài / văn phong mạng</option>
                </select>
              </div>
              <div>
                <label className="block text-[11px] font-bold text-purple-900 mb-1.5">Ngôn ngữ đích</label>
                <select
                  value={options.target_lang || 'vi'}
                  onChange={(e) => {
                    const lang = e.target.value;
                    const voices = {
                      vi: 'vi-VN-HoaiMyNeural',
                      en: 'en-US-AriaNeural',
                      th: 'th-TH-PremwadeeNeural',
                      id: 'id-ID-GadisNeural',
                      ja: 'ja-JP-NanamiNeural',
                      ko: 'ko-KR-SunHiNeural',
                      pt: 'pt-BR-FranciscaNeural',
                    };
                    handleChange('target_lang', lang);
                    onChange({ ...options, target_lang: lang, tts_voice: voices[lang] || options.tts_voice });
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
                  <span>Chọn Giọng Đọc Thuyết Minh Tiếng Việt:</span>
                  <span className="text-[10px] bg-purple-100 text-purple-800 px-2 py-0.5 rounded-full font-bold">
                    100% Tiếng Việt
                  </span>
                </label>
                <select
                  value={options.tts_voice || 'vi-VN-HoaiMyNeural'}
                  onChange={(e) => {
                    handleChange('tts_voice', e.target.value);
                    handleChange('target_lang', 'vi');
                  }}
                  className="w-full bg-white border border-purple-200 rounded-xl px-3 py-2.5 text-xs text-slate-900 font-bold outline-none focus:ring-2 focus:ring-purple-400 cursor-pointer shadow-2xs"
                >
                  <optgroup label="🌟 Giọng Đọc Nữ Tiếng Việt (Truyền Cảm & Tự Nhiên)">
                    <option value="vi-VN-HoaiMyNeural">🎙️ Nữ Hoài My (Ngọt ngào, truyền cảm, tâm sự, review ẩm thực)</option>
                    <option value="vi-VN-HoaiMy-Fast">⚡ Nữ Hoài My - Tiết Tấu Nhanh (Review TikTok/Shorts cuốn hút, sôi nổi)</option>
                    <option value="vi-VN-HoaiMy-Warm">🍵 Nữ Hoài My - Trầm Ấm (Đọc truyện, vlog đời sống, chữa lành)</option>
                  </optgroup>
                  <optgroup label="🔥 Giọng Đọc Nam Tiếng Việt (Cuốn Hút & Chuẩn Phóng Sự)">
                    <option value="vi-VN-NamMinhNeural">🎙️ Nam Nam Minh (Trầm ấm, lịch lãm, review phim, tài liệu chuẩn VTV)</option>
                    <option value="vi-VN-NamMinh-Fast">⚡ Nam Nam Minh - Tốc Độ Cao (Tóm tắt phim kịch tính, tin tức nóng)</option>
                    <option value="vi-VN-NamMinh-Deep">🌙 Nam Nam Minh - Trầm Sâu (Kể chuyện đêm khuya, truyện ma, bí ẩn)</option>
                  </optgroup>
                  <optgroup label="🚀 Kokoro-82M AI Thế Hệ Mới (24kHz Studio)">
                    <option value="kokoro-af_heart">💎 Kokoro-82M Neural (Âm thanh 24kHz trong trẻo, tự nhiên)</option>
                  </optgroup>
                  <optgroup label="🤖 Giọng Phổ Thông & Meme">
                    <option value="gtts-vi">🤖 Chị Google (Giọng chuẩn meme, review hài hước viral)</option>
                  </optgroup>
                </select>
              </div>


              <div className="bg-purple-100/60 p-2.5 rounded-xl border border-purple-200/60 flex items-start gap-2">
                <Sparkles className="w-3.5 h-3.5 text-purple-600 shrink-0 mt-0.5" />
                <p className="text-[10px] text-purple-900 leading-relaxed font-medium">
                  Hệ thống tự động nhận lời thoại → dịch Việt ngắn khớp nhịp miệng → TTS đúng cửa sổ thời gian (lip-sync), mix nhạc nền.
                </p>
              </div>
            </div>
          )}
        </div>


        {/* Channel Assignment & Distribution Section */}
        <div className="p-4 bg-gradient-to-br from-blue-50/70 via-indigo-50/40 to-slate-50 rounded-2xl border border-blue-100/90 space-y-3.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <Tv className="w-4 h-4 text-blue-600" />
              <span className="text-xs font-extrabold text-slate-900">
                Phân Bổ Kênh Xuất Bản (Channel Assignment)
              </span>
            </div>
            <span className="text-[10px] bg-blue-100/80 text-blue-700 px-2 py-0.5 rounded-full font-bold">
              Tự động gán video
            </span>
          </div>

          {/* Channel Selector */}
          <div className="space-y-1.5">
            <label className="block text-[11px] font-bold text-slate-700 flex items-center justify-between">
              <span>Chọn Kênh Đích:</span>
              {loadingChannels && (
                <span className="text-[10px] text-blue-600 flex items-center gap-1 font-normal">
                  <Loader2 className="w-3 h-3 animate-spin" /> Đang tải...
                </span>
              )}
            </label>
            <select
              value={options.channel_id || 'none'}
              onChange={(e) => handleChannelSelect(e.target.value)}
              className="w-full text-xs font-semibold bg-white border border-slate-200 rounded-xl px-3 py-2.5 focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 shadow-2xs"
            >

              <option value="none">📦 Không gán kênh (Chỉ lưu vào kho thành phẩm)</option>
              {channels.map((chan) => (
                <option key={chan.channel_id || chan.id} value={chan.channel_id || chan.id}>
                  📺 [{chan.platform?.toUpperCase() || 'KHÁC'}] {chan.name} {chan.tags?.length ? `(${chan.tags.join(', ')})` : ''}
                </option>
              ))}
            </select>
          </div>

          {activeChannel && (activeChannel.overlays || []).length > 0 && (
            <div className="flex items-start gap-2.5 p-2.5 rounded-xl bg-white border border-indigo-200/80">
              <Layers className="w-3.5 h-3.5 text-indigo-600 shrink-0 mt-0.5" />
              <div className="min-w-0">
                <p className="text-[11px] font-extrabold text-slate-800">
                  Gắn {activeChannel.overlays.length} logo/khung xuyên suốt video
                </p>
                <p className="text-[10px] text-slate-500 font-medium">
                  Vị trí đã chỉnh trong tab Kênh — logo hiện từ đầu đến cuối video thành phẩm.
                </p>
                <div className="flex items-center gap-1.5 mt-1.5">
                  {activeChannel.overlays.slice(0, 5).map((ov) => (
                    <img
                      key={ov.id}
                      src={getMediaUrl(ov.url)}
                      alt=""
                      className="w-7 h-7 rounded-md object-contain bg-slate-900 border border-slate-200"
                    />
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* If Channel is Selected: Metadata & Caption Form */}
          {options.channel_id && options.channel_id !== 'none' && (
            <div className="space-y-3 pt-2 border-t border-blue-100/80 animate-fadeIn">
              {/* Post Title */}
              <div>
                <label className="block text-[11px] font-bold text-slate-700 mb-1 flex items-center gap-1">
                  <FileText className="w-3.5 h-3.5 text-blue-600" />
                  Tiêu Đề Đăng Bài:
                </label>
                <input
                  type="text"
                  value={options.post_title || ''}
                  onChange={(e) => handleChange('post_title', e.target.value)}
                  placeholder="Ví dụ: Review chi tiết cảnh đẹp Hồ Nhĩ Hải..."
                  className="w-full text-xs bg-white border border-slate-200 rounded-xl px-3 py-2 focus:ring-1 focus:ring-blue-500 focus:border-blue-500 shadow-2xs"
                />
              </div>

              {/* Post Caption & Hashtags */}
              <div>
                <label className="block text-[11px] font-bold text-slate-700 mb-1 flex items-center justify-between">
                  <span className="flex items-center gap-1">
                    <Tag className="w-3.5 h-3.5 text-indigo-600" />
                    Mô Tả & Hashtags:
                  </span>
                  {activeChannel?.tags?.length > 0 && (
                    <span className="text-[10px] text-slate-500 font-normal">
                      Bấm thẻ bên dưới để chèn nhanh
                    </span>
                  )}
                </label>
                <textarea
                  rows={2}
                  value={options.post_caption || ''}
                  onChange={(e) => handleChange('post_caption', e.target.value)}
                  placeholder="Nhập nội dung caption và hashtags..."
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
                  Trạng Thái Bài Viết:
                </label>
                <div className="grid grid-cols-3 gap-2">
                  {[
                    { id: 'READY', label: '🚀 Sẵn Sàng', color: 'border-emerald-300 text-emerald-700 bg-emerald-50/50' },
                    { id: 'DRAFT', label: '📝 Bản Nháp', color: 'border-amber-300 text-amber-700 bg-amber-50/50' },
                    { id: 'PUBLISHED', label: '✅ Đã Đăng', color: 'border-blue-300 text-blue-700 bg-blue-50/50' },
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
