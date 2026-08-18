import React from 'react';
import { Link2, Sliders, Layers, FolderDown } from 'lucide-react';

export function Navigation({ activeTab, setActiveTab, extractedCount, queueCount, outputCount }) {
  const tabs = [
    { id: 'extract', label: '1. URL Extraction', icon: Link2, badge: extractedCount },
    { id: 'workbench', label: '2. Video & ROI Workbench', icon: Sliders },
    { id: 'queue', label: '3. Batch Processing Queue', icon: Layers, badge: queueCount },
    { id: 'gallery', label: '4. Output Gallery', icon: FolderDown, badge: outputCount },
  ];

  return (
    <nav className="bg-gray-900 border-b border-gray-800">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex space-x-2 overflow-x-auto py-2">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-4 py-2.5 text-xs font-bold rounded-xl transition flex items-center gap-2 border whitespace-nowrap ${
                  isActive
                    ? 'bg-blue-600/15 border-blue-500 text-blue-400 shadow-md shadow-blue-500/10'
                    : 'bg-gray-950/40 border-gray-800 text-gray-400 hover:text-gray-200 hover:bg-gray-800/50'
                }`}
              >
                <Icon className={`w-4 h-4 ${isActive ? 'text-blue-400' : 'text-gray-400'}`} />
                {tab.label}
                {tab.badge !== undefined && tab.badge > 0 && (
                  <span className={`px-1.5 py-0.5 text-[10px] font-extrabold rounded-full ${
                    isActive ? 'bg-blue-500 text-white' : 'bg-gray-800 text-gray-300'
                  }`}>
                    {tab.badge}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
