import React, { useEffect, useState } from 'react';
import { fetchOutputs, getDownloadUrl, downloadBatchZip, deleteOutput } from '../services/api';
import { ConfirmModal } from './ConfirmModal';
import { Toast } from './Toast';
import { VideoModal } from './VideoModal';
import { FolderDown, Download, CheckSquare, Square, FileVideo, Loader2, Trash2, RefreshCw, ShieldCheck, Play } from 'lucide-react';

export function OutputGallery() {
  const [outputs, setOutputs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedIds, setSelectedIds] = useState([]);
  const [zipping, setZipping] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null); // jobId to delete
  const [toast, setToast] = useState(null); // { type, title, message }
  const [previewVideo, setPreviewVideo] = useState(null);

  const loadOutputs = async () => {
    try {
      const data = await fetchOutputs();
      const list = Array.isArray(data) ? data : (data.outputs || data.items || []);
      setOutputs(list);
    } catch (e) {
      console.error('Failed to load output files:', e);
      setOutputs([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadOutputs();
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

  const outputList = Array.isArray(outputs) ? outputs : [];

  return (
    <div className="clean-panel rounded-3xl p-6 sm:p-8 shadow-xs space-y-6">
      {/* Custom Confirmation Modal */}
      <ConfirmModal
        isOpen={!!deleteTarget}
        title="Xóa Video Thành Phẩm"
        message={`Bạn có chắc chắn muốn xóa file video "${deleteTarget}" khỏi kho lưu trữ không?`}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
        confirmText="Xác Nhận Xóa"
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

        <div className="flex items-center gap-2.5">
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
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center space-x-2.5 min-w-0">
                    <div className="p-2.5 rounded-xl bg-blue-50 text-blue-600 shrink-0 border border-blue-100">
                      <FileVideo className="w-5 h-5" />
                    </div>
                    <span className="text-xs font-bold text-slate-900 truncate max-w-[180px]">
                      {item.filename || item.title || jobId}
                    </span>
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
                        setPreviewVideo(item);
                      }}
                      title="Xem trực tiếp video này"
                      className="px-3 py-1.5 bg-blue-50 hover:bg-blue-100 text-blue-700 text-xs font-bold rounded-xl border border-blue-200 transition flex items-center gap-1 cursor-pointer shadow-xs"
                    >
                      <Play className="w-3.5 h-3.5 text-blue-600 fill-blue-600" /> Xem
                    </button>
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
