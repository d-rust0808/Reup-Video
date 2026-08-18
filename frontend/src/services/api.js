/**
 * API Service for interacting with FastAPI Backend endpoints
 */

const API_BASE = '/api/v1';

export async function checkHealth() {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error('Health check failed');
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

