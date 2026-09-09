import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Film,
  FolderOpen,
  Hash,
  Loader2,
  Send,
  ShoppingBag,
  Trash2,
  Type,
  Upload,
} from 'lucide-react';
import { fetchChannelGroups, fetchPublishLog, publishOriginalVideos } from '../services/api';
import { loadSession, saveSession } from '../services/session';
import { Toast } from './Toast';

function parseHashtagInput(raw) {
  return String(raw || '')
    .split(/[\s,]+/)
    .map((tag) => tag.replace(/^#/, '').trim())
    .filter(Boolean)
    .slice(0, 12);
}

function formatBytesFixed(n) {
  const size = Number(n) || 0;
  if (size >= 1024 * 1024 * 1024) return `${(size / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${size} B`;
}

export function OriginalPublish() {
  const boot = loadSession().originalPublish || {};
  const [files, setFiles] = useState([]);
  const [groups, setGroups] = useState([]);
  const [picked, setPicked] = useState(boot.group_ids || []);
  const [title, setTitle] = useState(boot.title || '');
  const [caption, setCaption] = useState(boot.caption || '');
  const [hashtags, setHashtags] = useState(boot.hashtags || '');
  const [intent, setIntent] = useState(boot.post_intent || '');
  const [affiliateLink, setAffiliateLink] = useState(boot.affiliate_link || '');
  const [affiliateProduct, setAffiliateProduct] = useState(boot.affiliate_product || '');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const [log, setLog] = useState([]);
  const fileRef = useRef(null);

  const persist = useCallback((patch) => {
    const prev = loadSession().originalPublish || {};
    saveSession({ originalPublish: { ...prev, ...patch } });
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [g, p] = await Promise.all([fetchChannelGroups(), fetchPublishLog(20)]);
      setGroups(g.groups || []);
      setLog((p.events || []).filter((ev) => String(ev.job_id || '').startsWith('job_orig_')));
    } catch (err) {
      setToast({ type: 'error', title: 'Lỗi', message: err.message || 'Không tải được nhóm' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    persist({
      group_ids: picked,
      title,
      caption,
      hashtags,
      post_intent: intent,
      affiliate_link: affiliateLink,
      affiliate_product: affiliateProduct,
    });
  }, [picked, title, caption, hashtags, intent, affiliateLink, affiliateProduct, persist]);

  const addFiles = (incoming) => {
    const next = [];
    const seen = new Set(files.map((f) => f.path || f.name));
    for (const item of incoming) {
      const path = item.path || item;
      const name = item.name || String(path).split(/[/\\]/).pop();
      const key = path || name;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      next.push({
        path,
        name,
        size: item.size || 0,
      });
    }
    if (next.length) setFiles((prev) => [...prev, ...next]);
  };

  const pickFromDisk = async () => {
    if (window.electronAPI?.selectVideoFiles) {
      const paths = await window.electronAPI.selectVideoFiles();
      if (Array.isArray(paths) && paths.length) {
        addFiles(paths.map((path) => ({ path, name: String(path).split(/[/\\]/).pop() })));
      }
      return;
    }
    fileRef.current?.click();
  };

  const onHtmlFiles = (event) => {
    const list = Array.from(event.target.files || []);
    addFiles(list.map((file) => ({
      path: file.path || '',
      name: file.name,
      size: file.size,
      file,
    })));
    event.target.value = '';
  };

  const toggleGroup = (groupId) => {
    setPicked((prev) => (prev.includes(groupId) ? prev.filter((id) => id !== groupId) : [...prev, groupId]));
  };

  const selected = groups.filter((g) => picked.includes(g.group_id));
  const pageCount = selected.reduce((sum, g) => sum + (g.member_count || (g.channel_ids || []).length || 0), 0);
  const tags = parseHashtagInput(hashtags);
  const canSubmit = files.length > 0 && picked.length > 0 && !busy;

  const submit = async () => {
    if (!canSubmit) return;
    const missingPath = files.some((f) => !f.path);
    if (missingPath) {
      setToast({
        type: 'error',
        title: 'Cần đường dẫn file',
        message: 'Mở app desktop rồi chọn video từ máy. Trình duyệt web không gửi được path local.',
      });
      return;
    }
    setBusy(true);
    try {
      const res = await publishOriginalVideos({
        paths: files.map((f) => f.path),
        group_ids: picked,
        title,
        caption,
        hashtags: tags,
        post_intent: intent,
        affiliate_link: affiliateLink,
        affiliate_product: affiliateProduct,
      });
      setToast({ type: 'success', title: 'Đã xếp đăng', message: res.message });
      setFiles([]);
      window.dispatchEvent(new Event('reup:channels-changed'));
      await load();
    } catch (err) {
      setToast({ type: 'error', title: 'Không đăng được', message: err.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <Toast toast={toast} onClose={() => setToast(null)} />

      <section className="bg-white rounded-3xl border border-slate-200 shadow-xs p-5 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-extrabold text-slate-900 flex items-center gap-2">
              <Film className="w-5 h-5 text-emerald-600" />
              Đăng video tự làm
            </h2>
            <p className="text-xs text-slate-500 font-medium mt-1">
              Không reup. Chọn file từ máy, điền title / hashtag / giỏ hàng, đăng thẳng nhóm Fanpage.
            </p>
          </div>
          <button
            type="button"
            onClick={pickFromDisk}
            className="px-3.5 py-2 rounded-xl bg-slate-900 hover:bg-slate-800 text-white text-xs font-extrabold flex items-center gap-1.5"
          >
            <FolderOpen className="w-3.5 h-3.5" />
            Chọn video máy
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="video/mp4,video/quicktime,video/*"
            multiple
            className="hidden"
            onChange={onHtmlFiles}
          />
        </div>

        <div
          className="rounded-2xl border border-dashed border-slate-300 bg-slate-50/70 p-4 min-h-[140px]"
          onDragOver={(e) => { e.preventDefault(); }}
          onDrop={(e) => {
            e.preventDefault();
            const dropped = Array.from(e.dataTransfer.files || []).filter((f) => f.type.startsWith('video/') || /\.(mp4|mov|mkv|webm|avi)$/i.test(f.name));
            addFiles(dropped.map((file) => ({ path: file.path || '', name: file.name, size: file.size, file })));
          }}
        >
          {!files.length ? (
            <button type="button" onClick={pickFromDisk} className="w-full py-8 text-center text-xs text-slate-500 font-semibold">
              <Upload className="w-6 h-6 mx-auto mb-2 text-slate-400" />
              Kéo video vào đây hoặc bấm chọn từ máy — danh sách hiện bên dưới
            </button>
          ) : (
            <ul className="space-y-2">
              {files.map((file, index) => (
                <li key={`${file.path || file.name}-${index}`} className="flex items-center justify-between gap-3 bg-white border border-slate-200 rounded-xl px-3 py-2">
                  <div className="min-w-0">
                    <p className="text-xs font-extrabold text-slate-900 truncate">{file.name}</p>
                    <p className="text-[10px] text-slate-500 font-medium truncate">
                      {file.size ? formatBytesFixed(file.size) : 'Video máy'}
                      {file.path ? ` · ${file.path}` : ''}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setFiles((prev) => prev.filter((_, i) => i !== index))}
                    className="p-1.5 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <section className="bg-white rounded-3xl border border-slate-200 shadow-xs p-5 space-y-4">
          <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-800">Title, hashtag, nội dung</h3>
          <label className="block text-[11px] font-bold text-slate-700">
            <span className="inline-flex items-center gap-1"><Type className="w-3.5 h-3.5" /> Title</span>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Hook ngắn cho Reel"
              className="mt-1 w-full text-xs font-semibold bg-slate-50 border border-slate-200 rounded-xl px-3 py-2"
            />
          </label>
          <label className="block text-[11px] font-bold text-slate-700">
            Caption (tuỳ chọn)
            <textarea
              rows={3}
              value={caption}
              onChange={(e) => setCaption(e.target.value)}
              placeholder="2–4 câu mô tả. Để trống nếu chỉ dùng title + hashtag."
              className="mt-1 w-full text-xs font-semibold bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 resize-none"
            />
          </label>
          <label className="block text-[11px] font-bold text-slate-700">
            <span className="inline-flex items-center gap-1"><Hash className="w-3.5 h-3.5" /> Hashtag</span>
            <input
              value={hashtags}
              onChange={(e) => setHashtags(e.target.value)}
              placeholder="reviewphim, phimhay, xuhuong"
              className="mt-1 w-full text-xs font-semibold bg-slate-50 border border-slate-200 rounded-xl px-3 py-2"
            />
            {tags.length > 0 && (
              <p className="mt-1.5 text-[10px] text-slate-500 font-medium">
                {tags.map((tag) => `#${tag}`).join(' ')}
              </p>
            )}
          </label>
          <label className="block text-[11px] font-bold text-slate-700">
            Nội dung hướng tới (tuỳ chọn — agy viết bài khác nhau cho từng page)
            <textarea
              rows={3}
              value={intent}
              onChange={(e) => setIntent(e.target.value)}
              placeholder="Cần đập phá tháo dỡ nhà, lột gạch liên hệ 0777704099"
              className="mt-1 w-full text-xs font-semibold bg-white border border-slate-200 rounded-xl px-3 py-2 resize-none"
            />
          </label>

          <div className="rounded-2xl border border-orange-100 bg-orange-50/70 p-3 space-y-2">
            <div className="flex items-center gap-1.5 text-[11px] font-extrabold text-slate-800">
              <ShoppingBag className="w-3.5 h-3.5 text-orange-600" />
              Giỏ hàng Shopee / link tiếp thị
            </div>
            <input
              type="url"
              value={affiliateLink}
              onChange={(e) => setAffiliateLink(e.target.value)}
              placeholder="https://shopee.vn/ten-sp-i.123.456 hoặc https://shp.ee/..."
              className="w-full text-xs font-semibold bg-white border border-orange-200 rounded-xl px-3 py-2"
            />
            <input
              type="text"
              value={affiliateProduct}
              onChange={(e) => setAffiliateProduct(e.target.value)}
              placeholder="Tên sản phẩm, vd. giấy vệ sinh"
              className="w-full text-xs font-semibold bg-white border border-orange-200 rounded-xl px-3 py-2"
            />
            {affiliateLink.trim() ? (
              <p className="text-[10px] text-slate-600 font-medium leading-snug whitespace-pre-wrap">
                {affiliateLink.trim()}{'\n\n'}Bạn cần mua {affiliateProduct.trim() || 'sản phẩm'} hãy ủng hộ shop qua link: {affiliateLink.trim()}
              </p>
            ) : (
              <p className="text-[10px] text-slate-400 font-medium">Để trống nếu video này không gắn link.</p>
            )}
          </div>
        </section>

        <section className="bg-white rounded-3xl border border-slate-200 shadow-xs p-5 space-y-4">
          <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-800">Nhóm Fanpage</h3>
          {loading ? (
            <p className="text-xs text-slate-500 font-semibold flex items-center gap-2">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Đang tải nhóm…
            </p>
          ) : !groups.length ? (
            <p className="text-sm text-slate-600 font-medium bg-amber-50 border border-amber-100 rounded-2xl p-4">
              Chưa có nhóm. Vào tab Fanpage & Đăng Bài tạo nhóm rồi quay lại đây.
            </p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {groups.map((g) => {
                const on = picked.includes(g.group_id);
                const count = g.member_count || (g.channel_ids || []).length || 0;
                return (
                  <button
                    key={g.group_id}
                    type="button"
                    onClick={() => toggleGroup(g.group_id)}
                    className={`px-3 py-1.5 rounded-xl text-xs font-extrabold border ${
                      on
                        ? 'bg-emerald-600 text-white border-emerald-700'
                        : 'bg-white text-slate-700 border-slate-200 hover:border-emerald-300'
                    }`}
                  >
                    {g.name} ({count})
                  </button>
                );
              })}
            </div>
          )}

          <p className="text-[11px] font-semibold text-slate-600 bg-slate-50 border border-slate-200 rounded-xl px-3 py-2">
            Clip &gt; 90s sẽ cắt còn 90s, không 9:16 thì crop dọc. Facebook vẫn quét bản quyền trên nháp trước khi lên page.
          </p>

          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            className="w-full px-5 py-3 rounded-2xl bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-extrabold text-sm flex items-center justify-center gap-2"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            {busy
              ? 'Đang chuẩn hoá & xếp đăng…'
              : `Đăng ${files.length || 0} video lên ${pageCount || 0} page`}
          </button>

          {log.length > 0 && (
            <div className="space-y-2 pt-2 border-t border-slate-100">
              <p className="text-[11px] font-extrabold text-slate-700">Lần đăng video tự làm gần đây</p>
              {log.slice(0, 8).map((ev) => (
                <article key={ev.id} className="rounded-xl border border-slate-200 p-2.5 text-[11px]">
                  <div className="flex items-center justify-between gap-2">
                    <strong className="truncate">{ev.page_name || ev.channel_name}</strong>
                    <span className={`font-bold ${ev.status === 'PUBLISHED' ? 'text-emerald-600' : ev.status === 'COPYRIGHT_BLOCKED' ? 'text-rose-700' : 'text-amber-600'}`}>
                      {ev.status}
                    </span>
                  </div>
                  {ev.title ? <p className="font-bold text-slate-800 mt-0.5 line-clamp-1">{ev.title}</p> : null}
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
