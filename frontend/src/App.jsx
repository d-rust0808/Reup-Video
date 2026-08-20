import React, { useState, useEffect, useCallback } from 'react';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { UrlExtractor } from './components/UrlExtractor';
import { VideoWorkbench } from './components/VideoWorkbench';
import { BatchQueue } from './components/BatchQueue';
import { OutputGallery } from './components/OutputGallery';
import { ChannelManager } from './components/ChannelManager';
import { fetchJobs, fetchOutputs } from './services/api';
import { WebSocketClient } from './services/websocket';

export default function App() {
  const [activeTab, setActiveTab] = useState('extract');
  const [collapsed, setCollapsed] = useState(false);
  const [serverOnline, setServerOnline] = useState(false);
  const [wsStatus, setWsStatus] = useState('disconnected');
  const [wsUpdate, setWsUpdate] = useState(null);

  const [extractedMediaList, setExtractedMediaList] = useState([]);
  const [selectedMedia, setSelectedMedia] = useState(null);
  const [queueCount, setQueueCount] = useState(0);
  const [outputCount, setOutputCount] = useState(0);

  // Fetch job and output counts for Sidebar badges & server online status
  const updateCounts = useCallback(async () => {
    try {
      const jobsData = await fetchJobs();
      const jobsList = Array.isArray(jobsData) ? jobsData : (jobsData.jobs || jobsData.items || []);
      setQueueCount(jobsList.length);
      setServerOnline(true);
    } catch {
      setServerOnline(false);
    }
    try {
      const outputsData = await fetchOutputs();
      const outputsList = Array.isArray(outputsData) ? outputsData : (outputsData.outputs || outputsData.items || []);
      setOutputCount(outputsList.length);
    } catch {
      // ignore offline
    }
  }, []);

  useEffect(() => {
    updateCounts();
    const interval = setInterval(updateCounts, 6000);
    return () => clearInterval(interval);
  }, [updateCounts]);

  // WebSocket client initialization
  useEffect(() => {
    const client = new WebSocketClient(
      (data) => setWsUpdate(data),
      (status) => setWsStatus(status)
    );
    client.connect();
    return () => client.close();
  }, []);

  // Global Keyboard Shortcuts (Ctrl/Cmd + 1, 2, 3, 4, 5)
  useEffect(() => {
    const handleKeyDown = (e) => {
      // Only trigger if modifier key Ctrl or Cmd is held
      if (!(e.ctrlKey || e.metaKey)) return;

      // Ignore when typing inside input / textarea
      if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;

      if (e.key === '1') {
        e.preventDefault();
        setActiveTab('extract');
      } else if (e.key === '2') {
        e.preventDefault();
        setActiveTab('workbench');
      } else if (e.key === '3') {
        e.preventDefault();
        setActiveTab('queue');
      } else if (e.key === '4') {
        e.preventDefault();
        setActiveTab('gallery');
      } else if (e.key === '5') {
        e.preventDefault();
        setActiveTab('channels');
      } else if (e.key.toLowerCase() === 'b') {
        e.preventDefault();
        setCollapsed((prev) => !prev);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const handleSelectForWorkbench = (mediaItem) => {
    setSelectedMedia(mediaItem);
    setActiveTab('workbench');
  };

  const handleJobSubmitted = () => {
    setActiveTab('queue');
  };

  const getActiveTabTitle = () => {
    switch (activeTab) {
      case 'extract':
        return 'Bóc Tách & Nạp Video';
      case 'workbench':
        return 'Studio Xoá Logo & Canvas ROI';
      case 'queue':
        return 'Hàng Chờ Xử Lý Realtime';
      case 'gallery':
        return 'Thư Viện Video Thành Phẩm';
      case 'channels':
        return 'Kênh & Quản Lý Nội Dung';
      default:
        return 'Reup Studio';
    }
  };

  return (
    <div className="flex h-screen bg-slate-50 text-slate-900 antialiased font-sans overflow-hidden selection:bg-blue-600 selection:text-white">
      {/* Redesigned Clean Light Sidebar Navigation */}
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        extractedCount={extractedMediaList.length}
        queueCount={queueCount}
        outputCount={outputCount}
        serverOnline={serverOnline}
        wsStatus={wsStatus}
        collapsed={collapsed}
        setCollapsed={setCollapsed}
      />

      {/* Main Right Content Clean Workspace */}
      <div className="flex-1 flex flex-col min-w-0 overflow-y-auto bg-slate-50/60">
        <Header
          activeTabTitle={getActiveTabTitle()}
          setActiveTab={setActiveTab}
          queueCount={queueCount}
          outputCount={outputCount}
        />

        <main className="p-6 md:p-8 max-w-7xl w-full mx-auto space-y-6">
          {activeTab === 'extract' && (
            <UrlExtractor
              onMediaExtracted={setExtractedMediaList}
              onSelectForWorkbench={handleSelectForWorkbench}
            />
          )}

          {activeTab === 'workbench' && (
            <VideoWorkbench
              selectedMedia={selectedMedia}
              onJobSubmitted={handleJobSubmitted}
            />
          )}

          {activeTab === 'queue' && <BatchQueue wsUpdates={wsUpdate} />}

          {activeTab === 'gallery' && <OutputGallery />}

          {activeTab === 'channels' && <ChannelManager />}
        </main>
      </div>
    </div>
  );
}

