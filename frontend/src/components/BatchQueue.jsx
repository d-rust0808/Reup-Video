import React, { useEffect, useState, useRef, useCallback } from 'react';
import { fetchJobs, fetchJobLogs, cancelJob, getDownloadUrl, deleteJob, clearJobs, retryJob, retryFailedJobs } from '../services/api';
import { Toast } from './Toast';
import { ConfirmModal } from './ConfirmModal';
import { VideoModal } from './VideoModal';
import {
  Layers,
  Loader2,
  CheckCircle2,
  XCircle,
  Clock,
  Trash2,
  RefreshCw,
  Download,
  Play,
  Terminal,
  Copy,
  Check,
  RotateCcw,
  Sparkles,
  Cpu,
  SquareTerminal,
  ArrowDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from 'lucide-react';

function logIdentity(entry) {
  if (!entry) return '';
  if (typeof entry === 'string') return entry;
  return `${entry.timestamp || ''}|${entry.message || ''}`;
}

function mergeJobLogs(prev = [], incoming = []) {
  const older = Array.isArray(prev) ? prev : [];
  const newer = Array.isArray(incoming) ? incoming : [];
  if (!newer.length) return older;
  if (!older.length) return newer;
  // A restarted / crop-only rerun starts with a different first line — replace, do not keep old delogo rows.
  if (logIdentity(newer[0]) !== logIdentity(older[0])) return newer;
  const olderSmear = older.some((e) => /che .*(delogo)|chỉ crop\/delogo/i.test(String(e?.message || e || '')));
  const newerClean = newer.some((e) => /không delogo/i.test(String(e?.message || e || '')));
  if (olderSmear && newerClean) return newer;
  if (newer.length >= older.length) return newer;
  return older;
}

function mergeJobRecord(previous, incoming) {
  if (!previous) return incoming;
  return {
    ...previous,
    ...incoming,
    logs: mergeJobLogs(previous.logs, incoming.logs),
  };
}

export function BatchQueue({ wsUpdates }) {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [toast, setToast] = useState(null);
  const [cancelTarget, setCancelTarget] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [clearTarget, setClearTarget] = useState(null); // 'completed' | 'all'
  const [previewVideo, setPreviewVideo] = useState(null);

  // Live Terminal Log Console States
  const [selectedJobId, setSelectedJobId] = useState(null);
  const [copied, setCopied] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const logContainerRef = useRef(null);

  // Pagination States (Per User Request)
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(8);
  const [nowTs, setNowTs] = useState(Date.now());

  const totalJobs = jobs.length;
  const totalPages = Math.max(1, Math.ceil(totalJobs / pageSize));
  const safePage = Math.min(Math.max(1, currentPage), totalPages);
  const startIndex = (safePage - 1) * pageSize;
  const endIndex = Math.min(startIndex + pageSize, totalJobs);
  const paginatedJobs = jobs.slice(startIndex, startIndex + pageSize);

  const getPageNumbers = () => {
    const pages = [];
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) pages.push(i);
    } else {
      if (safePage <= 4) {
        pages.push(1, 2, 3, 4, 5, '...', totalPages);
      } else if (safePage >= totalPages - 3) {
        pages.push(1, '...', totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages);
      } else {
        pages.push(1, '...', safePage - 1, safePage, safePage + 1, '...', totalPages);
      }
    }
    return pages;
  };

  const loadJobs = useCallback(async (isManualRefresh = false) => {
    if (isManualRefresh) setRefreshing(true);
    try {
      const data = await fetchJobs();
      const jobList = Array.isArray(data) ? data : (data.jobs || data.items || []);
      setJobs((prev) =>
        jobList.map((job) => mergeJobRecord(prev.find((item) => item.job_id === job.job_id), job))
      );
      if (jobList.length > 0) {
        setSelectedJobId((prev) => {
          if (prev && jobList.some((j) => j.job_id === prev)) return prev;
          const runningJob = jobList.find((j) => !['COMPLETED', 'FAILED', 'CANCELLED'].includes(j.status?.toUpperCase()));
          return runningJob ? runningJob.job_id : jobList[0].job_id;
        });
      } else {
        setSelectedJobId(null);
      }
    } catch (e) {
      console.error('Failed to load jobs:', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadJobs(false);
    const poll = setInterval(() => loadJobs(false), 2500);
    const timer = setTimeout(() => setLoading(false), 2000);
    return () => {
      clearInterval(poll);
      clearTimeout(timer);
    };
  }, [loadJobs]);

  useEffect(() => {
    if (!selectedJobId) return undefined;
    let cancelled = false;
    const pullLogs = async () => {
      try {
        const data = await fetchJobLogs(selectedJobId);
        if (cancelled || !data) return;
        setJobs((prev) =>
          prev.map((job) => {
            if (job.job_id !== selectedJobId) return job;
            return mergeJobRecord(job, {
              ...job,
              status: data.status || job.status,
              progress_percent: data.progress_percent ?? job.progress_percent,
              message: data.message || job.message,
              logs: data.logs || job.logs,
            });
          })
        );
      } catch {
        /* keep current console */
      }
    };
    pullLogs();
    const tick = setInterval(pullLogs, 1500);
    return () => {
      cancelled = true;
      clearInterval(tick);
    };
  }, [selectedJobId]);

  useEffect(() => {
    const selected = jobs.find((j) => j.job_id === selectedJobId) || jobs[0];
    const running =
      selected && !['COMPLETED', 'FAILED', 'CANCELLED'].includes(selected.status?.toUpperCase());
    if (!running) return undefined;
    const tick = setInterval(() => setNowTs(Date.now()), 1000);
    return () => clearInterval(tick);
  }, [jobs, selectedJobId]);


  // Handle incoming WebSocket broadcast updates
  useEffect(() => {
    if (!wsUpdates) return;
    setJobs((prevJobs) => {
      const list = Array.isArray(prevJobs) ? prevJobs : [];
      const existingIdx = list.findIndex((j) => j.job_id === wsUpdates.job_id);
      if (existingIdx >= 0) {
        const updated = [...list];
        updated[existingIdx] = mergeJobRecord(updated[existingIdx], wsUpdates);
        return updated;
      }
      return [wsUpdates, ...list];
    });

    // Auto-focus selected job if none is selected yet
    if (wsUpdates.job_id) {
      setSelectedJobId((prev) => prev || wsUpdates.job_id);
    }

    // Trigger Native Desktop Notification on completion
    const statusUpper = (wsUpdates.status || '').toUpperCase();
    if (statusUpper === 'COMPLETED') {
      if (typeof window !== 'undefined' && window.electronAPI?.sendNotification) {
        window.electronAPI.sendNotification(
          'Reup Studio - Hoàn Tất Xử Lý! 🎬',
          `Video "${wsUpdates.job_id}" đã hoàn thành và sẵn sàng tải về.`
        );
      }
    }
  }, [wsUpdates]);

  // Auto-scroll log container to bottom when logs update
  useEffect(() => {
    if (autoScroll && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [jobs, selectedJobId, autoScroll]);

  const confirmCancel = async () => {
    if (!cancelTarget) return;
    const jobId = cancelTarget;
    setCancelTarget(null);
    try {
      const result = await cancelJob(jobId);
      if (result?.already_finished) {
        setToast({
          type: 'info',
          title: 'Job đã kết thúc',
          message: result.message || `Job ${jobId} không còn đang chạy.`,
        });
      } else {
        setToast({
          type: 'info',
          title: 'Đã Hủy Job',
          message: `Đã gửi yêu cầu dừng tiến trình của job ${jobId}.`,
        });
      }
      loadJobs();
    } catch (e) {
      setToast({
        type: 'error',
        title: 'Hủy Job Thất Bại',
        message: e.message || 'Không thể hủy job',
      });
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    const jobId = deleteTarget;
    setDeleteTarget(null);
    try {
      await deleteJob(jobId);
      setToast({
        type: 'success',
        title: 'Đã Xóa Job',
        message: `Đã xóa Job "${jobId}" khỏi hàng chờ.`,
      });
      loadJobs();
    } catch (e) {
      setToast({
        type: 'error',
        title: 'Xóa Job Thất Bại',
        message: e.message || 'Không thể xóa job',
      });
    }
  };

  const confirmClear = async () => {
    if (!clearTarget) return;
    const targetType = clearTarget;
    setClearTarget(null);
    try {
      const statusParam = targetType === 'completed' ? 'COMPLETED' : null;
      const res = await clearJobs(statusParam);
      setToast({
        type: 'success',
        title: 'Đã Dọn Dẹp Hàng Chờ',
        message: res.message || `Đã dọn dẹp ${res.deleted_count || 0} job.`,
      });
      loadJobs();
    } catch (e) {
      setToast({
        type: 'error',
        title: 'Dọn Dẹp Thất Bại',
        message: e.message || 'Không thể dọn dẹp hàng chờ',
      });
    }
  };

  const getStatusBadge = (status) => {
    switch (status?.toLowerCase()) {
      case 'completed':
        return (
          <span className="px-2.5 py-1 text-xs font-bold rounded-lg bg-emerald-100 text-emerald-700 border border-emerald-200 flex items-center gap-1.5 w-max">
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" /> Hoàn thành
          </span>
        );
      case 'processing':
      case 'extracting':
      case 'removing_watermark':
      case 'watermark_removal':
      case 'rendering':
      case 'downloading':
      case 'reup_transform':
        return (
          <span className="px-2.5 py-1 text-xs font-bold rounded-lg bg-blue-100 text-blue-700 border border-blue-200 flex items-center gap-1.5 w-max shadow-xs">
            <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-600" /> Đang xử lý
          </span>
        );
      case 'failed':
        return (
          <span className="px-2.5 py-1 text-xs font-bold rounded-lg bg-rose-100 text-rose-700 border border-rose-200 flex items-center gap-1.5 w-max">
            <XCircle className="w-3.5 h-3.5 text-rose-600" /> Thất bại
          </span>
        );
      case 'cancelled':
        return (
          <span className="px-2.5 py-1 text-xs font-bold rounded-lg bg-slate-100 text-slate-700 border border-slate-200 flex items-center gap-1.5 w-max">
            <XCircle className="w-3.5 h-3.5 text-slate-500" /> Đã hủy
          </span>
        );
      default:
        return (
          <span className="px-2.5 py-1 text-xs font-bold rounded-lg bg-amber-100 text-amber-700 border border-amber-200 flex items-center gap-1.5 w-max">
            <Clock className="w-3.5 h-3.5 text-amber-600" /> {status || 'Chờ xử lý'}
          </span>
        );
    }
  };

  const selectedJob = jobs.find((j) => j.job_id === selectedJobId) || jobs[0] || null;
  const currentLogs = selectedJob?.logs || [];
  const isJobRunning = selectedJob && !['COMPLETED', 'FAILED', 'CANCELLED'].includes(selectedJob.status?.toUpperCase());
  const lastActivity = (job) => {
    const logs = job?.logs || [];
    const last = logs.length ? logs[logs.length - 1] : null;
    return (last?.message || job?.message || '').trim();
  };
  const activityAgeSec = (() => {
    if (!isJobRunning || !selectedJob) return 0;
    const t = Date.parse(selectedJob.updated_at || '');
    if (!t) return 0;
    return Math.max(0, Math.round((nowTs - t) / 1000));
  })();
  const formatAge = (sec) => {
    if (sec < 5) return 'vừa xong';
    if (sec < 60) return `${sec}s trước`;
    const minutes = Math.floor(sec / 60);
    const rem = sec % 60;
    return rem ? `${minutes} phút ${rem}s trước` : `${minutes} phút trước`;
  };

  const handleCopyLogs = () => {
    if (!selectedJob) return;
    const formatted = (selectedJob.logs || [])
      .map((l) => `[${l.timestamp || '--:--:--'}] [${l.level || 'INFO'}]${l.stage ? ` [${l.stage}]` : ''} ${l.message || ''}`)
      .join('\n');

    const textToCopy = formatted || `Job ID: ${selectedJob.job_id}\nTrạng Thái: ${selectedJob.status}\nTiến Độ: ${selectedJob.progress_percent || 0}%\nThông điệp: ${selectedJob.message || '—'}`;
    navigator.clipboard.writeText(textToCopy);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="clean-panel rounded-3xl p-6 sm:p-8 shadow-xs space-y-6">
      {/* Custom Cancel Confirmation Modal */}
      <ConfirmModal
        isOpen={!!cancelTarget}
        title="Hủy Tiến Trình Job"
        message={`Bạn có chắc chắn muốn dừng tiến trình xử lý của Job "${cancelTarget}" không?`}
        onConfirm={confirmCancel}
        onCancel={() => setCancelTarget(null)}
        confirmText="Xác Nhận Hủy"
        cancelText="Giữ Lại"
      />

      {/* Custom Delete Confirmation Modal */}
      <ConfirmModal
        isOpen={!!deleteTarget}
        title="Xóa Job Khỏi Hàng Chờ"
        message={`Bạn có chắc chắn muốn xóa Job "${deleteTarget}" và file video liên quan khỏi hệ thống không?`}
        onConfirm={confirmDelete}
        onCancel={() => setDeleteTarget(null)}
        confirmText="Xác Nhận Xóa"
        cancelText="Hủy Bỏ"
      />

      {/* Custom Batch Clear Confirmation Modal */}
      <ConfirmModal
        isOpen={!!clearTarget}
        title={clearTarget === 'completed' ? 'Dọn Dẹp Job Hoàn Tất' : 'Xóa Toàn Bộ Hàng Chờ'}
        message={
          clearTarget === 'completed'
            ? 'Bạn có chắc chắn muốn xóa tất cả các Job đã hoàn thành khỏi hàng chờ và kho lưu trữ?'
            : 'Bạn có chắc chắn muốn dọn dẹp toàn bộ danh sách Job khỏi hàng chờ?'
        }
        onConfirm={confirmClear}
        onCancel={() => setClearTarget(null)}
        confirmText="Xác Nhận Dọn Dẹp"
        cancelText="Hủy Bỏ"
      />

      {/* Custom In-App Toast */}
      <Toast toast={toast} onClose={() => setToast(null)} />

      {/* Queue Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-100">
        <div>
          <h3 className="text-base font-extrabold text-slate-900 flex items-center gap-2">
            <Layers className="w-5 h-5 text-blue-600" />
            Hàng Chờ Xử Lý Batch & Live Pipeline Monitor
          </h3>
          <p className="text-xs text-slate-500 mt-1 font-medium">Giám sát tiến độ 4 giai đoạn xử lý video realtime qua WebSocket.</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={async () => {
              try {
                const r = await retryFailedJobs();
                setToast({ type: 'success', title: 'Chạy lại lỗi', message: r.message || `${r.retried} job` });
                loadJobs(false);
              } catch (e) {
                setToast({ type: 'error', title: 'Lỗi', message: e.message });
              }
            }}
            disabled={!jobs.some((j) => ['FAILED', 'CANCELLED'].includes(j.status?.toUpperCase()))}
            className="px-3 py-2 bg-amber-50 hover:bg-amber-100 disabled:opacity-40 text-amber-800 text-xs font-bold rounded-xl transition flex items-center gap-1.5 border border-amber-200 cursor-pointer shadow-xs"
          >
            <RotateCcw className="w-3.5 h-3.5" /> Chạy lại job lỗi
          </button>
          <button
            onClick={() => setClearTarget('completed')}
            disabled={!jobs.some((j) => j.status?.toUpperCase() === 'COMPLETED')}
            className="px-3 py-2 bg-slate-100 hover:bg-rose-50 hover:text-rose-700 hover:border-rose-200 disabled:opacity-40 disabled:cursor-not-allowed text-slate-700 text-xs font-bold rounded-xl transition flex items-center gap-1.5 border border-slate-200 cursor-pointer shadow-xs"
            title="Xóa tất cả các job đã hoàn thành"
          >
            <Trash2 className="w-3.5 h-3.5 text-rose-500" /> Xóa Đã Xong
          </button>
          <button
            onClick={() => setClearTarget('all')}
            disabled={jobs.length === 0}
            className="px-3 py-2 bg-slate-100 hover:bg-rose-50 hover:text-rose-700 hover:border-rose-200 disabled:opacity-40 disabled:cursor-not-allowed text-slate-700 text-xs font-bold rounded-xl transition flex items-center gap-1.5 border border-slate-200 cursor-pointer shadow-xs"
            title="Xóa toàn bộ các job trong hàng chờ"
          >
            <Trash2 className="w-3.5 h-3.5 text-rose-600" /> Xóa Tất Cả
          </button>
          <button
            onClick={() => loadJobs(true)}
            disabled={refreshing}
            className="px-3.5 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-bold rounded-xl transition flex items-center gap-1.5 border border-slate-200 cursor-pointer shadow-xs disabled:opacity-60"
            title="Cập nhật nhanh danh sách"
          >
            <RefreshCw className={`w-3.5 h-3.5 text-blue-600 ${refreshing ? 'animate-spin' : ''}`} />
            {refreshing ? 'Đang Tải...' : 'Cập Nhật'}
          </button>
        </div>
      </div>

      {/* Jobs Table */}
      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xs">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-slate-50/90 text-slate-600 font-bold border-b border-slate-200/90">
                <th className="px-4 py-3.5 whitespace-nowrap">Mã Job ID</th>
                <th className="px-4 py-3.5 whitespace-nowrap">Nguồn Video</th>
                <th className="px-4 py-3.5 whitespace-nowrap">Trạng Thái</th>
                <th className="px-4 py-3.5 whitespace-nowrap min-w-[200px]">Tiến Độ Xử Lý</th>
                <th className="px-4 py-3.5 whitespace-nowrap text-right">Thao Tác</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {loading && jobs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="text-center py-12 text-slate-400">
                    <div className="flex items-center justify-center gap-2 font-medium">
                      <Loader2 className="w-4 h-4 animate-spin text-blue-600" />
                      <span>Đang tải hàng chờ...</span>
                    </div>
                  </td>
                </tr>
              ) : jobs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="text-center py-12 text-slate-400 font-medium">
                    Chưa có job nào trong hàng chờ. Hãy chọn video và nhấn Bắt Đầu Xử Lý từ Studio.
                  </td>
                </tr>

              ) : (
                paginatedJobs.map((job) => {
                  const isCompleted = job.status?.toUpperCase() === 'COMPLETED';
                  const isFailed = job.status?.toUpperCase() === 'FAILED';
                  const isCancelled = job.status?.toUpperCase() === 'CANCELLED';
                  const isSelected = job.job_id === selectedJobId;
                  const progressPct = isCompleted
                    ? 100
                    : Math.round(
                        job.progress_percent ??
                          (job.progress ? (job.progress <= 1 ? job.progress * 100 : job.progress) : 0)
                      );

                  const formatStage = (s) => {
                    const st = (s || '').toUpperCase();
                    if (st === 'DOWNLOADING') return 'Đang nạp video';
                    if (st === 'WATERMARK_REMOVAL') return 'Xóa Watermark AI';
                    if (st === 'REUP_TRANSFORM') return 'Biến đổi Reup';
                    if (st === 'COMPLETED') return 'Hoàn thành';
                    if (st === 'FAILED') return 'Thất bại';
                    return s || 'Đang chờ';
                  };

                  const etaText = (() => {
                    if (isCompleted || isFailed || isCancelled || progressPct < 4) return null;
                    const t = Date.parse(job.updated_at || job.created_at || '');
                    if (!t) return null;
                    const elapsed = Date.now() - t;
                    const remain = elapsed * (100 - progressPct) / progressPct;
                    const sec = Math.max(5, Math.round(remain / 1000));
                    if (sec < 60) return `~${sec}s`;
                    return `~${Math.round(sec / 60)} phút`;
                  })();

                  return (
                    <tr
                      key={job.job_id}
                      onClick={() => setSelectedJobId(job.job_id)}
                      className={`cursor-pointer transition-all ${
                        isSelected
                          ? 'bg-blue-50/80 border-l-4 border-blue-600 font-medium'
                          : 'hover:bg-slate-50/70'
                      }`}
                    >
                      <td className="px-4 py-3.5 font-mono font-bold text-slate-900 whitespace-nowrap">
                        <div className="flex items-center gap-2">
                          {isSelected && <span className="w-2 h-2 rounded-full bg-blue-600 animate-pulse shrink-0" />}
                          <span className="truncate max-w-[140px]">{job.job_id}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3.5 whitespace-nowrap">
                        <span className="px-2.5 py-0.5 rounded-md text-[10px] font-black uppercase tracking-wider bg-slate-100 text-slate-700 border border-slate-200">
                          {job.platform || 'douyin'}
                        </span>
                      </td>
                      <td className="px-4 py-3.5 whitespace-nowrap">{getStatusBadge(job.status)}</td>
                      <td className="px-4 py-3.5 min-w-[200px]">
                        <div className="space-y-1.5">
                          <div className="flex items-center justify-between text-[11px] font-mono">
                            <span className="text-slate-600 font-bold truncate max-w-[150px]">
                              {formatStage(job.stage || job.status)}
                            </span>
                            <span className="font-extrabold text-blue-700 ml-2">{progressPct}%{etaText ? ` · ${etaText}` : ''}</span>
                          </div>
                          <div className="w-full bg-slate-100 rounded-full h-2.5 overflow-hidden border border-slate-200/80">
                            <div
                              className={`h-full transition-all duration-300 rounded-full ${
                                isCompleted ? 'bg-emerald-500' : isFailed ? 'bg-rose-500' : 'bg-blue-600'
                              }`}
                              style={{ width: `${progressPct}%` }}
                            />
                          </div>
                          {!isCompleted && !isFailed && !isCancelled && lastActivity(job) && (
                            <p
                              className="text-[10px] text-slate-500 font-medium truncate max-w-[240px]"
                              title={lastActivity(job)}
                            >
                              {lastActivity(job)}
                            </p>
                          )}
                          {(isFailed || isCancelled) && (job.error_message || job.error) && (
                            <p className="text-[10px] text-rose-600 font-medium truncate max-w-[220px]" title={job.error_message || job.error}>
                              {job.error_message || job.error}
                            </p>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3.5 whitespace-nowrap text-right" onClick={(e) => e.stopPropagation()}>

                        {isCompleted ? (
                          <div className="flex items-center justify-end gap-1.5">
                            <button
                              type="button"
                              onClick={() => setPreviewVideo(job)}
                              className="px-2.5 py-1.5 text-[11px] font-bold bg-blue-50 hover:bg-blue-100 text-blue-700 rounded-xl border border-blue-200 transition inline-flex items-center gap-1 cursor-pointer shadow-xs"
                            >
                              <Play className="w-3.5 h-3.5 text-blue-600 fill-blue-600" /> Xem Video
                            </button>
                            <a
                              href={getDownloadUrl(job.job_id)}
                              download
                              className="px-2.5 py-1.5 text-[11px] font-bold bg-emerald-50 hover:bg-emerald-100 text-emerald-700 rounded-xl border border-emerald-200 transition inline-flex items-center gap-1 cursor-pointer shadow-xs"
                            >
                              <Download className="w-3.5 h-3.5 text-emerald-600" /> Tải Xuống
                            </a>
                            <button
                              type="button"
                              onClick={() => setDeleteTarget(job.job_id)}
                              className="p-1.5 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-xl border border-transparent hover:border-rose-200 transition cursor-pointer"
                              title="Xóa job này"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ) : isFailed || isCancelled ? (
                          <div className="flex items-center justify-end gap-1.5">
                            <button
                              type="button"
                              onClick={async () => {
                                try {
                                  await retryJob(job.job_id);
                                  setToast({ type: 'success', title: 'Đã xếp lại', message: job.job_id });
                                  loadJobs(false);
                                } catch (e) {
                                  setToast({ type: 'error', title: 'Retry lỗi', message: e.message });
                                }
                              }}
                              className="px-2.5 py-1.5 text-[11px] font-bold bg-amber-50 hover:bg-amber-100 text-amber-800 rounded-xl border border-amber-200 transition inline-flex items-center gap-1 cursor-pointer"
                            >
                              <RotateCcw className="w-3.5 h-3.5" /> Chạy lại
                            </button>
                            <button
                              onClick={() => setSelectedJobId(job.job_id)}
                              className="px-2.5 py-1.5 text-[11px] font-bold bg-rose-50 hover:bg-rose-100 text-rose-700 rounded-xl border border-rose-200 transition inline-flex items-center gap-1 cursor-pointer"
                            >
                              Chi Tiết Lỗi
                            </button>
                            <button
                              type="button"
                              onClick={() => setDeleteTarget(job.job_id)}
                              className="p-1.5 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-xl border border-transparent hover:border-rose-200 transition cursor-pointer"
                              title="Xóa job này"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ) : (
                          <div className="flex items-center justify-end gap-1.5">
                            <button
                              onClick={() => setSelectedJobId(job.job_id)}
                              className="px-2.5 py-1.5 text-[11px] font-bold bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-xl border border-slate-200 transition inline-flex items-center gap-1 cursor-pointer"
                            >
                              <Terminal className="w-3 h-3 text-blue-600" /> Logs
                            </button>
                            <button
                              onClick={() => setCancelTarget(job.job_id)}
                              className="px-3 py-1.5 text-[11px] font-bold bg-rose-50 hover:bg-rose-100 text-rose-700 rounded-xl border border-rose-200 transition inline-flex items-center gap-1.5 cursor-pointer"
                            >
                              <Trash2 className="w-3.5 h-3.5 text-rose-600" /> Hủy Job
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination Bar (Per User Request) */}
        {totalJobs > 0 && (
          <div className="px-4 py-3 bg-slate-50/90 border-t border-slate-200/90 flex flex-wrap items-center justify-between gap-3 text-xs">
            <div className="flex items-center gap-3 text-slate-600 font-medium">
              <span>
                Hiển thị <strong className="text-slate-900 font-bold">{startIndex + 1}</strong>–
                <strong className="text-slate-900 font-bold">{endIndex}</strong> trong{' '}
                <strong className="text-slate-900 font-bold">{totalJobs}</strong> jobs
              </span>
              <div className="flex items-center gap-1.5 border-l border-slate-200 pl-3">
                <span className="text-[11px] text-slate-500">Mỗi trang:</span>
                <select
                  value={pageSize}
                  onChange={(e) => {
                    setPageSize(Number(e.target.value));
                    setCurrentPage(1);
                  }}
                  className="bg-white border border-slate-200 text-slate-800 font-bold text-xs rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-blue-400 cursor-pointer shadow-2xs"
                >
                  <option value={5}>5 / trang</option>
                  <option value={8}>8 / trang</option>
                  <option value={10}>10 / trang</option>
                  <option value={20}>20 / trang</option>
                  <option value={50}>50 / trang</option>
                </select>
              </div>
            </div>

            {totalPages > 1 && (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setCurrentPage(1)}
                  disabled={safePage <= 1}
                  className="p-1.5 rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition shadow-2xs"
                  title="Trang đầu"
                >
                  <ChevronsLeft className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                  disabled={safePage <= 1}
                  className="p-1.5 rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition shadow-2xs"
                  title="Trang trước"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                </button>

                <div className="flex items-center gap-1 px-1">
                  {getPageNumbers().map((p, idx) =>
                    p === '...' ? (
                      <span key={`dots-${idx}`} className="px-1.5 py-1 text-slate-400 font-bold text-xs">
                        ...
                      </span>
                    ) : (
                      <button
                        key={`page-${p}`}
                        type="button"
                        onClick={() => setCurrentPage(p)}
                        className={`min-w-[28px] h-7 px-2 text-xs font-extrabold rounded-lg transition cursor-pointer ${
                          safePage === p
                            ? 'bg-blue-600 text-white shadow-xs'
                            : 'bg-white border border-slate-200 text-slate-700 hover:bg-slate-100 shadow-2xs'
                        }`}
                      >
                        {p}
                      </button>
                    )
                  )}
                </div>

                <button
                  type="button"
                  onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                  disabled={safePage >= totalPages}
                  className="p-1.5 rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition shadow-2xs"
                  title="Trang tiếp"
                >
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => setCurrentPage(totalPages)}
                  disabled={safePage >= totalPages}
                  className="p-1.5 rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition shadow-2xs"
                  title="Trang cuối"
                >
                  <ChevronsRight className="w-3.5 h-3.5" />
                </button>
              </div>
            )}
          </div>
        )}
      </div>


      {/* ========================================================================= */}
      {/* Real-Time Live Terminal Log Viewer (Per User Request)                     */}
      {/* ========================================================================= */}
      <div className="rounded-2xl border border-slate-800 bg-[#0B0F19] text-slate-200 overflow-hidden shadow-lg">
        {/* Terminal Header Bar */}
        <div className="px-4 py-3 bg-[#111827] border-b border-slate-800/80 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            {/* Mac OS Window Controls */}
            <div className="flex items-center gap-1.5">
              <span className="w-3 h-3 rounded-full bg-rose-500/90 inline-block shadow-inner" />
              <span className="w-3 h-3 rounded-full bg-amber-500/90 inline-block shadow-inner" />
              <span className="w-3 h-3 rounded-full bg-emerald-500/90 inline-block shadow-inner" />
            </div>
            <div className="h-4 w-px bg-slate-700 mx-1" />
            <div className="flex items-center gap-2 font-mono text-xs font-bold text-slate-300">
              <SquareTerminal className="w-4 h-4 text-blue-400" />
              <span>Pipeline Live Console & Execution Stream</span>
            </div>
            {selectedJob && (
              <span className="px-2.5 py-0.5 rounded-full text-[11px] font-mono font-bold bg-blue-950/80 text-blue-300 border border-blue-700/50 flex items-center gap-1.5">
                {isJobRunning && <span className="w-2 h-2 rounded-full bg-blue-400 animate-ping" />}
                {selectedJob.job_id} ({selectedJob.platform || 'auto'})
              </span>
            )}
          </div>

          {/* Terminal Action Buttons */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => setAutoScroll(!autoScroll)}
              className={`px-2.5 py-1 text-[11px] font-mono font-semibold rounded-lg border transition flex items-center gap-1 cursor-pointer ${
                autoScroll
                  ? 'bg-blue-600/20 text-blue-300 border-blue-500/40 hover:bg-blue-600/30'
                  : 'bg-slate-800 text-slate-400 border-slate-700 hover:bg-slate-700'
              }`}
              title="Tự động cuộn theo log mới nhất"
            >
              <ArrowDown className={`w-3 h-3 ${autoScroll ? 'text-blue-400' : 'text-slate-500'}`} />
              Auto-Scroll: {autoScroll ? 'ON' : 'OFF'}
            </button>

            <button
              onClick={handleCopyLogs}
              className="px-2.5 py-1 text-[11px] font-mono font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition flex items-center gap-1 cursor-pointer"
              title="Sao chép toàn bộ nhật ký ra clipboard"
            >
              {copied ? (
                <>
                  <Check className="w-3 h-3 text-emerald-400" />
                  <span className="text-emerald-400">Đã Copy!</span>
                </>
              ) : (
                <>
                  <Copy className="w-3 h-3 text-slate-400" />
                  <span>Copy Logs</span>
                </>
              )}
            </button>

            <button
              onClick={loadJobs}
              className="px-2.5 py-1 text-[11px] font-mono font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition flex items-center gap-1 cursor-pointer"
              title="Tải lại logs từ máy chủ"
            >
              <RotateCcw className="w-3 h-3 text-slate-400" />
              <span>Làm Mới</span>
            </button>
          </div>
        </div>

        {/* Terminal Stream Output Body */}
        <div
          ref={logContainerRef}
          className="p-4 font-mono text-xs leading-relaxed max-h-[36rem] min-h-64 overflow-y-auto space-y-1.5 select-text selection:bg-blue-600 selection:text-white"
        >
          {!selectedJob ? (
            <div className="py-12 text-center text-slate-500 space-y-2">
              <Terminal className="w-8 h-8 mx-auto text-slate-600 opacity-60" />
              <p>Chưa có tác vụ nào được chọn. Hãy chọn một tác vụ ở bảng trên để xem logs thời gian thực.</p>
            </div>
          ) : currentLogs.length === 0 ? (
            <div className="py-8 text-slate-400 space-y-2">
              <div className="flex items-center gap-2 text-slate-300">
                <span className="text-emerald-400 font-bold">$</span>
                <span>Khởi tạo tiến trình giám sát cho tác vụ [{selectedJob.job_id}]...</span>
              </div>
              <div className="flex items-center gap-2 text-slate-400">
                <span className="text-blue-400 font-bold">&gt;</span>
                <span>Trạng thái hiện tại: {selectedJob.status} ({selectedJob.progress_percent || 0}%)</span>
              </div>
              {selectedJob.message && (
                <div className="flex items-center gap-2 text-slate-300">
                  <span className="text-amber-400 font-bold">&gt;</span>
                  <span>{selectedJob.message}</span>
                </div>
              )}
              {isJobRunning && (
                <div className="flex items-center gap-2 text-blue-400 pt-2 animate-pulse">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>Đang lắng nghe dữ liệu pipeline realtime từ backend worker...</span>
                </div>
              )}
            </div>
          ) : (
            currentLogs.map((log, index) => {
              const level = (log.level || 'INFO').toUpperCase();
              let levelColor = 'text-blue-400 bg-blue-950/60 border-blue-800/40';
              if (level === 'SUCCESS') levelColor = 'text-emerald-400 bg-emerald-950/60 border-emerald-800/40';
              else if (level === 'WARN') levelColor = 'text-amber-400 bg-amber-950/60 border-amber-800/40';
              else if (level === 'ERROR') levelColor = 'text-rose-400 bg-rose-950/60 border-rose-800/40';
              else if (level === 'STAGE') levelColor = 'text-purple-400 bg-purple-950/60 border-purple-800/40';

              return (
                <div key={index} className="flex items-start gap-2 hover:bg-slate-900/50 py-0.5 px-1 rounded transition">
                  <span className="text-slate-600 select-none text-[10px] w-6 text-right pt-0.5">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <span className="text-cyan-400/70 select-none text-[11px] pt-0.5">
                    [{log.timestamp || '--:--:--'}]
                  </span>
                  <span className={`px-1.5 py-0.2 rounded text-[10px] font-bold border select-none ${levelColor}`}>
                    {level}
                  </span>
                  {log.stage && (
                    <span className="text-purple-400/80 text-[11px] font-bold select-none">
                      [{log.stage}]
                    </span>
                  )}
                  <span className="text-slate-200 break-all flex-1">
                    {log.message}
                  </span>
                </div>
              );
            })
          )}

          {/* Active Job Blinking Cursor */}
          {isJobRunning && (
            <div className="flex items-start gap-2 text-emerald-400 pt-2 font-bold">
              <Loader2 className="w-3.5 h-3.5 animate-spin mt-0.5 shrink-0" />
              <span className="text-slate-300 text-xs font-medium break-all">
                {lastActivity(selectedJob) || 'Pipeline đang chạy...'}
                <span className="text-slate-500 font-normal"> · log mới nhất {formatAge(activityAgeSec)}</span>
              </span>
              <span className="inline-block w-2 h-4 bg-emerald-400 animate-pulse ml-0.5 shrink-0" />
            </div>
          )}
        </div>

        {/* Terminal Footer Status Bar */}
        <div className="px-4 py-2 bg-[#0d1322] border-t border-slate-800/60 text-[11px] font-mono text-slate-400 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <span className="flex items-center gap-1">
              <Cpu className="w-3.5 h-3.5 text-blue-400" /> Worker Engine: Active
            </span>
            <span className="flex items-center gap-1">
              <Sparkles className="w-3.5 h-3.5 text-emerald-400" /> Pipeline: Anti-Halo 5x5 + Telea Inpainter
            </span>
          </div>
          <div>
            <span>
              {isJobRunning
                ? `Đang làm: ${lastActivity(selectedJob) || selectedJob.status} · ${currentLogs.length} dòng`
                : `Tổng cộng: ${currentLogs.length} dòng log`}
            </span>
          </div>
        </div>
      </div>

      {/* Direct Video Player Modal */}
      <VideoModal
        isOpen={!!previewVideo}
        onClose={() => setPreviewVideo(null)}
        video={previewVideo}
      />
    </div>
  );
}
