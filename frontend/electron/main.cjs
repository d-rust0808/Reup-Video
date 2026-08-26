const { app, BrowserWindow, ipcMain, dialog, shell, Notification, Tray, Menu, nativeImage } = require('electron');
const path = require('path');
const http = require('http');
const { spawn, spawnSync, execSync } = require('child_process');
const fs = require('fs');

function isBrokenPipeError(err) {
  const code = err && err.code;
  return code === 'EPIPE' || code === 'ERR_STREAM_DESTROYED' || code === 'ERR_STREAM_WRITE_AFTER_END';
}

for (const stream of [process.stdout, process.stderr]) {
  if (!stream || typeof stream.on !== 'function') continue;
  stream.on('error', (err) => {
    if (!isBrokenPipeError(err)) throw err;
  });
}

process.on('uncaughtException', (err) => {
  if (isBrokenPipeError(err)) return;
  console.error('[Electron] Uncaught exception:', err);
  try {
    dialog.showErrorBox('A JavaScript error occurred in the main process', err.stack || String(err));
  } catch {
    // Dialog is unavailable before app ready.
  }
});

let mainWindow = null;
let pythonProcess = null;
let tray = null;
let isQuitting = false;

function requestCleanQuit(signal) {
  if (isQuitting) return;
  isQuitting = true;
  console.log(`[Electron] Received ${signal}; shutting down cleanly...`);
  app.quit();
}

// Dev runners stop child processes with signals. Translating them to app.quit()
// lets macOS record a normal exit instead of showing its window-restore warning.
process.once('SIGINT', () => requestCleanQuit('SIGINT'));
process.once('SIGTERM', () => requestCleanQuit('SIGTERM'));
if (process.platform === 'darwin') {
  process.once('SIGHUP', () => requestCleanQuit('SIGHUP'));
}

// 1. Fix PATH on macOS GUI applications to reach Homebrew, Python, FFmpeg & Conda
if (process.platform === 'darwin') {
  const home = process.env.HOME || '';
  const searchPaths = [
    '/opt/homebrew/bin',
    '/opt/homebrew/sbin',
    '/opt/homebrew/opt/python@3.11/bin',
    '/usr/local/bin',
    '/usr/local/sbin',
    path.join(home, '.local', 'bin'),
    path.join(home, '.pyenv', 'shims'),
    path.join(home, 'miniforge3', 'bin'),
    path.join(home, 'miniconda3', 'bin'),
    path.join(home, 'anaconda3', 'bin'),
    '/usr/bin',
    '/bin',
    '/usr/sbin',
    '/sbin',
  ];
  const existingPath = process.env.PATH || '';
  process.env.PATH = Array.from(new Set([...searchPaths.filter((p) => fs.existsSync(p)), ...existingPath.split(':')])).join(':');
}

if (process.platform === 'win32') {
  const home = process.env.USERPROFILE || process.env.HOME || '';
  const local = process.env.LOCALAPPDATA || path.join(home, 'AppData', 'Local');
  const searchPaths = [
    path.join(home, '.local', 'bin'),
    path.join(local, 'agy'),
    path.join(local, 'Programs', 'agy'),
    path.join(local, 'Google', 'Antigravity'),
    path.join(local, 'Antigravity', 'cli'),
    path.join(home, 'AppData', 'Roaming', 'npm'),
  ];
  const existingPath = process.env.PATH || '';
  process.env.PATH = Array.from(new Set([...searchPaths.filter((p) => fs.existsSync(p)), ...existingPath.split(';')])).join(';');
}

const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;
const ROOT_DIR = isDev
  ? path.resolve(__dirname, '..', '..')
  : (fs.existsSync(path.join(process.resourcesPath, 'app')) ? process.resourcesPath : path.resolve(__dirname, '..', '..'));
const BACKEND_PORT = 6000;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
const ELECTRON_MANAGES_BACKEND = !isDev;

function bundledWindowsPython() {
  return path.join(process.resourcesPath, 'python', 'python.exe');
}

function pathDelimiter() {
  return process.platform === 'win32' ? ';' : ':';
}

function bundledSitePackages() {
  return path.join(process.resourcesPath, 'python', 'Lib', 'site-packages');
}

function applyWindowsRuntimePath() {
  if (process.platform !== 'win32' || isDev) return;
  const pythonDir = path.join(process.resourcesPath, 'python');
  const ffmpegDir = path.join(process.resourcesPath, 'ffmpeg');
  const torchLib = path.join(pythonDir, 'Lib', 'site-packages', 'torch', 'lib');
  const extras = [pythonDir, ffmpegDir, torchLib].filter((item) => fs.existsSync(item));
  if (extras.length) {
    process.env.PATH = [...extras, process.env.PATH || ''].join(';');
  }
  const ffmpeg = path.join(ffmpegDir, 'ffmpeg.exe');
  const ffprobe = path.join(ffmpegDir, 'ffprobe.exe');
  if (fs.existsSync(ffmpeg)) process.env.FFMPEG_PATH = ffmpeg;
  if (fs.existsSync(ffprobe)) process.env.FFPROBE_PATH = ffprobe;
}

function pythonPathValue() {
  const parts = [
    ROOT_DIR,
    path.join(ROOT_DIR, 'app'),
    path.resolve(__dirname, '..', '..'),
  ];
  const site = bundledSitePackages();
  if (fs.existsSync(site)) parts.push(site);
  if (process.env.PYTHONPATH) parts.push(process.env.PYTHONPATH);
  return parts.filter(Boolean).join(pathDelimiter());
}

function patchBundledPythonPth(pythonDir) {
  const pth = path.join(pythonDir, 'python312._pth');
  if (!fs.existsSync(pythonDir)) return;
  try {
    fs.writeFileSync(
      pth,
      [
        'python312.zip',
        '.',
        'Lib',
        'Lib\\site-packages',
        ROOT_DIR,
        '..',
        'import site',
        '',
      ].join('\n'),
      'utf8',
    );
  } catch (err) {
    console.warn('[Electron] Could not patch python312._pth:', err.message);
  }
}

function writeBackendLauncher() {
  const dir = app.getPath('userData');
  fs.mkdirSync(dir, { recursive: true });
  const launcher = path.join(dir, 'launch_backend.py');
  const rootLiteral = JSON.stringify(ROOT_DIR);
  fs.writeFileSync(
    launcher,
    [
      'import os, sys',
      'from pathlib import Path',
      `ROOT = Path(${rootLiteral})`,
      'os.chdir(ROOT)',
      'sys.path.insert(0, str(ROOT))',
      'os.environ["PYTHONPATH"] = str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")',
      'import uvicorn',
      `uvicorn.run("app.main:app", host="127.0.0.1", port=${BACKEND_PORT}, log_level="info")`,
      '',
    ].join('\n'),
    'utf8',
  );
  return launcher;
}

function readLogTail(logFile, maxChars = 2500) {
  try {
    if (!fs.existsSync(logFile)) return '';
    const text = fs.readFileSync(logFile, 'utf8');
    return text.slice(-maxChars).trim();
  } catch {
    return '';
  }
}

// --- PYTHON BACKEND LIFECYCLE ---

function testPythonRuntime(bin) {
  if (!bin) return false;
  if (path.isAbsolute(bin) && !fs.existsSync(bin)) return false;
  try {
    const res = spawnSync(bin, ['-c', 'import uvicorn, fastapi; print("RUNTIME_OK")'], {
      timeout: 8000,
      encoding: 'utf-8',
      env: {
        ...process.env,
        PYTHONPATH: pythonPathValue(),
        PYTHONNOUSERSITE: '1',
      },
    });
    return res.status === 0 && res.stdout && res.stdout.includes('RUNTIME_OK');
  } catch {
    return false;
  }
}

function resolveWindowsPython() {
  const candidates = [
    path.join(process.env.LocalAppData || '', 'Programs', 'Python', 'Python311', 'python.exe'),
    path.join(process.env.ProgramFiles || '', 'Python311', 'python.exe'),
    path.join(process.env.ProgramFiles || '', 'Python312', 'python.exe'),
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (testPythonVersion(candidate)) return candidate;
  }
  try {
    const launcher = spawnSync('py', ['-3.11', '-c', 'import sys; print(sys.executable)'], {
      timeout: 5000,
      encoding: 'utf-8',
      windowsHide: true,
    });
    const candidate = (launcher.stdout || '').trim().split(/\r?\n/).pop();
    if (candidate && testPythonVersion(candidate)) return candidate;
  } catch {
    // Python Launcher is optional on Windows.
  }
  try {
    const result = spawnSync('where', ['python'], { timeout: 5000, encoding: 'utf-8', windowsHide: true });
    for (const candidate of (result.stdout || '').split(/\r?\n/).map((item) => item.trim()).filter(Boolean)) {
      if (testPythonVersion(candidate)) return candidate;
    }
  } catch {
    // PATH lookup is best effort; the error is surfaced by the caller.
  }
  return null;
}

function testPythonVersion(bin) {
  if (!bin || (path.isAbsolute(bin) && !fs.existsSync(bin))) return false;
  try {
    const result = spawnSync(bin, ['-c', 'import sys; print(sys.version_info[0], sys.version_info[1])'], {
      timeout: 4000,
      encoding: 'utf-8',
      windowsHide: true,
    });
    const match = (result.stdout || '').trim().match(/^(\d+)\s+(\d+)/);
    return result.status === 0 && match && Number(match[1]) === 3 && Number(match[2]) >= 10;
  } catch {
    return false;
  }
}

function ensureWindowsPythonRuntime() {
  if (process.platform !== 'win32' || !ELECTRON_MANAGES_BACKEND) return true;
  applyWindowsRuntimePath();
  const bundled = bundledWindowsPython();
  if (fs.existsSync(bundled) && testPythonRuntime(bundled)) return true;
  if (fs.existsSync(bundled)) {
    console.log(`[Electron] Bundled Windows Python present at ${bundled}`);
    return true;
  }
  const venvPython = path.join(process.resourcesPath, 'venv', 'Scripts', 'python.exe');
  if (testPythonRuntime(venvPython)) return true;

  const basePython = resolveWindowsPython();
  if (!basePython) {
    dialog.showErrorBox(
      'Chưa tìm thấy Python',
      'Reup-Video đã tự tìm Python Launcher, PATH và thư mục cài đặt mặc định nhưng không thấy Python 3.10+.'
    );
    return false;
  }

  try {
    fs.mkdirSync(path.dirname(venvPython), { recursive: true });
    const venvResult = spawnSync(basePython, ['-m', 'venv', path.dirname(path.dirname(venvPython))], {
      timeout: 120000,
      encoding: 'utf-8',
      windowsHide: true,
    });
    if (venvResult.status !== 0 || !fs.existsSync(venvPython)) {
      throw new Error((venvResult.stderr || 'Không tạo được môi trường Python').trim());
    }
    const requirementsPath = path.join(process.resourcesPath, 'requirements.txt');
    const pipResult = spawnSync(venvPython, ['-m', 'pip', 'install', '-r', requirementsPath], {
      timeout: 900000,
      encoding: 'utf-8',
      windowsHide: true,
    });
    if (pipResult.status !== 0) {
      throw new Error((pipResult.stderr || pipResult.stdout || 'Cài backend thất bại').slice(-2000));
    }
    return testPythonRuntime(venvPython);
  } catch (error) {
    dialog.showErrorBox('Không thể cài backend', `Tự động chuẩn bị Python thất bại:\n${error.message}`);
    return false;
  }
}

function findPythonExecutable() {
  if (process.env.PYTHON_PATH && (testPythonRuntime(process.env.PYTHON_PATH) || fs.existsSync(process.env.PYTHON_PATH))) {
    return process.env.PYTHON_PATH;
  }

  if (!isDev && process.platform === 'win32') {
    const bundled = bundledWindowsPython();
    if (fs.existsSync(bundled)) {
      console.log(`[Electron] Using bundled Windows Python: ${bundled}`);
      return bundled;
    }
  }

  const home = process.env.HOME || '';
  const devWorkspaceRoot = path.resolve(__dirname, '..', '..');

  const candidates = [
    // 1. Packaged or local venvs inside project
    path.join(ROOT_DIR, 'venv311', 'bin', 'python'),
    path.join(ROOT_DIR, 'venv311', 'bin', 'python3'),
    path.join(ROOT_DIR, 'venv', 'bin', 'python'),
    path.join(ROOT_DIR, 'venv', 'bin', 'python3'),
    path.join(ROOT_DIR, '.venv', 'bin', 'python'),
    path.join(ROOT_DIR, '.venv', 'bin', 'python3'),
    path.join(devWorkspaceRoot, 'venv311', 'bin', 'python'),
    path.join(devWorkspaceRoot, 'venv', 'bin', 'python'),

    // 2. ExtraResources location if bundled
    path.join(process.resourcesPath, 'python', 'python.exe'),
    path.join(process.resourcesPath, 'venv311', 'bin', 'python'),
    path.join(process.resourcesPath, 'venv311', 'bin', 'python3'),
    path.join(process.resourcesPath, 'venv', 'bin', 'python'),
    path.join(process.resourcesPath, 'venv', 'Scripts', 'python.exe'),

    // 3. User Application Support venv
    path.join(home, 'Library', 'Application Support', 'Reup-Video Studio', 'venv', 'bin', 'python'),
    path.join(home, '.reup-video', 'venv', 'bin', 'python'),

    // 4. Standard macOS Python versions (Homebrew, Pyenv, Conda)
    '/opt/homebrew/bin/python3.11',
    '/opt/homebrew/opt/python@3.11/bin/python3.11',
    '/opt/homebrew/bin/python3.12',
    '/opt/homebrew/bin/python3.10',
    '/opt/homebrew/bin/python3',
    '/usr/local/bin/python3.11',
    '/usr/local/bin/python3.12',
    '/usr/local/bin/python3',
    path.join(home, '.pyenv', 'shims', 'python3'),
    path.join(home, 'miniforge3', 'bin', 'python3'),
    path.join(home, 'miniconda3', 'bin', 'python3'),

    // Windows candidates
    path.join(ROOT_DIR, 'venv', 'Scripts', 'python.exe'),
    path.join(ROOT_DIR, 'venv311', 'Scripts', 'python.exe'),
    path.join(ROOT_DIR, '.venv', 'Scripts', 'python.exe'),
    path.join(devWorkspaceRoot, 'venv', 'Scripts', 'python.exe'),
    path.join(process.resourcesPath, 'venv', 'Scripts', 'python.exe'),
  ];

  // Pick first candidate that actually passes the fastapi/uvicorn test
  for (const p of candidates) {
    if (testPythonRuntime(p)) {
      console.log(`[Electron] Selected verified Python runtime: ${p}`);
      return p;
    }
  }

  // Fallback: search which python3 in enriched PATH
  try {
    const whichCmd = process.platform === 'win32' ? 'where python' : 'which -a python3.11 python3 python 2>/dev/null';
    const out = execSync(whichCmd, { encoding: 'utf-8', env: process.env }).trim().split('\n');
    for (const line of out) {
      const trimmed = line.trim();
      if (trimmed && testPythonRuntime(trimmed)) {
        console.log(`[Electron] Selected PATH Python runtime: ${trimmed}`);
        return trimmed;
      }
    }
  } catch {
    // fallback
  }

  // Last resort: return first existing path or default
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return process.platform === 'win32' ? 'python.exe' : 'python3';
}

function checkBackendHealth(timeout = 3000) {
  return new Promise((resolve) => {
    const req = http.get(`${BACKEND_URL}/health`, { timeout }, (res) => {
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function waitForBackend(maxAttempts = 50, interval = 300, requestTimeout = 3000) {
  for (let i = 0; i < maxAttempts; i++) {
    const healthy = await checkBackendHealth(requestTimeout);
    if (healthy) return true;
    await new Promise((r) => setTimeout(r, interval));
  }
  return false;
}

async function startPythonBackend() {
  if (!ELECTRON_MANAGES_BACKEND) {
    console.log('[Electron] Development backend is managed by the npm dev process; waiting for health...');
    const isReady = await waitForBackend(60, 500, 500);
    if (!isReady) {
      console.error('[Electron] Development backend did not become healthy. Electron will not start a duplicate backend.');
    }
    return isReady;
  }

  const isAlreadyRunning = await waitForBackend(3, 300);
  if (isAlreadyRunning) {
    console.log('[Electron] Backend is already running on port', BACKEND_PORT);
    return true;
  }

  if (!ensureWindowsPythonRuntime()) return false;
  applyWindowsRuntimePath();
  const pythonBin = findPythonExecutable();
  const pythonDir = path.dirname(pythonBin);
  patchBundledPythonPth(pythonDir);
  const launcher = writeBackendLauncher();
  console.log(`[Electron] Starting FastAPI backend with: ${pythonBin} ${launcher} (cwd: ${ROOT_DIR})`);

  const logDir = app.getPath('userData');
  if (!fs.existsSync(logDir)) {
    fs.mkdirSync(logDir, { recursive: true });
  }
  const logFile = path.join(logDir, 'backend.log');
  const logStream = fs.createWriteStream(logFile, { flags: 'a' });
  logStream.write(`\n--- [${new Date().toISOString()}] Starting Backend (${pythonBin}) ---\n`);
  logStream.write(`[LAUNCHER] ${launcher}\n[ROOT] ${ROOT_DIR}\n`);

  const ffmpegDir = path.join(process.resourcesPath, 'ffmpeg');
  const torchLib = path.join(pythonDir, 'Lib', 'site-packages', 'torch', 'lib');
  const pathExtras = [pythonDir, ffmpegDir, torchLib].filter((item) => fs.existsSync(item));
  const cacheDir = path.join(logDir, 'cache');
  fs.mkdirSync(cacheDir, { recursive: true });
  const pythonEnv = {
    ...process.env,
    PYTHONUNBUFFERED: '1',
    PYTHONNOUSERSITE: '1',
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
    PYTHONPATH: pythonPathValue(),
    PATH: [...pathExtras, process.env.PATH || ''].join(pathDelimiter()),
    PORT: String(BACKEND_PORT),
    REUP_ROOT: ROOT_DIR,
    HF_HOME: path.join(cacheDir, 'huggingface'),
    HUGGINGFACE_HUB_CACHE: path.join(cacheDir, 'huggingface'),
    TORCH_HOME: path.join(cacheDir, 'torch'),
    XDG_CACHE_HOME: cacheDir,
    NUMBA_CACHE_DIR: path.join(cacheDir, 'numba'),
  };
  if (fs.existsSync(path.join(ffmpegDir, 'ffmpeg.exe'))) {
    pythonEnv.FFMPEG_PATH = path.join(ffmpegDir, 'ffmpeg.exe');
  }
  if (fs.existsSync(path.join(ffmpegDir, 'ffprobe.exe'))) {
    pythonEnv.FFPROBE_PATH = path.join(ffmpegDir, 'ffprobe.exe');
  }

  try {
    pythonProcess = spawn(pythonBin, [launcher], {
      cwd: ROOT_DIR,
      env: pythonEnv,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    pythonProcess.stdout.on('data', (data) => {
      const msg = data.toString();
      console.log(`[FastAPI] ${msg.trim()}`);
      logStream.write(`[STDOUT] ${msg}`);
    });

    pythonProcess.stderr.on('data', (data) => {
      const msg = data.toString();
      console.error(`[FastAPI Stderr] ${msg.trim()}`);
      logStream.write(`[STDERR] ${msg}`);
    });

    pythonProcess.on('error', (err) => {
      console.error('[Electron] Failed to spawn Python process:', err);
      logStream.write(`[ERROR] Failed to spawn: ${err.message}\n`);
    });

    pythonProcess.on('exit', (code, signal) => {
      console.log(`[Electron] Python backend exited with code ${code}, signal ${signal}`);
      logStream.write(`[EXIT] code: ${code}, signal: ${signal}\n`);
      pythonProcess = null;
    });

    let isReady = false;
    for (let i = 0; i < 90; i++) {
      if (await checkBackendHealth(800)) {
        isReady = true;
        break;
      }
      if (!pythonProcess) break;
      await new Promise((r) => setTimeout(r, 500));
    }
    if (!isReady) {
      console.warn('[Electron] Backend did not respond within timeout.');
      const tail = readLogTail(logFile);
      dialog.showErrorBox(
        'Backend Python không chạy',
        [
          'Giao diện đã mở nhưng API Server đang Offline.',
          `Python: ${pythonBin}`,
          `Thư mục: ${ROOT_DIR}`,
          `Log: ${logFile}`,
          '',
          tail || 'Chưa ghi được log. Thường do Python embeddable không import được gói app.',
        ].join('\n'),
      );
    }
    return isReady;
  } catch (err) {
    console.error('[Electron] Exception spawning python process:', err);
    logStream.write(`[EXCEPTION] ${err.stack || err.message}\n`);
    return false;
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
    width: 1320,
    height: 880,
    minWidth: 1080,
    minHeight: 720,
    title: 'Reup-Video Studio',
    icon: iconToUse,
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    trafficLightPosition: { x: 16, y: 18 },
    backgroundColor: '#ffffff',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
      webSecurity: false, // Allows seamless local blob / media playback under file:// protocol
    },
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  // Pipe renderer console logs to Node stdout for debugging
  mainWindow.webContents.on('console-message', (_event, _level, message) => {
    console.log(`[Renderer] ${message}`);
  });

  const devServerUrl = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:5273';

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
        label: 'Kiểm tra Backend (Port 6000)',
        click: async () => {
          const healthy = await checkBackendHealth();
          dialog.showMessageBox({
            type: 'info',
            title: 'Trạng Thái Backend',
            message: healthy
              ? 'FastAPI Backend đang hoạt động bình thường! (127.0.0.1:6000)'
              : 'Backend đang ngắt kết nối. Xem backend.log trong thư mục dữ liệu ứng dụng.',
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
    const outputDir = path.join(ROOT_DIR, 'data', 'outputs');
    if (fs.existsSync(outputDir)) {
      shell.openPath(outputDir);
      return true;
    }
    return false;
  });

  ipcMain.handle('shell:openExternal', async (event, url) => {
    if (url && (url.startsWith('http://') || url.startsWith('https://'))) {
      await shell.openExternal(url);
      return true;
    }
    return false;
  });

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

  ipcMain.handle('app:getInfo', async () => {
    const isBackendReady = await checkBackendHealth();
    return {
      version: app.getVersion(),
      platform: process.platform,
      backendUrl: BACKEND_URL,
      backendHealthy: isBackendReady,
    };
  });

  ipcMain.handle('backend:restart', async () => {
    if (!ELECTRON_MANAGES_BACKEND) {
      console.log('[Electron] Backend restart is delegated to the npm dev process.');
      return await waitForBackend(60, 500, 500);
    }
    killPythonBackend();
    await startPythonBackend();
    return await waitForBackend();
  });

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

  // Show the UI immediately while the backend is starting or recovering jobs.
  createMainWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    } else if (mainWindow) {
      mainWindow.show();
    }
  });

  const backendReady = await startPythonBackend();
  if (!backendReady) {
    console.error('[Electron] Backend is unavailable; the UI remains open so the error can be surfaced and retried.');
  }
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
