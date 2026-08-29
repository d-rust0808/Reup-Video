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

export function clampPreviewSpeed(raw) {
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return 1;
  return Math.max(0.8, Math.min(1.5, n));
}

export function applyVideoSpeed(video, raw) {
  if (!video) return 1;
  const rate = clampPreviewSpeed(raw);
  try {
    if (Math.abs((Number(video.defaultPlaybackRate) || 0) - rate) > 0.001) {
      video.defaultPlaybackRate = rate;
    }
    if (Math.abs((Number(video.playbackRate) || 0) - rate) > 0.001) {
      video.playbackRate = rate;
    }
  } catch {
    /* some browsers reject mid-load */
  }
  return rate;
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

export function clampCanvasFill(raw) {
  const n = Number(raw);
  if (!Number.isFinite(n)) return 0;
  const x = n > 1 ? n / 100 : n;
  return Math.max(0, Math.min(1, x));
}

/** Image logo on 9:16 uses the full leftover plate when the slider is still at 0. */
export function resolveCanvasFill(raw, imageCover = false, logoAspect = 0) {
  const t = clampCanvasFill(raw);
  if (imageCover && t < 0.02 && Number(logoAspect) > 0.05) return 1;
  return t;
}

export function clampCoverHeight(raw) {
  if (raw === null || raw === undefined || raw === '') return 0.22;
  let n = Number(raw);
  if (!Number.isFinite(n)) return 0.22;
  if (n > 1) n /= 100;
  if (n < 0) n = 0;
  return Math.max(0, Math.min(0.36, n));
}

export function clampCoverPad(raw) {
  let n = Number(raw);
  if (!Number.isFinite(n) || n < 0) n = 0;
  if (n > 1) n /= 100;
  return Math.max(0, Math.min(0.40, n));
}

/**
 * Where the caption-cover / logo image is drawn on the after-config stage.
 * Logo sits *under* the picture, always full picture width (object-cover).
 */
export function captionPlateBox(box, coverH = 0, opts = {}) {
  const offsetX = Number(box?.offsetX) || 0;
  const offsetY = Number(box?.offsetY) || 0;
  const contentW = Number(box?.contentW) || 0;
  const contentH = Number(box?.contentH) || 0;
  const plateH = Math.max(0, Number(box?.plateH) || 0);
  const image = Boolean(opts?.image);
  const bottomPad = Math.max(0, Number(box?.bottomPad) || 0);
  const logoSlot = plateH > 0.5 ? plateH : bottomPad;
  if (image && logoSlot > 0.5) {
    const aspect = Number(opts?.logoAspect) || 0;
    const naturalH = aspect > 0.05 ? contentW / aspect : 0;
    const h = plateH > 0.5
      ? plateH
      : (naturalH > 0.5 ? Math.min(logoSlot, naturalH) : logoSlot);
    return {
      left: offsetX,
      top: offsetY + contentH,
      width: contentW,
      height: h,
      objectFit: 'cover',
      overlap: false,
    };
  }
  // Color cover pins to the picture bottom. Height is a fraction of the
  // picture (22% = 22% đáy hình). 0 = off.
  const h = clampCoverHeight(coverH);
  if (h <= 0) {
    return {
      left: offsetX,
      top: offsetY + contentH,
      width: contentW,
      height: 0,
      objectFit: 'cover',
      overlap: true,
      pad: 0,
    };
  }
  const coverPxH = Math.max(28, h * Math.max(0, contentH));
  return {
    left: offsetX,
    top: offsetY + contentH - coverPxH,
    width: contentW,
    height: coverPxH,
    objectFit: 'cover',
    overlap: true,
    pad: 0,
  };
}

/**
 * Vietsub cue center from the top of the picture.
 * 0 / unset = auto on the color-cover band (or near the bottom).
 * coverH / coverPad are fractions of the short side; pass pictureAspect = w/h
 * when you need the auto position in picture-height space.
 */
export function previewSubtitleY(subtitleY, coverH = 0, coverOn = false) {
  const raw = Number(subtitleY);
  if (Number.isFinite(raw) && raw > 0.04) {
    const y = raw > 1 ? raw / 100 : raw;
    return Math.max(0.04, Math.min(0.96, y));
  }
  if (coverOn) {
    const h = clampCoverHeight(coverH);
    return Math.max(0.08, Math.min(0.94, 1 - h * 0.45));
  }
  return 0.90;
}

export function clampSubtitleBoxW(raw) {
  let n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) n = 0.88;
  if (n > 1) n /= 100;
  return Math.max(0.40, Math.min(1, n));
}

export function clampSubtitleBoxH(raw) {
  if (raw === null || raw === undefined || raw === '') return 0.08;
  let n = Number(raw);
  if (!Number.isFinite(n)) return 0.08;
  if (n > 1) n /= 100;
  if (n < 0) n = 0;
  return Math.max(0, Math.min(0.22, n));
}

/** Vietsub background rect on the after-config picture (independent of cue length). */
export function subtitleBoxRect(box, subtitleY, boxW, boxH, coverH = 0, coverOn = false) {
  const contentW = Math.max(0, Number(box?.contentW) || 0);
  const contentH = Math.max(0, Number(box?.contentH) || 0);
  const offsetX = Number(box?.offsetX) || 0;
  const offsetY = Number(box?.offsetY) || 0;
  const y = previewSubtitleY(subtitleY, coverH, coverOn);
  const w = clampSubtitleBoxW(boxW) * contentW;
  const hFrac = clampSubtitleBoxH(boxH);
  const plateOff = hFrac <= 0;
  const h = (plateOff ? 0.045 : hFrac) * contentH;
  return {
    left: offsetX + Math.max(0, (contentW - w) / 2),
    top: offsetY + y * contentH - h / 2,
    width: w,
    height: h,
    y,
    plateOff,
  };
}

/**
 * Fit the cropped keep-rect into a target canvas.
 * Picture size is always contain (letterbox). fill grows a bottom logo plate
 * — no zoom, no stretch. When logoAspect is known, the plate is capped at the
 * image's natural height at canvas width so leftover 9:16 space stays as
 * letterbox around the video+logo group, not a black void under the logo.
 */
export function fitKeepIntoCanvas(keep, srcW, srcH, canvasW, canvasH, fill = 0, logoAspect = 0) {
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
  const contain = Math.min(cw / keepWpx, ch / keepHpx);
  const t = clampCanvasFill(fill);
  const fittedW = Math.min(cw, keepWpx * contain);
  const fittedH = Math.min(ch, keepHpx * contain);
  const rest = Math.max(0, ch - fittedH);
  const aspect = Number(logoAspect) || 0;
  const naturalPlateH = aspect > 0.05 ? Math.min(rest, fittedW / aspect) : rest;
  const plateH = Math.min(rest * t, naturalPlateH);
  const padX = (cw - fittedW) / 2;
  const padY = (rest - plateH) / 2;
  const bottomPad = Math.max(0, rest - padY - plateH);
  const videoW = sw * contain;
  const videoH = sh * contain;
  return {
    fittedW,
    fittedH,
    padX,
    padY,
    plateH,
    naturalPlateH,
    bottomPad,
    videoW,
    videoH,
    videoLeft: padX - left * videoW,
    videoTop: padY - top * videoH,
    scale: contain,
    containScale: contain,
    fill: t,
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
