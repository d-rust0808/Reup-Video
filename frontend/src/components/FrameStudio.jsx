import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import { Frame, Upload, Check, Loader2, Sparkles, Move, ZoomIn, X } from 'lucide-react';
import { fetchFramePresets, getMediaUrl, renderFramePreset, uploadCustomFrame } from '../services/api';
import { loadSession, saveSession } from '../services/session';

const FALLBACK = [
  { id: 'cinema', label: 'Điện ảnh', desc: 'Viền đen dày + chỉ trắng', color: '#000000', thickness: 28 },
  { id: 'gold', label: 'Gold', desc: 'Viền vàng gold sang', color: '#C9A227', thickness: 22 },
  { id: 'neon', label: 'Neon', desc: 'Tím neon TikTok', color: '#7C3AED', thickness: 18 },
  { id: 'polaroid', label: 'Polaroid', desc: 'Viền trắng, đáy dày', color: '#F8F5F0', thickness: 36 },
  { id: 'vintage', label: 'Vintage', desc: 'Nâu cổ + kem', color: '#3C2A1E', thickness: 24 },
  { id: 'slim', label: 'Mỏng', desc: 'Viền trắng mảnh', color: '#FFFFFF', thickness: 8 },
  { id: 'rose', label: 'Hồng', desc: 'Viền hồng reels', color: '#E11D48', thickness: 18 },
];

function cssRing(color, thickness, extraBottom) {
  const t = Math.max(4, Number(thickness) || 16);
  const extra = extraBottom ? `0 0 0 ${t}px ${color}, 0 ${Math.round(t * 1.8)}px 0 ${t}px ${color}` : `0 0 0 ${t}px ${color}`;
  return extra;
}

// ── Drag & Resize Overlay ──────────────────────────────────────────────────

function OverlayPreview({ overlay, onTransformChange }) {
  const containerRef = useRef(null);

  // Normalized: 0-1 range relative to container
  const [tx, setTx] = useState(overlay?.x ?? 0.5);
  const [ty, setTy] = useState(overlay?.y ?? 0.5);
  const [scale, setScale] = useState(overlay?.w ?? 1.0);
  const [selected, setSelected] = useState(false);

  // Sync when overlay prop changes (new upload or preset switch)
  useEffect(() => {
    setTx(overlay?.x ?? 0.5);
    setTy(overlay?.y ?? 0.5);
    setScale(overlay?.w ?? 1.0);
    setSelected(false);
  }, [overlay?.url, overlay?.x, overlay?.y, overlay?.w]);

  const notify = useCallback(() => {
    onTransformChange({ x: tx, y: ty, w: scale });
  }, [tx, ty, scale, onTransformChange]);

  // ── Drag to move ──
  const onMoveStart = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setSelected(true);
    if (!overlay?.url) return;
    if (!containerRef.current) return;

    const container = containerRef.current;
    const rect = container.getBoundingClientRect();

    const startX = e.clientX;
    const startY = e.clientY;
    const startTx = tx;
    const startTy = ty;

    const pointerId = e.pointerId;
    try { e.target.setPointerCapture?.(pointerId); } catch {}

    const onMove = (ev) => {
      const dx = (ev.clientX - startX) / rect.width;
      const dy = (ev.clientY - startY) / rect.height;
      setTx(Math.max(0, Math.min(1, startTx + dx)));
      setTy(Math.max(0, Math.min(1, startTy + dy)));
    };

    const onUp = (ev) => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      try { ev.target.releasePointerCapture?.(pointerId); } catch {}
      notify();
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  // ── Drag corner to resize ──
  const onResizeStart = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!overlay?.url) return;

    const startX = e.clientX;
    const startScale = scale;
    const pointerId = e.pointerId;
    try { e.target.setPointerCapture?.(pointerId); } catch {}

    const onMove = (ev) => {
      const dx = (ev.clientX - startX) / 60;
      setScale(Math.max(0.1, Math.min(3.0, startScale + dx)));
    };

    const onUp = (ev) => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      try { ev.target.releasePointerCapture?.(pointerId); } catch {}
      notify();
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const wrapperStyle = {
    position: 'absolute',
    inset: 0,
    pointerEvents: 'none',
    zIndex: 10,
  };

  const imgStyle = {
    position: 'absolute',
    left: `${tx * 100}%`,
    top: `${ty * 100}%`,
    width: `${scale * 100}%`,
    height: 'auto',
    transform: 'translate(-50%, -50%)',
    cursor: overlay?.url ? 'move' : 'default',
    userSelect: 'none',
    pointerEvents: 'auto',
    touchAction: 'none',
    zIndex: 10,
  };

  return (
    <>
      {overlay?.url && (
        <div style={wrapperStyle}>
          <img
            src={getMediaUrl(overlay.url)}
            alt=""
            style={imgStyle}
            onPointerDown={onMoveStart}
            onClick={(e) => { e.stopPropagation(); setSelected(true); }}
            draggable={false}
          />
        </div>
      )}
      {overlay?.url && selected && (
        <>
          <div
            className="absolute z-20 bg-blue-600/90 text-white text-[9px] font-bold px-1.5 py-0.5 rounded flex items-center gap-1 pointer-events-none whitespace-nowrap"
            style={{
              left: `${tx * 100}%`,
              top: `calc(${ty * 100}% + ${scale * 30}px)`,
              transform: 'translate(-50%, 0)',
            }}
          >
            <Move className="w-2.5 h-2.5" /> Kéo để di chuyển
          </div>
          <div
            className="absolute z-20 bg-white border-2 border-blue-600 rounded-full w-5 h-5 flex items-center justify-center cursor-se-resize shadow-sm"
            style={{
              left: `${tx * 100 + (scale * 100) / 2 - 0.5}%`,
              top: `${ty * 100 + (scale * 100) / 2 - 0.5}%`,
              transform: 'translate(-50%, -50%)',
            }}
            onPointerDown={onResizeStart}
            title="Kéo để phóng to / thu nhỏ"
          >
            <ZoomIn className="w-2.5 h-2.5 text-blue-600" />
          </div>
          <div
            className="absolute z-20 bg-white border border-slate-300 rounded-full w-4 h-4 flex items-center justify-center cursor-pointer shadow-sm"
            style={{
              left: `${tx * 100 - (scale * 100) / 2}%`,
              top: `${ty * 100 - (scale * 100) / 2}%`,
              transform: 'translate(-50%, -50%)',
            }}
            onClick={(e) => { e.stopPropagation(); setSelected(false); }}
            title="Bỏ chọn"
          >
            <X className="w-2 h-2 text-slate-500" />
          </div>
        </>
      )}
    </>
  );
}

// ── Main Component ──────────────────────────────────────────────────────────

export function FrameStudio() {
  const boot = loadSession().frameStudio || {};
  const [presets, setPresets] = useState(FALLBACK);
  const [preset, setPreset] = useState(boot.preset || 'cinema');
  const [color, setColor] = useState(boot.color || '#000000');
  const [thickness, setThickness] = useState(boot.thickness || 28);
  const [enabled, setEnabled] = useState(boot.enabled !== false);
  const [overlay, setOverlay] = useState(boot.overlay || null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  useEffect(() => {
    fetchFramePresets()
      .then((d) => {
        if (Array.isArray(d.presets) && d.presets.length) setPresets(d.presets);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    saveSession({ frameStudio: { enabled, preset, color, thickness, overlay } });
  }, [enabled, preset, color, thickness, overlay]);

  const active = useMemo(
    () => presets.find((p) => p.id === preset) || presets[0],
    [presets, preset]
  );

  const applyPreset = (p) => {
    setPreset(p.id);
    setColor(p.color || color);
    setThickness(p.thickness || thickness);
    setEnabled(true);
    setOverlay(null);
  };

  const bakeAndSave = async (nextOverlay) => {
    const prev = loadSession().workbenchOptions || {};
    const others = (prev.overlays || []).filter((o) => o.kind !== 'frame');
    const overlays = nextOverlay ? [nextOverlay, ...others] : others;
    saveSession({
      frameStudio: { enabled, preset, color, thickness, overlay: nextOverlay || overlay },
      workbenchOptions: { ...prev, frame_enabled: enabled && !nextOverlay, frame_color: color, frame_thickness: thickness, overlays },
    });
  };

  const handleApply = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const res = await renderFramePreset({ preset, color, thickness });
      const ov = res.overlay;
      setOverlay(ov);
      await bakeAndSave(ov);
      setMsg({ type: 'ok', text: 'Đã gắn khung. Job reup tiếp theo sẽ in khung vào từng frame.' });
    } catch (e) {
      setMsg({ type: 'err', text: e.message || 'Không render được khung' });
    } finally {
      setBusy(false);
    }
  };

  const handleTransformChange = useCallback((transform) => {
    setOverlay((prev) => {
      const updated = prev ? { ...prev, ...transform } : prev;
      if (!updated) return prev;
      const prevSession = loadSession().workbenchOptions || {};
      const others = (prevSession.overlays || []).filter((o) => o.kind !== 'frame');
      saveSession({
        frameStudio: { enabled, preset, color, thickness, overlay: updated },
        workbenchOptions: { ...prevSession, overlays: [updated, ...others] },
      });
      return updated;
    });
  }, [enabled, preset, color, thickness]);

  const handleUpload = async (file) => {
    if (!file) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await uploadCustomFrame(file);
      setPreset('custom');
      setEnabled(true);
      const ov = { ...res.overlay, x: 0.5, y: 0.5, w: 1.0 };
      setOverlay(ov);
      await bakeAndSave(ov);
      setMsg({ type: 'ok', text: 'Đã nhận PNG — kéo để định vị, kéo góc để phóng/thu.' });
    } catch (e) {
      setMsg({ type: 'err', text: e.message || 'Tải khung thất bại' });
    } finally {
      setBusy(false);
    }
  };

  const extraBottom = preset === 'polaroid';

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-black text-slate-900 flex items-center gap-2">
            <Frame className="w-5 h-5 text-blue-600" /> Khung video
          </h2>
          <p className="text-xs text-slate-500 font-medium mt-1">
            Chọn mẫu, chỉnh màu / độ dày, rồi in thẳng vào clip khi reup. PNG custom có thể kéo thả định vị trên preview.
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs font-bold text-slate-700 bg-white border border-slate-200 rounded-xl px-3 py-2">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="accent-blue-600" />
          Bật khung
        </label>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[240px_1fr] gap-6">
        {/* ── Preview ── */}
        <div className="flex justify-center">
          <div
            className="relative w-[200px] aspect-[9/16] rounded-[1.6rem] overflow-hidden bg-slate-900 shadow-xl ring-4 ring-slate-900 select-none touch-none"
          >
            <div className="absolute inset-0 bg-gradient-to-br from-slate-700 via-slate-800 to-slate-950" />
            <div className="absolute inset-0 flex flex-col items-center justify-center text-white/80 px-4 text-center pointer-events-none">
              <Sparkles className="w-6 h-6 mb-2 text-amber-300" />
              <p className="text-[11px] font-extrabold leading-tight">Xem trước 9:16</p>
              <p className="text-[10px] text-white/60 mt-1">{active?.label || preset}</p>
            </div>
            {enabled && (
              <div
                className="absolute inset-0 pointer-events-none"
                style={{ boxShadow: `inset ${cssRing(color, Math.max(4, thickness / 3), extraBottom)}` }}
              />
            )}
            <OverlayPreview overlay={overlay} onTransformChange={handleTransformChange} />
          </div>
        </div>

        {/* ── Controls ── */}
        <div className="space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            {presets.map((p) => {
              const on = preset === p.id;
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => applyPreset(p)}
                  className={`text-left rounded-2xl border px-3 py-2.5 transition ${
                    on ? 'bg-blue-600 border-blue-700 text-white shadow-sm' : 'bg-white border-slate-200 hover:border-blue-300'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="w-4 h-4 rounded-md border border-black/10 shrink-0" style={{ background: p.color }} />
                    <span className="text-xs font-extrabold">{p.label}</span>
                  </div>
                  <p className={`text-[10px] mt-1 ${on ? 'text-blue-100' : 'text-slate-500'}`}>{p.desc}</p>
                </button>
              );
            })}
          </div>

          <div className="grid grid-cols-2 gap-3 bg-white border border-slate-200 rounded-2xl p-4">
            <label className="text-[11px] font-bold text-slate-600">
              Màu viền
              <input type="color" value={color} onChange={(e) => setColor(e.target.value)} className="mt-1 w-full h-9 rounded-lg border border-slate-200 cursor-pointer" />
            </label>
            <label className="text-[11px] font-bold text-slate-600">
              Độ dày {thickness}px
              <input type="range" min="6" max="60" step="2" value={thickness} onChange={(e) => setThickness(parseInt(e.target.value, 10))} className="mt-3 w-full accent-blue-600" />
            </label>
          </div>

          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={handleApply} disabled={busy} className="inline-flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-xs font-extrabold px-4 py-2.5 rounded-xl shadow-sm">
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              In khung vào video
            </button>
            <label className="inline-flex items-center gap-2 bg-white border border-slate-200 hover:border-blue-300 text-slate-800 text-xs font-bold px-4 py-2.5 rounded-xl cursor-pointer">
              <Upload className="w-4 h-4" /> Tải PNG khung custom
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="hidden"
                onChange={(e) => { handleUpload(e.target.files?.[0]); e.target.value = ''; }}
              />
            </label>
          </div>

          {msg && (
            <p className={`text-xs font-bold ${msg.type === 'ok' ? 'text-emerald-700' : 'text-rose-600'}`}>
              {msg.text}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
