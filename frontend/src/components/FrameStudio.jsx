import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Frame, ImagePlus, Loader2, Move, Trash2, Upload, ZoomIn } from 'lucide-react';
import { getMediaUrl, uploadCustomFrame } from '../services/api';
import { loadSession, saveSession } from '../services/session';

const FRAME_LAYER_ID = 'frame_custom';

function isFrameLayer(item) {
  return item?.id === FRAME_LAYER_ID || item?.kind === 'frame';
}

function OverlayPreview({ overlay, onChange }) {
  const stageRef = useRef(null);
  const transformRef = useRef(overlay);
  const [selected, setSelected] = useState(true);

  useEffect(() => {
    transformRef.current = overlay;
    setSelected(!!overlay);
  }, [overlay]);

  const update = useCallback((next) => {
    transformRef.current = next;
    onChange(next);
  }, [onChange]);

  const startMove = (event) => {
    event.preventDefault();
    event.stopPropagation();
    setSelected(true);
    const stage = stageRef.current;
    if (!stage || !transformRef.current) return;
    const rect = stage.getBoundingClientRect();
    const start = transformRef.current;
    const startX = event.clientX;
    const startY = event.clientY;
    const move = (nextEvent) => update({
      ...start,
      x: Math.max(-2, Math.min(1, start.x + (nextEvent.clientX - startX) / rect.width)),
      y: Math.max(-2, Math.min(1, start.y + (nextEvent.clientY - startY) / rect.height)),
    });
    const end = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
  };

  const startResize = (event) => {
    event.preventDefault();
    event.stopPropagation();
    const stage = stageRef.current;
    if (!stage || !transformRef.current) return;
    const start = transformRef.current;
    const startX = event.clientX;
    const stageWidth = stage.getBoundingClientRect().width;
    const move = (nextEvent) => update({
      ...start,
      w: Math.max(0.04, Math.min(3, start.w + (nextEvent.clientX - startX) / stageWidth)),
    });
    const end = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
  };

  return (
    <div ref={stageRef} className="absolute inset-0" onPointerDown={() => setSelected(false)}>
      {overlay && (
        <div
          className={`absolute touch-none ${selected ? 'ring-2 ring-blue-500 ring-offset-1 ring-offset-transparent' : ''}`}
          style={{ left: `${overlay.x * 100}%`, top: `${overlay.y * 100}%`, width: `${overlay.w * 100}%` }}
          onPointerDown={startMove}
        >
          <img src={getMediaUrl(overlay.url)} alt="Lớp phủ" className="block w-full h-auto max-w-none select-none" draggable={false} />
          {selected && (
            <button
              type="button"
              aria-label="Kéo để đổi kích thước"
              title="Kéo để phóng to hoặc thu nhỏ"
              className="absolute -bottom-2.5 -right-2.5 w-6 h-6 rounded-full bg-white border-2 border-blue-600 shadow flex items-center justify-center cursor-se-resize"
              onPointerDown={startResize}
            >
              <ZoomIn className="w-3 h-3 text-blue-600" />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export function FrameStudio() {
  const boot = loadSession().frameStudio || {};
  const inputRef = useRef(null);
  const [overlay, setOverlay] = useState(boot.overlay || null);
  const [draggingFile, setDraggingFile] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const persist = useCallback((nextOverlay) => {
    const previous = loadSession().workbenchOptions || {};
    const others = (previous.overlays || []).filter((item) => !isFrameLayer(item));
    saveSession({
      frameStudio: { enabled: !!nextOverlay, overlay: nextOverlay },
      workbenchOptions: { ...previous, frame_enabled: false, overlays: nextOverlay ? [nextOverlay, ...others] : others },
    });
  }, []);

  const handleTransformChange = useCallback((nextOverlay) => {
    setOverlay(nextOverlay);
    persist(nextOverlay);
  }, [persist]);

  const handleUpload = async (file) => {
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      setMsg({ type: 'err', text: 'Vui lòng chọn file ảnh PNG, JPG hoặc WebP.' });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const result = await uploadCustomFrame(file);
      const nextOverlay = { ...result.overlay, id: FRAME_LAYER_ID, kind: 'overlay', x: 0, y: 0, w: 1 };
      setOverlay(nextOverlay);
      persist(nextOverlay);
      setMsg({ type: 'ok', text: 'Đã thêm ảnh. Kéo ảnh để di chuyển, kéo nút ở góc để đổi kích thước.' });
    } catch (error) {
      setMsg({ type: 'err', text: error.message || 'Không tải được ảnh.' });
    } finally {
      setBusy(false);
    }
  };

  const removeOverlay = () => {
    setOverlay(null);
    persist(null);
    setMsg({ type: 'ok', text: 'Đã xóa lớp phủ khỏi video.' });
  };

  const onDrop = (event) => {
    event.preventDefault();
    setDraggingFile(false);
    handleUpload(event.dataTransfer.files?.[0]);
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-black text-slate-900 flex items-center gap-2">
          <Frame className="w-5 h-5 text-blue-600" /> Lớp phủ video
        </h2>
        <p className="text-xs text-slate-500 font-medium mt-1">
          Tải ảnh lên rồi kéo trực tiếp trên màn hình để đặt đúng vị trí. Ảnh sẽ được phủ xuyên suốt clip.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-7 items-start">
        <div className="lg:col-span-5 flex justify-center">
          <div className="relative w-[230px] aspect-[9/16] rounded-[1.6rem] overflow-hidden bg-slate-900 shadow-xl ring-4 ring-slate-900 select-none">
            <div className="absolute inset-0 bg-gradient-to-br from-slate-700 via-slate-800 to-slate-950" />
            <div className="absolute inset-0 flex flex-col items-center justify-center text-white/80 px-5 text-center pointer-events-none">
              {overlay ? <Move className="w-7 h-7 mb-2 text-blue-300" /> : <ImagePlus className="w-7 h-7 mb-2 text-amber-300" />}
              <p className="text-[11px] font-extrabold">{overlay ? 'Kéo ảnh để chỉnh vị trí' : 'Xem trước video 9:16'}</p>
              <p className="text-[10px] text-white/55 mt-1">{overlay ? 'Kéo nút góc để đổi kích thước' : 'Ảnh tải lên sẽ xuất hiện tại đây'}</p>
            </div>
            <OverlayPreview overlay={overlay} onChange={handleTransformChange} />
          </div>
        </div>

        <div className="lg:col-span-7 space-y-4">
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            onDragEnter={(event) => { event.preventDefault(); setDraggingFile(true); }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={() => setDraggingFile(false)}
            onDrop={onDrop}
            disabled={busy}
            className={`w-full min-h-52 rounded-3xl border-2 border-dashed px-6 py-8 flex flex-col items-center justify-center text-center transition cursor-pointer disabled:opacity-60 ${draggingFile ? 'border-blue-500 bg-blue-50' : 'border-slate-300 bg-white hover:border-blue-400 hover:bg-blue-50/40'}`}
          >
            {busy ? <Loader2 className="w-9 h-9 text-blue-600 animate-spin" /> : <Upload className="w-9 h-9 text-blue-600" />}
            <span className="mt-3 text-sm font-black text-slate-900">{overlay ? 'Thay ảnh lớp phủ' : 'Tải ảnh lớp phủ lên'}</span>
            <span className="mt-1 text-xs font-medium text-slate-500">Kéo thả ảnh vào đây hoặc bấm để chọn file</span>
            <span className="mt-3 text-[10px] font-bold text-slate-400 uppercase tracking-wider">PNG trong suốt · JPG · WebP</span>
          </button>
          <input ref={inputRef} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={(event) => { handleUpload(event.target.files?.[0]); event.target.value = ''; }} />

          {overlay && (
            <div className="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-3">
              <div className="min-w-0">
                <p className="text-xs font-extrabold text-slate-800 truncate">{overlay.filename || 'Ảnh lớp phủ'}</p>
                <p className="text-[10px] text-slate-500 mt-0.5">Đã sẵn sàng để phủ lên video</p>
              </div>
              <button type="button" onClick={removeOverlay} className="shrink-0 inline-flex items-center gap-1.5 text-xs font-bold text-rose-600 hover:bg-rose-50 px-3 py-2 rounded-xl cursor-pointer">
                <Trash2 className="w-4 h-4" /> Xóa ảnh
              </button>
            </div>
          )}

          {msg && <p className={`text-xs font-bold ${msg.type === 'ok' ? 'text-emerald-700' : 'text-rose-600'}`}>{msg.text}</p>}
        </div>
      </div>
    </div>
  );
}
