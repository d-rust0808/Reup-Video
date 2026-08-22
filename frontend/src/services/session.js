const KEY = 'reup.studio.session.v1';

const EMPTY = {
  activeTab: 'extract',
  collapsed: false,
  selectedMedia: null,
  extractedMediaList: [],
  workbenchOptions: null,
};

export function loadSession() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...EMPTY };
    const parsed = JSON.parse(raw);
    return {
      ...EMPTY,
      ...parsed,
      extractedMediaList: Array.isArray(parsed.extractedMediaList) ? parsed.extractedMediaList : [],
    };
  } catch {
    return { ...EMPTY };
  }
}

export function saveSession(partial) {
  try {
    const prev = loadSession();
    const next = { ...prev, ...partial, savedAt: Date.now() };
    localStorage.setItem(KEY, JSON.stringify(next));
    return next;
  } catch {
    return partial;
  }
}
