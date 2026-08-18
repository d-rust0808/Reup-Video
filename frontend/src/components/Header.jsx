import React from 'react';
import {
  Layers,
  Video,
  FolderDown,
} from 'lucide-react';

export function Header({
  activeTabTitle,
  setActiveTab,
  queueCount,
  outputCount,
}) {
  return (
    <header className="h-16 bg-white/95 backdrop-blur-md border-b border-slate-200/90 px-6 flex items-center justify-between sticky top-0 z-20 shadow-xs">
      {/* Left Breadcrumbs & Context Title */}
      <div className="flex items-center space-x-3">
        <div className="flex items-center space-x-2 text-xs font-semibold text-slate-500">
          <span
            className="flex items-center gap-1.5 text-blue-600 font-bold hover:text-blue-700 cursor-pointer"
            onClick={() => setActiveTab('extract')}
          >
            <Video className="w-4 h-4" /> Reup Studio
          </span>
          <span className="text-slate-300 font-normal">/</span>
          <span className="text-slate-900 font-extrabold tracking-tight text-sm sm:text-base flex items-center gap-2">
            {activeTabTitle}
          </span>
        </div>
      </div>

      {/* Right Stats, Engine Tags & Actions */}
      <div className="flex items-center space-x-3">

        {/* Supported Platforms Pills */}
        <div className="hidden sm:flex items-center space-x-1.5">
          <span className="px-2.5 py-1 text-xs font-bold rounded-lg bg-pink-50 text-pink-700 border border-pink-200">
            Douyin
          </span>
          <span className="px-2.5 py-1 text-xs font-bold rounded-lg bg-amber-50 text-amber-800 border border-amber-200">
            Kuaishou
          </span>
          <span className="px-2.5 py-1 text-xs font-bold rounded-lg bg-red-50 text-red-700 border border-red-200">
            Xiaohongshu
          </span>
        </div>

        {/* Quick Navigate to Queue / Outputs */}
        {queueCount > 0 && (
          <button
            onClick={() => setActiveTab('queue')}
            className="bg-amber-50 hover:bg-amber-100 text-amber-800 border border-amber-200 px-3.5 py-1.5 rounded-xl text-xs font-bold transition flex items-center gap-1.5 cursor-pointer shadow-xs"
          >
            <Layers className="w-3.5 h-3.5 text-amber-600 animate-spin" />
            <span>{queueCount} Job Đang Chờ</span>
          </button>
        )}

        {outputCount > 0 && (
          <button
            onClick={() => setActiveTab('gallery')}
            className="bg-emerald-50 hover:bg-emerald-100 text-emerald-800 border border-emerald-200 px-3.5 py-1.5 rounded-xl text-xs font-bold transition flex items-center gap-1.5 cursor-pointer shadow-xs"
          >
            <FolderDown className="w-3.5 h-3.5 text-emerald-600" />
            <span>{outputCount} Thành Phẩm</span>
          </button>
        )}
      </div>
    </header>
  );
}
