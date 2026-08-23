import React, { useEffect, useState } from 'react';
import { Music, Link2, Upload, Trash2, Check, Loader2 } from 'lucide-react';
import { extractBgm, fetchBgmLibrary, uploadBgmFile, deleteBgm, getMediaUrl } from '../services/api';
import { loadSession, saveSession } from '../services/session';

export function BgmStudio() {
  const boot = loadSession();
  const [url, setUrl] = useState('');
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [selected, setSelected] = useState(boot.workbenchOptions?.bgm_id || null);

  const load = async () => {
    try {
      const data = await fetchBgmLibrary();
      setItems(data.items || []);
    } catch {
      setItems([]);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const applyTrack = (item) => {
    const prev = loadSession().workbenchOptions || {};
    saveSession({
      workbenchOptions: {
        ...prev,
        bgm_path: item.path || item.id,
        bgm_id: item.id,
        bgm_title: item.title,
        bgm_volume: prev.bgm_volume ?? 0.85,
      },
    });
    setSelected(item.id);
    setMsg({ type: 'ok', text: `Đã chọn «${item.title}» — job reup tiếp theo dùng nhạc này.` });
  };

  const handleExtract = async (e) => {
    e?.preventDefault();
    if (!url.trim()) {
      setMsg({ type: 'err', text: 'Dán link video có nhạc hay (Douyin / Kuaishou / …).' });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const res = await extractBgm({ url: url.trim() });
      const item = res.item;
      setItems((prev) => [item, ...prev.filter((x) => x.id !== item.id)]);
      applyTrack(item);
      setUrl('');
    } catch (err) {
      setMsg({ type: 'err', text: err.message || 'Không tách được nhạc nền' });
    } finally {
      setBusy(false);
    }
  };

  const handleUpload = async (file) => {
    if (!file) return;
    setBusy(true);
    setMsg(null);
    try {
      const res = await uploadBgmFile(file);
      const item = res.item;
      setItems((prev) => [item, ...prev.filter((x) => x.id !== item.id)]);
      applyTrack(item);
    } catch (err) {
      setMsg({ type: 'err', text: err.message || 'Tải nhạc thất bại' });
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id) => {
    try {
      await deleteBgm(id);
      setItems((prev) => prev.filter((x) => x.id !== id));
      if (selected === id) {
        const prev = loadSession().workbenchOptions || {};
        saveSession({ workbenchOptions: { ...prev, bgm_path: null, bgm_id: null, bgm_title: null } });
        setSelected(null);
      }
    } catch (err) {
      setMsg({ type: 'err', text: err.message || 'Xóa thất bại' });
    }
  };

  const fmtDur = (s) => {
    const n = Math.round(Number(s) || 0);
    return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-black text-slate-900 flex items-center gap-2">
          <Music className="w-5 h-5 text-blue-600" /> Nhạc nền
        </h2>
        <p className="text-xs text-slate-500 font-medium mt-1">
          Dán link video có BGM hay — máy tách phần nhạc (bỏ thoại), lưu vào kho, rồi gắn vào clip reup của bạn.
        </p>
      </div>

      <form onSubmit={handleExtract} className="clean-panel rounded-3xl p-5 space-y-3">
        <label className="text-xs font-extrabold text-slate-700">Link video nguồn nhạc</label>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Link2 className="w-4 h-4 text-blue-600 absolute left-3 top-3" />
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://v.douyin.com/… hoặc đoạn chia sẻ"
              className="w-full bg-slate-50 border border-slate-300 rounded-xl pl-10 pr-3 py-2.5 text-sm font-mono"
            />
          </div>
          <button
            type="submit"
            disabled={busy}
            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-xs font-extrabold px-4 py-2.5 rounded-xl"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Tách nhạc nền'}
          </button>
        </div>
        <label className="inline-flex items-center gap-2 text-xs font-bold text-slate-600 cursor-pointer">
          <Upload className="w-4 h-4 text-purple-600" />
          Hoặc tải MP3/WAV có sẵn
          <input
            type="file"
            accept="audio/mpeg,audio/mp3,audio/wav,audio/x-m4a,audio/aac,.mp3,.wav,.m4a"
            className="hidden"
            onChange={(e) => handleUpload(e.target.files?.[0])}
          />
        </label>
        {msg && (
          <p className={`text-xs font-bold ${msg.type === 'ok' ? 'text-emerald-700' : 'text-rose-700'}`}>{msg.text}</p>
        )}
      </form>

      <div className="clean-panel rounded-3xl p-5 space-y-3">
        <h3 className="text-xs font-black text-slate-500 uppercase tracking-wider">Kho nhạc ({items.length})</h3>
        {items.length === 0 ? (
          <p className="text-xs text-slate-400 font-medium py-6 text-center">Chưa có bản nào. Dán link video hay để tách.</p>
        ) : (
          <div className="space-y-3">
            {items.map((it) => {
              const on = selected === it.id;
              return (
                <div
                  key={it.id}
                  className={`rounded-2xl border p-4 space-y-2 ${on ? 'border-blue-400 bg-blue-50/70' : 'border-slate-200 bg-white'}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-bold text-slate-900 truncate">{it.title}</p>
                      <p className="text-[11px] text-slate-500 font-medium">
                        {fmtDur(it.duration)} · {it.method === 'demucs' ? 'tách stem sạch' : it.method === 'upload' ? 'file tải lên' : 'lọc thoại FFmpeg'}
                      </p>
                    </div>
                    <div className="flex gap-1.5 shrink-0">
                      <button
                        type="button"
                        onClick={() => applyTrack(it)}
                        className={`px-3 py-1.5 text-[11px] font-extrabold rounded-xl border ${
                          on ? 'bg-blue-600 text-white border-blue-700' : 'bg-white text-blue-700 border-blue-200'
                        }`}
                      >
                        {on ? <span className="inline-flex items-center gap-1"><Check className="w-3 h-3" /> Đang dùng</span> : 'Dùng cho reup'}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(it.id)}
                        className="p-1.5 text-slate-400 hover:text-rose-600 rounded-xl"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                  <audio
                    controls
                    preload="metadata"
                    src={getMediaUrl(it.stream_url || `/api/v1/bgm/${it.id}/audio`)}
                    className="w-full h-8"
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
