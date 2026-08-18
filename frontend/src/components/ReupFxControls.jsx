import React from 'react';
import {
  Wand2,
  Hash,
  FlipHorizontal,
  Volume2,
  Palette,
  Zap,
  VolumeX,
  Mic,
  Languages,
} from 'lucide-react';

export function ReupFxControls({ options, onChange, onSubmit, submitting }) {
  const handleChange = (key, value) => {
    onChange({ ...options, [key]: value });
  };

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
            { id: 'crop', label: '🌟 Cắt Bỏ Phụ Đề Đáy (Crop 13%)', desc: 'Sạch 100% không tỳ vết, không vệt mờ (Khuyên dùng)' },
            { id: 'boxblur', label: '🎬 Dải Mờ Điện Ảnh (Blur Bar)', desc: 'Mờ mịn dải phụ đề đáy phong cách phim' },
            { id: 'auto', label: '🔮 Inpaint Nét Chữ AI + OpenCV', desc: 'Tự quét & xóa nét chữ động' },
            { id: 'telea', label: '⚡ OpenCV Telea', desc: 'Inpaint nhanh mượt mà' },
            { id: 'ns', label: '🔬 OpenCV Navier-Stokes', desc: 'Inpaint khử biên mượt mà' },
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
                Khử Giọng Nói Gốc (Giữ Nhạc Nền BGM)
              </label>
              <span className="text-[10px] text-slate-500 block">Tự động loại bỏ tiếng nói ngoại ngữ gốc</span>
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

        {/* TTS Dubbing Control - VIP Pro Universe */}
        <div className="p-4 bg-purple-50/70 border border-purple-200 rounded-2xl space-y-3.5">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2.5">
              <Mic className="w-4 h-4 text-purple-600 shrink-0" />
              <div>
                <label className="text-xs font-bold text-purple-900 cursor-pointer block" htmlFor="tts-toggle">
                  Lồng Tiếng AI Đa Ngôn Ngữ (VIP Pro Neural Dubbing)
                </label>
                <span className="text-[10px] text-purple-600 block">
                  Whisper STT &rarr; Dịch Thuật Đa Ngữ &rarr; Lồng Giọng Đọc AI Cao Cấp
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

          {options.enable_tts && (
            <div className="space-y-3 pt-2.5 border-t border-purple-200/80">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 text-xs">
                {/* Target Language Selector */}
                <div>
                  <span className="text-[10px] font-bold text-purple-900 block mb-1 flex items-center gap-1">
                    <Languages className="w-3 h-3 text-purple-600" /> Ngôn Ngữ Thuyết Minh
                  </span>
                  <select
                    value={options.target_lang || 'vi'}
                    onChange={(e) => {
                      const newLang = e.target.value;
                      let defaultVoice = 'vi-VN-HoaiMyNeural';
                      if (newLang === 'en') defaultVoice = 'en-US-GuyNeural';
                      else if (newLang === 'zh') defaultVoice = 'zh-CN-YunxiNeural';
                      else if (newLang === 'ja') defaultVoice = 'ja-JP-NanamiNeural';
                      else if (newLang === 'ko') defaultVoice = 'ko-KR-SunHiNeural';
                      else if (newLang === 'th') defaultVoice = 'th-TH-PremwadeeNeural';
                      else if (newLang === 'fr') defaultVoice = 'fr-FR-DeniseNeural';
                      else if (newLang === 'es') defaultVoice = 'es-ES-ElviraNeural';
                      else if (newLang === 'de') defaultVoice = 'de-DE-KatjaNeural';
                      else if (newLang === 'ru') defaultVoice = 'ru-RU-DmitryNeural';
                      else if (newLang === 'id') defaultVoice = 'id-ID-GadisNeural';
                      onChange({ ...options, target_lang: newLang, tts_voice: defaultVoice });
                    }}
                    className="w-full bg-white border border-purple-200 rounded-xl p-2 text-xs text-slate-800 font-bold outline-none focus:ring-2 focus:ring-purple-400 cursor-pointer"
                  >
                    <option value="vi">🇻🇳 Tiếng Việt (Việt Nam)</option>
                    <option value="en">🇺🇸 Tiếng Anh (English Global)</option>
                    <option value="zh">🇨🇳 Tiếng Trung (Douyin Trends)</option>
                    <option value="ja">🇯🇵 Tiếng Nhật (Anime & Manga)</option>
                    <option value="ko">🇰🇷 Tiếng Hàn (K-Drama & K-Pop)</option>
                    <option value="th">🇹🇭 Tiếng Thái (Thai Drama)</option>
                    <option value="fr">🇫🇷 Tiếng Pháp (French)</option>
                    <option value="es">🇪🇸 Tiếng Tây Ban Nha (Spanish)</option>
                    <option value="de">🇩🇪 Tiếng Đức (German)</option>
                    <option value="ru">🇷🇺 Tiếng Nga (Russian)</option>
                    <option value="id">🇮🇩 Tiếng Indonesia</option>
                  </select>
                </div>

                {/* Voice Model Selector */}
                <div>
                  <span className="text-[10px] font-bold text-purple-900 block mb-1">
                    Giọng Đọc VIP Pro
                  </span>
                  <select
                    value={options.tts_voice || 'vi-VN-HoaiMyNeural'}
                    onChange={(e) => handleChange('tts_voice', e.target.value)}
                    className="w-full bg-white border border-purple-200 rounded-xl p-2 text-xs text-slate-800 font-bold outline-none focus:ring-2 focus:ring-purple-400 cursor-pointer"
                  >
                    {/* Vietnamese */}
                    {(options.target_lang === 'vi' || !options.target_lang) && (
                      <>
                        <option value="vi-VN-HoaiMyNeural">🎙️ Nữ Bắc: Hoài My (Ngọt ngào, đọc truyện, kể chuyện)</option>
                        <option value="vi-VN-NamMinhNeural">🎙️ Nam Bắc: Nam Minh (Trầm ấm, review phim, tin tức)</option>
                        <option value="gtts-vi">🎙️ Nữ Phổ Thông: Google Neural Vi (Tự nhiên, rõ chữ)</option>
                      </>
                    )}

                    {/* English */}
                    {options.target_lang === 'en' && (
                      <>
                        <option value="en-US-GuyNeural">🎙️ Nam US: Guy (Viral Shorts, cuốn hút, TikTok trend)</option>
                        <option value="en-US-JennyNeural">🎙️ Nữ US: Jenny (Tươi vui, lifestyle, vlog)</option>
                        <option value="en-US-AriaNeural">🎙️ Nữ US: Aria (Kể chuyện kịch tính, tài liệu)</option>
                        <option value="en-GB-RyanNeural">🎙️ Nam UK: Ryan (Quý tộc Anh, tin tức sang trọng)</option>
                      </>
                    )}

                    {/* Chinese */}
                    {options.target_lang === 'zh' && (
                      <>
                        <option value="zh-CN-YunxiNeural">🎙️ Nam Douyin: Yunxi (Review phim 'Chú ý xem...')</option>
                        <option value="zh-CN-XiaoxiaoNeural">🎙️ Nữ Douyin: Xiaoxiao (Dịu dàng, ẩm thực & du lịch)</option>
                        <option value="zh-CN-YunjianNeural">🎙️ Nam Kiếm Hiệp: Yunjian (Hùng tráng, cổ trang)</option>
                      </>
                    )}

                    {/* Japanese */}
                    {options.target_lang === 'ja' && (
                      <>
                        <option value="ja-JP-NanamiNeural">🎙️ Nữ: Nanami (Giọng Anime ngọt ngào, tươi sáng)</option>
                        <option value="ja-JP-KeitaNeural">🎙️ Nam: Keita (Giọng Manga trầm, lịch lãm)</option>
                      </>
                    )}

                    {/* Korean */}
                    {options.target_lang === 'ko' && (
                      <>
                        <option value="ko-KR-SunHiNeural">🎙️ Nữ: Sun-Hi (Nữ chính K-Drama thanh lịch)</option>
                        <option value="ko-KR-InJoonNeural">🎙️ Nam: InJoon (Nam thần K-Drama trầm ấm)</option>
                      </>
                    )}

                    {/* Thai */}
                    {options.target_lang === 'th' && (
                      <>
                        <option value="th-TH-PremwadeeNeural">🎙️ Nữ: Premwadee (Phim truyền hình Thái)</option>
                        <option value="th-TH-NiwatNeural">🎙️ Nam: Niwat (Nam tính, kịch tính)</option>
                      </>
                    )}

                    {/* French, Spanish, German, Russian, Indonesian */}
                    {options.target_lang === 'fr' && (
                      <option value="fr-FR-DeniseNeural">🎙️ Nữ Pháp: Denise (Quyến rũ, chuẩn Paris)</option>
                    )}
                    {options.target_lang === 'es' && (
                      <option value="es-ES-ElviraNeural">🎙️ Nữ TBN: Elvira (Sôi động, phóng khoáng)</option>
                    )}
                    {options.target_lang === 'de' && (
                      <option value="de-DE-KatjaNeural">🎙️ Nữ Đức: Katja (Chuẩn mực, công nghệ)</option>
                    )}
                    {options.target_lang === 'ru' && (
                      <option value="ru-RU-DmitryNeural">🎙️ Nam Nga: Dmitry (Trầm mạnh mẽ)</option>
                    )}
                    {options.target_lang === 'id' && (
                      <option value="id-ID-GadisNeural">🎙️ Nữ Indo: Gadis (Tự nhiên Đông Nam Á)</option>
                    )}
                  </select>
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
