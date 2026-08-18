import React, { useEffect } from 'react';
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react';

export function Toast({ toast, onClose }) {
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => {
      onClose();
    }, toast.duration || 4000);
    return () => clearTimeout(timer);
  }, [toast, onClose]);

  if (!toast) return null;

  const isSuccess = toast.type === 'success';
  const isError = toast.type === 'error';

  return (
    <div className="fixed bottom-6 right-6 z-50 flex items-center gap-3 p-4 bg-white rounded-2xl shadow-2xl border border-slate-200/90 max-w-sm animate-in slide-in-from-bottom-5 duration-300">
      <div className="shrink-0">
        {isSuccess && (
          <div className="p-2 rounded-xl bg-emerald-50 text-emerald-600 border border-emerald-100">
            <CheckCircle2 className="w-5 h-5" />
          </div>
        )}
        {isError && (
          <div className="p-2 rounded-xl bg-rose-50 text-rose-600 border border-rose-100">
            <AlertCircle className="w-5 h-5" />
          </div>
        )}
        {!isSuccess && !isError && (
          <div className="p-2 rounded-xl bg-blue-50 text-blue-600 border border-blue-100">
            <Info className="w-5 h-5" />
          </div>
        )}
      </div>

      <div className="flex-1 min-w-0">
        {toast.title && <h4 className="text-xs font-bold text-slate-900 leading-snug">{toast.title}</h4>}
        <p className="text-xs text-slate-600 font-medium leading-relaxed mt-0.5">{toast.message}</p>
      </div>

      <button
        onClick={onClose}
        className="p-1 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg transition shrink-0 cursor-pointer"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}
