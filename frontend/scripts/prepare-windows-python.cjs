const { spawnSync } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const https = require('https');
const path = require('path');

const PYTHON_VERSION = '3.12.10';
const PYTHON_URL = `https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip`;
const PERTH_URL = 'https://files.pythonhosted.org/packages/49/ca/3bfa6b12f28b30ef64a2e63405f9008ae50c014ffd5ef85a4ec00245b29c/perth-1.0.0.tar.gz';

const repoRoot = path.resolve(__dirname, '..', '..');
const frontendDir = path.resolve(__dirname, '..');
const destDir = path.join(frontendDir, '.cache', 'win-python');
const siteDir = path.join(destDir, 'Lib', 'site-packages');
const stampPath = path.join(destDir, '.bundle-ready');
const requirementsPath = path.join(repoRoot, 'requirements.txt');
const isWin = process.platform === 'win32';

const BINARY_PACKAGES = [
  'fastapi>=0.110.0',
  'uvicorn>=0.28.0',
  'httptools>=0.6.0',
  'watchfiles>=0.21.0',
  'python-multipart>=0.0.9',
  'pydantic>=2.6.0',
  'httpx>=0.27.0',
  'keyring>=25.0.0',
  'aiofiles>=23.2.0',
  'websockets>=12.0',
  'numpy>=1.24.0',
  'opencv-python-headless>=4.9.0',
  'onnxruntime>=1.22.0,<1.25',
  'edge-tts>=6.1.0',
  'faster-whisper>=1.0.0',
  'deep-translator>=1.11.4',
  'gtts>=2.5.0',
  'Pillow>=10.0.0',
  'requests>=2.31.0',
  'tenacity>=8.2.0',
  'yt-dlp>=2024.12.13',
  'colorama>=0.4.6',
  'PyYAML>=6.0',
  'huggingface_hub',
  'librosa>=0.11.0',
  'soundfile',
  'soxr',
  'tokenizers>=0.20',
  'kaldi-native-fbank>=1.20',
  'tqdm',
  'einops',
  'pyyaml',
  'lameenc',
  'sea-g2p',
  'sniffio',
  'safetensors',
  'starlette>=0.46.0,<2',
  'anyio>=4.0.0,<5',
];

// Pure-Python ASGI stack. Installed again without --platform/--abi so pip
// unpacks complete py3-none-any wheels (starlette as a real package, not a
// namespace). --target + --platform win_amd64 --abi cp312 otherwise leaves
// `from starlette import status` failing with "(unknown location)".
const WEB_STACK_PACKAGES = [
  'starlette==1.6.0',
  'anyio==4.14.2',
  'fastapi==0.141.1',
  'uvicorn==0.52.4',
  'click>=8.1.0,<8.2',
];

function fail(message) {
  console.error(`\n[win-python] ${message}\n`);
  process.exit(1);
}

function stampPayload() {
  const req = fs.existsSync(requirementsPath) ? fs.readFileSync(requirementsPath) : Buffer.alloc(0);
  return crypto.createHash('sha256').update(PYTHON_VERSION).update(req).digest('hex');
}

function run(cmd, args, extra = {}) {
  const result = spawnSync(cmd, args, {
    stdio: 'inherit',
    encoding: 'utf-8',
    windowsHide: true,
    ...extra,
  });
  if (result.status !== 0) {
    fail(`Command failed (${result.status}): ${cmd} ${args.join(' ')}`);
  }
}

function resolvePipPython() {
  const home = process.env.USERPROFILE || process.env.HOME || '';
  const local = process.env.LOCALAPPDATA || path.join(home, 'AppData', 'Local');
  const candidates = [
    process.env.PYTHON_PATH,
    path.join(repoRoot, 'venv311', 'Scripts', 'python.exe'),
    path.join(repoRoot, 'venv', 'Scripts', 'python.exe'),
    path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),
    path.join(repoRoot, 'venv311', 'bin', 'python3'),
    path.join(repoRoot, 'venv311', 'bin', 'python'),
    path.join(repoRoot, 'venv', 'bin', 'python3'),
    path.join(repoRoot, 'venv', 'bin', 'python'),
    path.join(local, 'Programs', 'Python', 'Python312', 'python.exe'),
    path.join(local, 'Programs', 'Python', 'Python311', 'python.exe'),
    path.join(local, 'Programs', 'Python', 'Python310', 'python.exe'),
  ].filter(Boolean);
  const found = candidates.find((item) => fs.existsSync(item));
  if (found) return found;

  const cmd = isWin ? 'python' : 'python3';
  const probe = spawnSync(cmd, ['-c', 'import sys; print(sys.executable)'], {
    encoding: 'utf-8',
    timeout: 8000,
    windowsHide: true,
  });
  const exe = (probe.stdout || '').trim().split(/\r?\n/).pop();
  if (probe.status === 0 && exe && fs.existsSync(exe)) return exe;
  fail('Missing Python with pip. Install Python 3.10+ or create venv first.');
}

function download(url, dest) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    const get = (current) => {
      https.get(current, { headers: { 'User-Agent': 'Reup-Video-Build' } }, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          res.resume();
          get(res.headers.location);
          return;
        }
        if (res.statusCode !== 200) {
          reject(new Error(`Download ${current} failed: HTTP ${res.statusCode}`));
          return;
        }
        res.pipe(file);
        file.on('finish', () => file.close(resolve));
      }).on('error', reject);
    };
    get(url);
  });
}

function pipEnv() {
  const py = resolvePipPython();
  const pyDir = path.dirname(py);
  const delim = isWin ? ';' : ':';
  const blocked = ['win-python', 'reup-video studio', 'python312.dll'];
  const pathParts = (process.env.PATH || '')
    .split(delim)
    .filter((item) => {
      const lower = item.toLowerCase();
      return item && !blocked.some((token) => lower.includes(token));
    });
  const env = { ...process.env, PATH: [pyDir, ...pathParts].join(delim) };
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;
  env.PYTHONNOUSERSITE = '1';
  return { py, env };
}

function pip(args) {
  const { py, env } = pipEnv();
  run(py, ['-m', 'pip', ...args], { cwd: repoRoot, env });
}

function unpackArchive(archive, dest) {
  fs.mkdirSync(dest, { recursive: true });
  if (isWin) {
    const tarArgs = archive.endsWith('.tar.gz') || archive.endsWith('.tgz')
      ? ['-xzf', archive, '-C', dest]
      : ['-xf', archive, '-C', dest];
    const tar = spawnSync('tar', tarArgs, { stdio: 'inherit', windowsHide: true });
    if (tar.status === 0) return;
    if (archive.endsWith('.zip') || archive.endsWith('.whl')) {
      const zipPath = archive.endsWith('.whl') ? `${archive}.zip` : archive;
      if (zipPath !== archive) fs.copyFileSync(archive, zipPath);
      run('powershell.exe', [
        '-NoProfile',
        '-NonInteractive',
        '-Command',
        `Expand-Archive -LiteralPath ${JSON.stringify(zipPath)} -DestinationPath ${JSON.stringify(dest)} -Force`,
      ]);
      if (zipPath !== archive) fs.rmSync(zipPath, { force: true });
      return;
    }
    fail(`Could not unpack ${archive}`);
  }
  if (archive.endsWith('.zip') || archive.endsWith('.whl')) {
    run('unzip', ['-qo', archive, '-d', dest]);
    return;
  }
  run('tar', ['-xzf', archive, '-C', dest]);
}

function copyDir(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  fs.cpSync(src, dest, { recursive: true });
}

function vendorSdist(name) {
  const tmp = path.join(frontendDir, '.cache', `tmp-${name}`);
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.mkdirSync(tmp, { recursive: true });
  pip(['download', '-d', tmp, '--no-binary', ':all:', '--no-deps', name]);
  const archive = fs.readdirSync(tmp).find((file) => file.endsWith('.tar.gz') || file.endsWith('.zip'));
  if (!archive) fail(`Could not download sdist for ${name}`);
  unpackArchive(path.join(tmp, archive), tmp);
  const extracted = fs.readdirSync(tmp).find((entry) => {
    const full = path.join(tmp, entry);
    return fs.statSync(full).isDirectory() && (entry.startsWith(name) || entry.startsWith(name.replace('-', '_')));
  });
  if (!extracted) fail(`Could not unpack sdist for ${name}`);
  const root = path.join(tmp, extracted);
  const pkgName = name.replace(/-/g, '_');
  const candidates = [path.join(root, pkgName), path.join(root, name), path.join(root, 'src', pkgName), path.join(root, 'dora')];
  const pkgDir = candidates.find((candidate) => fs.existsSync(path.join(candidate, '__init__.py')) || fs.existsSync(candidate));
  if (!pkgDir || !fs.existsSync(pkgDir)) fail(`sdist for ${name} has no package directory`);
  copyDir(pkgDir, path.join(siteDir, path.basename(pkgDir)));
  fs.rmSync(tmp, { recursive: true, force: true });
}

async function vendorPerth() {
  const tmp = path.join(frontendDir, '.cache', 'tmp-perth');
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.mkdirSync(tmp, { recursive: true });
  const tarball = path.join(tmp, 'perth.tar.gz');
  await download(PERTH_URL, tarball);
  unpackArchive(tarball, tmp);
  const extracted = fs.readdirSync(tmp).find((name) => name.startsWith('perth-') && fs.statSync(path.join(tmp, name)).isDirectory());
  if (!extracted) fail('Could not unpack perth sdist');
  copyDir(path.join(tmp, extracted, 'perth'), path.join(siteDir, 'perth'));
  fs.rmSync(tmp, { recursive: true, force: true });
}

async function vendorVieneu() {
  const tmp = path.join(frontendDir, '.cache', 'tmp-vieneu');
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.mkdirSync(tmp, { recursive: true });
  pip([
    'download',
    '-d',
    tmp,
    '--only-binary=:all:',
    '--no-deps',
    '--platform',
    'win_amd64',
    '--python-version',
    '312',
    '--implementation',
    'cp',
    '--abi',
    'cp312',
    'vieneu==3.3.0',
  ]);
  const wheel = fs.readdirSync(tmp).find((name) => name.startsWith('vieneu-') && name.endsWith('.whl'));
  if (!wheel) fail('Could not download vieneu wheel');
  unpackArchive(path.join(tmp, wheel), tmp);
  const pkg = path.join(tmp, 'vieneu');
  if (!fs.existsSync(pkg)) fail('vieneu wheel missing package directory');
  copyDir(pkg, path.join(siteDir, 'vieneu'));
  const dist = fs.readdirSync(tmp).find((name) => name.startsWith('vieneu-') && name.endsWith('.dist-info'));
  if (dist) copyDir(path.join(tmp, dist), path.join(siteDir, dist));
  fs.rmSync(tmp, { recursive: true, force: true });
}

function writePth() {
  const pth = path.join(destDir, 'python312._pth');
  fs.writeFileSync(
    pth,
    [
      'python312.zip',
      '.',
      'Lib',
      'Lib\\site-packages',
      // Packaged layout: resources/python/python.exe → parent is extraResources root
      // (where the FastAPI `app` package lives). Embeddable Python ignores PYTHONPATH.
      '..',
      'import site',
      '',
    ].join('\n'),
    'utf8',
  );
}

function writeSitecustomize() {
  fs.mkdirSync(siteDir, { recursive: true });
  fs.writeFileSync(
    path.join(siteDir, 'sitecustomize.py'),
    [
      '"""Put Electron extraResources (parent of bundled python/) on sys.path."""',
      'import sys',
      'from pathlib import Path',
      '',
      '_python_root = Path(__file__).resolve().parents[2]',
      '_resources_root = _python_root.parent',
      'for candidate in (_resources_root, _python_root):',
      '    text = str(candidate)',
      '    if text not in sys.path:',
      '        sys.path.insert(0, text)',
      '',
    ].join('\n'),
    'utf8',
  );
}

function pruneRuntime() {
  const dropDirs = [
    path.join(siteDir, 'torch', 'include'),
    path.join(siteDir, 'torch', 'testing'),
  ];
  for (const dir of dropDirs) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function copyMsvcDlls() {
  const dest = path.join(destDir, 'msvcp140.dll');
  const candidates = [path.join(siteDir, 'sklearn', '.libs', 'msvcp140.dll')];
  const numpyLibs = path.join(siteDir, 'numpy.libs');
  if (fs.existsSync(numpyLibs)) {
    for (const name of fs.readdirSync(numpyLibs)) {
      if (/^msvcp140/i.test(name)) candidates.push(path.join(numpyLibs, name));
    }
  }
  const src = candidates.find((item) => fs.existsSync(item));
  if (!src) return;
  fs.copyFileSync(src, dest);
  console.log(`[win-python] Vendored ${path.basename(src)} -> msvcp140.dll`);
}

function pipWin(packages, extra = []) {
  pip([
    'install',
    '--target', siteDir,
    '--upgrade',
    '--platform', 'win_amd64',
    '--python-version', '312',
    '--implementation', 'cp',
    '--abi', 'cp312',
    '--only-binary=:all:',
    '--no-compile',
    ...extra,
    ...packages,
  ]);
}

function wipeSitePackage(name) {
  const folded = name.replace(/-/g, '_');
  for (const dir of [name, folded]) {
    fs.rmSync(path.join(siteDir, dir), { recursive: true, force: true });
  }
  if (!fs.existsSync(siteDir)) return;
  for (const entry of fs.readdirSync(siteDir)) {
    const isMeta = entry.endsWith('.dist-info') || entry.endsWith('.egg-info');
    if (isMeta && (entry.startsWith(`${name}-`) || entry.startsWith(`${folded}-`))) {
      fs.rmSync(path.join(siteDir, entry), { recursive: true, force: true });
    }
  }
}

function pipWebStack(packages) {
  pip([
    'install',
    '--target', siteDir,
    '--upgrade',
    '--force-reinstall',
    '--python-version', '312',
    '--only-binary=:all:',
    '--no-compile',
    ...packages,
  ]);
}

function verifyWebStack() {
  const py = path.join(destDir, 'python.exe');
  if (!fs.existsSync(py)) fail(`Missing ${py} for web-stack verify`);
  const probe = [
    'from fastapi import FastAPI',
    'from starlette import status',
    'import uvicorn, anyio, starlette, inspect',
    'assert getattr(starlette, "__file__", None), "starlette is a namespace package"',
    'assert inspect.getfile(status)',
    'print("WEB_STACK_OK", "starlette", starlette.__version__, "fastapi", FastAPI.__module__)',
  ].join('; ');
  const result = spawnSync(py, ['-c', probe], {
    encoding: 'utf-8',
    timeout: 30000,
    windowsHide: true,
  });
  const out = `${result.stdout || ''}${result.stderr || ''}`;
  if (result.status !== 0 || !out.includes('WEB_STACK_OK')) {
    fail(`Bundled Python cannot import FastAPI/Starlette:\n${out || `exit ${result.status}`}`);
  }
  console.log(`[win-python] ${out.trim()}`);
}

function ensureWebStack() {
  console.log('[win-python] Reinstalling FastAPI/Starlette/Uvicorn/AnyIO (complete wheels)...');
  for (const name of ['starlette', 'fastapi', 'uvicorn', 'anyio']) {
    wipeSitePackage(name);
  }
  pipWebStack(WEB_STACK_PACKAGES);
  verifyWebStack();
}

function hasPkg(name) {
  const folded = name.replace(/-/g, '_');
  return fs.existsSync(path.join(siteDir, name)) || fs.existsSync(path.join(siteDir, folded));
}

function ensureExtraPackages() {
  const missing = ['sniffio', 'safetensors'].filter((name) => !hasPkg(name));
  if (missing.length) {
    console.log(`[win-python] Installing extra wheels: ${missing.join(', ')}`);
    pipWin(missing);
  }
  if (!hasPkg('sphn')) {
    console.log('[win-python] Trying optional sphn wheel for Demucs...');
    const { py, env } = pipEnv();
    const result = spawnSync(
      py,
      [
        '-m',
        'pip',
        'install',
        '--target', siteDir,
        '--upgrade',
        '--platform', 'win_amd64',
        '--python-version', '312',
        '--implementation', 'cp',
        '--abi', 'cp312',
        '--only-binary=:all:',
        '--no-compile',
        'sphn',
      ],
      { stdio: 'inherit', encoding: 'utf-8', cwd: repoRoot, windowsHide: true, env },
    );
    if (result.status !== 0) {
      console.warn('[win-python] sphn has no Windows wheel; Demucs may be unavailable.');
    }
  }
}

function findNamedFile(root, name) {
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.name.toLowerCase() === name) return full;
    }
  }
  return null;
}

function copyLocalFfmpeg(ffmpegDest, ffmpegExe, ffprobeExe) {
  const home = process.env.USERPROFILE || process.env.HOME || '';
  const local = process.env.LOCALAPPDATA || path.join(home, 'AppData', 'Local');
  const which = spawnSync(isWin ? 'where' : 'which', ['ffmpeg'], {
    encoding: 'utf-8',
    windowsHide: true,
  });
  const whichPath = (which.stdout || '').trim().split(/\r?\n/).find(Boolean);
  const ffmpegCandidates = [
    whichPath,
    path.join(home, 'ffmpeg', 'ffmpeg.exe'),
    path.join(home, 'ffmpeg', 'bin', 'ffmpeg.exe'),
    path.join(local, 'Microsoft', 'WinGet', 'Links', 'ffmpeg.exe'),
    'C:\\ffmpeg\\bin\\ffmpeg.exe',
    'C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe',
  ].filter(Boolean);
  const ffmpeg = ffmpegCandidates.find((item) => fs.existsSync(item));
  if (!ffmpeg) return false;
  const ffprobe = [
    path.join(path.dirname(ffmpeg), 'ffprobe.exe'),
    path.join(path.dirname(ffmpeg), 'ffprobe'),
  ].find((item) => fs.existsSync(item));
  if (!ffprobe) return false;
  fs.mkdirSync(ffmpegDest, { recursive: true });
  fs.copyFileSync(ffmpeg, ffmpegExe);
  fs.copyFileSync(ffprobe, ffprobeExe);
  console.log(`[win-python] Copied local FFmpeg from ${path.dirname(ffmpeg)}`);
  return true;
}

async function ensureFfmpeg() {
  const ffmpegDest = path.join(frontendDir, '.cache', 'win-ffmpeg');
  const ffmpegExe = path.join(ffmpegDest, 'ffmpeg.exe');
  const ffprobeExe = path.join(ffmpegDest, 'ffprobe.exe');
  if (fs.existsSync(ffmpegExe) && fs.existsSync(ffprobeExe)) {
    console.log(`[win-python] Using cached FFmpeg at ${ffmpegDest}`);
    return;
  }

  console.log('[win-python] Downloading Windows FFmpeg (GPL, includes libass)...');
  const zipPath = path.join(frontendDir, '.cache', 'ffmpeg-win64-gpl.zip');
  try {
    if (!fs.existsSync(zipPath)) {
      await download(
        'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip',
        zipPath,
      );
    }
    const tmp = path.join(frontendDir, '.cache', 'tmp-ffmpeg');
    fs.rmSync(tmp, { recursive: true, force: true });
    unpackArchive(zipPath, tmp);
    const ffmpeg = findNamedFile(tmp, 'ffmpeg.exe');
    const ffprobe = findNamedFile(tmp, 'ffprobe.exe');
    if (!ffmpeg || !ffprobe) fail('FFmpeg zip missing ffmpeg.exe/ffprobe.exe');
    fs.mkdirSync(ffmpegDest, { recursive: true });
    fs.copyFileSync(ffmpeg, ffmpegExe);
    fs.copyFileSync(ffprobe, ffprobeExe);
    fs.rmSync(tmp, { recursive: true, force: true });
    console.log(`[win-python] FFmpeg ready: ${ffmpegDest}`);
    return;
  } catch (error) {
    console.warn(`[win-python] FFmpeg download failed: ${error.message}`);
    if (copyLocalFfmpeg(ffmpegDest, ffmpegExe, ffprobeExe)) return;
    fail('Could not bundle FFmpeg. Install FFmpeg or allow the GitHub download.');
  }
}

async function main() {
  const expected = stampPayload();
  if (fs.existsSync(stampPath) && fs.existsSync(path.join(destDir, 'python.exe'))) {
    const current = fs.readFileSync(stampPath, 'utf8').trim();
    if (current === expected && process.env.FORCE_WIN_PYTHON !== '1') {
      console.log(`[win-python] Using cached runtime at ${destDir}`);
      writePth();
      writeSitecustomize();
      ensureWebStack();
      ensureExtraPackages();
      pruneRuntime();
      copyMsvcDlls();
      await ensureFfmpeg();
      return;
    }
  }

  console.log('[win-python] Preparing bundled Windows Python 3.12 + site-packages...');
  fs.rmSync(destDir, { recursive: true, force: true });
  fs.mkdirSync(path.dirname(destDir), { recursive: true });

  const zipPath = path.join(frontendDir, '.cache', `python-${PYTHON_VERSION}-embed-amd64.zip`);
  if (!fs.existsSync(zipPath)) {
    console.log(`[win-python] Downloading ${PYTHON_URL}`);
    await download(PYTHON_URL, zipPath);
  }
  unpackArchive(zipPath, destDir);
  fs.mkdirSync(siteDir, { recursive: true });
  writePth();
  writeSitecustomize();

  console.log('[win-python] Installing Windows CPU PyTorch wheels...');
  pip([
    'install',
    '--target', siteDir,
    '--upgrade',
    '--platform', 'win_amd64',
    '--python-version', '312',
    '--implementation', 'cp',
    '--abi', 'cp312',
    '--only-binary=:all:',
    '--index-url', 'https://download.pytorch.org/whl/cpu',
    'torch',
    'torchaudio',
  ]);

  console.log('[win-python] Installing Windows backend wheels...');
  pip([
    'install',
    '--target', siteDir,
    '--upgrade',
    '--platform', 'win_amd64',
    '--python-version', '312',
    '--implementation', 'cp',
    '--abi', 'cp312',
    '--only-binary=:all:',
    '--no-compile',
    ...BINARY_PACKAGES,
  ]);

  ensureWebStack();

  pip([
    'install',
    '--target', siteDir,
    '--upgrade',
    '--platform', 'win_amd64',
    '--python-version', '312',
    '--implementation', 'cp',
    '--abi', 'cp312',
    '--only-binary=:all:',
    '--no-deps',
    'demucs>=4.1.0,<5.0',
    'julius',
    'openunmix',
  ]);

  console.log('[win-python] Vendoring vieneu + perth + dora-search...');
  await vendorPerth();
  await vendorVieneu();
  vendorSdist('dora-search');

  if (!fs.existsSync(path.join(destDir, 'python.exe'))) {
    fail('python.exe missing after extract');
  }
  fs.writeFileSync(stampPath, `${expected}\n`, 'utf8');
  console.log(`[win-python] Ready: ${destDir}`);
  writePth();
  writeSitecustomize();
  ensureWebStack();
  ensureExtraPackages();
  pruneRuntime();
  copyMsvcDlls();
  await ensureFfmpeg();
}

main().catch((error) => fail(error.stack || error.message));
