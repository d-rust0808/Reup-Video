import React, { useCallback, useEffect, useState } from 'react';
import {
  bindFacebookPage,
  fetchFacebookPages,
  fetchFacebookSettings,
  fetchFacebookShopStatus,
  importFacebookPage,
  saveFacebookSettings,
  syncFacebookPages,
} from '../services/api';
import { CheckCircle2, KeyRound, Loader2, RefreshCw, Search, Send, ShieldCheck, ShoppingBag } from 'lucide-react';

const EMPTY_FORM = {
  app_id: '',
  app_secret: '',
  user_token: '',
  graph_version: 'v24.0',
  exchange_token: true,
};

export function FacebookPublishingPanel({ activeChannel, onChanged }) {
  const [settings, setSettings] = useState(null);
  const [pages, setPages] = useState([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [selectedPageId, setSelectedPageId] = useState('');
  const [pageQuery, setPageQuery] = useState('');
  const [importRef, setImportRef] = useState('');
  const [autoPublish, setAutoPublish] = useState(true);
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState(null);
  const [shopScan, setShopScan] = useState(null);

  const load = useCallback(async () => {
    try {
      const [settingsData, pagesData] = await Promise.all([
        fetchFacebookSettings(),
        fetchFacebookPages(),
      ]);
      setSettings(settingsData);
      setPages(pagesData.pages || []);
      setForm((prev) => ({
        ...prev,
        app_id: settingsData.app_id || prev.app_id,
        graph_version: settingsData.graph_version || prev.graph_version,
      }));
      if ((pagesData.pages || []).length > 0) await onChanged?.();
    } catch (error) {
      setMessage({ type: 'error', text: error.message });
    }
  }, [onChanged]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await load();
      try {
        const settingsData = await fetchFacebookSettings();
        if (cancelled || !settingsData?.connected) return;
        const result = await syncFacebookPages();
        if (cancelled) return;
        setMessage({ type: 'success', text: result.message });
        await load();
      } catch (error) {
        if (!cancelled) {
          setMessage({ type: 'error', text: error.message || 'Đồng bộ page từ Facebook thất bại' });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  useEffect(() => {
    setSelectedPageId(activeChannel?.facebook_page_id || '');
    setAutoPublish(activeChannel?.facebook_auto_publish ?? true);
  }, [activeChannel]);

  const handleConnect = async (event) => {
    event.preventDefault();
    setBusy('connect');
    setMessage(null);
    try {
      const result = await saveFacebookSettings(form);
      setMessage({ type: 'success', text: result.message });
      setForm((prev) => ({ ...prev, app_secret: '', user_token: '' }));
      await load();
    } catch (error) {
      setMessage({ type: 'error', text: error.message });
    } finally {
      setBusy('');
    }
  };

  const handleSync = async () => {
    setBusy('sync');
    setMessage(null);
    try {
      const result = await syncFacebookPages();
      setMessage({ type: 'success', text: result.message });
      await load();
    } catch (error) {
      setMessage({ type: 'error', text: error.message });
    } finally {
      setBusy('');
    }
  };

  const handleImport = async () => {
    const value = importRef.trim();
    if (!value) return;
    setBusy('import');
    setMessage(null);
    try {
      const result = await importFacebookPage(value);
      setMessage({ type: 'success', text: result.message });
      setImportRef('');
      if (result.page_id) setSelectedPageId(result.page_id);
      await load();
    } catch (error) {
      setMessage({ type: 'error', text: error.message });
    } finally {
      setBusy('');
    }
  };

  const handleShopScan = async () => {
    setBusy('shop');
    setMessage(null);
    try {
      const data = await fetchFacebookShopStatus();
      setShopScan(data);
      setMessage({ type: 'success', text: data.message || 'Đã quét xong Fanpage' });
    } catch (error) {
      setMessage({ type: 'error', text: error.message });
    } finally {
      setBusy('');
    }
  };

  const handleBind = async () => {
    if (!activeChannel || !selectedPageId) return;
    setBusy('bind');
    setMessage(null);
    try {
      const result = await bindFacebookPage(activeChannel.channel_id, {
        page_id: selectedPageId,
        auto_publish: autoPublish,
      });
      setMessage({ type: 'success', text: result.message });
      await onChanged?.();
    } catch (error) {
      setMessage({ type: 'error', text: error.message });
    } finally {
      setBusy('');
    }
  };

  const connected = settings?.connected;
  const isFacebookChannel = activeChannel?.platform === 'facebook';

  return (
    <section className="rounded-3xl border border-blue-200 bg-gradient-to-br from-blue-950 via-blue-900 to-sky-800 text-white shadow-lg overflow-hidden">
      <div className="grid grid-cols-1 xl:grid-cols-2">
        <div className="p-5 md:p-6 border-b xl:border-b-0 xl:border-r border-white/10 space-y-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-xs font-extrabold uppercase tracking-wider text-blue-200">
                <KeyRound className="w-4 h-4" /> Facebook Graph API
              </div>
              <h3 className="text-lg font-black mt-1">App, token và Fanpage</h3>
              <p className="text-xs text-blue-100/75 mt-1">
                Secret được lưu trong Keychain/Credential Manager, không trả lại giao diện.
              </p>
            </div>
            <span className={`px-2.5 py-1 rounded-full text-[10px] font-extrabold border ${
              settings?.status === 'EXPIRED'
                ? 'bg-rose-400/15 text-rose-200 border-rose-300/30'
                : connected
                ? 'bg-emerald-400/15 text-emerald-200 border-emerald-300/30'
                : 'bg-amber-300/10 text-amber-200 border-amber-200/20'
            }`}>
              {settings?.status === 'EXPIRED'
                ? 'Token hết hạn — kết nối lại'
                : connected
                  ? `Đã kết nối · ${settings.page_count || 0} Page`
                  : 'Chưa kết nối'}
            </span>
          </div>

          {settings?.status === 'EXPIRED' && (
            <div className="rounded-xl bg-rose-400/12 border border-rose-300/25 px-3 py-2 text-[11px] text-rose-100">
              Session Facebook đã bị thu hồi (đổi mật khẩu hoặc Facebook reset token).
              Dán user token mới bên dưới rồi kết nối lại — job reup vừa rồi không dính bản quyền.
              {settings.last_error ? ` (${settings.last_error})` : ''}
            </div>
          )}

          {connected && settings.token_expires_at && (
            <div className="flex items-center justify-between gap-3 rounded-xl bg-white/8 border border-white/10 px-3 py-2 text-[11px] text-blue-100">
              <span>Token: <strong>{settings.token_kind === 'LONG_LIVED' ? 'Long-lived' : 'Short-lived'}</strong></span>
              <span>Hết hạn: <strong>{new Date(settings.token_expires_at).toLocaleString('vi-VN')}</strong></span>
            </div>
          )}

          <form onSubmit={handleConnect} className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
            <input
              required
              value={form.app_id}
              onChange={(e) => setForm({ ...form, app_id: e.target.value })}
              placeholder="Facebook App ID"
              className="px-3 py-2.5 rounded-xl bg-white/10 border border-white/15 placeholder:text-blue-200/50 focus:outline-none focus:border-sky-300"
            />
            <input
              required
              type="password"
              value={form.app_secret}
              onChange={(e) => setForm({ ...form, app_secret: e.target.value })}
              placeholder="App Secret"
              className="px-3 py-2.5 rounded-xl bg-white/10 border border-white/15 placeholder:text-blue-200/50 focus:outline-none focus:border-sky-300"
            />
            <textarea
              required
              rows={2}
              value={form.user_token}
              onChange={(e) => setForm({ ...form, user_token: e.target.value })}
              placeholder="User Access Token có pages_show_list, pages_read_engagement, pages_manage_posts, pages_manage_engagement"
              className="sm:col-span-2 px-3 py-2.5 rounded-xl bg-white/10 border border-white/15 placeholder:text-blue-200/50 focus:outline-none focus:border-sky-300 resize-none"
            />
            <div className="flex items-center gap-2">
              <input
                value={form.graph_version}
                onChange={(e) => setForm({ ...form, graph_version: e.target.value })}
                placeholder="v24.0"
                className="w-24 px-3 py-2.5 rounded-xl bg-white/10 border border-white/15 focus:outline-none focus:border-sky-300"
              />
              <label className="flex items-center gap-2 text-blue-100 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.exchange_token}
                  onChange={(e) => setForm({ ...form, exchange_token: e.target.checked })}
                  className="accent-sky-400"
                />
                Tự đổi sang long-lived token
              </label>
            </div>
            <div className="flex justify-end gap-2">
              {connected && (
                <button
                  type="button"
                  onClick={handleSync}
                  disabled={!!busy}
                  className="px-3 py-2.5 rounded-xl bg-white/10 hover:bg-white/15 border border-white/15 font-bold flex items-center gap-1.5 disabled:opacity-50"
                >
                  {busy === 'sync' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                  Đồng bộ Page
                </button>
              )}
              <button
                type="submit"
                disabled={!!busy}
                className="px-4 py-2.5 rounded-xl bg-sky-400 hover:bg-sky-300 text-blue-950 font-extrabold flex items-center gap-1.5 disabled:opacity-50"
              >
                {busy === 'connect' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
                Xác thực & lưu
              </button>
            </div>
          </form>
        </div>

        <div className="p-5 md:p-6 space-y-4 bg-white/[0.04]">
          <div>
            <div className="flex items-center gap-2 text-xs font-extrabold uppercase tracking-wider text-blue-200">
              <Send className="w-4 h-4" /> Đích đăng Reel
            </div>
            <h3 className="text-lg font-black mt-1">
              {isFacebookChannel ? activeChannel.name : 'Chọn một kênh Facebook'}
            </h3>
          </div>

          {isFacebookChannel ? (
            <div className="space-y-3 text-xs">
              <input
                value={pageQuery}
                onChange={(e) => setPageQuery(e.target.value)}
                placeholder="Tìm page theo tên…"
                className="w-full px-3 py-2 rounded-xl bg-blue-950/60 border border-white/15 text-white placeholder:text-blue-200/40 focus:outline-none focus:border-sky-300"
              />
              <select
                value={selectedPageId}
                onChange={(e) => setSelectedPageId(e.target.value)}
                className="w-full px-3 py-3 rounded-xl bg-blue-950/60 border border-white/15 text-white focus:outline-none focus:border-sky-300"
              >
                <option value="">Chọn Fanpage...</option>
                {pages
                  .filter((page) => {
                    const q = pageQuery.trim().toLowerCase();
                    if (!q) return true;
                    return String(page.name || '').toLowerCase().includes(q)
                      || String(page.page_id || '').includes(q);
                  })
                  .map((page) => {
                    const hasToken = Boolean(page.page_token_ref || page.has_page_token);
                    let note = '';
                    if (!hasToken) note = ' · cần token mới';
                    else if (!page.can_publish) note = ' · thiếu quyền đăng';
                    return (
                      <option key={page.page_id} value={page.page_id} disabled={!page.can_publish}>
                        {page.name}{note}
                      </option>
                    );
                  })}
              </select>
              <div className="rounded-xl bg-amber-300/10 border border-amber-200/20 px-3 py-2 text-[10px] text-amber-50 leading-relaxed space-y-1">
                <p className="font-extrabold text-amber-100">Page mới không nằm trong token — không phải thiếu tên quyền.</p>
                <ol className="list-decimal pl-4 space-y-0.5 text-amber-50/90">
                  <li>Graph Explorer: app <strong>JENJO TREEBOT</strong> ({settings?.app_id || form.app_id || '3287685321396427'}), loại <strong>Mã người dùng</strong>.</li>
                  <li>Quyền cần có: <code className="text-[9px]">pages_show_list</code>, <code className="text-[9px]">pages_read_engagement</code>, <code className="text-[9px]">pages_manage_posts</code>. Thêm <code className="text-[9px]">pages_manage_engagement</code> để studio tự bình luận link giỏ hàng trên Reel.</li>
                  <li>Bấm <strong>Generate Access Token</strong>. Cửa sổ Facebook hỏi chọn Trang — mở <strong>Xem thêm trang</strong>, tick page mới (vd. Phim Hay Nè). Đừng để nguyên 22 trang cũ.</li>
                  <li>Copy token, dán vào ô trên, bấm <strong>Xác thực &amp; lưu</strong>. Generate xong mà không dán vào app thì app vẫn dùng token cũ.</li>
                </ol>
              </div>
              <div className="flex gap-2">
                <input
                  value={importRef}
                  onChange={(e) => setImportRef(e.target.value)}
                  placeholder="Dán Page ID hoặc link facebook.com/…"
                  className="flex-1 px-3 py-2 rounded-xl bg-blue-950/60 border border-white/15 text-white placeholder:text-blue-200/40 focus:outline-none focus:border-sky-300"
                />
                <button
                  type="button"
                  onClick={handleImport}
                  disabled={!importRef.trim() || !!busy}
                  className="px-3 py-2 rounded-xl bg-white/10 hover:bg-white/15 border border-white/15 font-bold disabled:opacity-40"
                >
                  {busy === 'import' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Thêm'}
                </button>
              </div>
              <label className="flex items-center justify-between gap-4 p-3 rounded-xl bg-white/8 border border-white/10">
                <span>
                  <strong className="block">Tự đăng khi reup xong</strong>
                  <span className="text-blue-100/65">Chỉ chạy khi video có trạng thái READY.</span>
                </span>
                <input
                  type="checkbox"
                  checked={autoPublish}
                  onChange={(e) => setAutoPublish(e.target.checked)}
                  className="w-4 h-4 accent-sky-400"
                />
              </label>
              <button
                type="button"
                onClick={handleBind}
                disabled={!selectedPageId || !!busy}
                className="w-full py-3 rounded-xl bg-white text-blue-900 hover:bg-blue-50 font-extrabold flex items-center justify-center gap-2 disabled:opacity-40"
              >
                {busy === 'bind' ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
                Lưu Page đích
              </button>
            </div>
          ) : (
            <div className="rounded-2xl border border-dashed border-white/20 p-6 text-center text-xs text-blue-100/70">
              Chọn hoặc tạo kênh nền tảng Facebook Reels để liên kết Fanpage.
            </div>
          )}

          {message && (
            <div className={`rounded-xl px-3 py-2 text-xs font-bold border ${
              message.type === 'error'
                ? 'bg-rose-400/10 text-rose-100 border-rose-300/20'
                : 'bg-emerald-400/10 text-emerald-100 border-emerald-300/20'
            }`}>
              {message.text}
            </div>
          )}
        </div>
      </div>

      {connected ? (
        <div className="border-t border-white/10 p-5 md:p-6 space-y-3 bg-black/10">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-xs font-extrabold uppercase tracking-wider text-amber-200">
                <ShoppingBag className="w-4 h-4" /> Giỏ hàng Shopee
              </div>
              <p className="text-xs text-blue-100/75 mt-1">
                Shopee không cho API danh sách «Đã liên kết». App quét bài gần đây trên từng Fanpage:
                có link shopee.vn / shp.ee thì page đó gắn giỏ được.
              </p>
            </div>
            <button
              type="button"
              onClick={handleShopScan}
              disabled={!!busy}
              className="px-4 py-2.5 rounded-xl bg-amber-400 hover:bg-amber-300 text-amber-950 font-extrabold text-xs flex items-center gap-1.5 disabled:opacity-50 shrink-0"
            >
              {busy === 'shop' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
              {busy === 'shop' ? 'Đang quét…' : 'Kiểm tra từng page'}
            </button>
          </div>
          {shopScan?.pages?.length ? (
            <div className="overflow-x-auto rounded-2xl border border-white/10">
              <table className="w-full text-[11px]">
                <thead className="bg-white/5 text-blue-200 font-bold">
                  <tr>
                    <th className="text-left px-3 py-2">Fanpage</th>
                    <th className="text-right px-3 py-2">Follow</th>
                    <th className="text-left px-3 py-2">Kết quả</th>
                  </tr>
                </thead>
                <tbody>
                  {shopScan.pages.map((row) => {
                    const tone =
                      row.status === 'has_cart_signal'
                        ? 'text-emerald-200'
                        : row.status === 'no_token' || row.status === 'cannot_read'
                          ? 'text-rose-200'
                          : 'text-amber-100';
                    return (
                      <tr key={row.page_id} className="border-t border-white/10">
                        <td className="px-3 py-2 font-bold text-white">{row.name}</td>
                        <td className="px-3 py-2 text-right text-blue-100/80">{row.followers || 0}</td>
                        <td className={`px-3 py-2 ${tone}`}>
                          <span className="font-extrabold">{row.label}</span>
                          {row.detail ? <span className="block text-blue-100/70 font-medium">{row.detail}</span> : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
