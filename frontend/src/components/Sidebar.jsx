import React from 'react';
import logoImg from '../assets/logo.jpg';
import {
  Link2,
  Sliders,
  Layers,
  FolderDown,
  Tv2,
  Server,
  Wifi,
  Cpu,
  ChevronLeft,
  ChevronRight,
  ShieldCheck,
} from 'lucide-react';

export function Sidebar({
  activeTab,
  setActiveTab,
  extractedCount,
  queueCount,
  outputCount,
  serverOnline,
  wsStatus,
  collapsed,
  setCollapsed,
}) {
  const isDesktopApp = typeof window !== 'undefined' && !!window.electronAPI?.isDesktop;

  const menuItems = [
    {
      id: 'extract',
      label: 'Bóc Tách & Nạp Video',
      shortLabel: 'Bóc Tách',
      icon: Link2,
      badge: extractedCount,
      shortcut: '⌘1',
      desc: 'Douyin, Kuaishou, XHS & File máy',
      activeBg: 'bg-blue-50/90 text-blue-800 border-blue-200 shadow-xs font-bold',
      iconActive: 'bg-blue-600 text-white shadow-sm shadow-blue-500/20',
      iconInactive: 'bg-slate-100 text-slate-600 group-hover:bg-slate-200 group-hover:text-slate-900',
    },
    {
      id: 'workbench',
      label: 'Studio & Canvas ROI',
      shortLabel: 'Studio',
      icon: Sliders,
      shortcut: '⌘2',
      desc: 'Xoá watermark, logo & chỉnh Reup',
      activeBg: 'bg-blue-50/90 text-blue-800 border-blue-200 shadow-xs font-bold',
      iconActive: 'bg-blue-600 text-white shadow-sm shadow-blue-500/20',
      iconInactive: 'bg-slate-100 text-slate-600 group-hover:bg-slate-200 group-hover:text-slate-900',
    },
    {
      id: 'queue',
      label: 'Hàng Chờ Xử Lý Live',
      shortLabel: 'Hàng Chờ',
      icon: Layers,
      badge: queueCount,
      shortcut: '⌘3',
      desc: 'Theo dõi tiến trình 4 giai đoạn',
      activeBg: 'bg-blue-50/90 text-blue-800 border-blue-200 shadow-xs font-bold',
      iconActive: 'bg-blue-600 text-white shadow-sm shadow-blue-500/20',
      iconInactive: 'bg-slate-100 text-slate-600 group-hover:bg-slate-200 group-hover:text-slate-900',
    },
    {
      id: 'gallery',
      label: 'Kho Video & Tải ZIP',
      shortLabel: 'Thư Viện',
      icon: FolderDown,
      badge: outputCount,
      shortcut: '⌘4',
      desc: 'Video thành phẩm chuẩn H.264/AAC',
      activeBg: 'bg-blue-50/90 text-blue-800 border-blue-200 shadow-xs font-bold',
      iconActive: 'bg-blue-600 text-white shadow-sm shadow-blue-500/20',
      iconInactive: 'bg-slate-100 text-slate-600 group-hover:bg-slate-200 group-hover:text-slate-900',
    },
    {
      id: 'channels',
      label: 'Kênh & Quản Lý Nội Dung',
      shortLabel: 'Quản Lý Kênh',
      icon: Tv2,
      shortcut: '⌘5',
      desc: 'Gắn nhãn, phân loại & lịch đăng',
      activeBg: 'bg-blue-50/90 text-blue-800 border-blue-200 shadow-xs font-bold',
      iconActive: 'bg-blue-600 text-white shadow-sm shadow-blue-500/20',
      iconInactive: 'bg-slate-100 text-slate-600 group-hover:bg-slate-200 group-hover:text-slate-900',
    },
  ];


  return (
    <aside
      className={`${
        collapsed ? 'w-20' : 'w-72'
      } bg-white border-r border-slate-200 flex flex-col justify-between shrink-0 transition-all duration-300 z-30 shadow-sm relative select-none`}
    >
      {/* Collapse Toggle Button */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className={`absolute -right-3.5 ${isDesktopApp ? 'top-23' : 'top-18'} w-7 h-7 bg-white border border-slate-200 hover:bg-slate-50 hover:border-slate-300 text-slate-600 rounded-full flex items-center justify-center shadow-md transition z-40 cursor-pointer`}
        style={{ WebkitAppRegion: 'no-drag' }}
        title={collapsed ? 'Mở rộng sidebar (⌘B)' : 'Thu gọn sidebar (⌘B)'}
      >
        {collapsed ? <ChevronRight className="w-4 h-4 text-slate-600" /> : <ChevronLeft className="w-4 h-4 text-slate-600" />}
      </button>

      {/* Top Brand & Navigation */}
      <div className={`${collapsed ? 'p-2.5' : 'p-4'} space-y-4 ${isDesktopApp ? 'pt-7' : ''}`}>
        {/* macOS Traffic Lights drag region */}
        {isDesktopApp && (
          <div
            className="h-3 w-full"
            style={{ WebkitAppRegion: 'drag' }}
          />
        )}

        {/* Brand Header with AI Generated Logo */}
        <div className={`flex items-center ${collapsed ? 'justify-center' : 'space-x-3 px-1'} py-1`} style={{ WebkitAppRegion: 'no-drag' }}>
          <div className="w-10 h-10 rounded-2xl overflow-hidden shadow-md shadow-blue-600/15 border border-slate-200/80 shrink-0 bg-white flex items-center justify-center cursor-pointer">
            <img
              src={logoImg}
              alt="Reup Studio AI"
              className="w-full h-full object-cover transform hover:scale-105 transition-transform"
            />
          </div>

          {!collapsed && (
            <div className="overflow-hidden">
              <div className="flex items-center space-x-1.5">
                <h1 className="text-base font-black text-slate-900 tracking-tight">Reup Studio</h1>
                <span className="px-1.5 py-0.2 text-[9px] font-black rounded bg-blue-600 text-white uppercase shadow-xs">
                  AI PRO
                </span>
              </div>
              <p className="text-xs text-slate-500 font-medium truncate">Video Watermark Engine</p>
            </div>
          )}
        </div>

        {/* Navigation Menu */}
        <nav className="space-y-1.5 pt-1">
          {!collapsed && (
            <div className="flex items-center justify-between px-2.5 mb-2">
              <p className="text-[11px] font-extrabold text-slate-400 uppercase tracking-wider">Khâu Xử Lý</p>
              <span className="text-[11px] font-mono text-slate-400 font-medium">Phím tắt</span>
            </div>
          )}

          {menuItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeTab === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                className={`w-full ${collapsed ? 'p-2.5 justify-center' : 'p-2.5 justify-between'} rounded-2xl transition-all duration-150 flex items-center group cursor-pointer border ${
                  isActive
                    ? item.activeBg
                    : 'border-transparent text-slate-700 hover:text-slate-900 hover:bg-slate-50 font-semibold'
                }`}
                title={collapsed ? `${item.label} (${item.desc}) - ${item.shortcut}` : undefined}
              >
                <div className={`flex items-center ${collapsed ? 'justify-center' : 'space-x-3'} min-w-0 relative`}>
                  <div
                    className={`p-2 rounded-xl transition-all shrink-0 ${
                      isActive ? item.iconActive : item.iconInactive
                    }`}
                  >
                    <Icon className="w-4 h-4" />
                  </div>

                  {collapsed && item.badge !== undefined && item.badge > 0 && (
                    <span className="absolute -top-1 -right-1 w-4 h-4 bg-blue-600 text-white text-[9px] font-black rounded-full flex items-center justify-center shadow-xs">
                      {item.badge}
                    </span>
                  )}

                  {!collapsed && (
                    <div className="text-left overflow-hidden">
                      <span className={`text-xs block leading-tight truncate font-bold ${isActive ? 'text-blue-950 font-extrabold' : 'text-slate-800'}`}>
                        {item.label}
                      </span>
                      <span className="text-[11px] text-slate-500 font-medium block leading-tight mt-0.5 truncate">
                        {item.desc}
                      </span>
                    </div>
                  )}
                </div>

                {!collapsed && (
                  <div className="flex items-center space-x-1.5 shrink-0 ml-2">
                    {item.badge !== undefined && item.badge > 0 && (
                      <span
                        className={`px-2 py-0.5 text-xs font-black rounded-full shadow-xs ${
                          isActive
                            ? 'bg-blue-600 text-white'
                            : 'bg-slate-100 text-slate-700 border border-slate-200'
                        }`}
                      >
                        {item.badge}
                      </span>
                    )}
                    <span className="text-[10px] font-mono text-slate-500 font-bold px-1.5 py-0.5 rounded-md bg-slate-100 border border-slate-200">
                      {item.shortcut}
                    </span>
                  </div>
                )}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Bottom Health & System Specs */}
      <div className={`${collapsed ? 'p-2 m-2' : 'p-3.5 m-3'} bg-slate-50 rounded-2xl border border-slate-200/80 space-y-2.5`}>
        {!collapsed ? (
          <>
            <div className="flex items-center justify-between text-xs">
              <span className="text-xs font-bold text-slate-600 flex items-center gap-1.5">
                <Server className="w-3.5 h-3.5 text-blue-600" /> API Server
              </span>
              {serverOnline ? (
                <span className="px-2.5 py-0.5 text-xs font-bold rounded-full bg-emerald-100 text-emerald-800 border border-emerald-200 flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-600 animate-pulse"></span> Online
                </span>
              ) : (
                <span className="px-2.5 py-0.5 text-xs font-bold rounded-full bg-rose-100 text-rose-800 border border-rose-200">
                  Offline
                </span>
              )}
            </div>

            <div className="flex items-center justify-between text-xs">
              <span className="text-xs font-bold text-slate-600 flex items-center gap-1.5">
                <Wifi className="w-3.5 h-3.5 text-indigo-600" /> WebSocket
              </span>
              {wsStatus === 'connected' ? (
                <span className="px-2.5 py-0.5 text-xs font-bold rounded-full bg-emerald-100 text-emerald-800 border border-emerald-200 flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-600 animate-pulse"></span> Live Sync
                </span>
              ) : (
                <span className="px-2.5 py-0.5 text-xs font-bold rounded-full bg-amber-100 text-amber-800 border border-amber-200">
                  Connecting...
                </span>
              )}
            </div>

            <div className="pt-2 border-t border-slate-200 flex items-center justify-between text-xs text-slate-500 font-mono">
              <span className="flex items-center gap-1 font-semibold text-slate-600">
                <Cpu className="w-3.5 h-3.5 text-blue-600" /> H.264 / AAC
              </span>
              <span className="flex items-center gap-1 text-slate-700 font-bold">
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" /> MD5 Anti-Ban
              </span>
            </div>
          </>
        ) : (
          <div className="flex flex-col items-center space-y-2 py-1">
            <span
              className={`w-2.5 h-2.5 rounded-full ${
                serverOnline ? 'bg-emerald-500 ring-2 ring-emerald-200' : 'bg-rose-400'
              }`}
              title={serverOnline ? 'API Server Online' : 'API Offline'}
            ></span>
            <span
              className={`w-2.5 h-2.5 rounded-full ${
                wsStatus === 'connected' ? 'bg-emerald-500 ring-2 ring-emerald-200' : 'bg-amber-400'
              }`}
              title={wsStatus === 'connected' ? 'WebSocket Live Sync Ready' : 'WebSocket Connecting...'}
            ></span>
          </div>
        )}
      </div>
    </aside>

  );
}
