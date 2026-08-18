/**
 * Main Application Controller & Orchestrator
 * Target Path: app/static/js/app.js
 */

import { VideoROIPlayer } from './player.js';
import { wsSubscriber } from './websocket.js';
import { outputsManager } from './outputs.js';

export class AppController {
  constructor() {
    this.state = {
      activeTab: 'extract',
      extractedItems: [],
      activeMedia: null,
      jobs: new Map()
    };

    this.player = null;
    this.init();
  }

  init() {
    console.log('[AppController] Initializing Reup Video Studio Dashboard...');
    this.initROIPlayer();
    this.bindTabNavigation();
    this.bindExtractionForm();
    this.bindWorkbenchControls();
    this.bindProcessJobForm();
    this.initWebSocket();
    this.loadInitialJobs();
    outputsManager.fetchOutputs();
  }

  initROIPlayer() {
    const videoEl = document.getElementById('preview-video') || document.getElementById('videoPlayer');
    const canvasEl = document.getElementById('roi-canvas') || document.getElementById('roiCanvas');
    const containerEl = document.getElementById('player-wrapper') || document.getElementById('player-container');

    if (videoEl && canvasEl) {
      this.player = new VideoROIPlayer({
        container: containerEl,
        video: videoEl,
        canvas: canvasEl,
        onCoordinatesChange: (norm, rawPx) => {
          this.updateROIReadouts(norm, rawPx);
        }
      });
      window.playerRoi = this.player;
    }
  }

  updateROIReadouts(norm, rawPx) {
    const elX = document.getElementById('roi-x-input') || document.getElementById('roiX');
    const elY = document.getElementById('roi-y-input') || document.getElementById('roiY');
    const elW = document.getElementById('roi-w-input') || document.getElementById('roiW');
    const elH = document.getElementById('roi-h-input') || document.getElementById('roiH');

    if (elX) elX.value = norm.x_norm.toFixed(4);
    if (elY) elY.value = norm.y_norm.toFixed(4);
    if (elW) elW.value = norm.w_norm.toFixed(4);
    if (elH) elH.value = norm.h_norm.toFixed(4);

    const pxDisplay = document.getElementById('roi-px-display');
    if (pxDisplay && rawPx) {
      pxDisplay.textContent = `Display: X:${Math.round(rawPx.x)} Y:${Math.round(rawPx.y)} W:${Math.round(rawPx.w)} H:${Math.round(rawPx.h)}`;
    }
  }

  bindTabNavigation() {
    const tabs = ['extract', 'workbench', 'queue', 'gallery', 'outputs'];
    tabs.forEach((tab) => {
      const btn = document.getElementById(`nav-tab-${tab}`) || document.getElementById(`tab-${tab}`);
      if (btn) {
        btn.addEventListener('click', (e) => {
          e.preventDefault();
          this.switchTab(tab === 'outputs' ? 'gallery' : tab);
        });
      }
    });
  }

  switchTab(targetTab) {
    const normalizedTab = targetTab === 'outputs' ? 'gallery' : targetTab;
    this.state.activeTab = normalizedTab;

    const allTabs = ['extract', 'workbench', 'queue', 'gallery'];
    allTabs.forEach((tab) => {
      const btn = document.getElementById(`nav-tab-${tab}`) || document.getElementById(`tab-${tab}`);
      const panel = document.getElementById(`panel-${tab}`) || document.getElementById(`section-${tab}`);

      if (btn) {
        if (tab === normalizedTab) {
          btn.classList.add('border-blue-500', 'text-blue-400', 'bg-gray-800/80');
          btn.classList.remove('border-transparent', 'text-gray-400');
        } else {
          btn.classList.remove('border-blue-500', 'text-blue-400', 'bg-gray-800/80');
          btn.classList.add('border-transparent', 'text-gray-400');
        }
      }

      if (panel) {
        if (tab === normalizedTab) {
          panel.classList.remove('hidden');
        } else {
          panel.classList.add('hidden');
        }
      }
    });

    if (normalizedTab === 'workbench' && this.player) {
      setTimeout(() => {
        this.player.updateGeometry();
        this.player.render();
      }, 100);
    } else if (normalizedTab === 'gallery') {
      outputsManager.fetchOutputs();
    }
  }

  bindExtractionForm() {
    const btnExtract = document.getElementById('btn-extract') || document.getElementById('btnExtract');
    const txtUrl = document.getElementById('url-textarea') || document.getElementById('urlInput');

    if (btnExtract && txtUrl) {
      btnExtract.addEventListener('click', async () => {
        const rawContent = txtUrl.value.trim();
        if (!rawContent) {
          this.showToast('Please paste at least one video URL or share text string.', 'warning');
          return;
        }

        const urls = rawContent.split('\n').map(s => s.trim()).filter(Boolean);
        btnExtract.disabled = true;
        const origText = btnExtract.textContent;
        btnExtract.textContent = 'Extracting Media...';

        try {
          const resp = await fetch('/api/v1/extract', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ urls })
          });

          if (!resp.ok) {
            const errData = await resp.json().catch(() => ({}));
            throw new Error(errData.detail || `Extraction failed (HTTP ${resp.status})`);
          }

          const data = await resp.json();
          this.state.extractedItems = data.items || [];
          this.renderExtractedList();
          this.showToast(`Extracted ${this.state.extractedItems.length} media item(s) successfully!`, 'success');
        } catch (err) {
          console.error('[AppController] Extraction error:', err);
          this.showToast(err.message, 'error');
        } finally {
          btnExtract.disabled = false;
          btnExtract.textContent = origText;
        }
      });
    }
  }

  renderExtractedList() {
    const container = document.getElementById('extracted-cards-container') || document.getElementById('extractedList');
    if (!container) return;

    if (this.state.extractedItems.length === 0) {
      container.innerHTML = `<div class="text-center py-6 text-gray-400">No media extracted yet.</div>`;
      return;
    }

    container.innerHTML = this.state.extractedItems.map((item) => `
      <div class="bg-gray-800/80 rounded-lg p-4 border border-gray-700 hover:border-gray-600 flex flex-col md:flex-row justify-between items-start md:items-center gap-4 transition-all">
        <div class="flex items-center space-x-3">
          <span class="px-2.5 py-1 text-xs font-bold rounded uppercase bg-purple-900/80 text-purple-200 border border-purple-700/50">
            ${item.platform || 'auto'}
          </span>
          <div>
            <h4 class="text-sm font-semibold text-white truncate max-w-md">${item.title || item.video_id}</h4>
            <p class="text-xs text-gray-400 font-mono">ID: ${item.video_id}</p>
          </div>
        </div>
        <button class="btn-load-workbench bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold px-4 py-2 rounded-lg transition flex items-center gap-1.5 shadow"
                data-id="${item.video_id}">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
          Send to Video Workbench & ROI
        </button>
      </div>
    `).join('');

    container.querySelectorAll('.btn-load-workbench').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const vid = e.currentTarget.getAttribute('data-id');
        const item = this.state.extractedItems.find(i => i.video_id === vid);
        if (item) {
          this.loadMediaIntoWorkbench(item);
        }
      });
    });
  }

  loadMediaIntoWorkbench(item) {
    this.state.activeMedia = item;
    const videoEl = document.getElementById('preview-video') || document.getElementById('videoPlayer');
    if (videoEl) {
      const streamUrl = item.direct_stream_url || `/api/v1/videos/stream/${item.video_id}`;
      videoEl.src = streamUrl;
      videoEl.load();
    }

    this.switchTab('workbench');
    this.showToast(`Loaded ${item.title || item.video_id} into Video Workbench.`, 'info');
  }

  bindWorkbenchControls() {
    // Quick Presets Buttons
    const btnDouyin = document.getElementById('btn-preset-douyin');
    const btnKuaishou = document.getElementById('btn-preset-kuaishou');
    const btnSubtitle = document.getElementById('btn-preset-subtitle');
    const btnClear = document.getElementById('btn-clear-roi');

    if (btnDouyin) btnDouyin.addEventListener('click', () => this.player?.applyPreset('DOUYIN_TL'));
    if (btnKuaishou) btnKuaishou.addEventListener('click', () => this.player?.applyPreset('KUAISHOU_BR'));
    if (btnSubtitle) btnSubtitle.addEventListener('click', () => this.player?.applyPreset('SUBTITLE_BAND'));
    if (btnClear) btnClear.addEventListener('click', () => this.player?.applyPreset('CLEAR'));

    // Frame Stepping Controls
    const btnStepPrev = document.getElementById('btn-step-prev');
    const btnPlayPause = document.getElementById('btn-step-play-pause') || document.getElementById('btn-play-pause');
    const btnStepNext = document.getElementById('btn-step-next');

    if (btnStepPrev) btnStepPrev.addEventListener('click', () => this.player?.stepFrame(-1));
    if (btnStepNext) btnStepNext.addEventListener('click', () => this.player?.stepFrame(1));
    if (btnPlayPause) {
      btnPlayPause.addEventListener('click', () => {
        const video = this.player?.video;
        if (video) {
          if (video.paused) video.play();
          else video.pause();
        }
      });
    }

    // Range Sliders Sync Readouts
    const bindRangeReadout = (inputId, readoutId, suffix = '') => {
      const input = document.getElementById(inputId);
      const readout = document.getElementById(readoutId);
      if (input && readout) {
        input.addEventListener('input', () => {
          readout.textContent = `${input.value}${suffix}`;
        });
      }
    };

    bindRangeReadout('input-speed', 'speed-val', 'x');
    bindRangeReadout('input-crop', 'crop-val', '%');
    bindRangeReadout('input-brightness', 'brightness-val', '');
    bindRangeReadout('input-contrast', 'contrast-val', '');
    bindRangeReadout('input-saturation', 'saturation-val', '');
  }

  bindProcessJobForm() {
    const btnSubmit = document.getElementById('btn-submit-job') || document.getElementById('btnSubmitJob');
    if (!btnSubmit) return;

    btnSubmit.addEventListener('click', async (e) => {
      e.preventDefault();

      if (!this.player || !this.player.roi) {
        this.showToast('Please select a Watermark ROI region on the canvas first.', 'warning');
        return;
      }

      const mediaId = this.state.activeMedia ? this.state.activeMedia.video_id : 'douyin_123';
      const norm = this.player.getNormalizedROI();

      const videoW = this.player.video.videoWidth || 1920;
      const videoH = this.player.video.videoHeight || 1080;

      // Scale normalized ROI coordinates directly to native video resolution
      const roiPx = [
        Math.round(norm.x_norm * videoW),
        Math.round(norm.y_norm * videoH),
        Math.max(1, Math.round(norm.w_norm * videoW)),
        Math.max(1, Math.round(norm.h_norm * videoH))
      ];

      // Watermark Method Selection
      const wmMethodEl = document.querySelector('input[name="wm_method"]:checked') || document.getElementById('watermarkMethod');
      const wmMethod = wmMethodEl ? wmMethodEl.value : 'auto';

      // Reup FX Options
      const hflip = document.getElementById('toggle-hflip')?.checked ?? document.getElementById('chkHflip')?.checked ?? true;
      const speedRatio = parseFloat(document.getElementById('input-speed')?.value || document.getElementById('inputSpeedRatio')?.value || 1.03);
      const pitchShift = document.getElementById('toggle-pitch')?.checked ?? document.getElementById('chkPitchShift')?.checked ?? true;
      const cropMargin = parseFloat(document.getElementById('input-crop')?.value || 1.5) / 100.0;
      const brightness = parseFloat(document.getElementById('input-brightness')?.value || 0.01);
      const contrast = parseFloat(document.getElementById('input-contrast')?.value || 1.02);
      const saturation = parseFloat(document.getElementById('input-saturation')?.value || 1.03);
      const modifyMd5 = document.getElementById('toggle-md5')?.checked ?? document.getElementById('chkModifyMd5')?.checked ?? true;

      const payload = {
        media_id: mediaId,
        input_path: this.state.activeMedia?.file_path || null,
        roi: roiPx,
        canvas_size: [videoW, videoH], // Matched 1:1 to video resolution
        video_resolution: [videoW, videoH],
        watermark_method: wmMethod,
        hflip: hflip,
        speed_ratio: speedRatio,
        pitch_shift: pitchShift,
        crop_percent: cropMargin,
        brightness: brightness,
        contrast: contrast,
        saturation: saturation,
        modify_md5: modifyMd5
      };

      btnSubmit.disabled = true;
      const origBtnText = btnSubmit.textContent;
      btnSubmit.textContent = 'Submitting Reup Job...';

      try {
        const resp = await fetch('/api/v1/process/job', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        if (!resp.ok) {
          const errData = await resp.json().catch(() => ({}));
          throw new Error(errData.detail || `Job submission failed (HTTP ${resp.status})`);
        }

        const resData = await resp.json();
        this.showToast(`Job ${resData.job_id} submitted to queue successfully!`, 'success');

        // Subscribe WebSocket to live job tracking
        wsSubscriber.subscribeJob(resData.job_id);

        this.switchTab('queue');
        await this.loadInitialJobs();
      } catch (err) {
        console.error('[AppController] Job submission error:', err);
        this.showToast(err.message, 'error');
      } finally {
        btnSubmit.disabled = false;
        btnSubmit.textContent = origBtnText;
      }
    });
  }

  initWebSocket() {
    wsSubscriber.connect();

    wsSubscriber.on('job_progress', (payload) => this.handleJobProgressEvent(payload));
    wsSubscriber.on('progress_update', (payload) => this.handleJobProgressEvent(payload));
    wsSubscriber.on('job_completed', (payload) => {
      this.handleJobProgressEvent(payload);
      outputsManager.fetchOutputs();
      this.showToast(`Job ${payload.job_id} completed successfully!`, 'success');
    });
    wsSubscriber.on('job_failed', (payload) => {
      this.handleJobProgressEvent(payload);
      this.showToast(`Job ${payload.job_id} failed: ${payload.error || 'Pipeline error'}`, 'error');
    });
  }

  handleJobProgressEvent(payload) {
    if (!payload || !payload.job_id) return;
    const existing = this.state.jobs.get(payload.job_id) || {};
    const updated = { ...existing, ...payload };
    this.state.jobs.set(payload.job_id, updated);
    this.renderQueueTable();
  }

  async loadInitialJobs() {
    try {
      const resp = await fetch('/api/v1/jobs');
      if (!resp.ok) return;
      const data = await resp.json();
      const jobList = data.jobs || [];
      jobList.forEach((j) => this.state.jobs.set(j.job_id, j));
      this.renderQueueTable();
    } catch (err) {
      console.error('[AppController] Failed to load initial jobs:', err);
    }
  }

  renderQueueTable() {
    const tbody = document.getElementById('queue-table-body') || document.getElementById('queueTableBody');
    if (!tbody) return;

    const jobList = Array.from(this.state.jobs.values());
    if (jobList.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="6" class="text-center py-8 text-gray-500">No processing jobs currently in queue.</td>
        </tr>`;
      return;
    }

    tbody.innerHTML = jobList.map((job) => {
      const jid = job.job_id;
      const status = (job.status || job.stage || 'QUEUED').toUpperCase();
      const prog = Math.min(100, Math.max(0, Math.round(job.progress_percent ?? job.progress ?? 0)));
      const msg = job.message || job.log || (prog === 100 ? 'Completed' : 'Processing video pipeline...');

      const badgeClass = {
        'QUEUED': 'badge-queued',
        'PENDING': 'badge-pending',
        'DOWNLOADING': 'badge-downloading',
        'WATERMARK_REMOVAL': 'badge-watermark_removal',
        'REUP_PROCESSING': 'badge-reup_processing',
        'RENDERING': 'badge-rendering',
        'COMPLETED': 'badge-completed',
        'FAILED': 'badge-failed',
        'CANCELLED': 'badge-cancelled'
      }[status] || 'badge-pending';

      const isTerminal = ['COMPLETED', 'FAILED', 'CANCELLED'].includes(status);

      return `
        <tr class="border-b border-gray-800 hover:bg-gray-800/50 transition">
          <td class="px-4 py-3 font-mono text-xs text-blue-400 font-semibold">${jid}</td>
          <td class="px-4 py-3 text-xs uppercase font-bold text-gray-300">${job.platform || 'auto'}</td>
          <td class="px-4 py-3 text-xs">
            <span class="badge ${badgeClass}">${status}</span>
          </td>
          <td class="px-4 py-3 w-48">
            <div class="flex items-center space-x-2">
              <div class="progress-bar-bg flex-1">
                <div class="progress-bar-fill ${!isTerminal ? 'progress-bar-striped' : ''}" style="width: ${prog}%"></div>
              </div>
              <span class="text-xs font-mono font-medium text-gray-300 w-9 text-right">${prog}%</span>
            </div>
          </td>
          <td class="px-4 py-3 text-xs text-gray-400 font-mono truncate max-w-xs" title="${msg}">${msg}</td>
          <td class="px-4 py-3 text-xs">
            <div class="flex space-x-2">
              ${!isTerminal ? `
                <button class="btn-cancel-job bg-rose-600/80 hover:bg-rose-600 text-white px-2.5 py-1 rounded transition text-xs"
                        data-job-id="${jid}">Cancel</button>
              ` : `
                <button class="btn-retry-job bg-amber-600/80 hover:bg-amber-600 text-white px-2.5 py-1 rounded transition text-xs"
                        data-job-id="${jid}">Retry</button>
              `}
            </div>
          </td>
        </tr>
      `;
    }).join('');

    this.bindQueueActionEvents(tbody);
  }

  bindQueueActionEvents(tbody) {
    tbody.querySelectorAll('.btn-cancel-job').forEach((btn) => {
      btn.addEventListener('click', async (e) => {
        const jid = e.currentTarget.getAttribute('data-job-id');
        try {
          const resp = await fetch(`/api/v1/jobs/${jid}/cancel`, { method: 'POST' });
          if (!resp.ok) throw new Error(`Cancel failed (HTTP ${resp.status})`);
          this.showToast(`Job ${jid} cancelled.`, 'info');
          await this.loadInitialJobs();
        } catch (err) {
          this.showToast(err.message, 'error');
        }
      });
    });

    tbody.querySelectorAll('.btn-retry-job').forEach((btn) => {
      btn.addEventListener('click', async (e) => {
        const jid = e.currentTarget.getAttribute('data-job-id');
        try {
          const resp = await fetch(`/api/v1/jobs/${jid}/retry`, { method: 'POST' });
          if (!resp.ok) throw new Error(`Retry failed (HTTP ${resp.status})`);
          this.showToast(`Job ${jid} re-enqueued for processing.`, 'success');
          await this.loadInitialJobs();
        } catch (err) {
          this.showToast(err.message, 'error');
        }
      });
    });
  }

  showToast(message, type = 'info', duration = 4000) {
    let container = document.getElementById('toast-container') || document.getElementById('toastContainer');
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
      <span>${message}</span>
      <button class="ml-3 text-white/80 hover:text-white font-bold">&times;</button>
    `;

    toast.querySelector('button').addEventListener('click', () => toast.remove());

    container.appendChild(toast);
    setTimeout(() => {
      if (toast.parentNode) {
        toast.remove();
      }
    }, duration);
  }
}

// Instantiate and attach controller on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  window.appController = new AppController();
});
