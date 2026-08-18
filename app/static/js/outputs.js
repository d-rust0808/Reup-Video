/**
 * OutputGalleryManager - Output File Gallery & Batch Download Controller
 * Target Path: app/static/js/outputs.js
 */

export class OutputGalleryManager {
  constructor(options = {}) {
    this.container = typeof options.container === 'string'
      ? document.querySelector(options.container)
      : (options.container || document.getElementById('output-grid') || document.getElementById('outputGalleryGrid'));

    this.selectAllCb = document.getElementById('btn-select-all') || document.getElementById('selectAllOutputs');
    this.btnBatchDownload = document.getElementById('btn-download-zip') || document.getElementById('btnBatchDownload');

    this.selectedJobIds = new Set();
    this.outputsList = [];

    this.initEventListeners();
  }

  initEventListeners() {
    if (this.selectAllCb) {
      this.selectAllCb.addEventListener('change', (e) => this.toggleSelectAll(e.target.checked));
      this.selectAllCb.addEventListener('click', (e) => {
        if (e.target.tagName === 'BUTTON') {
          const allChecked = this.outputsList.length > 0 && this.selectedJobIds.size === this.outputsList.length;
          this.toggleSelectAll(!allChecked);
        }
      });
    }

    if (this.btnBatchDownload) {
      this.btnBatchDownload.addEventListener('click', () => this.downloadBatch());
    }
  }

  async fetchOutputs() {
    try {
      const resp = await fetch('/api/v1/outputs');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      this.outputsList = data.outputs || [];
      this.render();
    } catch (err) {
      console.error('[OutputsManager] Failed to fetch output gallery:', err);
    }
  }

  render() {
    if (!this.container) {
      this.container = document.getElementById('output-grid') || document.getElementById('outputGalleryGrid');
    }
    if (!this.container) return;

    if (this.outputsList.length === 0) {
      this.container.innerHTML = `
        <div class="col-span-full text-center py-12 text-gray-400 bg-gray-800/50 rounded-lg border border-gray-700">
          <svg class="mx-auto h-12 w-12 text-gray-500 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
          </svg>
          <p class="text-base font-medium text-gray-300">No Processed Videos Yet</p>
          <p class="text-xs text-gray-400 mt-1">Submit a video processing task from the Video Workbench panel to generate outputs.</p>
        </div>`;
      this.updateSelectionUI();
      return;
    }

    this.container.innerHTML = this.outputsList.map((item) => {
      const isSelected = this.selectedJobIds.has(item.job_id);
      const filename = item.output_path ? item.output_path.split('/').pop() : `${item.job_id}.mp4`;
      const dateStr = item.created_at ? new Date(item.created_at).toLocaleString() : 'Just now';

      return `
        <div class="bg-gray-800 rounded-lg p-4 flex flex-col justify-between border border-gray-700 hover:border-gray-600 shadow-md transition-all">
          <div class="flex items-center justify-between mb-2">
            <label class="inline-flex items-center space-x-2 cursor-pointer">
              <input type="checkbox" class="output-select-cb rounded bg-gray-900 border-gray-600 text-blue-600 focus:ring-blue-500 w-4 h-4"
                     data-job-id="${item.job_id}" ${isSelected ? 'checked' : ''}>
              <span class="text-xs font-mono font-bold text-blue-400">${item.job_id}</span>
            </label>
            <span class="text-[10px] uppercase font-bold px-2 py-0.5 rounded bg-emerald-900/80 text-emerald-300 border border-emerald-700/50">MD5 Modified</span>
          </div>

          <div class="aspect-video bg-black rounded overflow-hidden mb-3 relative group">
            <video src="/api/v1/videos/stream/${item.job_id}" controls preload="metadata" class="w-full h-full object-contain"></video>
          </div>

          <div class="text-xs text-gray-400 mb-3 space-y-1 bg-gray-900/60 p-2.5 rounded border border-gray-800">
            <div class="truncate text-gray-300 font-medium" title="${filename}">${filename}</div>
            <div class="flex justify-between text-[11px]">
              <span>Size: <strong class="text-gray-200">${this.formatBytes(item.file_size)}</strong></span>
              <span>${dateStr}</span>
            </div>
          </div>

          <div class="flex gap-2">
            <button class="btn-download-single flex-1 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold py-2 px-3 rounded transition flex items-center justify-center gap-1"
                    data-job-id="${item.job_id}">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
              Download
            </button>
            <button class="btn-delete-output bg-rose-600/80 hover:bg-rose-600 text-white text-xs font-semibold py-2 px-3 rounded transition flex items-center justify-center"
                    data-job-id="${item.job_id}" title="Delete Video Output">
              <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
            </button>
          </div>
        </div>
      `;
    }).join('');

    this.bindDynamicEvents();
    this.updateSelectionUI();
  }

  bindDynamicEvents() {
    if (!this.container) return;

    this.container.querySelectorAll('.output-select-cb').forEach((cb) => {
      cb.addEventListener('change', (e) => {
        const jid = e.target.getAttribute('data-job-id');
        if (e.target.checked) {
          this.selectedJobIds.add(jid);
        } else {
          this.selectedJobIds.delete(jid);
        }
        this.updateSelectionUI();
      });
    });

    this.container.querySelectorAll('.btn-download-single').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const jid = e.currentTarget.getAttribute('data-job-id');
        this.downloadSingle(jid);
      });
    });

    this.container.querySelectorAll('.btn-delete-output').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const jid = e.currentTarget.getAttribute('data-job-id');
        this.deleteOutput(jid);
      });
    });
  }

  toggleSelectAll(checked) {
    if (checked) {
      this.outputsList.forEach((o) => this.selectedJobIds.add(o.job_id));
    } else {
      this.selectedJobIds.clear();
    }
    this.render();
  }

  updateSelectionUI() {
    if (this.btnBatchDownload) {
      const count = this.selectedJobIds.size;
      this.btnBatchDownload.disabled = count === 0;
      this.btnBatchDownload.textContent = count > 0 
        ? `Download Selected (.ZIP) [${count}]`
        : `Download Selected (.ZIP)`;
    }
  }

  downloadSingle(jobId) {
    const url = `/api/v1/outputs/download/${jobId}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = `${jobId}.mp4`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  downloadBatch() {
    if (this.selectedJobIds.size === 0) return;
    const idsParam = Array.from(this.selectedJobIds).join(',');
    const url = `/api/v1/outputs/download-batch?job_ids=${encodeURIComponent(idsParam)}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = 'reup_batch_outputs.zip';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async deleteOutput(jobId) {
    if (!confirm(`Are you sure you want to delete output file and record for ${jobId}?`)) return;

    try {
      const resp = await fetch(`/api/v1/outputs/${jobId}`, { method: 'DELETE' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      this.selectedJobIds.delete(jobId);
      await this.fetchOutputs();
    } catch (err) {
      console.error(`[OutputsManager] Delete failed for ${jobId}:`, err);
      alert(`Delete output failed: ${err.message}`);
    }
  }

  formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  }
}

export const outputsManager = new OutputGalleryManager();

if (typeof window !== 'undefined') {
  window.OutputGalleryManager = OutputGalleryManager;
  window.outputsManager = outputsManager;
}
