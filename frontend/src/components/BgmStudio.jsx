import React, { useEffect, useState } from 'react';
import { Music, Link2, Upload, Trash2, Check, Loader2, Search, Globe, Download, Scissors } from 'lucide-react';
import {
  extractBgm,
  fetchBgmLibrary,
  uploadBgmFile,
  deleteBgm,
  getMediaUrl,
  fetchBgmProviders,
  searchOnlineBgm,
  importOnlineBgm,
} from '../services/api';
import { loadSession, saveSession } from '../services/session';

export function BgmStudio() {
  const boot = loadSession();
  const [url, setUrl] = useState('');
  const [items, setItems] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [selected, setSelected] = useState(boot.workbenchOptions?.bgm_id || null);
  const [tab, setTab] = useState('harvest');
  const [providers, setProviders] = useState([]);
  const [provider, setProvider] = useState('openverse');
  const [query, setQuery] = useState('');
  const [instrumental, setInstrumental] = useState(true);
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [importingId, setImportingId] = useState(null);
  const [searchNote, setSearchNote] = useState(null);

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

  useEffect(() => {
    if (tab !== 'online' || providers.length > 0) return;
    fetchBgmProviders()
      .then((data) => {
        const list = data.providers || [];
        setProviders(list);
        const ready = list.find((p) => p.ready);
        if (ready) setProvider(ready.id);
      })
      .catch(() => setProviders([]));
  }, [tab, providers.length]);

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
      setResults((prev) => prev.map((r) => (r.in_library === id ? { ...r, in_library: null } : r)));
      if (selected === id) {
        const prev = loadSession().workbenchOptions || {};
        saveSession({ workbenchOptions: { ...prev, bgm_path: null, bgm_id: null, bgm_title: null } });
        setSelected(null);
      }
    } catch (err) {
      setMsg({ type: 'err', text: err.message || 'Xóa thất bại' });
    }
  };

  const handleSearch = async (e) => {
    e?.preventDefault();
    if (!query.trim()) {
      setSearchNote({ type: 'err', text: 'Nhập từ khóa: lofi, piano, chill, epic drum…' });
      return;
    }
    setSearching(true);
    setSearchNote(null);
    try {
      const data = await searchOnlineBgm({ q: query.trim(), provider, instrumental, limit: 24 });
      setResults(data.items || []);
      const errs = Object.entries(data.errors || {});
      if ((data.items || []).length === 0) {
        setSearchNote({ type: 'err', text: 'Không có bản nào khớp. Thử từ khóa tiếng Anh ngắn hơn.' });
      } else if (errs.length > 0) {
        setSearchNote({ type: 'err', text: errs.map(([k, v]) => `${k}: ${v}`).join(' · ') });
      }
    } catch (err) {
      setResults([]);
      setSearchNote({ type: 'err', text: err.message || 'Tìm nhạc thất bại' });
    } finally {
      setSearching(false);
    }
  };

  const handleImport = async (track) => {
    setImportingId(track.external_id);
    setSearchNote(null);
    try {
      const res = await importOnlineBgm(track);
      const item = res.item;
      setItems((prev) => [item, ...prev.filter((x) => x.id !== item.id)]);
      setResults((prev) =>
        prev.map((r) => (r.external_id === track.external_id ? { ...r, in_library: item.id } : r)),
      );
      applyTrack(item);
      setSearchNote({ type: 'ok', text: `Đã thêm «${item.title}» vào kho và chọn dùng cho reup.` });
    } catch (err) {
      setSearchNote({ type: 'err', text: err.message || 'Không thêm được bản nhạc' });
    } finally {
      setImportingId(null);
    }
  };

  const fmtDur = (s) => {
    const n = Math.round(Number(s) || 0);
    return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
  };

  const methodLabel = (m) => {
    if (m === 'demucs') return 'tách stem sạch';
    if (m === 'upload') return 'file tải lên';
    if (m === 'openverse') return 'kho Openverse';
    if (m === 'jamendo') return 'kho Jamendo';
    return 'lọc thoại FFmpeg';
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-black text-slate-900 flex items-center gap-2">
          <Music className="w-5 h-5 text-blue-600" /> Nhạc nền
        </h2>
        <p className="text-xs text-slate-500 font-medium mt-1">
          Tách nhạc từ link video, tải file có sẵn, hoặc lấy thêm từ kho nhạc bản quyền mở — tất cả lưu chung một kho để gắn vào clip reup.
        </p>
      </div>

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setTab('harvest')}
          className={`flex items-center gap-1.5 px-4 py-2 text-xs font-extrabold rounded-xl border ${
            tab === 'harvest' ? 'bg-blue-600 text-white border-blue-700' : 'bg-white text-slate-600 border-slate-200'
          }`}
        >
          <Scissors className="w-3.5 h-3.5" /> Tách từ video
        </button>
        <button
          type="button"
          onClick={() => setTab('online')}
          className={`flex items-center gap-1.5 px-4 py-2 text-xs font-extrabold rounded-xl border ${
            tab === 'online' ? 'bg-blue-600 text-white border-blue-700' : 'bg-white text-slate-600 border-slate-200'
          }`}
        >
          <Globe className="w-3.5 h-3.5" /> Kho online
        </button>
      </div>

      {tab === 'harvest' && (
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
      )}

      {tab === 'online' && (
        <div className="clean-panel rounded-3xl p-5 space-y-3">
          <form onSubmit={handleSearch} className="space-y-3">
            <label className="text-xs font-extrabold text-slate-700">Tìm nhạc trong kho bản quyền mở</label>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Search className="w-4 h-4 text-blue-600 absolute left-3 top-3" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="lofi, piano, chill, epic drum… (từ khóa tiếng Anh cho kết quả tốt nhất)"
                  className="w-full bg-slate-50 border border-slate-300 rounded-xl pl-10 pr-3 py-2.5 text-sm"
                />
              </div>
              <button
                type="submit"
                disabled={searching}
                className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-xs font-extrabold px-4 py-2.5 rounded-xl"
              >
                {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Tìm'}
              </button>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className="bg-slate-50 border border-slate-300 rounded-xl px-3 py-2 text-xs font-bold text-slate-700"
              >
                {providers.length > 1 && <option value="all">Tất cả nguồn khả dụng</option>}
                {providers.map((p) => (
                  <option key={p.id} value={p.id} disabled={!p.ready}>
                    {p.label}
                    {p.ready ? '' : ' (chưa có API key)'}
                  </option>
                ))}
              </select>
              <label className="inline-flex items-center gap-2 text-xs font-bold text-slate-600 cursor-pointer">
                <input
                  type="checkbox"
                  checked={instrumental}
                  onChange={(e) => setInstrumental(e.target.checked)}
                  className="rounded border-slate-300"
                />
                Chỉ bản không lời
              </label>
              <span className="text-[11px] text-slate-400 font-medium">
                Chỉ hiện license cho phép dùng thương mại + chỉnh sửa
              </span>
            </div>
          </form>

          {searchNote && (
            <p className={`text-xs font-bold ${searchNote.type === 'ok' ? 'text-emerald-700' : 'text-rose-700'}`}>
              {searchNote.text}
            </p>
          )}

          {results.length > 0 && (
            <div className="space-y-3 pt-1">
              {results.map((r) => (
                <div
                  key={`${r.provider}:${r.external_id}`}
                  className="rounded-2xl border border-slate-200 bg-white p-4 space-y-2"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-sm font-bold text-slate-900 truncate">{r.title}</p>
                      <p className="text-[11px] text-slate-500 font-medium truncate">
                        {r.artist || 'Không rõ nghệ sĩ'} · {fmtDur(r.duration)} ·{' '}
                        <span className="uppercase font-bold text-slate-600">{r.license || 'n/a'}</span> · {r.provider}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleImport(r)}
                      disabled={!!r.in_library || importingId === r.external_id}
                      className={`shrink-0 inline-flex items-center gap-1 px-3 py-1.5 text-[11px] font-extrabold rounded-xl border ${
                        r.in_library
                          ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                          : 'bg-white text-blue-700 border-blue-200 hover:bg-blue-50'
                      } disabled:opacity-60`}
                    >
                      {importingId === r.external_id ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : r.in_library ? (
                        <>
                          <Check className="w-3 h-3" /> Đã có
                        </>
                      ) : (
                        <>
                          <Download className="w-3 h-3" /> Thêm vào kho
                        </>
                      )}
                    </button>
                  </div>
                  <audio controls preload="none" src={r.audio_url} className="w-full h-8" />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

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
                        {fmtDur(it.duration)} · {methodLabel(it.method)}
                        {it.license ? (
                          <>
                            {' · '}
                            <span className="uppercase font-bold text-slate-600">{it.license}</span>
                          </>
                        ) : null}
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
