const fs = require('fs');
const path = require('path');

const target = (process.env.DESKTOP_PLATFORM || 'mac').toLowerCase();
const isWindows = target === 'win';

const extraResources = [
  {
    from: '../app',
    to: 'app',
    filter: [
      '**/*',
      '!**/__pycache__/**',
      '!**/*.pyc',
      '!**/tmp/**',
      '!**/output/**',
      '!**/modules/models/**',
      '!**/modules/tmp/**',
      '!**/modules/output/**',
      '!**/modules/logs/**',
    ],
  },
  {
    from: '../data',
    to: 'data',
    filter: [
      '**/*',
      '!**/cache/**',
      '!**/outputs/**',
      '!**/output/**',
      '!**/input/**',
      '!**/temp/**',
      '!**/previews/**',
      '!**/frames/**',
      '!**/*.sqlite*',
      '!**/gpm_orchestrator.db*',
    ],
  },
  { from: '../requirements.txt', to: 'requirements.txt' },
  { from: '../clean_data.py', to: 'clean_data.py' },
];

if (isWindows) {
  extraResources.push({
    from: '.cache/win-python',
    to: 'python',
    filter: [
      '**/*',
      '!**/*.pyc',
      '!**/__pycache__/**',
      '!**/torch/include/**',
      '!**/torch/testing/**',
    ],
  });
  extraResources.push({
    from: '.cache/win-ffmpeg',
    to: 'ffmpeg',
    filter: ['ffmpeg.exe', 'ffprobe.exe'],
  });
} else {
  extraResources.push({
    from: '../venv311',
    to: 'venv311',
    filter: ['**/*', '!**/__pycache__/*', '!**/*.pyc'],
  });
}

const envFile = path.resolve(__dirname, '..', '.env');
if (fs.existsSync(envFile)) {
  extraResources.push({ from: '../.env', to: '.env' });
}

module.exports = {
  appId: 'com.reupvideo.studio',
  productName: 'Reup-Video Studio',
  directories: {
    output: `release/${target}`,
  },
  files: ['dist/**/*', 'electron/**/*', 'package.json'],
  extraResources,
  mac: {
    target: [
      { target: 'dmg', arch: ['arm64'] },
      { target: 'zip', arch: ['arm64'] },
    ],
    category: 'public.app-category.video',
    icon: 'electron/assets/icon.icns',
    identity: null,
  },
  dmg: {
    title: 'Reup-Video Studio',
    contents: [
      { x: 130, y: 220 },
      { x: 410, y: 220, type: 'link', path: '/Applications' },
    ],
  },
  win: {
    target: [
      { target: 'zip', arch: ['x64'] },
      { target: 'nsis', arch: ['x64'] },
      { target: 'portable', arch: ['x64'] },
    ],
    icon: 'electron/assets/icon.png',
    artifactName: '${productName}-${version}-win-${arch}.${ext}',
  },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: 'Reup-Video Studio',
    deleteAppDataOnUninstall: false,
  },
  portable: {
    artifactName: '${productName}-${version}-win-${arch}-portable.${ext}',
  },
};
