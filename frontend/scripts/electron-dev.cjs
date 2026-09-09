const { spawn, execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const frontendDir = path.resolve(__dirname, '..');
const repoRoot = path.resolve(frontendDir, '..');
const binDir = path.join(frontendDir, 'node_modules', '.bin');
const concurrentlyJs = path.join(frontendDir, 'node_modules', 'concurrently', 'dist', 'bin', 'index.js');
const BACKEND_PORT = 6000;
const FRONTEND_PORT = 6001;
const reload = process.argv.includes('--reload');
const isWin = process.platform === 'win32';

function fail(message) {
  console.error(`\n[electron:dev] ${message}\n`);
  process.exit(1);
}

function firstExisting(paths) {
  return paths.find((item) => item && fs.existsSync(item)) || null;
}

function resolvePythonBin() {
  if (process.env.PYTHON_PATH && fs.existsSync(process.env.PYTHON_PATH)) {
    return process.env.PYTHON_PATH;
  }
  const home = process.env.USERPROFILE || process.env.HOME || '';
  const local = process.env.LOCALAPPDATA || path.join(home, 'AppData', 'Local');
  return firstExisting([
    path.join(repoRoot, 'venv311', 'Scripts', 'python.exe'),
    path.join(repoRoot, 'venv', 'Scripts', 'python.exe'),
    path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),
    path.join(repoRoot, 'venv311', 'bin', 'python'),
    path.join(repoRoot, 'venv311', 'bin', 'python3'),
    path.join(repoRoot, 'venv', 'bin', 'python'),
    path.join(home, '.local', 'bin', 'python3.11'),
    path.join(local, 'Programs', 'Python', 'Python311', 'python.exe'),
  ]);
}

function getPidsOnPort(port) {
  try {
    if (isWin) {
      const output = execSync('netstat -ano -p tcp', { encoding: 'utf8', windowsHide: true });
      const lines = output.split(/\r?\n/);
      const pids = new Set();
      for (const line of lines) {
        if (!line.includes(`:${port}`) || !line.includes('LISTENING')) continue;
        const parts = line.trim().split(/\s+/);
        const pid = parseInt(parts[parts.length - 1], 10);
        if (pid && pid > 0 && pid !== process.pid) {
          pids.add(pid);
        }
      }
      return Array.from(pids);
    }
    const output = execSync(`lsof -ti tcp:${port} -sTCP:LISTEN`, {
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'ignore'],
    });
    return output
      .split(/\r?\n/)
      .map((p) => parseInt(p.trim(), 10))
      .filter((p) => p && p > 0 && p !== process.pid);
  } catch {
    return [];
  }
}

function freePort(port) {
  const pids = getPidsOnPort(port);
  if (!pids.length) return;
  console.log(`[electron:dev] Port ${port} is in use by PID(s): ${pids.join(', ')}. Cleaning up...`);

  for (const pid of pids) {
    try {
      if (isWin) {
        execSync(`taskkill /pid ${pid} /T /F`, { windowsHide: true, stdio: 'ignore' });
      } else {
        process.kill(pid, 'SIGTERM');
      }
    } catch {
      // Process may already have terminated
    }
  }

  const deadline = Date.now() + 1200;
  while (Date.now() < deadline) {
    const remaining = getPidsOnPort(port);
    if (!remaining.length) return;
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 100);
  }

  const stillAlive = getPidsOnPort(port);
  for (const pid of stillAlive) {
    try {
      if (isWin) {
        execSync(`taskkill /pid ${pid} /T /F`, { windowsHide: true, stdio: 'ignore' });
      } else {
        process.kill(pid, 'SIGKILL');
      }
    } catch {}
  }
}

function ensurePortsFree() {
  freePort(BACKEND_PORT);
  freePort(FRONTEND_PORT);
}

if (!fs.existsSync(concurrentlyJs)) {
  fail('Missing frontend dependencies. Run: cd frontend && npm install');
}

const pythonBin = resolvePythonBin();
if (!pythonBin) {
  fail(
    isWin
      ? 'Missing Python env. Create it with: py -3.11 -m venv venv311 && venv311\\Scripts\\pip install -r requirements.txt'
      : 'Missing Python env at ../venv311. Create it with: python3.11 -m venv ../venv311 && ../venv311/bin/pip install -r ../requirements.txt'
  );
}

const extraPath = [
  binDir,
  path.join(process.env.USERPROFILE || process.env.HOME || '', '.local', 'bin'),
  path.join(process.env.USERPROFILE || process.env.HOME || '', 'ffmpeg'),
  path.join(process.env.USERPROFILE || process.env.HOME || '', 'ffmpeg', 'bin'),
  path.join(process.env.LOCALAPPDATA || '', 'agy'),
  path.join(process.env.LOCALAPPDATA || '', 'agy', 'bin'),
  path.join(process.env.LOCALAPPDATA || '', 'Programs', 'agy'),
  path.join(process.env.LOCALAPPDATA || '', 'Programs', 'agy', 'bin'),
  path.join(process.env.LOCALAPPDATA || '', 'Google', 'Antigravity'),
  path.join(process.env.LOCALAPPDATA || '', 'Antigravity', 'cli'),
].filter((item) => item && fs.existsSync(item));

const env = {
  ...process.env,
  PATH: [...extraPath, process.env.PATH || ''].join(path.delimiter),
  PORT: String(BACKEND_PORT),
  VITE_ORIGIN: `http://127.0.0.1:${FRONTEND_PORT}`,
  VITE_DEV_SERVER_URL: `http://127.0.0.1:${FRONTEND_PORT}`,
  NODE_ENV: 'development',
};

const execPrefix = isWin ? '' : 'exec ';
const cdRoot = isWin ? `cd /d ${JSON.stringify(repoRoot)}` : `cd ${JSON.stringify(repoRoot)}`;
const backend = [
  `${cdRoot} && ${execPrefix}${JSON.stringify(pythonBin)} -m uvicorn app.main:app --port ${BACKEND_PORT} --host 127.0.0.1`,
  reload ? '--reload' : '',
].filter(Boolean).join(' ');

ensurePortsFree();

const child = spawn(
  process.execPath,
  [
    concurrentlyJs,
    '-k',
    '--kill-timeout',
    '2500',
    '-n',
    'BACKEND,VITE,DESKTOP',
    '-c',
    'green,cyan,magenta',
    backend,
    `wait-on tcp:${BACKEND_PORT} && ${execPrefix}vite --port ${FRONTEND_PORT} --strictPort`,
    `wait-on tcp:${BACKEND_PORT} && wait-on tcp:${FRONTEND_PORT} && cross-env NODE_ENV=development electron .`,
  ],
  {
    cwd: frontendDir,
    env,
    stdio: 'inherit',
    shell: false,
  },
);

let isShuttingDown = false;

function cleanShutdown(exitCode = 0, signal = null) {
  if (isShuttingDown) return;
  isShuttingDown = true;

  if (child && !child.killed) {
    try {
      if (isWin) {
        execSync(`taskkill /pid ${child.pid} /T /F`, { windowsHide: true, stdio: 'ignore' });
      } else {
        child.kill(signal || 'SIGTERM');
      }
    } catch {}
  }

  ensurePortsFree();

  if (signal) {
    process.kill(process.pid, signal);
  } else {
    process.exit(exitCode);
  }
}

process.once('SIGINT', () => cleanShutdown(130, 'SIGINT'));
process.once('SIGTERM', () => cleanShutdown(143, 'SIGTERM'));
if (!isWin) {
  process.once('SIGHUP', () => cleanShutdown(129, 'SIGHUP'));
}

child.on('exit', (code, signal) => {
  cleanShutdown(code ?? (signal ? 1 : 0), signal);
});
