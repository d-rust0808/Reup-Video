import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ImagePlus,
  Frame,
  Move,
  Trash2,
  Loader2,
  Check,
  Layers,
  Upload,
  Maximize2,
} from 'lucide-react';
import {
  uploadChannelOverlay,
  saveChannelOverlays,
  deleteChannelOverlay,
  getMediaUrl,
} from '../services/api';

const PRESETS = [
  { id: 'tl', label: 'Trên trái', x: 0.04, y: 0.04 },
  { id: 'tr', label: 'Trên phải', x: 'right', y: 0.04 },
  { id: 'c', label: 'Giữa', x: 'center', y: 'center' },
  { id: 'bl', label: 'Dưới trái', x: 0.04, y: 'bottom' },
  { id: 'br', label: 'Dưới phải', x: 'right', y: 'bottom' },
];

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

function overlaySrc(channelId, ov) {
  if (ov?.url) return getMediaUrl(ov.url);
  if (ov?.id) return getMediaUrl(`/api/v1/channels/${channelId}/overlays/${ov.id}`);
  return '';
}

function resolvePreset(preset, w, aspect) {
  const h = clamp(w * (aspect || 1), 0.04, 0.95);
  let x = 0.04;
  let y = 0.04;
  if (preset.x === 'right') x = clamp(1 - w - 0.04, 0, 1 - w);
  else if (preset.x === 'center') x = clamp((1 - w) / 2, 0, 1 - w);
  else x = preset.x;
  if (preset.y === 'bottom') y = clamp(1 - h - 0.04, 0, 0.95);
  else if (preset.y === 'center') y = clamp((1 - h) / 2, 0, 0.95);
  else y = preset.y;
  return { x, y };
}

export function ChannelOverlayEditor({ channel, previewSrc, onOverlaysChange, onToast }) {
  const channelId = channel?.channel_id || channel?.id;
  const [items, setItems] = useState(channel?.overlays || []);
  const [aspects, setAspects] = useState({});
  const [selectedId, setSelectedId] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [saveState, setSaveState] = useState('idle');
  const stageRef = useRef(null);
  const dragRef = useRef(null);
  const itemsRef = useRef(items);
  const saveTimer = useRef(null);
  const logoInputRef = useRef(null);
  const frameInputRef = useRef(null);

  itemsRef.current = items;

  useEffect(() => {
    setItems(Array.isArray(channel?.overlays) ? channel.overlays : []);
    setSelectedId(null);
    setSaveState('idle');
  }, [channelId]);

  const persist = useCallback(
    (next) => {
      if (!channelId) return;
      setSaveState('saving');
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(async () => {
        try {
          await saveChannelOverlays(channelId, next);
          setSaveState('saved');
          onOverlaysChange?.(next);
          setTimeout(() => setSaveState((s) => (s === 'saved' ? 'idle' : s)), 1600);
        } catch (err) {
          setSaveState('idle');
          onToast?.({ type: 'error', title: 'Lỗi', message: err.message || 'Không lưu được vị trí logo' });
        }
      }, 280);
    },
    [channelId, onOverlaysChange, onToast]
  );

  const updateItem = (id, patch, { save = true } = {}) => {
    const next = itemsRef.current.map((ov) => (ov.id === id ? { ...ov, ...patch } : ov));
    setItems(next);
    itemsRef.current = next;
    if (save) persist(next);
  };

  const handleUpload = async (file, kind) => {
    if (!file || !channelId) return;
    setUploading(true);
    try {
      const meta =
        kind === 'frame'
          ? { kind: 'frame', x: 0, y: 0, w: 1, opacity: 1 }
          : { kind: 'logo', x: 0.78, y: 0.04, w: 0.18, opacity: 1 };
      const res = await uploadChannelOverlay(channelId, file, meta);
      const next = res.overlays || [...itemsRef.current, res.overlay];
      setItems(next);
      itemsRef.current = next;
      if (res.overlay?.id) setSelectedId(res.overlay.id);
      onOverlaysChange?.(next);
      onToast?.({
        type: 'success',
        title: 'Đã thêm',
        message: kind === 'frame' ? 'Khung kênh sẽ phủ xuyên suốt video.' : 'Logo đã gắn. Kéo để đặt đúng vị trí.',
      });
    } catch (err) {
      onToast?.({ type: 'error', title: 'Lỗi', message: err.message || 'Tải ảnh thất bại' });
    } finally {
      setUploading(false);
      if (logoInputRef.current) logoInputRef.current.value = '';
      if (frameInputRef.current) frameInputRef.current.value = '';
    }
  };

  const handleDelete = async (ov) => {
    if (!channelId || !ov?.id) return;
    try {
      const res = await deleteChannelOverlay(channelId, ov.id);
      const next = res.overlays || itemsRef.current.filter((x) => x.id !== ov.id);
      setItems(next);
      itemsRef.current = next;
      if (selectedId === ov.id) setSelectedId(null);
      onOverlaysChange?.(next);
    } catch (err) {
      onToast?.({ type: 'error', title: 'Lỗi', message: err.message || 'Xóa logo thất bại' });
    }
  };

  const onPointerDown = (e, ov, mode) => {
    if (ov.kind === 'frame' && mode === 'move') {
      setSelectedId(ov.id);
      return;
    }
    e.preventDefault();
    e.stopPropagation();
    const stage = stageRef.current?.getBoundingClientRect();
    if (!stage) return;
    setSelectedId(ov.id);
    dragRef.current = {
      id: ov.id,
      mode,
      startCX: e.clientX,
      startCY: e.clientY,
      origX: ov.x,
      origY: ov.y,
      origW: ov.w,
      stageW: stage.width,
      stageH: stage.height,
    };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = (e) => {
    const d = dragRef.current;
    if (!d) return;
    const ov = itemsRef.current.find((x) => x.id === d.id);
    if (!ov || ov.kind === 'frame') return;
    const dx = (e.clientX - d.startCX) / d.stageW;
    const dy = (e.clientY - d.startCY) / d.stageH;
    const aspect = aspects[ov.id] || 1;
    if (d.mode === 'resize') {
      const w = clamp(d.origW + dx, 0.06, 0.55);
      const x = clamp(ov.x, 0, 1 - w);
      updateItem(d.id, { w, x }, { save: false });
      return;
    }
    const w = ov.w;
    const h = clamp(w * aspect, 0.04, 0.95);
    updateItem(
      d.id,
      {
        x: clamp(d.origX + dx, 0, Math.max(0, 1 - w)),
        y: clamp(d.origY + dy, 0, Math.max(0, 1 - h)),
      },
      { save: false }
    );
  };

  const onPointerUp = () => {
    if (!dragRef.current) return;
    dragRef.current = null;
    persist(itemsRef.current);
  };

  const selected = items.find((x) => x.id === selectedId) || items[items.length - 1] || null;

  if (!channelId) return null;

  return (
    <div className="rounded-2xl border border-indigo-200/80 bg-gradient-to-br from-indigo-50/70 via-white to-slate-50 p-4 md:p-5 space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div>
          <h4 className="text-xs font-extrabold uppercase tracking-wider text-slate-800 flex items-center gap-1.5">
            <Layers className="w-3.5 h-3.5 text-indigo-600" />
            Khung & Logo Kênh
          </h4>
          <p className="text-[11px] text-slate-500 font-medium mt-0.5">
            Chèn PNG/JPG vào đúng vị trí. Ảnh sẽ hiển thị xuyên suốt mọi video reup của kênh này.
          </p>
        </div>
        <div className="flex items-center gap-1.5 text-[10px] font-bold text-slate-500">
          {saveState === 'saving' && (
            <span className="flex items-center gap-1 text-indigo-600">
              <Loader2 className="w-3 h-3 animate-spin" /> Đang lưu vị trí...
            </span>
          )}
          {saveState === 'saved' && (
            <span className="flex items-center gap-1 text-emerald-600">
              <Check className="w-3 h-3" /> Đã lưu vị trí
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-12 gap-4">
        {/* 9:16 stage */}
        <div className="md:col-span-5 flex flex-col items-center gap-2">
          <div
            ref={stageRef}
            className="overlay-stage relative w-[min(100%,240px)] aspect-[9/16] rounded-[1.35rem] overflow-hidden shadow-lg ring-4 ring-slate-900 select-none touch-none"
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
          >
            {previewSrc ? (
              <video
                src={previewSrc}
                className="absolute inset-0 w-full h-full object-cover"
                muted
                loop
                playsInline
                autoPlay
              />
            ) : (
              <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-500 px-4 text-center">
                <Move className="w-5 h-5 mb-1.5 text-slate-400" />
                <p className="text-[10px] font-bold leading-relaxed">
                  Khung 9:16 · kéo logo tới vị trí muốn hiện trên video
                </p>
              </div>
            )}

            {items.map((ov) => {
              const isFrame = ov.kind === 'frame';
              const src = overlaySrc(channelId, ov);
              const isSel = selected?.id === ov.id;
              const style = isFrame
                ? { left: 0, top: 0, width: '100%', height: '100%', opacity: ov.opacity }
                : {
                    left: `${ov.x * 100}%`,
                    top: `${ov.y * 100}%`,
                    width: `${ov.w * 100}%`,
                    opacity: ov.opacity,
                  };
              return (
                <div
                  key={ov.id}
                  className={`absolute ${isFrame ? 'z-[1]' : 'z-[2]'} ${
                    isSel && !isFrame ? 'ring-2 ring-amber-400 ring-offset-1 ring-offset-transparent' : ''
                  } ${isFrame ? 'cursor-default' : 'cursor-grab active:cursor-grabbing'}`}
                  style={style}
                  onPointerDown={(e) => onPointerDown(e, ov, 'move')}
                  onPointerMove={onPointerMove}
                  onPointerUp={onPointerUp}
                  onPointerCancel={onPointerUp}
                  onClick={() => setSelectedId(ov.id)}
                >
                  <img
                    src={src}
                    alt={ov.filename || ov.kind}
                    draggable={false}
                    className={`block w-full ${isFrame ? 'h-full object-cover' : 'h-auto object-contain'} pointer-events-none`}
                    onLoad={(e) => {
                      const im = e.currentTarget;
                      if (im.naturalWidth > 0) {
                        setAspects((prev) => ({
                          ...prev,
                          [ov.id]: im.naturalHeight / im.naturalWidth,
                        }));
                      }
                    }}
                  />
                  {!isFrame && isSel && (
                    <button
                      type="button"
                      aria-label="Kéo để đổi kích thước"
                      className="absolute -right-1.5 -bottom-1.5 w-4 h-4 rounded-sm bg-amber-400 border border-white shadow cursor-nwse-resize z-10"
                      onPointerDown={(e) => onPointerDown(e, ov, 'resize')}
                      onPointerMove={onPointerMove}
                      onPointerUp={onPointerUp}
                      onPointerCancel={onPointerUp}
                    >
                      <Maximize2 className="w-2.5 h-2.5 text-slate-900 m-auto" />
                    </button>
                  )}
                </div>
              );
            })}
          </div>
          <p className="text-[10px] text-slate-400 font-medium">Xem trước tỷ lệ dọc 9:16 (TikTok / Shorts / Reels)</p>
        </div>

        {/* Controls */}
        <div className="md:col-span-7 space-y-3">
          <div className="flex flex-wrap gap-2">
            <input
              ref={logoInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              className="hidden"
              onChange={(e) => handleUpload(e.target.files?.[0], 'logo')}
            />
            <input
              ref={frameInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              className="hidden"
              onChange={(e) => handleUpload(e.target.files?.[0], 'frame')}
            />
            <button
              type="button"
              disabled={uploading}
              onClick={() => logoInputRef.current?.click()}
              className="px-3 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-[11px] font-extrabold rounded-xl transition flex items-center gap-1.5 cursor-pointer"
            >
              {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ImagePlus className="w-3.5 h-3.5" />}
              Thêm Logo
            </button>
            <button
              type="button"
              disabled={uploading}
              onClick={() => frameInputRef.current?.click()}
              className="px-3 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-[11px] font-extrabold rounded-xl transition flex items-center gap-1.5 cursor-pointer"
            >
              <Frame className="w-3.5 h-3.5" />
              Thêm Khung Full
            </button>
          </div>

          <p className="text-[11px] text-slate-500 leading-relaxed">
            <span className="font-bold text-slate-700">Logo</span> — kéo thả góc/vị trí, giữ PNG nền trong suốt.{' '}
            <span className="font-bold text-slate-700">Khung</span> — PNG phủ toàn màn (viền kênh). Cả hai cháy vào mọi frame video.
          </p>

          {items.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-200 bg-white/70 p-4 text-center">
              <Upload className="w-6 h-6 text-slate-300 mx-auto mb-1.5" />
              <p className="text-xs font-bold text-slate-600">Chưa có logo / khung</p>
              <p className="text-[11px] text-slate-400 mt-0.5">Tải ảnh PNG trong suốt để gắn xuyên suốt video.</p>
            </div>
          ) : (
            <div className="space-y-2 max-h-[280px] overflow-y-auto pr-0.5">
              {items.map((ov) => {
                const isSel = selected?.id === ov.id;
                const isFrame = ov.kind === 'frame';
                return (
                  <div
                    key={ov.id}
                    onClick={() => setSelectedId(ov.id)}
                    className={`p-3 rounded-xl border bg-white cursor-pointer transition ${
                      isSel ? 'border-indigo-400 ring-1 ring-indigo-200' : 'border-slate-200 hover:border-slate-300'
                    }`}
                  >
                    <div className="flex items-center gap-2.5">
                      <img
                        src={overlaySrc(channelId, ov)}
                        alt=""
                        className="w-9 h-9 rounded-lg object-contain bg-slate-900 border border-slate-200"
                      />
                      <div className="min-w-0 flex-1">
                        <p className="text-[11px] font-extrabold text-slate-800 truncate">
                          {isFrame ? 'Khung full video' : 'Logo kênh'} · {ov.filename || ov.id}
                        </p>
                        <p className="text-[10px] text-slate-400 font-medium">
                          {isFrame
                            ? 'Phủ 100% khung hình'
                            : `X ${(ov.x * 100).toFixed(0)}% · Y ${(ov.y * 100).toFixed(0)}% · Rộng ${(ov.w * 100).toFixed(0)}%`}
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(ov);
                        }}
                        className="p-1.5 rounded-lg text-slate-400 hover:text-rose-600 hover:bg-rose-50"
                        title="Xóa"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>

                    {isSel && (
                      <div className="mt-2.5 space-y-2 pt-2 border-t border-slate-100">
                        <label className="flex items-center justify-between text-[10px] font-bold text-slate-600">
                          <span>Độ trong suốt</span>
                          <span className="font-mono text-indigo-600">{Math.round(ov.opacity * 100)}%</span>
                        </label>
                        <input
                          type="range"
                          min="0.15"
                          max="1"
                          step="0.05"
                          value={ov.opacity}
                          onChange={(e) => updateItem(ov.id, { opacity: parseFloat(e.target.value) })}
                          className="w-full accent-indigo-600 cursor-pointer"
                        />
                        {!isFrame && (
                          <>
                            <label className="flex items-center justify-between text-[10px] font-bold text-slate-600">
                              <span>Kích thước (theo chiều ngang video)</span>
                              <span className="font-mono text-indigo-600">{Math.round(ov.w * 100)}%</span>
                            </label>
                            <input
                              type="range"
                              min="0.08"
                              max="0.50"
                              step="0.01"
                              value={ov.w}
                              onChange={(e) => {
                                const w = parseFloat(e.target.value);
                                updateItem(ov.id, { w, x: clamp(ov.x, 0, 1 - w) });
                              }}
                              className="w-full accent-indigo-600 cursor-pointer"
                            />
                            <div className="flex flex-wrap gap-1 pt-0.5">
                              {PRESETS.map((p) => (
                                <button
                                  key={p.id}
                                  type="button"
                                  onClick={() => {
                                    const pos = resolvePreset(p, ov.w, aspects[ov.id] || 1);
                                    updateItem(ov.id, pos);
                                  }}
                                  className="px-2 py-1 rounded-lg text-[10px] font-bold bg-slate-100 hover:bg-indigo-50 hover:text-indigo-700 text-slate-600 border border-slate-200"
                                >
                                  {p.label}
                                </button>
                              ))}
                            </div>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
