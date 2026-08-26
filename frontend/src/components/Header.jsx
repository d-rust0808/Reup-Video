import React from 'react';
import {
  Layers,
  Video,
  FolderDown,
  MonitorCheck,
} from 'lucide-react';

export function Header({
  activeTabTitle,
  setActiveTab,
  queueCount,
  outputCount,
}) {
  const isDesktopApp = typeof window !== 'undefined' && !!window.electronAPI?.isDesktop;

  return (
    <header
      className={`${
        isDesktopApp ? 'h-18 pt-2.5' : 'h-16'
      } bg-white/95 backdrop-blur-md border-b border-slate-200/90 px-3 sm:px-6 flex items-center justify-between gap-3 sticky top-0 z-20 shadow-xs select-none transition-all`}
      style={{ WebkitAppRegion: 'drag' }}
    >

      {/* Left Breadcrumbs & Context Title */}
      <div className="flex items-center min-w-0 flex-1" style={{ WebkitAppRegion: 'no-drag' }}>
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-500 min-w-0">
          <span
            className="flex items-center gap-1.5 text-blue-600 font-bold hover:text-blue-700 cursor-pointer shrink-0"
            onClick={() => setActiveTab('extract')}
          >
            <Video className="w-4 h-4" /> <span className="hidden sm:inline">Reup Studio</span>
          </span>
          {isDesktopApp && (
            <span className="hidden md:inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-indigo-50 text-indigo-700 border border-indigo-200/80 shrink-0">
              <MonitorCheck className="w-3 h-3" /> Desktop
            </span>
          )}
          <span className="text-slate-300 font-normal hidden sm:inline shrink-0">/</span>
          <span className="text-slate-900 font-extrabold tracking-tight text-sm sm:text-base truncate">
            {activeTabTitle}
          </span>
        </div>
      </div>

      {/* Right Stats, Engine Tags & Actions */}
      <div className="flex items-center gap-2 sm:gap-3 shrink-0" style={{ WebkitAppRegion: 'no-drag' }}>

        {/* Supported Platforms Pills */}
        <div className="hidden xl:flex items-center space-x-1.5">
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
            <span className="hidden sm:inline">{queueCount} Job đang chờ</span>
            <span className="sm:hidden">{queueCount}</span>
          </button>
        )}

        {outputCount > 0 && (
          <button
            onClick={() => setActiveTab('gallery')}
            className="bg-emerald-50 hover:bg-emerald-100 text-emerald-800 border border-emerald-200 px-3.5 py-1.5 rounded-xl text-xs font-bold transition flex items-center gap-1.5 cursor-pointer shadow-xs"
          >
            <FolderDown className="w-3.5 h-3.5 text-emerald-600" />
            <span className="hidden sm:inline">{outputCount} Thành phẩm</span>
            <span className="sm:hidden">{outputCount}</span>
          </button>
        )}
      </div>
    </header>
  );
}
