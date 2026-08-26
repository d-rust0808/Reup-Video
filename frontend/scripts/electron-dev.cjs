const { spawn } = require('child_process');
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
  path.join(process.env.LOCALAPPDATA || '', 'agy'),
  path.join(process.env.LOCALAPPDATA || '', 'Google', 'Antigravity'),
].filter((item) => item && fs.existsSync(item));

const env = {
  ...process.env,
  PATH: [...extraPath, process.env.PATH || ''].join(path.delimiter),
  PORT: String(BACKEND_PORT),
  VITE_ORIGIN: `http://127.0.0.1:${FRONTEND_PORT}`,
  VITE_DEV_SERVER_URL: `http://127.0.0.1:${FRONTEND_PORT}`,
};

const cdRoot = isWin ? `cd /d ${JSON.stringify(repoRoot)}` : `cd ${JSON.stringify(repoRoot)}`;
const backend = [
  `${cdRoot} && ${JSON.stringify(pythonBin)} -m uvicorn app.main:app --port ${BACKEND_PORT} --host 127.0.0.1`,
  reload ? '--reload' : '',
].filter(Boolean).join(' ');

const child = spawn(
  process.execPath,
  [
    concurrentlyJs,
    '-k',
    '-n',
    'BACKEND,VITE,DESKTOP',
    '-c',
    'green,cyan,magenta',
    backend,
    `wait-on tcp:${BACKEND_PORT} && vite --port ${FRONTEND_PORT} --strictPort`,
    `wait-on tcp:${BACKEND_PORT} && wait-on tcp:${FRONTEND_PORT} && cross-env NODE_ENV=development electron .`,
  ],
  {
    cwd: frontendDir,
    env,
    stdio: 'inherit',
    shell: false,
  },
);

child.on('exit', (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});
