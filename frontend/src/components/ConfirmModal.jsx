import React from 'react';
import { AlertTriangle, X } from 'lucide-react';

export function ConfirmModal({ isOpen, title, message, onConfirm, onCancel, confirmText, confirmLabel, cancelText = 'Hủy Bỏ' }) {
  if (!isOpen) return null;
  const ok = confirmText || confirmLabel || 'Xác Nhận Xóa';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-xs transition-opacity animate-in fade-in duration-200">
      <div
        className="w-full max-w-md bg-white rounded-3xl p-6 shadow-2xl border border-slate-200/90 space-y-4 transform transition-transform animate-in zoom-in-95 duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center space-x-3">
            <div className="p-2.5 rounded-2xl bg-rose-50 text-rose-600 border border-rose-100 shrink-0">
              <AlertTriangle className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-extrabold text-slate-900 leading-tight">
                {title || 'Xác Nhận Thao Tác'}
              </h3>
              <p className="text-xs text-slate-500 font-medium mt-0.5">Hành động này không thể hoàn tác.</p>
            </div>
          </div>

          <button
            onClick={onCancel}
            className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-xl transition cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-sm text-slate-700 font-medium leading-relaxed bg-slate-50 p-4 rounded-2xl border border-slate-100">
          {message}
        </p>

        <div className="flex items-center justify-end gap-2.5 pt-2">
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2.5 rounded-xl border border-slate-200 bg-slate-50 hover:bg-slate-100 text-slate-700 font-bold text-xs transition cursor-pointer shadow-xs"
          >
            {cancelText}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="px-5 py-2.5 rounded-xl bg-rose-600 hover:bg-rose-700 text-white font-extrabold text-xs transition shadow-md shadow-rose-600/20 cursor-pointer"
          >
            {ok}
          </button>
        </div>
      </div>
    </div>
  );
}
