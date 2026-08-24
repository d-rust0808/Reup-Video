/**
 * API Service for interacting with FastAPI Backend endpoints
 */

export const isDesktop =
  typeof window !== 'undefined' &&
  (!!window.electronAPI?.isDesktop || window.location.protocol === 'file:');

export const getApiBase = () => {
  if (
    typeof window !== 'undefined' &&
    (window.location.protocol === 'file:' || !window.location.host || window.location.host === '')
  ) {
    return 'http://127.0.0.1:8000/api/v1';
  }
  return '/api/v1';
};

export const API_BASE = getApiBase();

export function getMediaUrl(path) {
  if (!path) return '';
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  if (path.startsWith('/')) {
    const origin =
      typeof window !== 'undefined' &&
      (window.location.protocol === 'file:' || !window.location.host)
        ? 'http://127.0.0.1:8000'
        : '';
    return `${origin}${path}`;
  }
  return path;
}

export async function checkHealth() {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error('Health check failed');
  return res.json();
}

export async function fetchSampleVideos() {
  const res = await fetch(`${API_BASE}/samples`);
  if (!res.ok) throw new Error('Failed to fetch sample videos');
  return res.json();
}

export async function fetchLibrary() {
  const res = await fetch(`${API_BASE}/library`);
  if (!res.ok) throw new Error('Failed to fetch video library');
  return res.json();
}

export async function deleteLibraryVideo(videoId) {
  const res = await fetch(`${API_BASE}/library/${encodeURIComponent(videoId)}`, { method: 'DELETE' });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || 'Không thể xóa video nguồn');
  }
  return res.json();
}

export async function extractUrls(urls) {
  const res = await fetch(`${API_BASE}/extract`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ urls }),
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to extract URLs');
  }
  return res.json();
}

export async function extractChannel(payload) {
  const res = await fetch(`${API_BASE}/extract/channel`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Không clone được kênh');
  }
  return res.json();
}

export async function uploadVideoFile(file) {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`${API_BASE}/videos/upload`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Upload video failed');
  }
  return res.json();
}

export async function submitJob(payload) {
  const res = await fetch(`${API_BASE}/process/job`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Job submission failed');
  }
  return res.json();
}

export async function fetchJobs() {
  const res = await fetch(`${API_BASE}/jobs`);
  if (!res.ok) throw new Error('Failed to fetch jobs');
  return res.json();
}

export async function fetchJobLogs(jobId) {
  const res = await fetch(`${API_BASE}/jobs/${jobId}/logs`);
  if (!res.ok) throw new Error('Failed to fetch job logs');
  return res.json();
}

export async function retryJob(jobId) {
  const res = await fetch(`${API_BASE}/jobs/${jobId}/retry`, { method: 'POST' });
  if (!res.ok) throw new Error('Không chạy lại được job');
  return res.json();
}

export async function retryFailedJobs() {
  const res = await fetch(`${API_BASE}/jobs/retry-failed`, { method: 'POST' });
  if (!res.ok) throw new Error('Không chạy lại hàng loạt');
  return res.json();
}

export async function cancelJob(jobId) {
  const res = await fetch(`${API_BASE}/jobs/${jobId}/cancel`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error('Failed to cancel job');
  return res.json();
}

export async function deleteJob(jobId) {
  const res = await fetch(`${API_BASE}/jobs/${jobId}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to delete job');
  }
  return res.json();
}

export async function clearJobs(status = null) {
  const query = status ? `?status=${encodeURIComponent(status)}` : '?all=true';
  const res = await fetch(`${API_BASE}/jobs${query}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to clear jobs');
  }
  return res.json();
}

export async function deleteBatchJobs(jobIds) {
  const res = await fetch(`${API_BASE}/jobs/delete-batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_ids: jobIds }),
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to delete batch jobs');
  }
  return res.json();
}

export async function fetchOutputs() {
  const res = await fetch(`${API_BASE}/outputs`);
  if (!res.ok) throw new Error('Failed to fetch output files');
  return res.json();
}

export function getStreamUrl(mediaId) {
  return `${API_BASE}/videos/stream/${mediaId}`;
}

export function getDownloadUrl(jobId) {
  return `${API_BASE}/outputs/download/${jobId}`;
}

export async function downloadBatchZip(jobIds) {
  const query = encodeURIComponent(jobIds.join(','));
  const res = await fetch(`${API_BASE}/outputs/download-batch?job_ids=${query}`);
  if (!res.ok) throw new Error('Batch ZIP download failed');
  const blob = await res.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `reup_videos_batch_${Date.now()}.zip`;
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

export async function deleteOutput(jobId) {
  const res = await fetch(`${API_BASE}/outputs/${jobId}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to delete output file');
  }
  return res.json();
}

export async function deleteBatchOutputs(jobIds) {
  const res = await fetch(`${API_BASE}/outputs/delete-batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ job_ids: jobIds }),
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to delete selected outputs');
  }
  return res.json();
}

export async function clearAllOutputs() {
  const res = await fetch(`${API_BASE}/outputs`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Failed to clear all outputs');
  }
  return res.json();
}

export async function fetchVoices() {
  const res = await fetch(`${API_BASE}/voices`);
  if (!res.ok) throw new Error('Failed to fetch voices list');
  return res.json();
}

export async function previewVoice({ voice, lang, engine }) {
  const res = await fetch(`${API_BASE}/voices/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ voice, lang, engine }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || 'Không thể tạo bản nghe thử');
  }
  return res.blob();
}

// -----------------------------------------------------------------------------
// Channel & Content Management APIs
// -----------------------------------------------------------------------------

export async function fetchChannels() {
  const res = await fetch(`${API_BASE}/channels`);
  if (!res.ok) throw new Error('Failed to fetch channels');
  return res.json();
}

export async function createChannel(payload) {
  const res = await fetch(`${API_BASE}/channels`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to create channel');
  }
  return res.json();
}

export async function updateChannel(channelId, payload) {
  const res = await fetch(`${API_BASE}/channels/${channelId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to update channel');
  }
  return res.json();
}

export async function deleteChannel(channelId) {
  const res = await fetch(`${API_BASE}/channels/${channelId}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to delete channel');
  }
  return res.json();
}

export async function fetchChannelVideos(channelId, status = '', tag = '') {
  let url = `${API_BASE}/channels/${channelId}/videos`;
  const params = new URLSearchParams();
  if (status) params.append('status_filter', status);
  if (tag) params.append('tag', tag);
  const q = params.toString();
  if (q) url += `?${q}`;

  const res = await fetch(url);
  if (!res.ok) throw new Error('Failed to fetch channel videos');
  return res.json();
}

export async function assignVideoToChannel(channelId, payload) {
  const res = await fetch(`${API_BASE}/channels/${channelId}/videos`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to assign video to channel');
  }
  return res.json();
}

export async function updateChannelVideo(videoId, payload) {
  const res = await fetch(`${API_BASE}/channel-videos/${videoId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to update video content');
  }
  return res.json();
}

export async function removeVideoFromChannel(videoId) {
  const res = await fetch(`${API_BASE}/channel-videos/${videoId}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Failed to remove video from channel');
  }
  return res.json();
}

export async function uploadChannelOverlay(channelId, file, meta = {}) {
  const form = new FormData();
  form.append('file', file);
  form.append('kind', meta.kind || 'logo');
  form.append('x', String(meta.x ?? 0.78));
  form.append('y', String(meta.y ?? 0.04));
  form.append('w', String(meta.w ?? 0.18));
  form.append('opacity', String(meta.opacity ?? 1));
  const res = await fetch(`${API_BASE}/channels/${channelId}/overlays`, {
    method: 'POST',
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Tải logo/khung kênh thất bại');
  }
  return res.json();
}

export async function saveChannelOverlays(channelId, overlays) {
  const res = await fetch(`${API_BASE}/channels/${channelId}/overlays`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ overlays: overlays || [] }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Lưu vị trí logo/khung thất bại');
  }
  return res.json();
}

export async function deleteChannelOverlay(channelId, overlayId) {
  const res = await fetch(`${API_BASE}/channels/${channelId}/overlays/${overlayId}`, {
    method: 'DELETE',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Xóa logo/khung thất bại');
  }
  return res.json();
}

export async function uploadStudioOverlay(file, meta = {}) {
  const form = new FormData();
  form.append('file', file);
  form.append('kind', meta.kind || 'logo');
  form.append('x', String(meta.x ?? 0.78));
  form.append('y', String(meta.y ?? 0.04));
  form.append('w', String(meta.w ?? 0.18));
  const res = await fetch(`${API_BASE}/studio/overlay`, { method: 'POST', body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Tải logo/khung thất bại');
  }
  return res.json();
}

export async function fetchFramePresets() {
  const res = await fetch(`${API_BASE}/frames/presets`);
  if (!res.ok) throw new Error('Không tải được mẫu khung');
  return res.json();
}

export async function renderFramePreset(payload) {
  const res = await fetch(`${API_BASE}/frames/render`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Không render được khung');
  }
  return res.json();
}

export async function uploadCustomFrame(file) {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${API_BASE}/frames/upload`, { method: 'POST', body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Tải khung thất bại');
  }
  return res.json();
}

export async function fetchBgmLibrary() {
  const res = await fetch(`${API_BASE}/bgm`);
  if (!res.ok) throw new Error('Không tải được kho nhạc');
  return res.json();
}

export async function extractBgm(payload) {
  const res = await fetch(`${API_BASE}/bgm/extract`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Không tách được nhạc nền');
  }
  return res.json();
}

export async function uploadBgmFile(file) {
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${API_BASE}/bgm/upload`, { method: 'POST', body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Tải nhạc thất bại');
  }
  return res.json();
}

export async function deleteBgm(id) {
  const res = await fetch(`${API_BASE}/bgm/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Không xóa được nhạc');
  return res.json();
}

export async function fetchBgmProviders() {
  const res = await fetch(`${API_BASE}/bgm/providers`);
  if (!res.ok) throw new Error('Không tải được danh sách nguồn nhạc');
  return res.json();
}

export async function searchOnlineBgm({
  q,
  provider = 'openverse',
  limit = 20,
  page = 1,
  instrumental = false,
  minDuration = 0,
  maxDuration = 0,
}) {
  const params = new URLSearchParams({
    q,
    provider,
    limit: String(limit),
    page: String(page),
    instrumental: String(!!instrumental),
  });
  if (minDuration > 0) params.set('min_duration', String(minDuration));
  if (maxDuration > 0) params.set('max_duration', String(maxDuration));
  const res = await fetch(`${API_BASE}/bgm/search?${params.toString()}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Không tìm được nhạc trên kho online');
  }
  return res.json();
}

export async function importOnlineBgm(track) {
  const res = await fetch(`${API_BASE}/bgm/import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      audio_url: track.audio_url,
      provider: track.provider,
      external_id: track.external_id,
      title: track.title,
      artist: track.artist,
      license: track.license,
      license_url: track.license_url,
      attribution: track.attribution,
      page_url: track.page_url,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || 'Không thêm được bản nhạc vào kho');
  }
  return res.json();
}
