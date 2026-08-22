import React, { useRef, useEffect, useState, useCallback } from 'react';
import { Crop } from 'lucide-react';
import { getMediaUrl } from '../services/api';

function getContentBox(video, canvas) {
  const rect = canvas.getBoundingClientRect();
  const vw = video?.videoWidth || 0;
  const vh = video?.videoHeight || 0;
  if (!vw || !vh || rect.width <= 0 || rect.height <= 0) {
    return { offsetX: 0, offsetY: 0, contentW: rect.width, contentH: rect.height, rect };
  }
  const scale = Math.min(rect.width / vw, rect.height / vh);
  const contentW = vw * scale;
  const contentH = vh * scale;
  return {
    offsetX: (rect.width - contentW) / 2,
    offsetY: (rect.height - contentH) / 2,
    contentW,
    contentH,
    rect,
  };
}

function eventToNorm(e, video, canvas) {
  const { offsetX, offsetY, contentW, contentH, rect } = getContentBox(video, canvas);
  const px = e.clientX - rect.left - offsetX;
  const py = e.clientY - rect.top - offsetY;
  const x = contentW > 0 ? px / contentW : 0;
  const y = contentH > 0 ? py / contentH : 0;
  return {
    x: Math.max(0, Math.min(1, x)),
    y: Math.max(0, Math.min(1, y)),
  };
}

function overlaySrc(ov) {
  if (ov?.url) return getMediaUrl(ov.url);
  return '';
}

function OverlayPreviewLayer({ videoRef, overlays }) {
  const wrapRef = useRef(null);
  const [box, setBox] = useState({ offsetX: 0, offsetY: 0, contentW: 0, contentH: 0 });

  const measure = useCallback(() => {
    const video = videoRef?.current;
    const wrap = wrapRef.current;
    if (!video || !wrap) return;
    const rect = wrap.getBoundingClientRect();
    const vw = video.videoWidth || 0;
    const vh = video.videoHeight || 0;
    if (!vw || !vh || rect.width <= 0 || rect.height <= 0) {
      setBox({ offsetX: 0, offsetY: 0, contentW: rect.width, contentH: rect.height });
      return;
    }
    const scale = Math.min(rect.width / vw, rect.height / vh);
    const contentW = vw * scale;
    const contentH = vh * scale;
    setBox({
      offsetX: (rect.width - contentW) / 2,
      offsetY: (rect.height - contentH) / 2,
      contentW,
      contentH,
    });
  }, [videoRef]);

  useEffect(() => {
    const video = videoRef?.current;
    measure();
    window.addEventListener('resize', measure);
    video?.addEventListener('loadedmetadata', measure);
    return () => {
      window.removeEventListener('resize', measure);
      video?.removeEventListener('loadedmetadata', measure);
    };
  }, [measure, videoRef, overlays]);

  if (!overlays || overlays.length === 0) return null;

  return (
    <div ref={wrapRef} className="absolute inset-0 pointer-events-none z-[9]">
      {overlays.map((ov) => {
        const isFrame = ov.kind === 'frame';
        const src = overlaySrc(ov);
        if (!src) return null;
        const left = isFrame ? box.offsetX : box.offsetX + ov.x * box.contentW;
        const top = isFrame ? box.offsetY : box.offsetY + ov.y * box.contentH;
        const width = isFrame ? box.contentW : ov.w * box.contentW;
        const height = isFrame ? box.contentH : undefined;
        return (
          <img
            key={ov.id || src}
            src={src}
            alt=""
            className={isFrame ? 'absolute object-cover' : 'absolute object-contain'}
            style={{
              left,
              top,
              width,
              height,
              opacity: ov.opacity ?? 1,
              zIndex: isFrame ? 1 : 2,
            }}
          />
        );
      })}
    </div>
  );
}

export function RoiCanvas({ videoRef, onRoiChange, overlays = [] }) {
  const canvasRef = useRef(null);
  const [roi, setRoi] = useState(null); // { x, y, w, h } normalized to VIDEO content (0 to 1)
  const isDraggingRef = useRef(false);
  const startPosRef = useRef({ x: 0, y: 0 });

  const drawCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    const video = videoRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (!roi) return;

    const box = video ? getContentBox(video, canvas) : { offsetX: 0, offsetY: 0, contentW: canvas.width, contentH: canvas.height };
    const pxX = box.offsetX + roi.x * box.contentW;
    const pxY = box.offsetY + roi.y * box.contentH;
    const pxW = roi.w * box.contentW;
    const pxH = roi.h * box.contentH;

    // Mask outside ROI
    ctx.fillStyle = 'rgba(15, 23, 42, 0.4)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.clearRect(pxX, pxY, pxW, pxH);

    // Border around ROI
    ctx.strokeStyle = '#2563eb';
    ctx.lineWidth = 2.5;
    ctx.setLineDash([6, 4]);
    ctx.strokeRect(pxX, pxY, pxW, pxH);

    // Corner handles
    ctx.fillStyle = '#2563eb';
    ctx.setLineDash([]);
    const handleSize = 8;
    const handles = [
      { x: pxX, y: pxY },
      { x: pxX + pxW, y: pxY },
      { x: pxX, y: pxY + pxH },
      { x: pxX + pxW, y: pxY + pxH },
    ];
    handles.forEach((h) => {
      ctx.fillRect(h.x - handleSize / 2, h.y - handleSize / 2, handleSize, handleSize);
    });

    // Label tag
    ctx.fillStyle = '#2563eb';
    ctx.fillRect(pxX, Math.max(0, pxY - 22), 115, 22);
    ctx.fillStyle = '#ffffff';
    ctx.font = 'bold 11px sans-serif';
    ctx.fillText('Vùng Xoá Logo', pxX + 8, Math.max(15, pxY - 7));
  }, [roi, videoRef]);

  const syncCanvasSize = useCallback(() => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;

    const rect = video.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      canvas.width = rect.width;
      canvas.height = rect.height;
      drawCanvas();
    }
  }, [drawCanvas, videoRef]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    video.addEventListener('loadedmetadata', syncCanvasSize);
    window.addEventListener('resize', syncCanvasSize);

    return () => {
      video.removeEventListener('loadedmetadata', syncCanvasSize);
      window.removeEventListener('resize', syncCanvasSize);
    };
  }, [syncCanvasSize, videoRef]);

  useEffect(() => {
    drawCanvas();
  }, [roi, drawCanvas]);

  const handleMouseDown = (e) => {
    const canvas = canvasRef.current;
    const video = videoRef.current;
    if (!canvas) return;
    const pos = eventToNorm(e, video, canvas);
    isDraggingRef.current = true;
    startPosRef.current = pos;
    setRoi({ x: pos.x, y: pos.y, w: 0.01, h: 0.01 });
  };

  const handleMouseMove = (e) => {
    if (!isDraggingRef.current) return;
    const canvas = canvasRef.current;
    const video = videoRef.current;
    if (!canvas) return;
    const current = eventToNorm(e, video, canvas);

    const x = Math.min(startPosRef.current.x, current.x);
    const y = Math.min(startPosRef.current.y, current.y);
    const w = Math.abs(current.x - startPosRef.current.x);
    const h = Math.abs(current.y - startPosRef.current.y);

    const newRoi = { x, y, w, h };
    setRoi(newRoi);
    onRoiChange?.(newRoi);
  };

  const handleMouseUp = () => {
    isDraggingRef.current = false;
  };

  const applyPreset = (type) => {
    let presetRoi = null;
    switch (type) {
      case 'douyin_tl':
        presetRoi = { x: 0.02, y: 0.03, w: 0.28, h: 0.12 };
        break;
      case 'douyin_tr':
        presetRoi = { x: 0.70, y: 0.03, w: 0.28, h: 0.12 };
        break;
      case 'kuaishou_br':
        presetRoi = { x: 0.65, y: 0.85, w: 0.32, h: 0.12 };
        break;
      case 'subtitle_bottom':
        presetRoi = { x: 0.05, y: 0.75, w: 0.90, h: 0.22 };
        break;
      case 'title_center':
        presetRoi = { x: 0.15, y: 0.20, w: 0.70, h: 0.45 };
        break;
      case 'fullscreen_text':
        presetRoi = { x: 0.0, y: 0.0, w: 1.0, h: 1.0 };
        break;
      case 'auto':
      case 'clear':
      default:
        presetRoi = null;
        break;
    }
    setRoi(presetRoi);
    onRoiChange?.(presetRoi);
  };

  return (
    <div className="w-full space-y-4">
      {/* Video Container with Overlay */}
      <div className="relative w-full aspect-video bg-slate-900 rounded-3xl overflow-hidden shadow-md border border-slate-200">
        <video
          ref={videoRef}
          onLoadedMetadata={syncCanvasSize}
          className="w-full h-full object-contain block"
          controls
          crossOrigin="anonymous"
        />
        <OverlayPreviewLayer videoRef={videoRef} overlays={overlays} />
        <canvas
          ref={canvasRef}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          className="absolute top-0 left-0 w-full h-full cursor-crosshair z-10"
        />
      </div>

      {/* Preset Toolbar */}
      <div className="clean-card p-4 rounded-2xl shadow-xs space-y-2.5 text-xs">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Crop className="w-4 h-4 text-blue-600" />
            <span className="font-bold text-slate-800">Khoanh Vùng Xoá Logo & Text:</span>
          </div>
          <span className="text-[11px] text-slate-500 font-medium">Kéo chuột trên video hoặc chọn mẫu nhanh:</span>
        </div>

        <div className="flex items-center gap-2 flex-wrap pt-1">
          <button
            type="button"
            onClick={() => applyPreset('auto')}
            className={`px-3 py-1.5 font-bold rounded-xl transition cursor-pointer ${
              !roi ? 'bg-blue-600 text-white shadow-xs' : 'bg-slate-100 hover:bg-slate-200 text-slate-700'
            }`}
          >
            ✨ Quét Tự Động Toàn Video
          </button>
          <button
            type="button"
            onClick={() => applyPreset('douyin_tl')}
            className="px-3 py-1.5 font-bold bg-pink-50 hover:bg-pink-100 text-pink-700 border border-pink-200 rounded-xl transition cursor-pointer"
          >
            Logo Trái Trên (Douyin/TikTok)
          </button>
          <button
            type="button"
            onClick={() => applyPreset('douyin_tr')}
            className="px-3 py-1.5 font-bold bg-purple-50 hover:bg-purple-100 text-purple-700 border border-purple-200 rounded-xl transition cursor-pointer"
          >
            Logo Phải Trên
          </button>
          <button
            type="button"
            onClick={() => applyPreset('kuaishou_br')}
            className="px-3 py-1.5 font-bold bg-amber-50 hover:bg-amber-100 text-amber-700 border border-amber-200 rounded-xl transition cursor-pointer"
          >
            Logo Phải Dưới (Kuaishou/XHS)
          </button>
          <button
            type="button"
            onClick={() => applyPreset('subtitle_bottom')}
            className="px-3 py-1.5 font-bold bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200 rounded-xl transition cursor-pointer"
          >
            Dải Chữ Subtitle Đáy
          </button>
          <button
            type="button"
            onClick={() => applyPreset('title_center')}
            className="px-3 py-1.5 font-bold bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200 rounded-xl transition cursor-pointer"
          >
            Tiêu Đề Giữa Màn Hình
          </button>
          <button
            type="button"
            onClick={() => applyPreset('fullscreen_text')}
            className="px-3 py-1.5 font-bold bg-teal-50 hover:bg-teal-100 text-teal-700 border border-teal-200 rounded-xl transition cursor-pointer"
          >
            Toàn Khung (Tẩy Chữ Mọi Vị Trí)
          </button>
          {roi && (
            <button
              type="button"
              onClick={() => applyPreset('clear')}
              className="px-3 py-1.5 font-bold bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 rounded-xl transition cursor-pointer"
            >
              Hủy Khoanh Vùng
            </button>
          )}
        </div>
      </div>

      {/* Normalized Readout */}
      {roi && (
        <div className="grid grid-cols-4 gap-3 text-xs font-mono clean-card p-3.5 rounded-2xl shadow-xs">
          <div>
            <span className="text-[10px] text-slate-400 font-bold block">Tọa độ X</span>
            <span className="text-blue-600 font-bold text-sm">{roi.x.toFixed(3)}</span>
          </div>
          <div>
            <span className="text-[10px] text-slate-400 font-bold block">Tọa độ Y</span>
            <span className="text-blue-600 font-bold text-sm">{roi.y.toFixed(3)}</span>
          </div>
          <div>
            <span className="text-[10px] text-slate-400 font-bold block">Độ Rộng W</span>
            <span className="text-blue-600 font-bold text-sm">{roi.w.toFixed(3)}</span>
          </div>
          <div>
            <span className="text-[10px] text-slate-400 font-bold block">Độ Cao H</span>
            <span className="text-blue-600 font-bold text-sm">{roi.h.toFixed(3)}</span>
          </div>
        </div>
      )}
    </div>
  );
}
