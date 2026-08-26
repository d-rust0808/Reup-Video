import React, { useCallback, useEffect, useState } from 'react';
import {
  createChannelGroup,
  deleteChannelGroup,
  fetchChannelGroups,
  fetchPublishLog,
  getMediaUrl,
  updateChannelGroup,
} from '../services/api';
import { Edit3, ExternalLink, FolderPlus, Loader2, NotebookPen, Plus, ScrollText, Trash2 } from 'lucide-react';

export function ChannelGroupsPanel({ channels = [], onToast }) {
  const [groups, setGroups] = useState([]);
  const [log, setLog] = useState([]);
  const [busy, setBusy] = useState('');
  const [name, setName] = useState('');
  const [notes, setNotes] = useState('');
  const [picked, setPicked] = useState([]);
  const [editingId, setEditingId] = useState(null);

  const facebookChannels = channels.filter((c) => String(c.platform || '').toLowerCase() === 'facebook');

  const load = useCallback(async (quiet = false) => {
    try {
      const [g, p] = await Promise.all([fetchChannelGroups(), fetchPublishLog(60)]);
      setGroups(g.groups || []);
      setLog(p.events || []);
    } catch (err) {
      if (!quiet) onToast?.({ type: 'error', title: 'Lỗi', message: err.message });
    }
  }, [onToast]);

  useEffect(() => { load(false); }, [load]);

  useEffect(() => {
    const tick = () => { load(true); };
    window.addEventListener('reup:channels-changed', tick);
    const id = window.setInterval(tick, 8000);
    return () => {
      window.removeEventListener('reup:channels-changed', tick);
      window.clearInterval(id);
    };
  }, [load]);

  const resetForm = () => {
    setEditingId(null);
    setName('');
    setNotes('');
    setPicked([]);
  };

  const startEdit = (group) => {
    setEditingId(group.group_id);
    setName(group.name || '');
    setNotes(group.notes || '');
    setPicked(group.channel_ids || []);
  };

  const save = async () => {
    if (!name.trim()) return;
    setBusy('save');
    try {
      const payload = { name: name.trim(), notes, channel_ids: picked };
      if (editingId) await updateChannelGroup(editingId, payload);
      else await createChannelGroup(payload);
      onToast?.({ type: 'success', title: 'Đã lưu', message: editingId ? 'Đã cập nhật nhóm' : 'Đã tạo nhóm Fanpage' });
      resetForm();
      await load();
      window.dispatchEvent(new Event('reup:channels-changed'));
    } catch (err) {
      onToast?.({ type: 'error', title: 'Lỗi', message: err.message });
    } finally {
      setBusy('');
    }
  };

  const remove = async (groupId) => {
    setBusy(groupId);
    try {
      await deleteChannelGroup(groupId);
      if (editingId === groupId) resetForm();
      await load();
      window.dispatchEvent(new Event('reup:channels-changed'));
    } catch (err) {
      onToast?.({ type: 'error', title: 'Lỗi', message: err.message });
    } finally {
      setBusy('');
    }
  };

  const toggle = (id) => {
    setPicked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <section className="bg-white rounded-2xl border border-slate-200 p-4 shadow-xs space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-800 flex items-center gap-2">
            <FolderPlus className="w-4 h-4 text-blue-600" /> Nhóm Fanpage
          </h3>
          {editingId ? (
            <button type="button" onClick={resetForm} className="text-[10px] font-bold text-slate-500 hover:text-slate-800">
              + Tạo nhóm mới
            </button>
          ) : null}
        </div>
        <div className={`rounded-xl border p-3 space-y-2 ${editingId ? 'border-blue-400 bg-blue-50/40' : 'border-slate-200 bg-slate-50/50'}`}>
          <p className="text-[10px] font-extrabold uppercase tracking-wide text-blue-700">
            {editingId ? `Đang sửa: ${name || 'nhóm'}` : 'Tạo nhóm mới'}
          </p>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Tên nhóm (vd. Xây dựng miền Nam)"
          className="w-full text-xs border border-slate-200 rounded-xl px-3 py-2 bg-white"
        />
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          placeholder="Note nhóm: đăng clip đập phá / liên hệ 0777..."
          className="w-full text-xs border border-slate-200 rounded-xl px-3 py-2 resize-none"
        />
        <div className="max-h-44 overflow-y-auto rounded-xl border border-slate-200 divide-y divide-slate-100">
          {facebookChannels.map((chan) => {
            const id = chan.channel_id;
            const on = picked.includes(id);
            const pic = chan.facebook_page_id
              ? getMediaUrl(`/api/v1/facebook/pages/${chan.facebook_page_id}/picture`)
              : '';
            return (
              <label key={id} className={`flex items-center gap-2 px-2 py-1.5 text-xs cursor-pointer ${on ? 'bg-blue-50' : ''}`}>
                <input type="checkbox" checked={on} onChange={() => toggle(id)} className="accent-blue-600" />
                {pic ? <img src={pic} alt="" className="w-7 h-7 rounded-md object-cover" /> : <span className="w-7 h-7 rounded-md bg-blue-600 text-white text-[9px] font-black flex items-center justify-center">{String(chan.name || '?').slice(0, 2)}</span>}
                <span className="truncate font-bold text-slate-800">{chan.name}</span>
              </label>
            );
          })}
        </div>
        <button
          type="button"
          onClick={save}
          disabled={!name.trim() || busy === 'save'}
          className="w-full py-2 rounded-xl bg-blue-600 text-white text-xs font-extrabold disabled:opacity-40 flex items-center justify-center gap-1.5"
        >
          {busy === 'save' ? <Loader2 className="w-4 h-4 animate-spin" /> : (editingId ? <><Edit3 className="w-3.5 h-3.5" /> Lưu chỉnh sửa nhóm</> : <><Plus className="w-3.5 h-3.5" /> Tạo nhóm</>)}
        </button>
        </div>
        <div className="space-y-2">
          {groups.map((g) => {
            const members = facebookChannels.filter((c) => (g.channel_ids || []).includes(c.channel_id));
            const editing = editingId === g.group_id;
            return (
            <div key={g.group_id} className={`rounded-xl border p-2.5 ${editing ? 'border-blue-500 ring-1 ring-blue-200' : 'border-slate-200'}`}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-xs font-extrabold text-slate-900">{g.name}</div>
                  <div className="text-[10px] text-slate-500">{g.member_count || members.length} page{g.notes ? ` · ${g.notes}` : ''}</div>
                  {members.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {members.map((c) => (
                        <span key={c.channel_id} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md bg-slate-100 text-[10px] font-bold text-slate-700">
                          {c.facebook_page_id ? (
                            <img src={getMediaUrl(`/api/v1/facebook/pages/${c.facebook_page_id}/picture`)} alt="" className="w-3.5 h-3.5 rounded object-cover" />
                          ) : null}
                          {c.name}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    type="button"
                    onClick={() => startEdit(g)}
                    className="px-2 py-1 rounded-lg bg-blue-50 text-blue-700 hover:bg-blue-100 text-[10px] font-extrabold flex items-center gap-1"
                  >
                    <Edit3 className="w-3 h-3" /> Sửa
                  </button>
                  <button type="button" onClick={() => remove(g.group_id)} className="p-1 text-slate-400 hover:text-rose-600">
                    {busy === g.group_id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                  </button>
                </div>
              </div>
            </div>
            );
          })}
          {groups.length === 0 && (
            <p className="text-[11px] text-slate-500">Chưa có nhóm. Tạo nhóm rồi tick page vào — lúc đăng chỉ cần chọn nhóm.</p>
          )}
        </div>
      </section>

      <section className="bg-white rounded-2xl border border-slate-200 p-4 shadow-xs space-y-3">
        <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-800 flex items-center gap-2">
          <ScrollText className="w-4 h-4 text-indigo-600" /> Nhật ký đăng
        </h3>
        <div className="max-h-[420px] overflow-y-auto space-y-2">
          {log.length === 0 && (
            <p className="text-[11px] text-slate-500">Chưa có lần đăng nào. Mỗi video gán kênh/nhóm sẽ được ghi lại ở đây.</p>
          )}
          {log.map((ev) => (
            <article key={ev.id} className="rounded-xl border border-slate-200 p-2.5 text-[11px]">
              <div className="flex items-center justify-between gap-2">
                <strong className="text-slate-900 truncate">{ev.page_name || ev.channel_name || ev.channel_id}</strong>
                <span className={`shrink-0 font-bold ${ev.status === 'PUBLISHED' ? 'text-emerald-600' : 'text-amber-600'}`}>{ev.status}</span>
              </div>
              <p className="text-slate-500 mt-0.5">
                {ev.group_name ? `Nhóm: ${ev.group_name} · ` : ''}
                {ev.job_id ? `Job ${ev.job_id.slice(-8)} · ` : ''}
                {ev.created_at ? new Date(ev.created_at).toLocaleString('vi-VN') : ''}
              </p>
              {ev.title ? <p className="font-bold text-slate-800 mt-1 line-clamp-1">{ev.title}</p> : null}
              {ev.caption ? <p className="text-slate-600 line-clamp-2">{ev.caption}</p> : null}
              {ev.permalink ? (
                <a
                  href={ev.permalink}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 inline-flex items-center gap-1 text-blue-700 font-extrabold hover:underline"
                >
                  Mở Reel trên Facebook <ExternalLink className="w-3 h-3" />
                </a>
              ) : null}
              {ev.notes ? <p className="text-indigo-700 mt-1 flex items-start gap-1"><NotebookPen className="w-3 h-3 mt-0.5 shrink-0" />{ev.notes}</p> : null}
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
