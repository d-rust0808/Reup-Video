import React, { useRef, useEffect, useLayoutEffect, useState, useCallback } from 'react';
import { Crop } from 'lucide-react';
import { getMediaUrl } from '../services/api';
import { applyVideoSpeed, captionPlateBox, clampCanvasFill, clampCoverHeight, clampPreviewSpeed, clampSubtitleBoxH, clampSubtitleBoxW, fitKeepIntoCanvas, formatPixelAspect, previewSubtitleY, resolveCanvasFill, stageBoxStyle, subtitleBoxRect } from '../lib/previewCanvas';

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

const COVER_SWATCH = {
  black_soft: { bg: 'rgba(0,0,0,0.62)', fg: '#fff' },
  white_soft: { bg: 'rgba(255,255,255,0.70)', fg: '#111' },
  black_solid: { bg: '#111', fg: '#fff' },
  white_solid: { bg: '#f4f4f4', fg: '#111' },
};

function computeKeepRect(preview = {}) {
  const p = Math.max(0, Math.min(0.2, (Number(preview.cropPercent) || 0) / 100));
  const cropMode = preview.wmMethod === 'crop';
  const isImageCover = preview.captionCover === 'image';
  const coverOn = isImageCover || Boolean(preview.captionCover && preview.captionCover !== 'off');
  const rawBottom = Math.max(0, Math.min(0.45, (Number(preview.bottomCrop) || 0) / 100));
  // Color plates paint the band. Custom logo still crops the hardsub strip.
  const cutBottom = (coverOn && !isImageCover) ? 0 : rawBottom;
  const top = p * (1 - cutBottom);
  const bottom = cutBottom + p * (1 - cutBottom);
  const left = p;
  const right = p;
  let coverH = 0;
  if (coverOn && !isImageCover) {
    coverH = clampCoverHeight(rawBottom);
  }
  return {
    left,
    top,
    right,
    bottom,
    keepW: Math.max(0.2, 1 - left - right),
    keepH: Math.max(0.2, 1 - top - bottom),
    coverOn,
    coverH,
  };
}

function PreviewTransport({ videoRef, speedRatio }) {
  const [playing, setPlaying] = useState(false);
  const [t, setT] = useState(0);
  const [d, setD] = useState(0);
  const rate = clampPreviewSpeed(speedRatio);

  useEffect(() => {
    const video = videoRef?.current;
    if (!video) return undefined;
    const onTime = () => setT(video.currentTime || 0);
    const onMeta = () => setD(video.duration || 0);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    video.addEventListener('timeupdate', onTime);
    video.addEventListener('loadedmetadata', onMeta);
    video.addEventListener('play', onPlay);
    video.addEventListener('pause', onPause);
    onTime();
    onMeta();
    setPlaying(!video.paused);
    return () => {
      video.removeEventListener('timeupdate', onTime);
      video.removeEventListener('loadedmetadata', onMeta);
      video.removeEventListener('play', onPlay);
      video.removeEventListener('pause', onPause);
    };
  }, [videoRef]);

  const toggle = () => {
    const video = videoRef?.current;
    if (!video) return;
    applyVideoSpeed(video, speedRatio);
    if (video.paused) video.play().catch(() => {});
    else video.pause();
  };

  const fmt = (sec) => {
    const n = Math.max(0, Math.floor(sec || 0));
    return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
  };

  return (
    <div className="flex items-center gap-2 px-3 py-2 bg-slate-950/90 text-white">
      <button
        type="button"
        onClick={toggle}
        className="shrink-0 w-8 h-8 rounded-lg bg-white/15 hover:bg-white/25 text-xs font-black"
      >
        {playing ? '❚❚' : '▶'}
      </button>
      <input
        type="range"
        min="0"
        max={d > 0 ? d : 0}
        step="0.05"
        value={Math.min(t, d || 0)}
        onChange={(e) => {
          const video = videoRef?.current;
          if (!video) return;
          video.currentTime = Number(e.target.value);
        }}
        className="flex-1 accent-blue-400"
      />
      <span className="text-[10px] font-mono text-white/80 w-20 text-right">
        {fmt(t)}/{fmt(d)}
      </span>
      {Math.abs(rate - 1) > 0.009 && (
        <span className="text-[10px] font-black text-amber-300 whitespace-nowrap">
          {rate.toFixed(2)}x → {fmt(d / rate)}
        </span>
      )}
    </div>
  );
}

function OverlayPreviewLayer({ videoRef, overlays, contained = false }) {
  const wrapRef = useRef(null);
  const [box, setBox] = useState({ offsetX: 0, offsetY: 0, contentW: 0, contentH: 0 });

  const measure = useCallback(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    if (contained) {
      setBox({ offsetX: 0, offsetY: 0, contentW: rect.width, contentH: rect.height });
      return;
    }
    const video = videoRef?.current;
    const vw = video?.videoWidth || 0;
    const vh = video?.videoHeight || 0;
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
  }, [videoRef, contained]);

  useEffect(() => {
    const video = videoRef?.current;
    measure();
    window.addEventListener('resize', measure);
    video?.addEventListener('loadedmetadata', measure);
    const wrap = wrapRef.current;
    const ro = typeof ResizeObserver === 'function' && wrap ? new ResizeObserver(measure) : null;
    if (wrap) ro?.observe(wrap);
    return () => {
      window.removeEventListener('resize', measure);
      video?.removeEventListener('loadedmetadata', measure);
      ro?.disconnect();
    };
  }, [measure, videoRef, overlays, contained]);

  if (!overlays || overlays.length === 0) return null;

  return (
    <div ref={wrapRef} className="absolute inset-0 pointer-events-none z-[9]">
      {overlays.map((ov) => {
        const isFrame = ov.kind === 'frame';
        const isBanner = ov.kind === 'banner' || ov.kind === 'caption';
        const src = overlaySrc(ov);
        if (!src) return null;
        const bandH = Math.max(0.10, Math.min(0.36, Number(ov.band_h || ov.h || 0.22)));
        const left = (isFrame || isBanner) ? box.offsetX : box.offsetX + ov.x * box.contentW;
        const width = (isFrame || isBanner) ? box.contentW : ov.w * box.contentW;
        const height = isFrame ? box.contentH : (isBanner ? bandH * box.contentH : undefined);
        const top = isFrame
          ? box.offsetY
          : (isBanner ? box.offsetY + (1 - bandH) * box.contentH : box.offsetY + ov.y * box.contentH);
        return (
          <img
            key={ov.id || src}
            src={src}
            alt=""
            className={isFrame || isBanner ? 'absolute object-cover' : 'absolute object-contain'}
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

export function RoiCanvas({ videoRef, onRoiChange, overlays = [], simple = true, preview = null, onCanvasAspect, onCanvasFill, onSubtitleY, onSubtitleBoxW, onSubtitleBoxH }) {
  const canvasRef = useRef(null);
  const stageRef = useRef(null);
  const [roi, setRoi] = useState(null); // { x, y, w, h } normalized to VIDEO content (0 to 1)
  const [viewMode, setViewMode] = useState('after');
  const [box, setBox] = useState({ offsetX: 0, offsetY: 0, contentW: 0, contentH: 0, videoW: 0, videoH: 0, plateH: 0, bottomPad: 0 });
  const [srcSize, setSrcSize] = useState({ w: 0, h: 0 });
  const isDraggingRef = useRef(false);
  const startPosRef = useRef({ x: 0, y: 0 });
  const keep = computeKeepRect(preview || {});
  const showAfter = simple && viewMode === 'after';
  const canvasAspect = preview?.canvasAspect || null;
  const coverKey = preview?.captionCover === 'white_black' ? 'white_solid' : preview?.captionCover;
  const coverStyle = COVER_SWATCH[coverKey] || null;
  const cropPercent = Number(preview?.cropPercent) || 0;
  const bottomCrop = Number(preview?.bottomCrop) || 0;
  const captionCover = preview?.captionCover || 'off';
  const wmMethod = preview?.wmMethod;
  const speedRatio = preview?.speedRatio;
  const canvasFill = clampCanvasFill(preview?.canvasFill);
  const fillDragRef = useRef(null);
  const subDragRef = useRef(null);
  const coverSrc = preview?.captionCover === 'image'
    ? (preview?.captionCoverUrl
      || overlaySrc((overlays || []).find((ov) => ov.kind === 'banner' || ov.kind === 'caption'))
      || '')
    : '';
  const [logoAspect, setLogoAspect] = useState(0);

  useEffect(() => {
    if (!coverSrc) {
      setLogoAspect(0);
      return undefined;
    }
    const img = new Image();
    img.onload = () => {
      const w = img.naturalWidth || 0;
      const h = img.naturalHeight || 0;
      setLogoAspect(w > 0 && h > 0 ? w / h : 0);
    };
    img.onerror = () => setLogoAspect(0);
    img.src = coverSrc;
    return () => {
      img.onload = null;
      img.onerror = null;
    };
  }, [coverSrc]);

  useEffect(() => {
    const video = videoRef?.current;
    if (!video) return undefined;
    const apply = () => applyVideoSpeed(video, speedRatio);
    apply();
    video.addEventListener('loadedmetadata', apply);
    video.addEventListener('play', apply);
    video.addEventListener('playing', apply);
    const onRate = () => {
      const wanted = clampPreviewSpeed(speedRatio);
      if (Math.abs((Number(video.playbackRate) || 0) - wanted) > 0.009) {
        apply();
      }
    };
    video.addEventListener('ratechange', onRate);
    return () => {
      video.removeEventListener('loadedmetadata', apply);
      video.removeEventListener('play', apply);
      video.removeEventListener('playing', apply);
      video.removeEventListener('ratechange', onRate);
    };
  }, [videoRef, speedRatio]);

  const measureStage = useCallback(() => {
    const video = videoRef.current;
    const stage = stageRef.current;
    if (!video || !stage) return;
    const rect = stage.getBoundingClientRect();
    const vw = video.videoWidth || 0;
    const vh = video.videoHeight || 0;
    if (vw && vh) setSrcSize((prev) => (prev.w === vw && prev.h === vh ? prev : { w: vw, h: vh }));
    if (!vw || !vh || rect.width <= 0 || rect.height <= 0) return;
    const chrome = showAfter ? 48 : 0;
    const fitW = rect.width;
    const fitH = Math.max(1, rect.height - chrome);
    const keepNow = computeKeepRect({
      cropPercent,
      bottomCrop,
      captionCover,
      wmMethod,
    });
    const useCanvas = showAfter && (canvasAspect === '16:9' || canvasAspect === '9:16');
    if (useCanvas) {
      const fill = resolveCanvasFill(canvasFill, captionCover === 'image', logoAspect);
      const fit = fitKeepIntoCanvas(keepNow, vw, vh, fitW, fitH, fill, logoAspect);
      setBox({
        offsetX: fit.padX,
        offsetY: fit.padY,
        contentW: fit.fittedW,
        contentH: fit.fittedH,
        videoW: fit.videoW,
        videoH: fit.videoH,
        plateH: fit.plateH || 0,
        bottomPad: fit.bottomPad || 0,
      });
      return;
    }
    const scale = Math.min(fitW / vw, fitH / vh);
    const contentW = vw * scale;
    const contentH = vh * scale;
    setBox({
      offsetX: (fitW - contentW) / 2,
      offsetY: (fitH - contentH) / 2,
      contentW,
      contentH,
      videoW: contentW,
      videoH: contentH,
    });
  }, [videoRef, showAfter, canvasAspect, cropPercent, bottomCrop, captionCover, wmMethod, canvasFill, logoAspect]);

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

    const onMeta = () => {
      syncCanvasSize();
      measureStage();
    };
    video.addEventListener('loadedmetadata', onMeta);
    window.addEventListener('resize', onMeta);
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(onMeta) : null;
    if (stageRef.current) ro?.observe(stageRef.current);
    onMeta();

    return () => {
      video.removeEventListener('loadedmetadata', onMeta);
      window.removeEventListener('resize', onMeta);
      ro?.disconnect();
    };
  }, [syncCanvasSize, measureStage, videoRef]);

  useEffect(() => {
    drawCanvas();
  }, [roi, drawCanvas]);

  useLayoutEffect(() => {
    measureStage();
  }, [measureStage]);

  const canStretchHeight = Boolean(
    showAfter && canvasAspect === '9:16' && srcSize.w > 0 && srcSize.h > 0 && srcSize.w >= srcSize.h,
  );

  useEffect(() => {
    if (!canStretchHeight) return undefined;
    const onMove = (ev) => {
      const drag = fillDragRef.current;
      const stage = stageRef.current;
      const video = videoRef?.current;
      if (!drag || !stage || !video) return;
      const rect = stage.getBoundingClientRect();
      const keepNow = computeKeepRect({
        cropPercent,
        bottomCrop,
        captionCover,
        wmMethod,
      });
      const contain = fitKeepIntoCanvas(
        keepNow, video.videoWidth, video.videoHeight, rect.width, rect.height, 0, logoAspect,
      );
      const maxFit = fitKeepIntoCanvas(
        keepNow, video.videoWidth, video.videoHeight, rect.width, rect.height, 1, logoAspect,
      );
      const videoBottom = contain.padY + contain.fittedH;
      const room = Math.max(1, maxFit.plateH || (rect.height - videoBottom));
      const y = ev.clientY - rect.top;
      onCanvasFill?.(clampCanvasFill((y - videoBottom) / room));
    };
    const onUp = () => {
      fillDragRef.current = null;
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  }, [canStretchHeight, cropPercent, bottomCrop, captionCover, wmMethod, videoRef, onCanvasFill, logoAspect]);

  useEffect(() => {
    if (!showAfter) return undefined;
    const onMove = (ev) => {
      const mode = subDragRef.current;
      if (!mode) return;
      const stage = stageRef.current;
      const video = videoRef?.current;
      if (!stage || !video) return;
      const rect = stage.getBoundingClientRect();
      const vw = video.videoWidth || 0;
      const vh = video.videoHeight || 0;
      if (!vw || !vh || rect.height <= 0) return;
      const chrome = 48;
      const fitW = rect.width;
      const fitH = Math.max(1, rect.height - chrome);
      const keepNow = computeKeepRect({
        cropPercent,
        bottomCrop,
        captionCover,
        wmMethod,
      });
      const useCanvas = canvasAspect === '16:9' || canvasAspect === '9:16';
      let top = 0;
      let height = fitH;
      let left = 0;
      let width = fitW;
      if (useCanvas) {
        const fit = fitKeepIntoCanvas(
          keepNow, vw, vh, fitW, fitH,
          resolveCanvasFill(canvasFill, captionCover === 'image', logoAspect),
          logoAspect,
        );
        top = fit.padY;
        height = fit.fittedH;
        left = fit.padX;
        width = fit.fittedW;
      } else {
        const scale = Math.min(fitW / vw, fitH / vh);
        height = vh * scale;
        width = vw * scale;
        top = (fitH - height) / 2;
        left = (fitW - width) / 2;
      }
      if (height < 8 || width < 8) return;
      const y = Math.max(0.04, Math.min(0.96, (ev.clientY - rect.top - top) / height));
      const x = Math.max(0, Math.min(1, (ev.clientX - rect.left - left) / width));
      if (mode === 'move') {
        onSubtitleY?.(y);
        return;
      }
      if (mode === 'n' || mode === 's') {
        const cy = previewSubtitleY(preview?.subtitleY, keepNow.coverH, keepNow.coverOn);
        onSubtitleBoxH?.(clampSubtitleBoxH(Math.abs(y - cy) * 2));
        return;
      }
      if (mode === 'w' || mode === 'e') {
        onSubtitleBoxW?.(clampSubtitleBoxW(Math.abs(x - 0.5) * 2));
      }
    };
    const onUp = () => {
      subDragRef.current = null;
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  }, [showAfter, cropPercent, bottomCrop, captionCover, wmMethod, canvasAspect, canvasFill, logoAspect, videoRef, onSubtitleY, onSubtitleBoxW, onSubtitleBoxH, preview?.subtitleY]);

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

  const b = Number(preview?.brightness) || 0;
  const c = Number(preview?.contrast) || 1;
  const sat = Number(preview?.saturation) || 1;
  const afterFilter = showAfter
    ? `brightness(${1 + b}) contrast(${c}) saturate(${sat})`
    : undefined;
  const afterFlip = showAfter && preview?.hflip ? 'scaleX(-1)' : undefined;
  const afterLayout = Boolean(showAfter && canvasAspect && box.contentW > 0 && box.videoW > 0);
  const keepPx = afterLayout
    ? {
        left: box.offsetX,
        top: box.offsetY,
        width: box.contentW,
        height: box.contentH,
      }
    : {
        left: box.offsetX + keep.left * box.contentW,
        top: box.offsetY + keep.top * box.contentH,
        width: keep.keepW * box.contentW,
        height: keep.keepH * box.contentH,
      };
  const plateH = afterLayout ? (box.plateH || 0) : 0;
  const bottomPad = afterLayout ? (box.bottomPad || 0) : 0;
  const plateBannerOv = (overlays || []).find((ov) => ov.kind === 'banner' || ov.kind === 'caption');
  const plateBannerSrc = preview?.captionCover === 'image'
    ? (preview?.captionCoverUrl || overlaySrc(plateBannerOv))
    : '';
  const plateLogo = (plateH > 0.5 || bottomPad > 0.5) && Boolean(plateBannerSrc);
  const coverBox = captionPlateBox(
    afterLayout
      ? {
          offsetX: box.offsetX,
          offsetY: box.offsetY,
          contentW: box.contentW,
          contentH: box.contentH,
          plateH,
          bottomPad,
        }
      : {
          offsetX: keepPx.left,
          offsetY: keepPx.top,
          contentW: keepPx.width,
          contentH: keepPx.height,
          plateH: 0,
        },
    keep.coverH,
    { image: Boolean(plateBannerSrc), logoAspect },
  );
  if (afterLayout && coverBox.overlap === false && coverBox.height > 0.5) {
    keepPx.height += coverBox.height;
  } else if (afterLayout && plateH) {
    keepPx.height += plateH;
  }
  const pictureBox = afterLayout
    ? {
        offsetX: box.offsetX,
        offsetY: box.offsetY,
        contentW: box.contentW,
        contentH: box.contentH,
      }
    : {
        offsetX: keepPx.left,
        offsetY: keepPx.top,
        contentW: keepPx.width,
        contentH: keepPx.height,
      };
  const subBox = subtitleBoxRect(
    pictureBox,
    preview?.subtitleY,
    preview?.subtitleBoxW,
    preview?.subtitleBoxH,
    keep.coverH,
    keep.coverOn,
  );
  const videoOverlays = (overlays || []).filter((ov) => {
    const isBanner = ov.kind === 'banner' || ov.kind === 'caption';
    if (!isBanner) return true;
    if (plateLogo || preview?.captionCover === 'image') return false;
    return true;
  });
  const srcLabel = formatPixelAspect(srcSize.w, srcSize.h);
  const stageStyle = stageBoxStyle(showAfter, canvasAspect, srcSize.w, srcSize.h);

  return (
    <div className="w-full space-y-4">
      {simple && (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 sm:gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex rounded-xl border border-slate-200 bg-slate-50 p-0.5 shrink-0">
              <button
                type="button"
                onClick={() => setViewMode('before')}
                className={`px-3 py-1.5 text-[11px] font-bold rounded-lg ${
                  viewMode === 'before' ? 'bg-white text-slate-900 shadow-xs' : 'text-slate-500'
                }`}
              >
                Gốc
              </button>
              <button
                type="button"
                onClick={() => setViewMode('after')}
                className={`px-3 py-1.5 text-[11px] font-bold rounded-lg ${
                  viewMode === 'after' ? 'bg-blue-600 text-white shadow-xs' : 'text-slate-500'
                }`}
              >
                Sau crop / config
              </button>
            </div>
            {showAfter && preview?.hasVertical && preview?.hasHorizontal && (
              <div className="inline-flex rounded-xl border border-blue-200 bg-blue-50 p-0.5 shrink-0">
                <button
                  type="button"
                  onClick={() => onCanvasAspect?.('9:16')}
                  className={`px-2.5 py-1.5 text-[11px] font-black rounded-lg ${
                    canvasAspect === '9:16' ? 'bg-blue-600 text-white shadow-xs' : 'text-slate-500'
                  }`}
                >
                  9:16
                </button>
                <button
                  type="button"
                  onClick={() => onCanvasAspect?.('16:9')}
                  className={`px-2.5 py-1.5 text-[11px] font-black rounded-lg ${
                    canvasAspect === '16:9' ? 'bg-blue-600 text-white shadow-xs' : 'text-slate-500'
                  }`}
                >
                  16:9
                </button>
              </div>
            )}
          </div>
          <p className="text-[11px] text-slate-500 font-medium min-w-0 sm:truncate">
            {showAfter
              ? `Khung xuất ${canvasAspect || srcLabel || 'nguồn'} sau cắt mép, cắt đáy, lật, màu và phủ chữ.`
              : 'Video gốc. Vùng xám = phần sẽ bị cắt.'}
          </p>
        </div>
      )}

      <div
        ref={stageRef}
        className="relative bg-slate-950 rounded-3xl overflow-hidden shadow-md border border-slate-200"
        style={stageStyle}
      >
        <div
          className={`absolute overflow-hidden ${afterLayout ? '' : 'inset-0'}`}
          style={afterLayout
            ? {
                left: box.offsetX,
                top: box.offsetY,
                width: box.contentW,
                height: box.contentH,
              }
            : undefined}
        >
          <video
            ref={videoRef}
            onLoadedMetadata={() => {
              syncCanvasSize();
              measureStage();
            }}
            className={afterLayout
              ? 'absolute max-w-none origin-center'
              : 'w-full h-full object-contain block origin-center'}
            controls={!showAfter}
            crossOrigin="anonymous"
            style={afterLayout
              ? {
                  left: -keep.left * box.videoW,
                  top: -keep.top * box.videoH,
                  width: box.videoW,
                  height: box.videoH,
                  objectFit: 'fill',
                  filter: afterFilter,
                  transform: afterFlip,
                }
              : {
                  filter: afterFilter,
                  transform: afterFlip,
                }}
          />
          <OverlayPreviewLayer videoRef={videoRef} overlays={videoOverlays} contained={afterLayout} />
        </div>

        {simple && box.contentW > 0 && !afterLayout && (
          <>
            <div
              className="absolute pointer-events-none z-[8] bg-slate-950/55"
              style={{ left: 0, top: 0, right: 0, height: keepPx.top }}
            />
            <div
              className="absolute pointer-events-none z-[8] bg-slate-950/55"
              style={{
                left: 0,
                top: keepPx.top + keepPx.height,
                right: 0,
                bottom: 0,
              }}
            />
            <div
              className="absolute pointer-events-none z-[8] bg-slate-950/55"
              style={{
                left: 0,
                top: keepPx.top,
                width: keepPx.left,
                height: keepPx.height,
              }}
            />
            <div
              className="absolute pointer-events-none z-[8] bg-slate-950/55"
              style={{
                left: keepPx.left + keepPx.width,
                top: keepPx.top,
                right: 0,
                height: keepPx.height,
              }}
            />
          </>
        )}

        {simple && box.contentW > 0 && (
          <div
            className="absolute z-[9] border-2 border-dashed border-sky-300/90"
            style={{
              left: keepPx.left,
              top: keepPx.top,
              width: keepPx.width,
              height: keepPx.height,
              pointerEvents: showAfter ? 'auto' : 'none',
            }}
            onClick={() => {
              if (!showAfter) return;
              const video = videoRef.current;
              if (!video) return;
              if (video.paused) video.play().catch(() => {});
              else video.pause();
            }}
          >
            {!afterLayout && (
              <span className="absolute left-2 top-2 rounded bg-sky-600 px-1.5 py-0.5 text-[10px] font-black text-white pointer-events-none">
                Khung sau crop
              </span>
            )}
            {canStretchHeight && (
              <>
                <button
                  type="button"
                  aria-label="Kéo dài phần logo phía dưới"
                  onPointerDown={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    fillDragRef.current = { edge: 'bottom' };
                  }}
                  className="absolute left-1/2 -bottom-1.5 z-20 h-3 w-14 -translate-x-1/2 cursor-ns-resize rounded-full border border-white/80 bg-blue-500 shadow"
                />
                <span className="absolute left-1/2 bottom-3 -translate-x-1/2 rounded bg-black/70 px-1.5 py-0.5 text-[10px] font-black text-white pointer-events-none">
                  {canvasFill < 0.02 ? 'Kéo dài ô logo' : `Ô logo +${Math.round(canvasFill * 100)}%`}
                </span>
              </>
            )}
          </div>
        )}

        {showAfter && (
          <div className="absolute top-2 left-2 z-30 flex flex-wrap items-center gap-1 pointer-events-none max-w-[calc(100%-1rem)]">
            {canvasAspect ? (
              <span className="rounded-md bg-blue-600 px-1.5 py-0.5 text-[10px] font-black text-white shadow">
                {canvasAspect}
              </span>
            ) : null}
            {srcLabel ? (
              <span className="rounded-md bg-black/65 px-1.5 py-0.5 text-[10px] font-bold text-white">
                {canvasAspect && srcLabel !== canvasAspect
                  ? `Gốc ${srcLabel} → fit ${canvasAspect}`
                  : `Gốc ${srcLabel}`}
              </span>
            ) : null}
          </div>
        )}

        {simple && showAfter && box.contentW > 0 && coverBox.height > 0.5 && (plateLogo || (keep.coverOn && (coverStyle || plateBannerSrc))) && (
          <>
            <div
              className="absolute pointer-events-none z-[21] overflow-hidden border-t border-white/40"
              style={{
                left: coverBox.left,
                width: coverBox.width,
                height: Math.max(coverBox.height, 36),
                top: coverBox.top,
                background: plateBannerSrc ? '#111' : (coverStyle?.bg || '#111'),
              }}
            >
              {plateBannerSrc ? (
                <img
                  src={plateBannerSrc}
                  alt=""
                  className="absolute inset-0 w-full h-full object-cover object-top"
                />
              ) : (
                <span
                  className="absolute inset-x-2 top-1.5 text-center text-[10px] font-black leading-tight"
                  style={{ color: coverStyle?.fg || '#fff' }}
                >
                  Phủ {coverKey === 'white_solid' || coverKey === 'white_soft' ? 'trắng' : 'đen'} đáy
                </span>
              )}
            </div>
          </>
        )}

        {showAfter && box.contentW > 0 && (
          <div
            className="absolute z-[22] touch-none"
            style={{
              left: subBox.left,
              top: subBox.top,
              width: Math.max(36, subBox.width),
              height: Math.max(subBox.plateOff ? 22 : 28, subBox.height),
              background: subBox.plateOff ? 'transparent' : (coverStyle?.bg || 'rgba(0,0,0,0.82)'),
              color: subBox.plateOff ? '#fff' : (coverStyle?.fg || '#fff'),
              border: subBox.plateOff
                ? '1.5px dashed rgba(255,255,255,0.8)'
                : (coverStyle?.fg === '#111' ? '1px solid rgba(0,0,0,0.25)' : '1px solid rgba(255,255,255,0.35)'),
              borderRadius: 8,
              boxShadow: subBox.plateOff ? 'none' : '0 1px 8px rgba(0,0,0,0.25)',
            }}
          >
            <button
              type="button"
              aria-label="Kéo vị trí Vietsub"
              onPointerDown={(e) => {
                e.preventDefault();
                e.stopPropagation();
                subDragRef.current = 'move';
              }}
              className="absolute inset-0 cursor-ns-resize bg-transparent"
            />
            <span className="absolute inset-x-2 top-1/2 -translate-y-1/2 text-center text-[10px] font-black leading-tight pointer-events-none">
              {subBox.plateOff ? 'Vietsub · không nền' : 'Vietsub · kéo / mép đổi size'}
            </span>
            <button
              type="button"
              aria-label="Đổi chiều cao nền Vietsub"
              onPointerDown={(e) => {
                e.preventDefault();
                e.stopPropagation();
                subDragRef.current = 'n';
              }}
              className="absolute left-1/2 top-0 z-10 h-2.5 w-14 -translate-x-1/2 -translate-y-1/2 cursor-ns-resize rounded-full border border-white/80 bg-sky-400 shadow"
            />
            <button
              type="button"
              aria-label="Đổi chiều cao nền Vietsub"
              onPointerDown={(e) => {
                e.preventDefault();
                e.stopPropagation();
                subDragRef.current = 's';
              }}
              className="absolute left-1/2 bottom-0 z-10 h-2.5 w-14 -translate-x-1/2 translate-y-1/2 cursor-ns-resize rounded-full border border-white/80 bg-sky-400 shadow"
            />
            <button
              type="button"
              aria-label="Đổi chiều rộng nền Vietsub"
              onPointerDown={(e) => {
                e.preventDefault();
                e.stopPropagation();
                subDragRef.current = 'w';
              }}
              className="absolute left-0 top-1/2 z-10 h-10 w-2.5 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize rounded-full border border-white/80 bg-sky-400 shadow"
            />
            <button
              type="button"
              aria-label="Đổi chiều rộng nền Vietsub"
              onPointerDown={(e) => {
                e.preventDefault();
                e.stopPropagation();
                subDragRef.current = 'e';
              }}
              className="absolute right-0 top-1/2 z-10 h-10 w-2.5 translate-x-1/2 -translate-y-1/2 cursor-ew-resize rounded-full border border-white/80 bg-sky-400 shadow"
            />
          </div>
        )}

        <canvas
          ref={canvasRef}
          onMouseDown={simple ? undefined : handleMouseDown}
          onMouseMove={simple ? undefined : handleMouseMove}
          onMouseUp={simple ? undefined : handleMouseUp}
          onMouseLeave={simple ? undefined : handleMouseUp}
          className={`absolute top-0 left-0 w-full h-full z-10 ${simple ? 'pointer-events-none' : 'cursor-crosshair'}`}
        />
        {showAfter && (
          <div className="absolute left-0 right-0 bottom-0 z-20">
            <PreviewTransport videoRef={videoRef} speedRatio={speedRatio} />
          </div>
        )}
      </div>

      {simple ? (
        <p className="text-[11px] text-slate-500 font-medium px-1">
          {showAfter
            ? `Khung ${canvasAspect || srcLabel || 'nguồn'} · crop mép ${Number(preview?.cropPercent || 0).toFixed(1)}%`
              + (preview?.captionCover === 'image'
                ? ` · cắt đáy ${Number(preview?.bottomCrop || 0).toFixed(0)}% · logo dưới ảnh`
                : (keep.coverOn
                  ? (keep.coverH <= 0 ? ' · tắt phủ đáy' : ` · phủ ${(keep.coverH * 100).toFixed(0)}% đáy`)
                  : ` · cắt đáy ${Number(preview?.bottomCrop || 0).toFixed(0)}%`))
              + (subBox.plateOff ? ' · Vietsub không nền' : ' · kéo Vietsub / kéo mép để phủ chữ gốc')
              + (preview?.hflip ? ' · lật ngang' : '')
              + (Number(preview?.speedRatio) && Math.abs(Number(preview.speedRatio) - 1) > 0.009
                ? ` · tốc độ ${Number(preview.speedRatio).toFixed(2)}x`
                : '')
              + (canStretchHeight
                ? (canvasFill < 0.02
                  ? ' · kéo chấm xanh dưới để hiện đủ logo dưới ảnh (khít ảnh, không dư đen)'
                  : ` · ô logo dưới ảnh +${Math.round(canvasFill * 100)}%`)
                : '')
              + '.'
            : 'Video gốc. Kéo slider crop / phủ chữ để xem khung sẽ ra.'}
        </p>
      ) : (
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
      )}
    </div>
  );
}
