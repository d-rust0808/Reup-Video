const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const buildDefaults = require('../desktop-build.json');

function readDotenvValue(name) {
  const envPath = path.resolve(__dirname, '..', '..', '.env');
  if (!fs.existsSync(envPath)) return '';
  const line = fs
    .readFileSync(envPath, 'utf8')
    .split(/\r?\n/)
    .find((item) => item.trim().startsWith(`${name}=`));
  return line ? line.slice(line.indexOf('=') + 1).trim().replace(/^['"]|['"]$/g, '') : '';
}

const cliTarget = process.argv.slice(2).find((arg) => !arg.startsWith('--'));
const configuredTarget = process.env.DESKTOP_PLATFORM || readDotenvValue('DESKTOP_PLATFORM');
const requested = (cliTarget || configuredTarget || buildDefaults.platform || '').toLowerCase();
const target = requested === 'windows' ? 'win' : requested === 'darwin' ? 'mac' : requested;
const isDirOnly = process.argv.includes('--dir');
const isPrintOnly = process.argv.includes('--print-config');

if (!['win', 'mac'].includes(target)) {
  console.error('Usage: npm run electron:build -- <win|mac> [--dir]');
  console.error('Examples: npm run electron:build -- win');
  console.error('          npm run electron:build -- mac --dir');
  process.exit(2);
}

if (isPrintOnly) {
  console.log(target);
  process.exit(0);
}

if (target === 'win') {
  const prepare = spawnSync(process.execPath, [path.join(__dirname, 'prepare-windows-python.cjs')], {
    cwd: path.resolve(__dirname, '..'),
    stdio: 'inherit',
    env: process.env,
  });
  if (prepare.status !== 0) {
    process.exit(prepare.status ?? 1);
  }
}

const builder = path.join(
  __dirname,
  '..',
  'node_modules',
  '.bin',
  process.platform === 'win32' ? 'electron-builder.cmd' : 'electron-builder',
);
const args = [`--${target}`, '--config', 'electron-builder.config.cjs'];
if (isDirOnly) args.push('--dir');

console.log(`[desktop-build] target=${target}${isDirOnly ? ' (unpacked)' : ''}`);
const result = spawnSync(builder, args, {
  cwd: path.resolve(__dirname, '..'),
  stdio: 'inherit',
  shell: process.platform === 'win32',
  env: { ...process.env, DESKTOP_PLATFORM: target },
});

if (result.error) {
  console.error(`[desktop-build] ${result.error.message}`);
  process.exit(1);
}

if (target === 'win') {
  const unpacked = path.resolve(__dirname, '..', 'release', 'win', 'win-unpacked');
  const pythonExe = path.join(unpacked, 'resources', 'python', 'python.exe');
  const ffmpegExe = path.join(unpacked, 'resources', 'ffmpeg', 'ffmpeg.exe');
  console.log(`[desktop-build] bundled python.exe: ${fs.existsSync(pythonExe) ? 'yes' : 'NO'} (${pythonExe})`);
  console.log(`[desktop-build] bundled ffmpeg.exe: ${fs.existsSync(ffmpegExe) ? 'yes' : 'NO'} (${ffmpegExe})`);

  if (fs.existsSync(unpacked) && (result.status ?? 1) !== 0) {
    const zipOut = path.resolve(__dirname, '..', 'release', 'win', 'Reup-Video Studio-1.0.0-win-x64.zip');
    console.log(`[desktop-build] installer failed; zipping unpacked app to ${zipOut}`);
    const zip = spawnSync('ditto', ['-c', '-k', '--sequesterRsrc', '--keepParent', unpacked, zipOut], {
      stdio: 'inherit',
    });
    if (zip.status === 0) {
      console.log(`[desktop-build] fallback zip ready: ${zipOut}`);
    }
  }
}

process.exit(result.status ?? 1);
