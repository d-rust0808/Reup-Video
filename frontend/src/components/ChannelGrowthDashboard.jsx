import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Eye,
  Heart,
  Loader2,
  MessageCircle,
  RefreshCw,
  Search,
  TrendingDown,
  TrendingUp,
  Users,
} from 'lucide-react';
import { fetchChannelGrowth, fetchChannelGrowthDetail, getMediaUrl, refreshChannelGrowth } from '../services/api';

const PERIODS = [
  { id: 7, label: '7 ngày' },
  { id: 30, label: '30 ngày' },
  { id: 90, label: '90 ngày' },
];

const METRICS = [
  { key: 'followers', label: 'Theo dõi', hint: 'Follow hiện tại so với đầu kỳ', icon: Users, color: '#2563eb', fill: 'rgba(37,99,235,0.16)', tone: 'bg-blue-50 text-blue-800 border-blue-200' },
  { key: 'likes', label: 'Like', hint: 'Tổng like trong kỳ', icon: Heart, color: '#e11d48', fill: 'rgba(225,29,72,0.14)', tone: 'bg-rose-50 text-rose-800 border-rose-200' },
  { key: 'comments', label: 'Bình luận', hint: 'Tổng comment trong kỳ', icon: MessageCircle, color: '#d97706', fill: 'rgba(217,119,6,0.16)', tone: 'bg-amber-50 text-amber-900 border-amber-200' },
  { key: 'views', label: 'Lượt xem', hint: 'Tổng view video/reel trong kỳ', icon: Eye, color: '#059669', fill: 'rgba(5,150,105,0.16)', tone: 'bg-emerald-50 text-emerald-800 border-emerald-200' },
];

function formatCount(n) {
  const v = Number(n) || 0;
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `${(v / 1_000_000).toFixed(1).replace(/\.0$/, '')}Tr`;
  if (abs >= 1_000) return `${(v / 1_000).toFixed(1).replace(/\.0$/, '')}k`;
  return String(Math.round(v));
}

function formatDate(iso) {
  if (!iso) return '';
  const parts = String(iso).slice(0, 10).split('-');
  if (parts.length !== 3) return String(iso).slice(0, 10);
  return `${parts[2]}/${parts[1]}`;
}

function deltaTone(delta) {
  if (delta > 0) return 'text-emerald-700 bg-emerald-50 border-emerald-200';
  if (delta < 0) return 'text-rose-700 bg-rose-50 border-rose-200';
  return 'text-slate-600 bg-slate-50 border-slate-200';
}

function GrowthChart({ series = [], color, fill, height = 148 }) {
  const raw = (series || []).map((p) => Number(p?.value) || 0);
  const dates = (series || []).map((p) => p?.date || '');
  if (!raw.length) {
    return (
      <div className="h-[148px] flex items-center justify-center text-xs text-slate-400 font-medium">
        Chưa có mốc lịch sử
      </div>
    );
  }
  const values = raw.length === 1 ? [raw[0], raw[0]] : raw;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = (max - min) * 0.12 || Math.max(1, max * 0.08);
  const yMin = Math.max(0, min - pad);
  const yMax = max + pad;
  const w = 360;
  const h = 118;
  const left = 6;
  const inner = w - 12;
  const pts = values.map((v, i) => {
    const x = left + (values.length === 1 ? inner / 2 : (i / (values.length - 1)) * inner);
    const y = 8 + (1 - (v - yMin) / (yMax - yMin || 1)) * (h - 16);
    return [x, y];
  });
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const area = `M${pts[0][0].toFixed(1)},${h} ${pts.map((p) => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ')} L${pts[pts.length - 1][0].toFixed(1)},${h} Z`;
  const last = pts[pts.length - 1];
  const firstDate = dates[0];
  const lastDate = dates[dates.length - 1];

  return (
    <svg viewBox={`0 0 ${w} ${height}`} className="w-full h-[148px]" role="img">
      <path d={area} fill={fill} />
      <path d={line} fill="none" stroke={color} strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={last[0]} cy={last[1]} r="3.4" fill="#fff" stroke={color} strokeWidth="2" />
      <text x="8" y="14" fontSize="10" fontWeight="700" fill="#64748b">
        {formatCount(max)}
      </text>
      <text x="8" y={height - 6} fontSize="10" fontWeight="600" fill="#94a3b8">
        {formatDate(firstDate)}
      </text>
      <text x={w - 8} y={height - 6} fontSize="10" fontWeight="600" fill="#94a3b8" textAnchor="end">
        {formatDate(lastDate)}
      </text>
    </svg>
  );
}

function MiniSpark({ series = [], color = '#2563eb' }) {
  const values = (series || []).map((p) => Number(p?.value) || 0);
  if (values.every((v) => v === 0)) {
    return <div className="h-7 w-16 rounded bg-slate-100" />;
  }
  const pts = values.length === 1 ? [values[0], values[0]] : values;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const w = 64;
  const h = 28;
  const d = pts.map((v, i) => {
    const x = pts.length === 1 ? w / 2 : (i / (pts.length - 1)) * w;
    const y = h - 2 - ((v - min) / (max - min || 1)) * (h - 4);
    return `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-16 h-7 shrink-0">
      <path d={d} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}

function ChannelAvatar({ channel }) {
  const src = channel?.picture_url ? getMediaUrl(channel.picture_url) : '';
  const [broken, setBroken] = useState(false);
  useEffect(() => { setBroken(false); }, [src]);
  const initials = String(channel?.name || '?').trim().slice(0, 2).toUpperCase();
  if (src && !broken) {
    return (
      <img
        src={src}
        alt=""
        onError={() => setBroken(true)}
        className="w-10 h-10 rounded-xl object-cover shrink-0 bg-slate-200 ring-1 ring-black/5"
      />
    );
  }
  return (
    <div className="w-10 h-10 rounded-xl bg-blue-600 text-white flex items-center justify-center text-[11px] font-black shrink-0">
      {initials}
    </div>
  );
}

export function ChannelGrowthDashboard() {
  const [days, setDays] = useState(30);
  const [overview, setOverview] = useState(null);
  const [posts, setPosts] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [groupId, setGroupId] = useState('');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const pollRef = useRef(null);
  const autoRefreshRef = useRef(new Set());

  const load = useCallback(async () => {
    try {
      const data = await fetchChannelGrowth(days);
      setOverview(data);
      setError('');
      return data;
    } catch (err) {
      setError(err.message || 'Không tải được dashboard');
      return null;
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  const channels = overview?.channels || [];
  const groups = overview?.groups || [];
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return channels.filter((ch) => {
      if (groupId && !(ch.group_ids || []).includes(groupId)) return false;
      if (q && !String(ch.name || '').toLowerCase().includes(q) && !String(ch.username || '').toLowerCase().includes(q)) {
        return false;
      }
      return true;
    });
  }, [channels, groupId, query]);
  const refreshState = overview?.refresh || {};
  const busy = refreshing || !!refreshState.running;

  useEffect(() => {
    const running = !!overview?.refresh?.running;
    if (!running) return undefined;
    pollRef.current = window.setTimeout(() => { load(); }, 2000);
    return () => window.clearTimeout(pollRef.current);
  }, [overview?.refresh?.running, overview?.refresh?.done, load]);

  useEffect(() => {
    if (!selectedId || busy) return;
    const channel = (overview?.channels || []).find((ch) => ch.channel_id === selectedId);
    if (!channel?.page_id || !channel.stale) return;
    if (autoRefreshRef.current.has(selectedId)) return;
    autoRefreshRef.current.add(selectedId);
    setRefreshing(true);
    refreshChannelGrowth(selectedId, 90)
      .then((data) => {
        if (data.posts) setPosts(data.posts);
        return load();
      })
      .catch((err) => setError(err.message || 'Không kéo được số liệu kênh này'))
      .finally(() => setRefreshing(false));
  }, [selectedId, overview, busy, load]);

  useEffect(() => {
    if (!filtered.length) {
      setSelectedId('');
      return;
    }
    if (!filtered.some((ch) => ch.channel_id === selectedId)) {
      setSelectedId(filtered[0].channel_id);
    }
  }, [filtered, selectedId]);

  useEffect(() => {
    if (!selectedId) {
      setPosts([]);
      return undefined;
    }
    let cancelled = false;
    fetchChannelGrowthDetail(selectedId, days)
      .then((data) => {
        if (!cancelled) setPosts(data.posts || []);
      })
      .catch(() => {
        if (!cancelled) setPosts([]);
      });
    return () => { cancelled = true; };
  }, [selectedId, days, overview?.refresh?.done, overview?.refresh?.finished_at]);

  const selected = filtered.find((ch) => ch.channel_id === selectedId) || channels.find((ch) => ch.channel_id === selectedId);

  const handleRefreshAll = async () => {
    setRefreshing(true);
    setError('');
    try {
      await refreshChannelGrowth('', 90);
      await load();
    } catch (err) {
      setError(err.message || 'Không kéo được số liệu');
    } finally {
      setRefreshing(false);
    }
  };

  const handleRefreshOne = async () => {
    if (!selectedId) return;
    setRefreshing(true);
    setError('');
    try {
      const data = await refreshChannelGrowth(selectedId, 90);
      if (data.posts) setPosts(data.posts);
      await load();
    } catch (err) {
      setError(err.message || 'Không kéo được số liệu kênh này');
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-3xl border border-slate-200/90 p-4 md:p-5 shadow-xs flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 className="text-lg font-extrabold text-slate-900 tracking-tight">Tăng trưởng theo kênh</h2>
          <p className="text-xs text-slate-500 font-medium mt-0.5">
            Follow, like, comment, view của từng Fanpage. App lưu mốc mỗi ngày để vẽ đường tăng trưởng.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-xl border border-slate-200 bg-slate-50 p-0.5">
            {PERIODS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setDays(item.id)}
                className={`px-3 py-1.5 text-xs font-bold rounded-lg ${
                  days === item.id ? 'bg-white text-slate-900 shadow-xs' : 'text-slate-500 hover:text-slate-800'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={handleRefreshAll}
            disabled={busy}
            className="px-3.5 py-2 rounded-xl text-xs font-extrabold bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white flex items-center gap-1.5"
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            {refreshState.running
              ? `Đang kéo ${refreshState.done}/${refreshState.total || '…'}`
              : 'Làm mới tất cả'}
          </button>
        </div>
      </div>

      {error ? (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-xs font-semibold text-rose-800">
          {error}
        </div>
      ) : null}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <aside className="lg:col-span-4 xl:col-span-3 bg-white rounded-3xl border border-slate-200/90 shadow-xs overflow-hidden flex flex-col min-h-[520px]">
          <div className="p-3.5 border-b border-slate-100 space-y-2.5">
            <div className="relative">
              <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Tìm Fanpage…"
                className="w-full pl-8 pr-3 py-2 rounded-xl border border-slate-200 text-xs font-semibold bg-slate-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-emerald-200"
              />
            </div>
            {groups.length > 0 && (
              <select
                value={groupId}
                onChange={(e) => setGroupId(e.target.value)}
                className="w-full px-3 py-2 rounded-xl border border-slate-200 text-xs font-bold bg-white text-slate-700"
              >
                <option value="">Tất cả nhóm</option>
                {groups.map((g) => (
                  <option key={g.group_id} value={g.group_id}>{g.name}</option>
                ))}
              </select>
            )}
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
            {loading && !channels.length ? (
              <div className="py-12 text-center text-xs text-slate-400 font-semibold">Đang tải kênh…</div>
            ) : !filtered.length ? (
              <div className="py-12 px-4 text-center text-xs text-slate-500 font-medium">
                Chưa có Fanpage. Vào tab Fanpage & Đăng Bài để đồng bộ page.
              </div>
            ) : (
              filtered.map((ch) => {
                const active = ch.channel_id === selectedId;
                const follow = ch.followers || {};
                return (
                  <button
                    key={ch.channel_id}
                    type="button"
                    onClick={() => setSelectedId(ch.channel_id)}
                    className={`w-full text-left p-2.5 rounded-2xl border flex items-center gap-2.5 ${
                      active
                        ? 'bg-emerald-50/80 border-emerald-400 ring-1 ring-emerald-500/20'
                        : 'border-transparent hover:bg-slate-50 hover:border-slate-200'
                    }`}
                  >
                    <ChannelAvatar channel={ch} />
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-extrabold text-slate-900 truncate">{ch.name}</p>
                      <p className="text-[11px] text-slate-500 font-semibold truncate">
                        {formatCount(follow.current || 0)} follow
                        {follow.delta ? ` · ${follow.delta > 0 ? '+' : ''}${formatCount(follow.delta)}` : ''}
                      </p>
                    </div>
                    <MiniSpark series={follow.series} color="#059669" />
                  </button>
                );
              })
            )}
          </div>
        </aside>

        <section className="lg:col-span-8 xl:col-span-9 space-y-4">
          {selected ? (
            <>
              <div className="bg-white rounded-3xl border border-slate-200/90 p-4 md:p-5 shadow-xs flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="flex items-center gap-3 min-w-0">
                  <ChannelAvatar channel={selected} />
                  <div className="min-w-0">
                    <h3 className="text-base font-extrabold text-slate-900 truncate">{selected.name}</h3>
                    <p className="text-xs text-slate-500 font-medium truncate">
                      {[selected.username ? `@${selected.username}` : '', selected.category, selected.last_snapshot_at ? `mốc ${formatDate(selected.last_snapshot_at)}` : '']
                        .filter(Boolean)
                        .join(' · ') || 'Facebook Fanpage'}
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={handleRefreshOne}
                  disabled={busy || !selected.page_id}
                  className="px-3.5 py-2 rounded-xl text-xs font-bold border border-slate-200 bg-white hover:bg-slate-50 disabled:opacity-50 flex items-center gap-1.5"
                >
                  {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                  Làm mới kênh này
                </button>
              </div>

              {!selected.page_id ? (
                <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs font-semibold text-amber-900">
                  Kênh này chưa gắn Fanpage nên chưa có follow/like/comment/view từ Facebook.
                </div>
              ) : null}

              <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
                {METRICS.map((metric) => {
                  const Icon = metric.icon;
                  const data = selected[metric.key] || {};
                  const DeltaIcon = (data.delta || 0) >= 0 ? TrendingUp : TrendingDown;
                  return (
                    <div key={metric.key} className="bg-white rounded-2xl border border-slate-200/90 p-3.5 shadow-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold border ${metric.tone}`}>
                          <Icon className="w-3 h-3" /> {metric.label}
                        </span>
                        <span className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md text-[10px] font-extrabold border ${deltaTone(data.delta || 0)}`}>
                          <DeltaIcon className="w-3 h-3" />
                          {(data.delta || 0) > 0 ? '+' : ''}{formatCount(data.delta || 0)}
                          {data.delta_pct != null ? ` · ${data.delta_pct > 0 ? '+' : ''}${data.delta_pct}%` : ''}
                        </span>
                      </div>
                      <p className="text-2xl font-black text-slate-900 tracking-tight mt-2">
                        {formatCount(data.current || 0)}
                      </p>
                      <p className="text-[11px] text-slate-500 font-medium mt-0.5">{metric.hint}</p>
                    </div>
                  );
                })}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {METRICS.map((metric) => {
                  const data = selected[metric.key] || {};
                  return (
                    <div key={`${metric.key}-chart`} className="bg-white rounded-3xl border border-slate-200/90 p-4 shadow-xs">
                      <div className="flex items-center justify-between mb-1">
                        <h4 className="text-sm font-extrabold text-slate-900">{metric.label}</h4>
                        <span className="text-[11px] font-bold text-slate-500">{days} ngày</span>
                      </div>
                      <GrowthChart series={data.series} color={metric.color} fill={metric.fill} />
                    </div>
                  );
                })}
              </div>

              <div className="bg-white rounded-3xl border border-slate-200/90 shadow-xs overflow-hidden">
                <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
                  <h4 className="text-sm font-extrabold text-slate-900">Bài đăng gần đây</h4>
                  <span className="text-[11px] font-bold text-slate-500">{posts.length} bài</span>
                </div>
                {!posts.length ? (
                  <p className="px-4 py-8 text-xs text-slate-500 font-medium text-center">
                    Chưa có bài. Bấm «Làm mới kênh này» để kéo like / comment / view từ Facebook.
                  </p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead className="bg-slate-50 text-slate-500 font-bold">
                        <tr>
                          <th className="text-left px-4 py-2">Ngày</th>
                          <th className="text-left px-4 py-2">Nội dung</th>
                          <th className="text-right px-3 py-2">Like</th>
                          <th className="text-right px-3 py-2">Comment</th>
                          <th className="text-right px-4 py-2">View</th>
                        </tr>
                      </thead>
                      <tbody>
                        {posts.map((post) => (
                          <tr key={post.post_id} className="border-t border-slate-100">
                            <td className="px-4 py-2.5 text-slate-500 font-semibold whitespace-nowrap">
                              {formatDate(post.created_time)}
                            </td>
                            <td className="px-4 py-2.5 font-semibold text-slate-800">
                              {post.permalink ? (
                                <a href={post.permalink} target="_blank" rel="noreferrer" className="hover:text-blue-700 line-clamp-2">
                                  {post.title || 'Bài không tiêu đề'}
                                </a>
                              ) : (
                                <span className="line-clamp-2">{post.title || 'Bài không tiêu đề'}</span>
                              )}
                            </td>
                            <td className="px-3 py-2.5 text-right font-bold text-rose-700">{formatCount(post.likes)}</td>
                            <td className="px-3 py-2.5 text-right font-bold text-amber-700">{formatCount(post.comments)}</td>
                            <td className="px-4 py-2.5 text-right font-bold text-emerald-700">{formatCount(post.views)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="bg-white rounded-3xl border border-slate-200/90 p-12 text-center shadow-xs">
              <TrendingUp className="w-10 h-10 text-slate-300 mx-auto mb-3" />
              <p className="text-sm font-extrabold text-slate-700">Chọn một kênh để xem biểu đồ</p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
