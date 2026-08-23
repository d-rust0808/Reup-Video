const { app, BrowserWindow, ipcMain, dialog, shell, Notification, Tray, Menu, nativeImage } = require('electron');
const path = require('path');
const http = require('http');
const { spawn, execSync } = require('child_process');
const fs = require('fs');

let mainWindow = null;
let pythonProcess = null;
let tray = null;
let isQuitting = false;

const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;
const ROOT_DIR = path.resolve(__dirname, '..', '..');
const BACKEND_PORT = 8000;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;

// --- PYTHON BACKEND LIFECYCLE ---

function findPythonExecutable() {
  if (process.env.PYTHON_PATH && fs.existsSync(process.env.PYTHON_PATH)) {
    return process.env.PYTHON_PATH;
  }

  // Check local venvs first
  const candidates = [
    path.join(ROOT_DIR, 'venv311', 'bin', 'python'),
    path.join(ROOT_DIR, 'venv311', 'bin', 'python3'),
    path.join(ROOT_DIR, '.venv', 'bin', 'python'),
    path.join(ROOT_DIR, '.venv', 'bin', 'python3'),
    path.join(ROOT_DIR, 'venv', 'bin', 'python'),
    path.join(ROOT_DIR, 'venv', 'bin', 'python3'),
    path.join(ROOT_DIR, '.venv', 'Scripts', 'python.exe'),
    path.join(ROOT_DIR, 'venv', 'Scripts', 'python.exe'),
  ];

  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }

  // Check system python3 or python
  try {
    const whichCmd = process.platform === 'win32' ? 'where python' : 'which python3 || which python';
    const out = execSync(whichCmd, { encoding: 'utf-8' }).trim().split('\n')[0];
    if (out && fs.existsSync(out)) return out;
  } catch {
    // fallback
  }

  return process.platform === 'win32' ? 'python.exe' : 'python3';
}

function checkBackendHealth() {
  return new Promise((resolve) => {
    const req = http.get(`${BACKEND_URL}/health`, { timeout: 1500 }, (res) => {
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function waitForBackend(maxAttempts = 40, interval = 300) {
  for (let i = 0; i < maxAttempts; i++) {
    const healthy = await checkBackendHealth();
    if (healthy) return true;
    await new Promise((r) => setTimeout(r, interval));
  }
  return false;
}

async function startPythonBackend() {
  const isAlreadyRunning = await checkBackendHealth();
  if (isAlreadyRunning) {
    console.log('[Electron] Backend is already running on port', BACKEND_PORT);
    return;
  }

  const pythonBin = findPythonExecutable();
  console.log(`[Electron] Starting FastAPI backend with: ${pythonBin} (cwd: ${ROOT_DIR})`);

  const args = ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)];

  try {
    pythonProcess = spawn(pythonBin, args, {
      cwd: ROOT_DIR,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    pythonProcess.stdout.on('data', (data) => {
      console.log(`[FastAPI] ${data.toString().trim()}`);
    });

    pythonProcess.stderr.on('data', (data) => {
      console.error(`[FastAPI Stderr] ${data.toString().trim()}`);
    });

    pythonProcess.on('error', (err) => {
      console.error('[Electron] Failed to start Python backend:', err);
    });

    pythonProcess.on('exit', (code, signal) => {
      console.log(`[Electron] Python backend exited with code ${code}, signal ${signal}`);
      pythonProcess = null;
    });
  } catch (err) {
    console.error('[Electron] Exception spawning python process:', err);
  }
}

function killPythonBackend() {
  if (pythonProcess && !pythonProcess.killed) {
    console.log('[Electron] Stopping Python backend...');
    try {
      if (process.platform === 'win32') {
        execSync(`taskkill /pid ${pythonProcess.pid} /T /F`);
      } else {
        process.kill(-pythonProcess.pid, 'SIGINT');
      }
    } catch {
      try {
        pythonProcess.kill('SIGTERM');
      } catch {
        // ignored
      }
    }
    pythonProcess = null;
  }
}

// --- WINDOW MANAGEMENT ---

const APP_ICON_PATH = path.join(__dirname, 'assets', 'icon.png');
const APP_ICNS_PATH = path.join(__dirname, 'assets', 'icon.icns');

function createMainWindow() {
  const iconToUse = fs.existsSync(APP_ICNS_PATH)
    ? APP_ICNS_PATH
    : fs.existsSync(APP_ICON_PATH)
    ? APP_ICON_PATH
    : undefined;

  mainWindow = new BrowserWindow({
    width: 1300,
    height: 860,
    minWidth: 1080,
    minHeight: 720,
    title: 'Reup-Video Studio',
    icon: iconToUse,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 16, y: 18 },
    backgroundColor: '#0f172a', // Deep slate dark background
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
      webSecurity: true,
    },
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  // Pipe renderer console logs to Node stdout for debugging
  mainWindow.webContents.on('console-message', (_event, _level, message) => {
    console.log(`[Renderer] ${message}`);
  });

  const devServerUrl = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:3000';

  mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription, validatedURL) => {
    console.warn(`[Electron] Failed to load ${validatedURL}: ${errorDescription} (${errorCode})`);
    if (isDev && !isQuitting) {
      setTimeout(() => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          console.log('[Electron] Retrying dev server load...');
          mainWindow.loadURL(devServerUrl);
        }
      }, 1200);
    }
  });

  if (isDev) {
    mainWindow.loadURL(devServerUrl);
  } else {
    const distPath = path.join(__dirname, '..', 'dist', 'index.html');
    mainWindow.loadFile(distPath);
  }

  mainWindow.on('close', (e) => {
    if (!isQuitting && process.platform === 'darwin') {
      e.preventDefault();
      mainWindow.hide();
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

function createTray() {
  try {
    const trayIcon = fs.existsSync(APP_ICON_PATH)
      ? nativeImage.createFromPath(APP_ICON_PATH).resize({ width: 18, height: 18 })
      : nativeImage.createEmpty();
    tray = new Tray(trayIcon);
    const contextMenu = Menu.buildFromTemplate([
      {
        label: 'Mở Reup-Video Studio',

        click: () => {
          if (mainWindow) {
            mainWindow.show();
            mainWindow.focus();
          }
        },
      },
      {
        label: 'Kiểm tra Backend (Port 8000)',
        click: async () => {
          const healthy = await checkBackendHealth();
          dialog.showMessageBox({
            type: 'info',
            title: 'Trạng Thái Backend',
            message: healthy ? 'FastAPI Backend đang hoạt động bình thường! (127.0.0.1:8000)' : 'Backend đang ngắt kết nối.',
          });
        },
      },
      { type: 'separator' },
      {
        label: 'Thoát',
        click: () => {
          isQuitting = true;
          app.quit();
        },
      },
    ]);
    tray.setToolTip('Reup-Video Studio');
    tray.setContextMenu(contextMenu);
  } catch (err) {
    console.warn('[Electron] Could not initialize tray:', err);
  }
}

// --- IPC HANDLERS ---

function setupIpcHandlers() {
  // Select directory native dialog
  ipcMain.handle('dialog:openDirectory', async () => {
    const res = await dialog.showOpenDialog(mainWindow, {
      properties: ['openDirectory', 'createDirectory'],
      title: 'Chọn thư mục lưu video thành phẩm',
    });
    if (!res.canceled && res.filePaths.length > 0) {
      return res.filePaths[0];
    }
    return null;
  });

  // Show item in Finder / Explorer
  ipcMain.handle('shell:showItemInFolder', async (event, filePath) => {
    if (!filePath) return false;
    let fullPath = filePath;
    if (!path.isAbsolute(filePath)) {
      fullPath = path.resolve(ROOT_DIR, filePath);
    }
    if (fs.existsSync(fullPath)) {
      shell.showItemInFolder(fullPath);
      return true;
    }
    // If specific file doesn't exist, open its directory or root output dir
    const outputDir = path.join(ROOT_DIR, 'data', 'outputs');
    if (fs.existsSync(outputDir)) {
      shell.openPath(outputDir);
      return true;
    }
    return false;
  });

  // Open external URL in default browser
  ipcMain.handle('shell:openExternal', async (event, url) => {
    if (url && (url.startsWith('http://') || url.startsWith('https://'))) {
      await shell.openExternal(url);
      return true;
    }
    return false;
  });

  // Native notification
  ipcMain.handle('notify:jobCompleted', async (event, { title, body }) => {
    if (Notification.isSupported()) {
      new Notification({
        title: title || 'Reup Studio',
        body: body || 'Xử lý video hoàn tất!',
        silent: false,
      }).show();
      return true;
    }
    return false;
  });

  // App & Backend Information
  ipcMain.handle('app:getInfo', async () => {
    const isBackendReady = await checkBackendHealth();
    return {
      version: app.getVersion(),
      platform: process.platform,
      backendUrl: BACKEND_URL,
      backendHealthy: isBackendReady,
    };
  });

  // Restart backend on demand
  ipcMain.handle('backend:restart', async () => {
    killPythonBackend();
    await startPythonBackend();
    return await waitForBackend();
  });

  // Window Controls
  ipcMain.handle('window:minimize', () => mainWindow?.minimize());
  ipcMain.handle('window:maximize', () => {
    if (mainWindow?.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow?.maximize();
    }
  });
  ipcMain.handle('window:close', () => mainWindow?.close());
}

// --- APP LIFECYCLE ---

app.whenReady().then(async () => {
  // Set custom Dock icon on macOS
  if (process.platform === 'darwin' && app.dock) {
    try {
      const iconImg = nativeImage.createFromPath(APP_ICON_PATH);
      if (!iconImg.isEmpty()) {
        app.dock.setIcon(iconImg);
      }
    } catch (err) {
      console.warn('[Electron] Could not set dock icon:', err);
    }
  }

  setupIpcHandlers();

  createTray();

  // Start Python Backend
  await startPythonBackend();

  // Create UI Window
  createMainWindow();


  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    } else if (mainWindow) {
      mainWindow.show();
    }
  });
});

app.on('before-quit', () => {
  isQuitting = true;
  killPythonBackend();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
