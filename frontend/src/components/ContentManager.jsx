import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  BookOpen,
  CheckSquare,
  ChevronDown,
  Download,
  ExternalLink,
  Link2,
  Loader2,
  Edit3,
  Play,
  Plus,
  RefreshCw,
  Square,
  Tag,
  Trash2,
  X,
} from 'lucide-react';
import {
  addContentChannel,
  deleteContentChannel,
  fetchContentChannelVideos,
  fetchContentChannels,
  fetchContentVideos,
  markContentVideoPosted,
  patchContentChannel,
  syncContentChannel,
} from '../services/api';

const PLATFORM_BADGE = {
  youtube: 'bg-rose-50 text-rose-700 border-rose-200',
  douyin: 'bg-pink-50 text-pink-700 border-pink-200',
  kuaishou: 'bg-amber-50 text-amber-800 border-amber-200',
  xiaohongshu: 'bg-red-50 text-red-700 border-red-200',
};

function platformLabel(p) {
  const v = String(p || '').toLowerCase();
  if (v === 'youtube') return 'YouTube';
  if (v === 'douyin') return 'Douyin';
  if (v === 'kuaishou') return 'Kuaishou';
  if (v === 'xiaohongshu') return 'Xiaohongshu';
  return p || 'Kênh';
}

function formatPublishedAt(raw) {
  const text = String(raw || '').trim();
  if (!text) return '';
  const dt = new Date(text);
  if (Number.isNaN(dt.getTime())) return '';
  return dt.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

function parseTags(text) {
  return String(text || '')
    .split(/[,;#]+/)
    .map((t) => t.trim().replace(/^#/, ''))
    .filter(Boolean);
}

export function ContentManager({ onSelectForWorkbench }) {
  const [channels, setChannels] = useState([]);
  const [openId, setOpenId] = useState(null);
  const [videos, setVideos] = useState([]);
  const [filter, setFilter] = useState('all');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [url, setUrl] = useState('');
  const [name, setName] = useState('');
  const [tags, setTags] = useState('');
  const [editing, setEditing] = useState(null);
  const openIdRef = useRef(null);
  const videosSeq = useRef(0);

  useEffect(() => {
    openIdRef.current = openId;
  }, [openId]);

  const loadChannels = useCallback(async () => {
    const data = await fetchContentChannels();
    setChannels(data.channels || []);
    return data.channels || [];
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    loadChannels()
      .catch((err) => {
        if (alive) setError(err.message || 'Không tải được kênh nguồn');
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [loadChannels]);

  const applyChannelStats = useCallback((channelId, data) => {
    if (!channelId || !data) return;
    setChannels((prev) =>
      prev.map((ch) =>
        ch.channel_id === channelId
          ? {
              ...ch,
              video_count: data.video_count ?? ch.video_count,
              posted_count: data.posted_count ?? ch.posted_count,
              unposted_count: data.unposted_count ?? ch.unposted_count,
              downloaded_count: data.downloaded_count ?? ch.downloaded_count,
            }
          : ch
      )
    );
  }, []);

  const loadVideos = useCallback(async (channelId, status = filter) => {
    const seq = ++videosSeq.current;
    const data = await fetchContentVideos(channelId, status);
    if (seq !== videosSeq.current) return data.videos || [];
    if (openIdRef.current && openIdRef.current !== channelId) return data.videos || [];
    setVideos(data.videos || []);
    applyChannelStats(channelId, data);
    return data.videos || [];
  }, [filter, applyChannelStats]);

  useEffect(() => {
    if (!openId || busy) return undefined;
    const refresh = () => {
      loadChannels().catch(() => {});
      loadVideos(openId, filter).catch(() => {});
    };
    window.addEventListener('reup:channels-changed', refresh);
    const timer = window.setInterval(refresh, 8000);
    return () => {
      window.removeEventListener('reup:channels-changed', refresh);
      window.clearInterval(timer);
    };
  }, [openId, filter, loadChannels, loadVideos, busy]);

  const openChannel = async (channel) => {
    const id = channel.channel_id;
    if (openId === id) {
      videosSeq.current += 1;
      setOpenId(null);
      setVideos([]);
      return;
    }
    videosSeq.current += 1;
    setOpenId(id);
    setFilter('all');
    setVideos([]);
    setBusy(`open-${id}`);
    setError('');
    try {
      if (!channel.video_count) {
        const res = await syncContentChannel(id);
        if (Array.isArray(res.videos)) setVideos(res.videos);
        applyChannelStats(id, res);
        await loadChannels();
        if (openIdRef.current !== id) return;
        if (!res.videos?.length) await loadVideos(id, 'all');
        return;
      }
      await loadVideos(id, 'all');
    } catch (err) {
      setError(err.message || 'Không mở được danh sách video');
    } finally {
      setBusy('');
    }
  };

  const handleAdd = async (e) => {
    e?.preventDefault();
    if (!url.trim()) {
      setError('Dán URL kênh để lấy danh sách video.');
      return;
    }
    setBusy('add');
    setError('');
    setMessage('');
    try {
      const res = await addContentChannel({
        url: url.trim(),
        name: name.trim() || undefined,
        tags: parseTags(tags),
      });
      setMessage(res.message || 'Đã thêm kênh');
      setUrl('');
      setName('');
      setTags('');
      const list = await loadChannels();
      const created = res.channel?.channel_id;
      if (created) {
        const ch = list.find((c) => c.channel_id === created) || res.channel;
        setOpenId(created);
        setFilter('all');
        if (Array.isArray(res.videos) && res.videos.length) {
          videosSeq.current += 1;
          setVideos(res.videos);
          applyChannelStats(created, res);
        } else {
          await loadVideos(created, 'all');
        }
        setChannels((prev) => prev.map((c) => (c.channel_id === created ? { ...c, ...ch } : c)));
      }
    } catch (err) {
      setError(err.message || 'Không thêm được kênh');
    } finally {
      setBusy('');
    }
  };

  const handleSync = async (channelId) => {
    setBusy(`sync-${channelId}`);
    setError('');
    setMessage('Đang đồng bộ danh sách — Douyin có thể mất khoảng 1 phút, đừng đóng kênh.');
    try {
      const res = await syncContentChannel(channelId);
      setMessage(res.message || 'Đã đồng bộ');
      applyChannelStats(channelId, res);
      if (Array.isArray(res.videos) && openIdRef.current === channelId) {
        videosSeq.current += 1;
        setVideos(res.videos);
      }
      await loadChannels();
      if (openIdRef.current === channelId && !res.videos?.length) {
        await loadVideos(channelId, filter);
      }
    } catch (err) {
      setError(err.message || 'Đồng bộ thất bại');
    } finally {
      setBusy('');
    }
  };

  const handleFetch = async (channelId, ids) => {
    setBusy(`fetch-${channelId}`);
    setError('');
    try {
      const res = await fetchContentChannelVideos(channelId, ids);
      setMessage(res.message || 'Đang tải');
      const pending = res.pending_ids || [];
      if (pending.length) {
        const started = Date.now();
        const poll = async () => {
          await loadChannels();
          if (openId === channelId) await loadVideos(channelId, filter);
          if (Date.now() - started < 3 * 60 * 60 * 1000) {
            window.setTimeout(poll, 6000);
          }
        };
        window.setTimeout(poll, 4000);
      }
    } catch (err) {
      setError(err.message || 'Không tải được video');
    } finally {
      setBusy('');
    }
  };

  const handlePosted = async (video, next) => {
    setVideos((prev) => prev.map((v) => (v.id === video.id ? { ...v, posted: next } : v)));
    try {
      await markContentVideoPosted(video.id, next);
      await loadChannels();
      if (openId) await loadVideos(openId, filter);
    } catch (err) {
      setVideos((prev) => prev.map((v) => (v.id === video.id ? { ...v, posted: !next } : v)));
      setError(err.message || 'Không đánh dấu được');
    }
  };

  const startEditChannel = (ch) => {
    setError('');
    setEditing({
      channelId: ch.channel_id,
      name: ch.name || '',
      tags: (ch.tags || []).join(', '),
      url: ch.url || '',
    });
  };

  const handleSaveChannel = async (e) => {
    e?.preventDefault();
    if (!editing) return;
    const nextName = (editing.name || '').trim();
    const nextUrl = (editing.url || '').trim();
    if (!nextName) {
      setError('Nhập tên kênh.');
      return;
    }
    if (nextUrl && !/^https?:\/\//i.test(nextUrl)) {
      setError('Link kênh phải bắt đầu bằng http.');
      return;
    }
    setBusy(`edit-${editing.channelId}`);
    setError('');
    setMessage('');
    try {
      const payload = { name: nextName, tags: parseTags(editing.tags) };
      if (nextUrl) payload.url = nextUrl;
      await patchContentChannel(editing.channelId, payload);
      setEditing(null);
      setMessage('Đã cập nhật kênh');
      await loadChannels();
    } catch (err) {
      setError(err.message || 'Không lưu được kênh');
    } finally {
      setBusy('');
    }
  };

  const handleDelete = async (channelId) => {
    if (!window.confirm('Xóa kênh nguồn và checklist video? File đã tải vẫn giữ trong thư viện.')) return;
    setBusy(`del-${channelId}`);
    try {
      await deleteContentChannel(channelId);
      if (openId === channelId) {
        setOpenId(null);
        setVideos([]);
      }
      await loadChannels();
    } catch (err) {
      setError(err.message || 'Không xóa được kênh');
    } finally {
      setBusy('');
    }
  };

  const visibleVideos = videos;

  return (
    <div className="space-y-5">
      <div className="clean-panel rounded-3xl p-5 sm:p-6 space-y-3">
        <div className="flex items-start gap-3">
          <div className="w-10 h-10 rounded-2xl bg-indigo-600 text-white flex items-center justify-center shrink-0">
            <BookOpen className="w-5 h-5" />
          </div>
          <div className="min-w-0">
            <h2 className="text-lg font-black text-slate-900">Quản lý nội dung nguồn</h2>
            <p className="text-xs text-slate-500 font-medium mt-0.5">
              Nhập tên, nhãn và link kênh. Hệ thống lấy danh sách video từ URL bạn dán,
              rồi đánh dấu đã đăng / chưa đăng trên từng clip.
            </p>
          </div>
        </div>

        <form onSubmit={handleAdd} className="grid grid-cols-1 lg:grid-cols-12 gap-2.5 pt-1">
          <div className="lg:col-span-4">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Tên kênh"
              className="w-full bg-slate-50 border border-slate-300 rounded-xl px-3 py-2.5 text-sm text-slate-900"
            />
          </div>
          <div className="lg:col-span-4 relative">
            <Tag className="w-4 h-4 text-indigo-600 absolute left-3 top-3" />
            <input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="Nhãn kênh (phẩy để tách)"
              className="w-full bg-slate-50 border border-slate-300 rounded-xl pl-10 pr-3 py-2.5 text-sm text-slate-900"
            />
          </div>
          <div className="lg:col-span-10 relative">
            <Link2 className="w-4 h-4 text-indigo-600 absolute left-3 top-3" />
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="Link kênh"
              className="w-full bg-slate-50 border border-slate-300 rounded-xl pl-10 pr-3 py-2.5 text-sm font-mono text-slate-900"
            />
          </div>
          <button
            type="submit"
            disabled={busy === 'add'}
            className="lg:col-span-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-extrabold text-xs rounded-xl px-4 py-2.5 flex items-center justify-center gap-1.5"
          >
            {busy === 'add' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
            Thêm kênh
          </button>
        </form>
      </div>

      {error && (
        <div className="p-3 rounded-2xl bg-rose-50 border border-rose-200 text-rose-700 text-xs font-bold">{error}</div>
      )}
      {message && !error && (
        <div className="p-3 rounded-2xl bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs font-bold">{message}</div>
      )}

      {loading && (
        <div className="flex items-center gap-2 text-xs font-bold text-slate-500">
          <Loader2 className="w-4 h-4 animate-spin" /> Đang tải danh sách kênh...
        </div>
      )}

      {!loading && channels.length === 0 && (
        <div className="clean-panel rounded-3xl p-8 text-center text-sm text-slate-500">
          Chưa có kênh nguồn. Nhập tên, nhãn và dán link kênh rồi bấm Thêm kênh.
        </div>
      )}

      <div className="space-y-3">
        {channels.map((ch) => {
          const open = openId === ch.channel_id;
          const isEditing = editing?.channelId === ch.channel_id;
          const badge = PLATFORM_BADGE[String(ch.platform || '').toLowerCase()] || 'bg-slate-100 text-slate-700 border-slate-200';
          return (
            <section
              key={ch.channel_id}
              className={`clean-panel rounded-3xl overflow-hidden ${isEditing ? 'ring-2 ring-indigo-300' : ''}`}
            >
              <div className="p-4 sm:p-5 flex flex-col gap-3">
                <div className="flex items-start justify-between gap-3">
                  <button
                    type="button"
                    onClick={() => openChannel(ch)}
                    className="min-w-0 flex-1 text-left"
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-sm font-black text-slate-900 truncate">
                        {ch.name}
                      </h3>
                      <span className={`px-2 py-0.5 text-[10px] font-extrabold rounded-lg border ${badge}`}>
                        {platformLabel(ch.platform)}
                      </span>
                      <ChevronDown className={`w-4 h-4 text-slate-400 transition ${open ? 'rotate-180' : ''}`} />
                    </div>
                    {ch.url ? (
                      <a
                        href={ch.url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="mt-1 inline-flex items-center gap-1 text-[11px] font-bold text-indigo-700 hover:underline break-all"
                      >
                        <ExternalLink className="w-3 h-3 shrink-0" /> {ch.url}
                      </a>
                    ) : null}
                  </button>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      type="button"
                      onClick={() => (isEditing ? setEditing(null) : startEditChannel(ch))}
                      className="px-2.5 py-1.5 rounded-xl bg-indigo-50 text-indigo-700 hover:bg-indigo-100 text-[11px] font-extrabold flex items-center gap-1"
                    >
                      <Edit3 className="w-3.5 h-3.5" />
                      {isEditing ? 'Đóng' : 'Sửa kênh'}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(ch.channel_id)}
                      className="p-2 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-xl"
                      title="Xóa kênh khỏi danh mục"
                    >
                      {busy === `del-${ch.channel_id}` ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                    </button>
                  </div>
                </div>

                {isEditing && (
                  <form
                    onSubmit={handleSaveChannel}
                    className="rounded-2xl border border-indigo-200 bg-indigo-50/50 p-3 space-y-2"
                  >
                    <p className="text-[11px] font-extrabold text-indigo-900">Chỉnh sửa kênh</p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      <input
                        value={editing.name}
                        onChange={(e) => setEditing((p) => ({ ...p, name: e.target.value }))}
                        placeholder="Tên kênh"
                        className="w-full bg-white border border-slate-300 rounded-xl px-3 py-2 text-sm font-bold text-slate-900"
                      />
                      <div className="relative">
                        <Tag className="w-4 h-4 text-indigo-600 absolute left-3 top-2.5" />
                        <input
                          value={editing.tags}
                          onChange={(e) => setEditing((p) => ({ ...p, tags: e.target.value }))}
                          placeholder="Nhãn kênh (phẩy để tách)"
                          className="w-full bg-white border border-slate-300 rounded-xl pl-10 pr-3 py-2 text-sm text-slate-900"
                        />
                      </div>
                    </div>
                    <div className="relative">
                      <Link2 className="w-4 h-4 text-indigo-600 absolute left-3 top-2.5" />
                      <input
                        value={editing.url}
                        onChange={(e) => setEditing((p) => ({ ...p, url: e.target.value }))}
                        placeholder="Link kênh"
                        className="w-full bg-white border border-slate-300 rounded-xl pl-10 pr-3 py-2 text-sm font-mono text-slate-900"
                      />
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="submit"
                        disabled={busy === `edit-${ch.channel_id}`}
                        className="px-3 py-1.5 rounded-xl bg-indigo-600 text-white text-[11px] font-extrabold flex items-center gap-1.5 disabled:opacity-50"
                      >
                        {busy === `edit-${ch.channel_id}` ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Edit3 className="w-3.5 h-3.5" />}
                        Lưu kênh
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditing(null)}
                        className="px-3 py-1.5 rounded-xl border border-slate-200 bg-white text-[11px] font-extrabold text-slate-700 flex items-center gap-1"
                      >
                        <X className="w-3.5 h-3.5" /> Hủy
                      </button>
                      <p className="text-[10px] font-semibold text-slate-500">
                        Đổi link xong bấm Đồng bộ danh sách để lấy video mới.
                      </p>
                    </div>
                  </form>
                )}

                <div className="flex flex-wrap items-center gap-1.5">
                  {(ch.tags || []).length === 0 && !isEditing ? (
                    <span className="text-[10px] font-bold text-slate-400">Chưa có nhãn</span>
                  ) : null}
                  {(ch.tags || []).map((tag) => (
                    <span key={tag} className="px-2 py-0.5 rounded-lg bg-slate-100 text-slate-700 text-[10px] font-bold">
                      #{tag}
                    </span>
                  ))}
                </div>

                <div className="flex flex-wrap items-center gap-2 text-[11px] font-bold text-slate-600">
                  <span className="px-2 py-1 rounded-lg bg-slate-100">{ch.video_count || 0} video</span>
                  <span className="px-2 py-1 rounded-lg bg-emerald-50 text-emerald-800">{ch.posted_count || 0} đã đăng</span>
                  <span className="px-2 py-1 rounded-lg bg-amber-50 text-amber-800">{ch.unposted_count || 0} chưa đăng</span>
                  <span className="px-2 py-1 rounded-lg bg-indigo-50 text-indigo-800">{ch.downloaded_count || 0} đã tải</span>
                </div>

                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => handleSync(ch.channel_id)}
                    className="px-3 py-1.5 rounded-xl border border-slate-200 bg-white text-xs font-extrabold text-slate-800 flex items-center gap-1.5"
                  >
                    {busy === `sync-${ch.channel_id}` ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5 text-indigo-600" />}
                    Đồng bộ danh sách
                  </button>
                  <button
                    type="button"
                    onClick={() => handleFetch(ch.channel_id)}
                    className="px-3 py-1.5 rounded-xl bg-indigo-600 text-white text-xs font-extrabold flex items-center gap-1.5"
                  >
                    {busy === `fetch-${ch.channel_id}` ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
                    Lấy toàn bộ video
                  </button>
                </div>
              </div>

              {open && (
                <div className="border-t border-slate-100 bg-slate-50/70 p-4 sm:p-5 space-y-3">
                  <div className="flex flex-wrap gap-1.5">
                    {[
                      { id: 'all', label: 'Tất cả' },
                      { id: 'unposted', label: 'Chưa đăng' },
                      { id: 'posted', label: 'Đã đăng' },
                      { id: 'downloaded', label: 'Đã tải' },
                      { id: 'missing', label: 'Chưa tải' },
                    ].map((tab) => (
                      <button
                        key={tab.id}
                        type="button"
                        onClick={async () => {
                          setFilter(tab.id);
                          try {
                            await loadVideos(ch.channel_id, tab.id);
                          } catch (err) {
                            setError(err.message);
                          }
                        }}
                        className={`px-2.5 py-1 rounded-lg text-[10px] font-extrabold border ${
                          filter === tab.id
                            ? 'bg-indigo-600 text-white border-indigo-700'
                            : 'bg-white text-slate-700 border-slate-200'
                        }`}
                      >
                        {tab.label}
                      </button>
                    ))}
                  </div>

                  {busy === `open-${ch.channel_id}` && (
                    <div className="text-xs font-bold text-slate-500 flex items-center gap-2">
                      <Loader2 className="w-4 h-4 animate-spin" /> Đang lấy danh sách video...
                    </div>
                  )}

                  <div className="max-h-[520px] overflow-y-auto rounded-2xl border border-slate-200 bg-white divide-y divide-slate-100">
                    {visibleVideos.length === 0 && busy !== `open-${ch.channel_id}` && (
                      <p className="p-4 text-xs text-slate-500 font-medium">
                        {busy === `sync-${ch.channel_id}`
                          ? 'Đang đồng bộ và ghi danh sách vào database...'
                          : filter !== 'all'
                            ? 'Không có video trong bộ lọc này.'
                            : ch.video_count
                              ? 'Checklist đang tải. Nếu chưa thấy, bấm lại «Đồng bộ danh sách».'
                              : 'Chưa có video. Bấm «Đồng bộ danh sách» để lấy catalog từ kênh.'}
                      </p>
                    )}
                    {visibleVideos.map((vid) => (
                      <div key={vid.id} className="flex items-start gap-2.5 px-3 py-2.5">
                        <button
                          type="button"
                          onClick={() => handlePosted(vid, !vid.posted)}
                          className="mt-0.5 shrink-0"
                          title={vid.posted ? 'Bỏ đánh dấu đã đăng' : 'Đánh dấu đã đăng'}
                        >
                          {vid.posted ? (
                            <CheckSquare className="w-5 h-5 text-emerald-600" />
                          ) : (
                            <Square className="w-5 h-5 text-slate-300" />
                          )}
                        </button>
                        <div className="min-w-0 flex-1">
                          <p className="text-xs font-bold text-slate-900 line-clamp-2">
                            {vid.title || vid.video_id}
                          </p>
                          <p className="text-[10px] text-slate-500 font-medium mt-0.5 flex flex-wrap gap-x-2">
                            <span className="font-mono">{vid.video_id}</span>
                            {vid.posted ? (
                              <span className="text-emerald-700 font-extrabold">Đã đăng</span>
                            ) : (
                              <span className="text-amber-700 font-extrabold">Chưa đăng</span>
                            )}
                            {vid.downloaded ? (
                              <span className="text-indigo-700 font-extrabold">Đã tải</span>
                            ) : (
                              <span className="text-slate-400">Chưa tải</span>
                            )}
                            {vid.posted_auto ? <span className="text-emerald-600">· đã lên Fanpage</span> : null}
                            {formatPublishedAt(vid.published_at) ? (
                              <span title={vid.published_at}>Đăng {formatPublishedAt(vid.published_at)}</span>
                            ) : null}
                          </p>
                        </div>
                        <div className="flex items-center gap-1 shrink-0">
                          {vid.url ? (
                            <a
                              href={vid.url}
                              target="_blank"
                              rel="noreferrer"
                              className="p-1.5 text-slate-400 hover:text-indigo-700"
                              title="Mở trên nền tảng"
                            >
                              <ExternalLink className="w-4 h-4" />
                            </a>
                          ) : null}
                          {vid.downloaded ? (
                            <button
                              type="button"
                              onClick={() =>
                                onSelectForWorkbench?.({
                                  video_id: vid.video_id,
                                  platform: ch.platform,
                                  file_path: vid.file_path,
                                  title: vid.title,
                                })
                              }
                              className="p-1.5 text-blue-600 hover:bg-blue-50 rounded-lg"
                              title="Mở trong Studio"
                            >
                              <Play className="w-4 h-4" />
                            </button>
                          ) : (
                            <button
                              type="button"
                              onClick={() => handleFetch(ch.channel_id, [vid.video_id])}
                              className="p-1.5 text-indigo-600 hover:bg-indigo-50 rounded-lg"
                              title="Tải clip này"
                            >
                              <Download className="w-4 h-4" />
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </section>
          );
        })}
      </div>
    </div>
  );
}
