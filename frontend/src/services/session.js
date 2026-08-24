import { getApiBase } from './api';

const KEY = 'reup.studio.session.v3';
const LEGACY_KEYS = ['reup.studio.session.v2', 'reup.studio.session.v1'];

export const EMPTY_SESSION = {
  sessionVersion: 3,
  activeTab: 'extract',
  collapsed: false,
  selectedMedia: null,
  extractedMediaList: [],
  workbenchOptions: null,
  extractMode: 'video',
  extractUrl: '',
  maxVideos: 8,
  autoReup: true,
  selectedChannelId: null,
  targetPlatforms: ['tiktok', 'youtube_shorts', 'facebook'],
  frameStudio: {
    enabled: false,
    preset: 'cinema',
    color: '#000000',
    thickness: 28,
    overlay: null,
  },
  savedAt: 0,
};

function isSafeUrl(value) {
  if (typeof value !== 'string' || !value) return false;
  if (value.startsWith('blob:') || value.startsWith('data:')) return false;
  return true;
}

export function compactMedia(item) {
  if (!item || typeof item !== 'object') return null;
  const videoId = item.video_id || item.media_id || item.id;
  if (!videoId) return null;
  const cover = item.cover_url || item.thumbnail || item.cover || '';
  const filePath = item.file_path || '';
  return {
    video_id: String(videoId),
    platform: item.platform || '',
    title: item.title || item.filename || '',
    author: item.author || '',
    file_path: isSafeUrl(filePath) || (typeof filePath === 'string' && filePath.startsWith('data/')) ? filePath : '',
    cover_url: isSafeUrl(cover) ? cover : '',
  };
}

export function compactOptions(options) {
  if (!options || typeof options !== 'object') return null;
  const { overlays, ...rest } = options;
  const cleanOverlays = Array.isArray(overlays)
    ? overlays
        .filter((o) => o && typeof o === 'object')
        .map((o) => ({
          id: o.id,
          kind: o.kind,
          src: isSafeUrl(o.src) ? o.src : '',
          url: isSafeUrl(o.url) ? o.url : '',
          image_path: typeof o.image_path === 'string' ? o.image_path : '',
          filename: o.filename || '',
          x: o.x,
          y: o.y,
          w: o.w,
          h: o.h,
          opacity: o.opacity,
        }))
        .filter((o) => o.src || o.url || o.image_path)
    : [];
  return { ...rest, overlays: cleanOverlays };
}

function compactSession(raw) {
  const incoming = raw || {};
  const isLegacy = Number(incoming.sessionVersion || 0) < 3;
  const src = { ...EMPTY_SESSION, ...incoming };
  const workbenchOptions = compactOptions(src.workbenchOptions);
  if (isLegacy && workbenchOptions) workbenchOptions.frame_enabled = false;
  const list = Array.isArray(src.extractedMediaList)
    ? src.extractedMediaList.map(compactMedia).filter(Boolean).slice(0, 80)
    : [];
  const platforms = Array.isArray(src.targetPlatforms)
    ? src.targetPlatforms.map(String).filter(Boolean).slice(0, 8)
    : EMPTY_SESSION.targetPlatforms;
  return {
    ...EMPTY_SESSION,
    activeTab: src.activeTab || 'extract',
    collapsed: !!src.collapsed,
    selectedMedia: compactMedia(src.selectedMedia),
    extractedMediaList: list,
    sessionVersion: 3,
    workbenchOptions,
    extractMode: src.extractMode === 'channel' ? 'channel' : 'video',
    extractUrl: typeof src.extractUrl === 'string' ? src.extractUrl.slice(0, 4000) : '',
    maxVideos: Math.max(1, Math.min(50, Number(src.maxVideos) || 8)),
    autoReup: src.autoReup !== false,
    selectedChannelId: src.selectedChannelId || null,
    targetPlatforms: platforms.length ? platforms : EMPTY_SESSION.targetPlatforms,
    frameStudio: {
      ...EMPTY_SESSION.frameStudio,
      ...(src.frameStudio || {}),
      ...(isLegacy ? { enabled: false, overlay: null } : {}),
    },
    savedAt: Number(src.savedAt) || Date.now(),
  };
}

export function loadSession() {
  try {
    const raw = localStorage.getItem(KEY) || LEGACY_KEYS.map((k) => localStorage.getItem(k)).find(Boolean);
    if (!raw) return { ...EMPTY_SESSION };
    return compactSession(JSON.parse(raw));
  } catch {
    return { ...EMPTY_SESSION };
  }
}

export function saveSession(partial) {
  try {
    const next = compactSession({ ...loadSession(), ...partial, savedAt: Date.now() });
    const json = JSON.stringify(next);
    try {
      localStorage.setItem(KEY, json);
    } catch {
      const slim = compactSession({ ...next, extractedMediaList: next.extractedMediaList.slice(0, 12) });
      localStorage.setItem(KEY, JSON.stringify(slim));
    }
    scheduleServerPush(next);
    return next;
  } catch {
    return compactSession(partial);
  }
}

let pushTimer = null;
let lastPushedAt = 0;
function scheduleServerPush(payload) {
  if (typeof window === 'undefined') return;
  clearTimeout(pushTimer);
  pushTimer = setTimeout(() => {
    pushServerSession(payload).catch(() => {});
  }, 450);
}

export async function pushServerSession(payload) {
  const body = compactSession(payload || loadSession());
  if (body.savedAt && body.savedAt === lastPushedAt) return;
  const res = await fetch(`${getApiBase()}/studio/session`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (res.ok) lastPushedAt = body.savedAt;
}

export async function hydrateSession() {
  const local = loadSession();
  try {
    const res = await fetch(`${getApiBase()}/studio/session`);
    if (!res.ok) return local;
    const remote = compactSession(await res.json());
    if ((remote.savedAt || 0) >= (local.savedAt || 0)) {
      try {
        localStorage.setItem(KEY, JSON.stringify(remote));
      } catch {
        /* ignore quota */
      }
      return remote;
    }
  } catch {
    /* offline — keep local */
  }
  return local;
}
