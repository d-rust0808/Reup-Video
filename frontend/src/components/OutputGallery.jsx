import React, { useEffect, useState } from 'react';
import { fetchOutputs, getDownloadUrl, getStreamUrl, downloadBatchZip, deleteOutput, deleteBatchOutputs, clearAllOutputs } from '../services/api';
import { ConfirmModal } from './ConfirmModal';
import { Toast } from './Toast';
import { VideoModal } from './VideoModal';
import { FolderDown, Download, CheckSquare, Square, FileVideo, Loader2, Trash2, RefreshCw, ShieldCheck, Play, FolderOpen } from 'lucide-react';

export function OutputGallery() {
  const [outputs, setOutputs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedIds, setSelectedIds] = useState([]);
  const [zipping, setZipping] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null); // jobId to delete
  const [batchDeleteTarget, setBatchDeleteTarget] = useState(false);
  const [clearAllTarget, setClearAllTarget] = useState(false);
  const [toast, setToast] = useState(null); // { type, title, message }
  const [search, setSearch] = useState('');

  const isDesktopApp = typeof window !== 'undefined' && !!window.electronAPI?.isDesktop;

  const handleOpenInDesktopFolder = (filePath = '') => {
    if (window.electronAPI?.showItemInFolder) {
      window.electronAPI.showItemInFolder(filePath);
    }
  };

  const loadOutputs = async () => {
    try {
      const data = await fetchOutputs();
      const list = Array.isArray(data) ? data : (data.outputs || data.items || []);
      setOutputs(list);
      // Clean selectedIds that no longer exist
      setSelectedIds((prev) => prev.filter((id) => list.some((item) => (item.job_id || item.filename) === id)));
    } catch (e) {
      console.error('Failed to load output files:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadOutputs();
    const timer = setTimeout(() => setLoading(false), 2000);
    return () => clearTimeout(timer);
  }, []);


  const toggleSelect = (jobId) => {
    setSelectedIds((prev) =>
      prev.includes(jobId) ? prev.filter((id) => id !== jobId) : [...prev, jobId]
    );
  };

  const handleSelectAll = () => {
    const list = Array.isArray(outputs) ? outputs : [];
    if (selectedIds.length === list.length) {
      setSelectedIds([]);
    } else {
      setSelectedIds(list.map((item) => item.job_id || item.filename));
    }
  };

  const handleBatchZip = async () => {
    if (selectedIds.length === 0) return;
    setZipping(true);
    try {
      await downloadBatchZip(selectedIds);
      setToast({
        type: 'success',
        title: 'Tải File Thành Công',
        message: `Đã đóng gói và tải xuống ${selectedIds.length} video thành phẩm.`,
      });
    } catch (e) {
      setToast({
        type: 'error',
        title: 'Tải ZIP Thất Bại',
        message: e.message || 'Không thể đóng gói file ZIP',
      });
    } finally {
      setZipping(false);
    }
  };

  const requestDelete = (e, jobId) => {
    e.stopPropagation();
    setDeleteTarget(jobId);
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    const target = deleteTarget;
    setDeleteTarget(null);
    try {
      await deleteOutput(target);
      setToast({
        type: 'success',
        title: 'Đã Xóa Video',
        message: `Đã xóa video ${target} khỏi kho lưu trữ.`,
      });
      loadOutputs();
    } catch (err) {
      setToast({
        type: 'error',
        title: 'Xóa Thất Bại',
        message: err.message || 'Không thể xóa video này',
      });
    }
  };

  const confirmBatchDelete = async () => {
    if (selectedIds.length === 0) return;
    setBatchDeleteTarget(false);
    try {
      const res = await deleteBatchOutputs(selectedIds);
      setToast({
        type: 'success',
        title: 'Đã Xóa Video Đã Chọn',
        message: res.message || `Đã xóa ${selectedIds.length} video khỏi kho lưu trữ.`,
      });
      setSelectedIds([]);
      loadOutputs();
    } catch (err) {
      setToast({
        type: 'error',
        title: 'Xóa Hàng Loạt Thất Bại',
        message: err.message || 'Không thể xóa các video đã chọn',
      });
    }
  };

  const confirmClearAll = async () => {
    setClearAllTarget(false);
    try {
      const res = await clearAllOutputs();
      setToast({
        type: 'success',
        title: 'Đã Dọn Dẹp Kho Video',
        message: res.message || 'Đã dọn dẹp toàn bộ video thành phẩm.',
      });
      setSelectedIds([]);
      loadOutputs();
    } catch (err) {
      setToast({
        type: 'error',
        title: 'Dọn Dẹp Thất Bại',
        message: err.message || 'Không thể xóa toàn bộ kho video',
      });
    }
  };

  const outputList = (Array.isArray(outputs) ? outputs : []).filter((item) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return [item.filename, item.title, item.caption, item.platform, item.job_id]
      .filter(Boolean)
      .some((v) => String(v).toLowerCase().includes(q));
  });

  return (
    <div className="clean-panel rounded-3xl p-6 sm:p-8 shadow-xs space-y-6">
      {/* Custom Confirmation Modal for Single Video */}
      <ConfirmModal
        isOpen={!!deleteTarget}
        title="Xóa Video Thành Phẩm"
        message={`Bạn có chắc chắn muốn xóa file video "${deleteTarget}" khỏi kho lưu trữ không?`}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
        confirmText="Xác Nhận Xóa"
        cancelText="Hủy Bỏ"
      />

      {/* Custom Confirmation Modal for Batch Delete Selected */}
      <ConfirmModal
        isOpen={batchDeleteTarget}
        title="Xóa Các Video Đã Chọn"
        message={`Bạn có chắc chắn muốn xóa ${selectedIds.length} video đã chọn khỏi kho lưu trữ không?`}
        onConfirm={confirmBatchDelete}
        onCancel={() => setBatchDeleteTarget(false)}
        confirmText="Xác Nhận Xóa Hàng Loạt"
        cancelText="Hủy Bỏ"
      />

      {/* Custom Confirmation Modal for Clear All */}
      <ConfirmModal
        isOpen={clearAllTarget}
        title="Xóa Toàn Bộ Kho Video"
        message="Bạn có chắc chắn muốn xóa tất cả video thành phẩm khỏi kho lưu trữ không? Thao tác này không thể hoàn tác."
        onConfirm={confirmClearAll}
        onCancel={() => setClearAllTarget(false)}
        confirmText="Xác Nhận Xóa Toàn Bộ"
        cancelText="Hủy Bỏ"
      />

      {/* Custom In-App Toast */}
      <Toast toast={toast} onClose={() => setToast(null)} />

      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-3 border-b border-slate-100">
        <div>
          <h3 className="text-base font-extrabold text-slate-900 flex items-center gap-2">
            <FolderDown className="w-5 h-5 text-emerald-600" />
            Kho Video Thành Phẩm & Tải Trọn Gói (.ZIP)
          </h3>
          <p className="text-xs text-slate-500 mt-1 font-medium">
            Xem lại các video đã xử lý inpaint & reup, kiểm tra mã hash MD5 đã đổi và tải xuống hàng loạt.
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Tìm caption / file / nền tảng…"
            className="bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs font-medium w-48"
          />
          <button
            onClick={loadOutputs}
            className="bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-bold p-2.5 rounded-xl border border-slate-200 transition cursor-pointer shadow-xs"
            title="Làm mới danh sách"
          >
            <RefreshCw className="w-4 h-4 text-blue-600" />
          </button>

          <button
            onClick={handleSelectAll}
            className="bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-bold px-3.5 py-2.5 rounded-xl border border-slate-200 transition flex items-center gap-1.5 cursor-pointer shadow-xs"
          >
            {selectedIds.length === outputList.length && outputList.length > 0 ? (
              <CheckSquare className="w-4 h-4 text-blue-600" />
            ) : (
              <Square className="w-4 h-4 text-slate-400" />
            )}
            <span>Chọn Tất Cả ({outputList.length})</span>
          </button>

          <button
            onClick={() => setBatchDeleteTarget(true)}
            disabled={selectedIds.length === 0}
            className="bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 disabled:opacity-40 disabled:cursor-not-allowed text-xs font-bold px-3.5 py-2.5 rounded-xl transition flex items-center gap-1.5 cursor-pointer shadow-xs"
            title="Xóa các video đã được chọn"
          >
            <Trash2 className="w-4 h-4 text-rose-600" />
            <span>Xóa Đã Chọn ({selectedIds.length})</span>
          </button>

          <button
            onClick={() => setClearAllTarget(true)}
            disabled={outputList.length === 0}
            className="bg-slate-100 hover:bg-rose-50 hover:text-rose-700 hover:border-rose-200 disabled:opacity-40 disabled:cursor-not-allowed text-slate-700 text-xs font-bold px-3 py-2.5 rounded-xl transition flex items-center gap-1.5 border border-slate-200 cursor-pointer shadow-xs"
            title="Xóa toàn bộ kho video thành phẩm"
          >
            <Trash2 className="w-4 h-4 text-rose-600" />
            <span>Xóa Tất Cả</span>
          </button>

          {isDesktopApp && (
            <button
              onClick={() => handleOpenInDesktopFolder('')}
              className="bg-indigo-50 hover:bg-indigo-100 text-indigo-700 border border-indigo-200 text-xs font-bold px-3.5 py-2.5 rounded-xl transition flex items-center gap-1.5 cursor-pointer shadow-xs"
              title="Mở thư mục video trên máy tính (Finder / Explorer)"
            >
              <FolderOpen className="w-4 h-4 text-indigo-600" />
              <span>Mở Thư Mục Máy</span>
            </button>
          )}

          <button
            onClick={handleBatchZip}
            disabled={selectedIds.length === 0 || zipping}
            className="bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-extrabold px-4 py-2.5 rounded-xl transition shadow-md shadow-emerald-600/20 flex items-center gap-2 cursor-pointer"
          >
            {zipping ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
            <span>Tải Đã Chọn ({selectedIds.length}) (.ZIP)</span>
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-center py-12 text-slate-400 text-xs flex items-center justify-center gap-2 font-medium">
          <Loader2 className="w-4 h-4 animate-spin text-blue-600" /> Đang tải danh sách video...
        </div>
      ) : outputList.length === 0 ? (
        <div className="text-center py-12 text-slate-400 text-xs font-medium">
          Chưa có video đầu ra nào. Hãy tạo job xử lý trong tab Studio để tạo video thành phẩm.
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {outputList.map((item, idx) => {
            const jobId = item.job_id || item.filename;
            const isSelected = selectedIds.includes(jobId);
            const plat = (item.platform || (String(item.filename || jobId).match(/\.([a-z_]+)\.mp4$/i) || [])[1] || '').toLowerCase();
            const platLabel = {
              tiktok: 'TikTok 9:16',
              youtube_shorts: 'YT Shorts 9:16',
              facebook: 'FB Reels 9:16',
              instagram: 'IG Reels 9:16',
              youtube: 'YouTube 16:9',
              douyin: 'Douyin 9:16',
            }[plat];

            return (
              <div
                key={jobId || idx}
                onClick={() => toggleSelect(jobId)}
                className={`p-5 rounded-2xl border transition-all duration-150 cursor-pointer flex flex-col justify-between space-y-4 ${
                  isSelected
                    ? 'bg-blue-50/80 border-blue-400 shadow-md ring-2 ring-blue-400/20'
                    : 'clean-card clean-card-hover'
                }`}
              >
                <div className="rounded-xl overflow-hidden bg-slate-950 aspect-video">
                  <video
                    src={getStreamUrl(jobId)}
                    muted
                    playsInline
                    preload="metadata"
                    controls
                    className="w-full h-full object-contain"
                    onClick={(e) => e.stopPropagation()}
                  />
                </div>
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center space-x-2.5 min-w-0">
                    <div className="p-2.5 rounded-xl bg-blue-50 text-blue-600 shrink-0 border border-blue-100">
                      <FileVideo className="w-5 h-5" />
                    </div>
                    <span className="text-xs font-bold text-slate-900 truncate max-w-[180px]">
                      {item.title || item.filename || jobId}
                    </span>
                    {platLabel && (
                      <span className="ml-1 shrink-0 text-[9px] font-extrabold px-1.5 py-0.5 rounded-full bg-blue-50 text-blue-700 border border-blue-100">
                        {platLabel}
                      </span>
                    )}
                  </div>
                  {isSelected ? (
                    <CheckSquare className="w-4 h-4 text-blue-600 shrink-0" />
                  ) : (
                    <Square className="w-4 h-4 text-slate-300 shrink-0" />
                  )}
                </div>

                {item.md5 && (
                  <div className="p-3 bg-slate-50 rounded-xl border border-slate-100 space-y-0.5">
                    <span className="text-[10px] text-slate-400 font-mono font-bold flex items-center gap-1">
                      <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" /> MD5 Anti-Ban:
                    </span>
                    <span className="text-[11px] font-mono text-emerald-700 font-bold block truncate">
                      {item.md5}
                    </span>
                  </div>
                )}

                <div className="pt-2 border-t border-slate-100 flex items-center justify-between">
                  <span className="text-[11px] text-slate-500 font-mono">
                    {item.file_size ? (item.file_size / (1024 * 1024)).toFixed(2) : (item.size_mb || '—')} MB
                  </span>
                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        const cap = item.caption || item.post_caption || item.title || item.filename || '';
                        navigator.clipboard.writeText(String(cap));
                        setToast({ type: 'success', title: 'Đã copy caption', message: cap.slice(0, 80) });
                      }}
                      title="Copy caption"
                      className="px-3 py-1.5 bg-slate-50 hover:bg-slate-100 text-slate-700 text-xs font-bold rounded-xl border border-slate-200 transition cursor-pointer shadow-xs"
                    >
                      Copy
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setPreviewVideo(item);
                      }}
                      title="Xem trực tiếp video này"
                      className="px-3 py-1.5 bg-blue-50 hover:bg-blue-100 text-blue-700 text-xs font-bold rounded-xl border border-blue-200 transition flex items-center gap-1 cursor-pointer shadow-xs"
                    >
                      <Play className="w-3.5 h-3.5 text-blue-600 fill-blue-600" /> Xem
                    </button>
                    {isDesktopApp && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleOpenInDesktopFolder(item.file_path || item.filename || '');
                        }}
                        title="Mở video này trong thư mục máy tính"
                        className="p-2 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl border border-transparent hover:border-indigo-200 transition cursor-pointer"
                      >
                        <FolderOpen className="w-4 h-4" />
                      </button>
                    )}
                    <button
                      onClick={(e) => requestDelete(e, jobId)}
                      title="Xóa video này"
                      className="p-2 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-xl border border-transparent hover:border-rose-200 transition cursor-pointer"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                    <a
                      href={getDownloadUrl(jobId)}
                      download
                      onClick={(e) => e.stopPropagation()}
                      className="bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold px-3 py-1.5 rounded-xl transition shadow-xs flex items-center gap-1 cursor-pointer"
                    >
                      <Download className="w-3.5 h-3.5" /> Tải
                    </a>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Direct Video Player Modal */}
      <VideoModal
        isOpen={!!previewVideo}
        onClose={() => setPreviewVideo(null)}
        video={previewVideo}
      />
    </div>
  );
}
