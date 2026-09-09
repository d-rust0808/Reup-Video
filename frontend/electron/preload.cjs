const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  isDesktop: true,
  
  // File & Dialog Operations
  selectDirectory: () => ipcRenderer.invoke('dialog:openDirectory'),
  selectVideoFiles: () => ipcRenderer.invoke('dialog:openVideos'),
  showItemInFolder: (filePath) => ipcRenderer.invoke('shell:showItemInFolder', filePath),
  openExternal: (url) => ipcRenderer.invoke('shell:openExternal', url),

  // Notifications
  sendNotification: (title, body) => ipcRenderer.invoke('notify:jobCompleted', { title, body }),

  // System & Backend Status
  getAppInfo: () => ipcRenderer.invoke('app:getInfo'),
  restartBackend: () => ipcRenderer.invoke('backend:restart'),
  onBackendStatusChange: (callback) => {
    const handler = (event, status) => callback(status);
    ipcRenderer.on('backend:status', handler);
    return () => ipcRenderer.removeListener('backend:status', handler);
  },

  // Window Controls
  minimizeWindow: () => ipcRenderer.invoke('window:minimize'),
  maximizeWindow: () => ipcRenderer.invoke('window:maximize'),
  closeWindow: () => ipcRenderer.invoke('window:close'),
});
