import React, { useEffect, useRef, useState } from 'react';
import { X, Download, ShieldCheck, Film, Captions } from 'lucide-react';
import { getStreamUrl, getSubtitleUrl, getDownloadUrl } from '../services/api';

const VERTICAL_PLATS = ['tiktok', 'youtube_shorts', 'facebook', 'instagram', 'douyin'];

function looksPortraitMedia(video) {
  const streamId = String(video?.stream_job_id || video?.job_id || video?.filename || '');
  const plat = String(video?.play_platform || '').toLowerCase();
  if (VERTICAL_PLATS.includes(plat) && plat !== 'douyin') return true;
  return VERTICAL_PLATS.some((p) => streamId.includes(`.${p}`));
}

export function VideoModal({ isOpen, onClose, video }) {
  const videoRef = useRef(null);
  const [captionsOn, setCaptionsOn] = useState(false);
  const [portrait, setPortrait] = useState(() => looksPortraitMedia(video));
  const mediaId = video?.job_id || video?.video_id || video?.filename;
  const streamId = video?.stream_job_id || mediaId;
  const streamCacheKey = [streamId, video?.output_md5, video?.updated_at, video?.file_size, 'p2'].filter(Boolean).join('-');
  const streamUrl = streamId ? getStreamUrl(streamId, streamCacheKey) : '';
  const downloadUrl = streamId ? getDownloadUrl(streamId) : '';

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        onClose?.();
      }
    };
    if (isOpen) {
      window.addEventListener('keydown', handleKeyDown);
      document.body.style.overflow = 'hidden';
    }
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = 'unset';
    };
  }, [isOpen, onClose]);

  useEffect(() => {
    setCaptionsOn(false);
    setPortrait(looksPortraitMedia(video));
  }, [video?.job_id, video?.video_id, video?.filename, video?.stream_job_id, video?.play_platform]);

  useEffect(() => {
    const el = videoRef.current;
    if (!el) return undefined;
    const unmute = () => {
      el.muted = false;
      if (el.volume === 0) el.volume = 1;
    };
    unmute();
    el.addEventListener('loadeddata', unmute);
    el.addEventListener('play', unmute);
    return () => {
      el.removeEventListener('loadeddata', unmute);
      el.removeEventListener('play', unmute);
    };
  }, [streamUrl]);

  if (!isOpen || !video) return null;

  const title = video.title || video.filename || `Video Thành Phẩm (${mediaId})`;
  const hasToggleableSubtitles = video.subtitle_mode === 'soft';

  const toggleCaptions = () => {
    const textTrack = videoRef.current?.textTracks?.[0];
    if (!textTrack) return;
    const nextOn = textTrack.mode !== 'showing';
    textTrack.mode = nextOn ? 'showing' : 'disabled';
    setCaptionsOn(nextOn);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/70 backdrop-blur-md animate-fade-in"
      onClick={onClose}
    >
      <div
        className={`bg-white rounded-3xl w-full overflow-hidden shadow-2xl border border-slate-200 flex flex-col max-h-[90vh] animate-scale-up ${
          portrait ? 'max-w-sm' : 'max-w-4xl'
        }`}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal Header */}
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/60">
          <div className="flex items-center space-x-3 min-w-0">
            <div className="p-2 rounded-xl bg-blue-50 text-blue-600 border border-blue-100 shrink-0">
              <Film className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <h3 className="text-sm font-extrabold text-slate-900 truncate">
                {title}
              </h3>
              <p className="text-[11px] font-mono text-slate-500 truncate">
                Mã ID: {mediaId} • Sàn: {video.platform?.toUpperCase() || 'DOUYIN'}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-xl transition cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Video Player Container */}
        <div
          className={`bg-slate-950 flex items-center justify-center relative w-full min-h-0 ${
            portrait ? 'aspect-[9/16] max-h-[min(72vh,640px)] mx-auto' : 'aspect-video max-h-[60vh]'
          }`}
        >
          <video
            key={streamUrl}
            ref={videoRef}
            src={streamUrl}
            controls
            autoPlay
            playsInline
            muted={false}
            preload="auto"
            onLoadedMetadata={() => {
              const el = videoRef.current;
              if (!el) return;
              setPortrait((el.videoHeight || 0) > (el.videoWidth || 0));
              el.play?.().catch(() => {});
            }}
            className="w-full h-full object-contain"
          >
            {hasToggleableSubtitles && (
              <track
                kind="subtitles"
                src={getSubtitleUrl(mediaId)}
                srcLang="vi"
                label="Vietsub"
              />
            )}
            Trình duyệt của bạn không hỗ trợ phát video HTML5.
          </video>
          {hasToggleableSubtitles && (
            <button
              type="button"
              onClick={toggleCaptions}
              className={`absolute bottom-14 right-4 z-10 inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] font-black shadow-lg transition ${
                captionsOn
                  ? 'border-amber-300 bg-amber-400 text-slate-950'
                  : 'border-white/30 bg-slate-950/75 text-white hover:bg-slate-900'
              }`}
              title={captionsOn ? 'Tắt Vietsub' : 'Bật Vietsub'}
            >
              <Captions className="h-4 w-4" /> CC {captionsOn ? 'ON' : 'OFF'}
            </button>
          )}
          {video.subtitle_mode === 'hard' && (
            <div className="absolute bottom-14 right-4 z-10 rounded-lg border border-white/25 bg-slate-950/80 px-2.5 py-1.5 text-[11px] font-bold text-white shadow-lg">
              Vietsub đã in cố định · Muốn có nút CC, hãy reup lại bằng chế độ CC
            </div>
          )}
        </div>

        {/* Modal Footer & Actions */}
        <div className="px-6 py-4 bg-white border-t border-slate-100 flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3">
            {video.md5 && (
              <div className="px-3 py-1.5 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-xl text-xs font-mono font-bold flex items-center gap-1.5">
                <ShieldCheck className="w-4 h-4 text-emerald-600" />
                <span>MD5: {video.md5.substring(0, 12)}...</span>
              </div>
            )}
            <span className="text-xs text-slate-500 font-medium">
              Chất lượng: <span className="font-bold text-slate-700">H.264 / AAC 60fps</span>
            </span>
          </div>

          <div className="flex items-center gap-2">
            <a
              href={downloadUrl}
              download
              className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-extrabold rounded-xl transition shadow-md shadow-emerald-600/20 flex items-center gap-1.5 cursor-pointer"
            >
              <Download className="w-4 h-4" /> Tải Video MP4
            </a>
            <button
              onClick={onClose}
              className="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-bold rounded-xl border border-slate-200 transition cursor-pointer"
            >
              Đóng
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
