import React, { useCallback, useEffect, useState } from 'react';
import {
  bindTikTokAccount,
  fetchTikTokAccounts,
  fetchTikTokSettings,
  saveTikTokSettings,
} from '../services/api';
import { ExternalLink, KeyRound, Loader2, ShieldCheck } from 'lucide-react';

const EMPTY_FORM = {
  client_key: '',
  client_secret: '',
  redirect_uri: '',
  auth_code: '',
  access_token: '',
  refresh_token: '',
  auto_publish: true,
};

export function TikTokPublishingPanel({ activeChannel, onChanged }) {
  const [settings, setSettings] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [form, setForm] = useState(EMPTY_FORM);
  const [selectedOpenId, setSelectedOpenId] = useState('');
  const [autoPublish, setAutoPublish] = useState(true);
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    try {
      const [settingsData, accountsData] = await Promise.all([
        fetchTikTokSettings(),
        fetchTikTokAccounts(),
      ]);
      setSettings(settingsData);
      setAccounts(accountsData.accounts || []);
      setForm((prev) => ({
        ...prev,
        client_key: settingsData.client_key || prev.client_key,
        redirect_uri: settingsData.redirect_uri || prev.redirect_uri,
      }));
      if ((accountsData.accounts || []).length > 0) await onChanged?.();
    } catch (error) {
      setMessage({ type: 'error', text: error.message });
    }
  }, [onChanged]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    setSelectedOpenId(activeChannel?.tiktok_open_id || '');
    setAutoPublish(activeChannel?.tiktok_auto_publish ?? true);
  }, [activeChannel]);

  const handleConnect = async (event) => {
    event.preventDefault();
    setBusy('connect');
    setMessage(null);
    try {
      const result = await saveTikTokSettings(form);
      setMessage({ type: 'success', text: result.message });
      setForm((prev) => ({ ...prev, client_secret: '', auth_code: '', access_token: '', refresh_token: '' }));
      await load();
    } catch (error) {
      setMessage({ type: 'error', text: error.message });
    } finally {
      setBusy('');
    }
  };

  const handleBind = async () => {
    if (!activeChannel || !selectedOpenId) return;
    setBusy('bind');
    setMessage(null);
    try {
      const result = await bindTikTokAccount(activeChannel.channel_id, {
        open_id: selectedOpenId,
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
  const isTikTokChannel = activeChannel?.platform === 'tiktok';
  const authorizeUrl = settings?.authorize_url || '';

  return (
    <section className="rounded-3xl border border-rose-200 bg-gradient-to-br from-slate-950 via-rose-950 to-fuchsia-900 text-white shadow-lg overflow-hidden">
      <div className="grid grid-cols-1 xl:grid-cols-2">
        <div className="p-5 md:p-6 border-b xl:border-b-0 xl:border-r border-white/10 space-y-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-xs font-extrabold uppercase tracking-wider text-rose-200">
                <KeyRound className="w-4 h-4" /> TikTok Content Posting API
              </div>
              <h3 className="text-lg font-black mt-1">Tự đăng lên TikTok</h3>
              <p className="text-xs text-rose-100/75 mt-1">
                Tạo app trên TikTok for Developers, bật Direct Post + quyền video.publish.
                Secret lưu Keychain, không hiện lại trên giao diện.
              </p>
            </div>
            <span className={`px-2.5 py-1 rounded-full text-[10px] font-extrabold border ${
              connected
                ? 'bg-emerald-400/15 text-emerald-200 border-emerald-300/30'
                : 'bg-amber-300/10 text-amber-200 border-amber-200/20'
            }`}
            >
              {connected ? `Đã kết nối · ${settings.account_count || 0} TK` : 'Chưa kết nối'}
            </span>
          </div>

          <form onSubmit={handleConnect} className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
            <input
              required
              value={form.client_key}
              onChange={(e) => setForm({ ...form, client_key: e.target.value })}
              placeholder="Client Key"
              className="px-3 py-2.5 rounded-xl bg-white/10 border border-white/15 placeholder:text-rose-200/50 focus:outline-none focus:border-rose-300"
            />
            <input
              required
              type="password"
              value={form.client_secret}
              onChange={(e) => setForm({ ...form, client_secret: e.target.value })}
              placeholder="Client Secret"
              className="px-3 py-2.5 rounded-xl bg-white/10 border border-white/15 placeholder:text-rose-200/50 focus:outline-none focus:border-rose-300"
            />
            <input
              value={form.redirect_uri}
              onChange={(e) => setForm({ ...form, redirect_uri: e.target.value })}
              placeholder="Redirect URI HTTPS đã đăng ký trên TikTok"
              className="sm:col-span-2 px-3 py-2.5 rounded-xl bg-white/10 border border-white/15 placeholder:text-rose-200/50 focus:outline-none focus:border-rose-300"
            />
            <textarea
              rows={2}
              value={form.auth_code}
              onChange={(e) => setForm({ ...form, auth_code: e.target.value })}
              placeholder="Dán URL redirect sau khi đăng nhập TikTok, hoặc chỉ mã code="
              className="sm:col-span-2 px-3 py-2.5 rounded-xl bg-white/10 border border-white/15 placeholder:text-rose-200/50 focus:outline-none focus:border-rose-300 resize-none"
            />
            <textarea
              rows={2}
              value={form.access_token}
              onChange={(e) => setForm({ ...form, access_token: e.target.value })}
              placeholder="Hoặc dán sẵn Access Token (có video.publish)"
              className="sm:col-span-2 px-3 py-2.5 rounded-xl bg-white/10 border border-white/15 placeholder:text-rose-200/50 focus:outline-none focus:border-rose-300 resize-none"
            />
            <input
              type="password"
              value={form.refresh_token}
              onChange={(e) => setForm({ ...form, refresh_token: e.target.value })}
              placeholder="Refresh Token (nếu dán access token tay)"
              className="sm:col-span-2 px-3 py-2.5 rounded-xl bg-white/10 border border-white/15 placeholder:text-rose-200/50 focus:outline-none focus:border-rose-300"
            />
            <label className="flex items-center gap-2 text-rose-100 cursor-pointer">
              <input
                type="checkbox"
                checked={form.auto_publish}
                onChange={(e) => setForm({ ...form, auto_publish: e.target.checked })}
                className="accent-rose-400"
              />
              Tự đăng khi video xong (9:16)
            </label>
            <div className="flex justify-end gap-2">
              {authorizeUrl ? (
                <a
                  href={authorizeUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="px-3 py-2.5 rounded-xl bg-white/10 hover:bg-white/15 border border-white/15 font-bold flex items-center gap-1.5"
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  Mở TikTok ủy quyền
                </a>
              ) : null}
              <button
                type="submit"
                disabled={!!busy}
                className="px-4 py-2.5 rounded-xl bg-rose-400 hover:bg-rose-300 text-rose-950 font-extrabold flex items-center gap-1.5 disabled:opacity-50"
              >
                {busy === 'connect' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
                Kết nối & lưu
              </button>
            </div>
          </form>
        </div>

        <div className="p-5 md:p-6 space-y-4 bg-black/20">
          <h4 className="text-sm font-black">Gắn tài khoản vào kênh TikTok</h4>
          {message && (
            <p className={`text-xs font-bold ${message.type === 'error' ? 'text-amber-200' : 'text-emerald-200'}`}>
              {message.text}
            </p>
          )}
          {accounts.length === 0 ? (
            <p className="text-xs text-rose-100/70">
              Chưa có tài khoản. Điền Client Key/Secret, bấm Mở TikTok ủy quyền, rồi dán URL redirect vào ô code.
              App chưa audit Direct Post thì clip chỉ hiện riêng tư (SELF_ONLY).
            </p>
          ) : (
            <div className="space-y-2">
              {accounts.map((acc) => (
                <label
                  key={acc.open_id}
                  className={`flex items-center gap-2.5 rounded-xl border px-3 py-2 cursor-pointer ${
                    selectedOpenId === acc.open_id ? 'bg-white/15 border-rose-300' : 'bg-white/5 border-white/10'
                  }`}
                >
                  <input
                    type="radio"
                    name="tiktok-account"
                    checked={selectedOpenId === acc.open_id}
                    onChange={() => setSelectedOpenId(acc.open_id)}
                    className="accent-rose-400"
                  />
                  {acc.avatar_url ? (
                    <img src={acc.avatar_url} alt="" className="w-8 h-8 rounded-lg object-cover" />
                  ) : (
                    <span className="w-8 h-8 rounded-lg bg-rose-600 text-[10px] font-black flex items-center justify-center">
                      {String(acc.nickname || acc.username || '?').slice(0, 2).toUpperCase()}
                    </span>
                  )}
                  <span className="min-w-0">
                    <span className="block text-[12px] font-extrabold truncate">{acc.nickname || acc.username}</span>
                    <span className="block text-[10px] text-rose-100/70">
                      @{acc.username || 'tiktok'} · {acc.can_publish ? 'Được đăng' : 'Thiếu video.publish'}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          )}
          {isTikTokChannel ? (
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-xs text-rose-100 cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoPublish}
                  onChange={(e) => setAutoPublish(e.target.checked)}
                  className="accent-rose-400"
                />
                Tự đăng khi job xong
              </label>
              <button
                type="button"
                onClick={handleBind}
                disabled={!selectedOpenId || !!busy}
                className="px-3 py-2 rounded-xl bg-rose-400 hover:bg-rose-300 text-rose-950 text-xs font-extrabold disabled:opacity-50"
              >
                {busy === 'bind' ? 'Đang lưu…' : 'Gắn vào kênh này'}
              </button>
            </div>
          ) : (
            <p className="text-[11px] text-rose-100/70">
              Chọn kênh platform TikTok ở danh sách bên trái (hoặc để app tự tạo khi kết nối) rồi bấm Gắn vào kênh.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
