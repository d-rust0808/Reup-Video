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
const pipBin = path.join(repoRoot, 'venv311', 'bin', 'pip');

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
    ...extra,
  });
  if (result.status !== 0) {
    fail(`Command failed (${result.status}): ${cmd} ${args.join(' ')}`);
  }
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

function pip(args) {
  if (!fs.existsSync(pipBin)) {
    fail(`Missing ${pipBin}. Create venv311 first.`);
  }
  run(pipBin, args, { cwd: repoRoot });
}

function unpackArchive(archive, dest) {
  fs.mkdirSync(dest, { recursive: true });
  if (archive.endsWith('.zip')) {
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
    const result = spawnSync(
      pipBin,
      [
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
      { stdio: 'inherit', encoding: 'utf-8', cwd: repoRoot },
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
}

async function main() {
  const expected = stampPayload();
  if (fs.existsSync(stampPath) && fs.existsSync(path.join(destDir, 'python.exe'))) {
    const current = fs.readFileSync(stampPath, 'utf8').trim();
    if (current === expected && process.env.FORCE_WIN_PYTHON !== '1') {
      console.log(`[win-python] Using cached runtime at ${destDir}`);
      writePth();
      writeSitecustomize();
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
  ensureExtraPackages();
  pruneRuntime();
  copyMsvcDlls();
  await ensureFfmpeg();
}

main().catch((error) => fail(error.stack || error.message));
