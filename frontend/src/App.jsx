import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { UrlExtractor } from './components/UrlExtractor';
import { VideoWorkbench } from './components/VideoWorkbench';
import { BatchQueue } from './components/BatchQueue';
import { OutputGallery } from './components/OutputGallery';
import { ChannelManager } from './components/ChannelManager';
import { FrameStudio } from './components/FrameStudio';
import { fetchJobs, fetchOutputs, fetchLibrary } from './services/api';
import { WebSocketClient } from './services/websocket';
import { loadSession, saveSession, hydrateSession } from './services/session';

function mergeMedia(a = [], b = []) {
  const map = new Map();
  [...b, ...a].forEach((item) => {
    if (item?.video_id && !map.has(item.video_id)) map.set(item.video_id, item);
  });
  return Array.from(map.values());
}

export default function App() {
  const boot = useRef(loadSession()).current;
  const [activeTab, setActiveTab] = useState(boot.activeTab || 'extract');
  const [collapsed, setCollapsed] = useState(!!boot.collapsed);
  const [serverOnline, setServerOnline] = useState(false);
  const [wsStatus, setWsStatus] = useState('disconnected');
  const [wsUpdate, setWsUpdate] = useState(null);

  const [extractedMediaList, setExtractedMediaList] = useState(boot.extractedMediaList || []);
  const [selectedMedia, setSelectedMedia] = useState(boot.selectedMedia || null);
  const [queueCount, setQueueCount] = useState(0);
  const [outputCount, setOutputCount] = useState(0);

  useEffect(() => {
    saveSession({
      activeTab,
      collapsed,
      selectedMedia,
      extractedMediaList,
    });
  }, [activeTab, collapsed, selectedMedia, extractedMediaList]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const remote = await hydrateSession();
      if (cancelled) return;
      if (remote.activeTab) setActiveTab(remote.activeTab);
      setCollapsed(!!remote.collapsed);
      if (remote.extractedMediaList?.length) {
        setExtractedMediaList((prev) => mergeMedia(prev, remote.extractedMediaList));
      }
      if (remote.selectedMedia) {
        setSelectedMedia((prev) => prev || remote.selectedMedia);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchLibrary();
        const items = data.items || [];
        if (cancelled || !items.length) return;
        setExtractedMediaList((prev) => mergeMedia(prev, items));
        setSelectedMedia((prev) => prev || items[0]);
      } catch {
        /* keep local session */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const updateCounts = useCallback(async () => {
    try {
      const jobsData = await fetchJobs();
      const jobsList = Array.isArray(jobsData) ? jobsData : (jobsData.jobs || jobsData.items || []);
      const active = jobsList.filter((j) => !['COMPLETED', 'FAILED', 'CANCELLED'].includes((j.status || '').toUpperCase()));
      setQueueCount(active.length);
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

  useEffect(() => {
    const client = new WebSocketClient(
      (data) => setWsUpdate(data),
      (status) => setWsStatus(status)
    );
    client.connect();
    return () => client.close();
  }, []);

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
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
      } else if (e.key === '6') {
        e.preventDefault();
        setActiveTab('frames');
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
      case 'frames':
        return 'Khung Video — In vào clip';
      default:
        return 'Reup Studio';
    }
  };

  return (
    <div className="flex h-screen bg-slate-50 text-slate-900 antialiased font-sans overflow-hidden selection:bg-blue-600 selection:text-white">
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

      <div className="flex-1 flex flex-col min-w-0 overflow-y-auto bg-slate-50/60">
        <Header
          activeTabTitle={getActiveTabTitle()}
          setActiveTab={setActiveTab}
          queueCount={queueCount}
          outputCount={outputCount}
        />

        <main className="p-6 md:p-8 max-w-7xl w-full mx-auto space-y-6">
          <div className={activeTab === 'extract' ? '' : 'hidden'}>
            <UrlExtractor
              initialMedia={extractedMediaList}
              onMediaExtracted={setExtractedMediaList}
              onSelectForWorkbench={handleSelectForWorkbench}
              onJobsQueued={() => setActiveTab('queue')}
            />
          </div>

          <div className={activeTab === 'workbench' ? '' : 'hidden'}>
            <VideoWorkbench
              selectedMedia={selectedMedia}
              onJobSubmitted={handleJobSubmitted}
            />
          </div>

          <div className={activeTab === 'queue' ? '' : 'hidden'}>
            <BatchQueue wsUpdates={wsUpdate} />
          </div>

          <div className={activeTab === 'gallery' ? '' : 'hidden'}>
            <OutputGallery />
          </div>

          <div className={activeTab === 'channels' ? '' : 'hidden'}>
            <ChannelManager />
          </div>

          <div className={activeTab === 'frames' ? '' : 'hidden'}>
            <FrameStudio />
          </div>
        </main>
      </div>
    </div>
  );
}
