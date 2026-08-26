/** Output canvas for after-config preview (matches platform_export PRESETS). */

export const VERTICAL_PLATFORMS = ['tiktok', 'youtube_shorts', 'facebook', 'instagram', 'douyin'];
export const HORIZONTAL_PLATFORMS = ['youtube'];
export const VERTICAL_TOGGLE = ['tiktok', 'youtube_shorts', 'facebook'];
export const HORIZONTAL_TOGGLE = ['youtube'];

export function platformsHaveVertical(platforms) {
  return (platforms || []).some((p) => VERTICAL_PLATFORMS.includes(String(p)));
}

export function platformsHaveHorizontal(platforms) {
  return (platforms || []).some((p) => HORIZONTAL_PLATFORMS.includes(String(p)));
}

/**
 * Which frame the after-config preview should draw.
 * `preferred` is the last ratio the user picked when both families are on.
 */
export function resolveCanvasAspect(platforms, preferred) {
  const vertical = platformsHaveVertical(platforms);
  const horizontal = platformsHaveHorizontal(platforms);
  if (preferred === '16:9' && horizontal) return '16:9';
  if (preferred === '9:16' && vertical) return '9:16';
  if (horizontal && !vertical) return '16:9';
  if (vertical && !horizontal) return '9:16';
  if (vertical && horizontal) return '9:16';
  return null;
}

export function canvasAspectRatio(aspect) {
  if (aspect === '16:9') return 16 / 9;
  if (aspect === '9:16') return 9 / 16;
  return null;
}

function gcd(a, b) {
  let x = Math.abs(Math.round(a));
  let y = Math.abs(Math.round(b));
  while (y) {
    const t = y;
    y = x % y;
    x = t;
  }
  return x || 1;
}

export function formatPixelAspect(width, height) {
  const w = Number(width) || 0;
  const h = Number(height) || 0;
  if (!w || !h) return '';
  const r = w / h;
  if (Math.abs(r - 16 / 9) < 0.04) return '16:9';
  if (Math.abs(r - 9 / 16) < 0.04) return '9:16';
  if (Math.abs(r - 1) < 0.04) return '1:1';
  if (Math.abs(r - 4 / 3) < 0.04) return '4:3';
  if (Math.abs(r - 3 / 4) < 0.04) return '3:4';
  const g = gcd(w, h);
  return `${Math.round(w / g)}:${Math.round(h / g)}`;
}

/**
 * Letterbox the cropped keep-rect into a target canvas (same as ffmpeg
 * scale=force_original_aspect_ratio=decrease + pad).
 */
export function fitKeepIntoCanvas(keep, srcW, srcH, canvasW, canvasH) {
  const keepW = Math.max(0.05, Number(keep?.keepW) || 1);
  const keepH = Math.max(0.05, Number(keep?.keepH) || 1);
  const left = Math.max(0, Number(keep?.left) || 0);
  const top = Math.max(0, Number(keep?.top) || 0);
  const sw = Math.max(1, Number(srcW) || 1);
  const sh = Math.max(1, Number(srcH) || 1);
  const cw = Math.max(1, Number(canvasW) || 1);
  const ch = Math.max(1, Number(canvasH) || 1);
  const keepWpx = keepW * sw;
  const keepHpx = keepH * sh;
  const scale = Math.min(cw / keepWpx, ch / keepHpx);
  const fittedW = keepWpx * scale;
  const fittedH = keepHpx * scale;
  const padX = (cw - fittedW) / 2;
  const padY = (ch - fittedH) / 2;
  const videoW = sw * scale;
  const videoH = sh * scale;
  return {
    fittedW,
    fittedH,
    padX,
    padY,
    videoW,
    videoH,
    videoLeft: padX - left * videoW,
    videoTop: padY - top * videoH,
    scale,
  };
}

export function stageBoxStyle(showAfter, canvasAspect, srcW, srcH) {
  const portraitCanvas = Boolean(showAfter && canvasAspect === '9:16');
  const landscapeCanvas = Boolean(showAfter && canvasAspect === '16:9');
  const nativePortrait = Boolean(!showAfter && srcW && srcH && srcH > srcW);
  if (portraitCanvas || nativePortrait) {
    return {
      aspectRatio: portraitCanvas ? '9 / 16' : `${srcW} / ${srcH}`,
      height: 'min(72vh, 680px)',
      width: 'auto',
      maxWidth: '100%',
      marginInline: 'auto',
    };
  }
  if (landscapeCanvas) {
    return {
      aspectRatio: '16 / 9',
      width: '100%',
      maxHeight: 'min(58vh, 540px)',
    };
  }
  if (srcW && srcH) {
    return {
      aspectRatio: `${srcW} / ${srcH}`,
      width: '100%',
      maxHeight: 'min(58vh, 540px)',
    };
  }
  return { aspectRatio: '16 / 9', width: '100%' };
}
