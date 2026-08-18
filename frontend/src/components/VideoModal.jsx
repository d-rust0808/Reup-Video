import React, { useEffect } from 'react';
import { X, Download, ShieldCheck, Film } from 'lucide-react';
import { getStreamUrl, getDownloadUrl } from '../services/api';

export function VideoModal({ isOpen, onClose, video }) {
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

  if (!isOpen || !video) return null;

  const mediaId = video.job_id || video.video_id || video.filename;
  const streamUrl = getStreamUrl(mediaId);
  const downloadUrl = getDownloadUrl(mediaId);
  const title = video.title || video.filename || `Video Thành Phẩm (${mediaId})`;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/70 backdrop-blur-md animate-fade-in"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-3xl max-w-4xl w-full overflow-hidden shadow-2xl border border-slate-200 flex flex-col max-h-[90vh] animate-scale-up"
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
        <div className="bg-slate-950 flex items-center justify-center relative aspect-video w-full max-h-[60vh]">
          <video
            src={streamUrl}
            controls
            autoPlay
            playsInline
            className="w-full h-full object-contain"
          >
            Trình duyệt của bạn không hỗ trợ phát video HTML5.
          </video>
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
