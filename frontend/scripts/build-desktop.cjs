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
process.exit(result.status ?? 1);
