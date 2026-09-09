import React, { useEffect, useState } from 'react';
import { Loader2, Send, ShoppingBag, X } from 'lucide-react';
import { fetchChannelGroups, publishJobToGroups } from '../services/api';
import { loadSession } from '../services/session';

export function PublishToGroupModal({ jobId, jobLabel, isOpen, onClose, onDone, onError }) {
  const [groups, setGroups] = useState([]);
  const [picked, setPicked] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [affiliateLink, setAffiliateLink] = useState('');
  const [affiliateProduct, setAffiliateProduct] = useState('');

  useEffect(() => {
    if (!isOpen) return undefined;
    let cancelled = false;
    setLoading(true);
    setPicked([]);
    const opts = loadSession().workbenchOptions || {};
    setAffiliateLink(opts.affiliate_link || '');
    setAffiliateProduct(opts.affiliate_product || '');
    fetchChannelGroups()
      .then((data) => {
        if (cancelled) return;
        setGroups(data.groups || []);
      })
      .catch((err) => {
        if (!cancelled) onError?.(err.message || 'Không tải được nhóm');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [isOpen, onError]);

  if (!isOpen || !jobId) return null;

  const toggle = (groupId) => {
    setPicked((prev) => (prev.includes(groupId) ? prev.filter((id) => id !== groupId) : [...prev, groupId]));
  };

  const selected = groups.filter((g) => picked.includes(g.group_id));
  const pageCount = selected.reduce((sum, g) => sum + (g.member_count || (g.channel_ids || []).length || 0), 0);

  const submit = async () => {
    if (!picked.length || busy) return;
    setBusy(true);
    try {
      const res = await publishJobToGroups(jobId, {
        group_ids: picked,
        affiliate_link: affiliateLink,
        affiliate_product: affiliateProduct,
      });
      onDone?.(res);
      onClose?.();
    } catch (err) {
      onError?.(err.message || 'Không đăng được');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-xs">
      <div className="w-full max-w-lg bg-white rounded-3xl p-6 shadow-2xl border border-slate-200/90 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="p-2.5 rounded-2xl bg-emerald-50 text-emerald-700 border border-emerald-100 shrink-0">
              <Send className="w-5 h-5" />
            </div>
            <div className="min-w-0">
              <h3 className="text-base font-extrabold text-slate-900">Đăng lại lên nhóm Fanpage</h3>
              <p className="text-xs text-slate-500 font-medium mt-0.5 truncate">
                {jobLabel || jobId} · video đã xong, chọn nhóm để đăng
              </p>
            </div>
          </div>
          <button type="button" onClick={onClose} className="p-1.5 text-slate-400 hover:bg-slate-100 rounded-xl">
            <X className="w-4 h-4" />
          </button>
        </div>

        {loading ? (
          <div className="py-8 text-center text-xs text-slate-500 font-semibold flex items-center justify-center gap-2">
            <Loader2 className="w-4 h-4 animate-spin" /> Đang tải nhóm…
          </div>
        ) : !groups.length ? (
          <p className="text-sm text-slate-600 font-medium bg-amber-50 border border-amber-100 rounded-2xl p-4">
            Chưa có nhóm Fanpage. Vào tab Fanpage & Đăng Bài để tạo nhóm rồi quay lại đây.
          </p>
        ) : (
          <div className="space-y-2">
            <p className="text-[11px] font-bold text-slate-600">Chọn nhóm để đăng:</p>
            <div className="flex flex-wrap gap-1.5">
              {groups.map((g) => {
                const on = picked.includes(g.group_id);
                const count = g.member_count || (g.channel_ids || []).length || 0;
                return (
                  <button
                    key={g.group_id}
                    type="button"
                    onClick={() => toggle(g.group_id)}
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
          </div>
        )}

        <div className="rounded-2xl border border-orange-100 bg-orange-50/70 p-3 space-y-2">
          <div className="flex items-center gap-1.5 text-[11px] font-extrabold text-slate-800">
            <ShoppingBag className="w-3.5 h-3.5 text-orange-600" />
            Link 1 món Shopee (dòng đầu bài + bình luận)
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
        </div>

        <p className="text-[11px] font-semibold text-slate-600 bg-slate-50 border border-slate-200 rounded-xl px-3 py-2">
          Trước khi lên page, app upload nháp rồi hỏi Facebook Rights Manager.
          Trùng bản quyền thì huỷ cả nhóm — không đăng, tránh dính strike hàng loạt.
        </p>

        <div className="flex items-center justify-end gap-2.5 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2.5 rounded-xl border border-slate-200 bg-slate-50 hover:bg-slate-100 text-slate-700 font-bold text-xs"
          >
            Hủy
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!picked.length || busy}
            className="px-5 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-extrabold text-xs flex items-center gap-1.5"
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
            {busy ? 'Đang xếp đăng…' : `Đăng lên ${pageCount || 0} page`}
          </button>
        </div>
      </div>
    </div>
  );
}
