const target = (process.env.DESKTOP_PLATFORM || 'mac').toLowerCase();
const isWindows = target === 'win';

const extraResources = [
  {
    from: '../app',
    to: 'app',
    filter: ['**/*', '!**/__pycache__/*'],
  },
  {
    from: '../data',
    to: 'data',
    filter: ['**/*', '!**/cache/*', '!**/outputs/*', '!**/*.sqlite*'],
  },
  { from: '../requirements.txt', to: 'requirements.txt' },
  { from: '../clean_data.py', to: 'clean_data.py' },
];

// A Python venv is platform-specific. Windows bootstraps its own venv on first run.
if (!isWindows) {
  extraResources.push({
    from: '../venv311',
    to: 'venv311',
    filter: ['**/*', '!**/__pycache__/*', '!**/*.pyc'],
  });
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
    target: ['nsis', 'portable'],
    icon: 'electron/assets/icon.png',
  },
};
