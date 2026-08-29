import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  applyVideoSpeed,
  clampCanvasFill,
  clampPreviewSpeed,
  captionPlateBox,
  resolveCanvasFill,
  previewSubtitleY,
  clampCoverHeight,
  clampSubtitleBoxW,
  clampSubtitleBoxH,
  subtitleBoxRect,
  fitKeepIntoCanvas,
  resolveCanvasAspect,
  formatPixelAspect,
  platformsHaveVertical,
  platformsHaveHorizontal,
} from './previewCanvas.js';

const fullKeep = { left: 0, top: 0, keepW: 1, keepH: 1 };

test('16:9 source fills a 16:9 canvas', () => {
  const fit = fitKeepIntoCanvas(fullKeep, 1920, 1080, 1920, 1080);
  assert.equal(Math.round(fit.fittedW), 1920);
  assert.equal(Math.round(fit.fittedH), 1080);
  assert.equal(Math.round(fit.padX), 0);
  assert.equal(Math.round(fit.padY), 0);
});

test('16:9 source letterboxes into 9:16 (bars top/bottom)', () => {
  const fit = fitKeepIntoCanvas(fullKeep, 1920, 1080, 1080, 1920);
  assert.equal(Math.round(fit.fittedW), 1080);
  assert.ok(fit.fittedH < 1920 - 10);
  assert.equal(Math.round(fit.padX), 0);
  assert.ok(fit.padY > 400);
});

test('canvas fill keeps picture size and only grows the bottom plate', () => {
  const contain = fitKeepIntoCanvas(fullKeep, 1920, 1080, 1080, 1920, 0);
  const mid = fitKeepIntoCanvas(fullKeep, 1920, 1080, 1080, 1920, 0.5);
  const tall = fitKeepIntoCanvas(fullKeep, 1920, 1080, 1080, 1920, 1);
  assert.equal(Math.round(mid.fittedW), Math.round(contain.fittedW));
  assert.equal(Math.round(mid.fittedH), Math.round(contain.fittedH));
  assert.equal(Math.round(tall.fittedH), Math.round(contain.fittedH));
  assert.equal(Math.round(mid.videoW), Math.round(contain.videoW));
  assert.equal(Math.round(mid.videoH), Math.round(contain.videoH));
  assert.ok(mid.padY < contain.padY - 10);
  assert.ok(tall.padY < 2);
  assert.ok(tall.plateH > contain.fittedH);
  assert.equal(clampCanvasFill(80), 0.8);
  assert.equal(clampCanvasFill(-1), 0);
});

test('logo aspect caps the plate so leftover 9:16 is letterbox, not a void under the image', () => {
  const contain = fitKeepIntoCanvas(fullKeep, 1920, 1080, 1080, 1920, 0, 16 / 9);
  const over = fitKeepIntoCanvas(fullKeep, 1920, 1080, 1080, 1920, 0.77, 16 / 9);
  const full = fitKeepIntoCanvas(fullKeep, 1920, 1080, 1080, 1920, 1, 16 / 9);
  assert.equal(Math.round(over.fittedH), Math.round(contain.fittedH));
  assert.ok(over.plateH < contain.fittedH + 2);
  assert.ok(over.plateH < (1920 - contain.fittedH) * 0.77 - 10);
  assert.equal(Math.round(over.plateH), Math.round(full.plateH));
  assert.ok(over.padY > 100);
  assert.ok(Math.abs(over.padY - (1920 - over.fittedH - over.plateH) / 2) < 1);
});

test('image logo sits in the plate below the picture, full width', () => {
  const tall = fitKeepIntoCanvas(fullKeep, 1920, 1080, 1080, 1920, 1);
  const box = {
    offsetX: tall.padX,
    offsetY: tall.padY,
    contentW: tall.fittedW,
    contentH: tall.fittedH,
    plateH: tall.plateH,
  };
  const plate = captionPlateBox(box, 0.30, { image: true, logoAspect: 16 / 9 });
  assert.equal(plate.overlap, false);
  assert.equal(plate.objectFit, 'cover');
  assert.equal(Math.round(plate.width), Math.round(box.contentW));
  assert.ok(plate.top >= box.offsetY + box.contentH - 0.51);
  assert.equal(Math.round(plate.height), Math.round(tall.plateH));
  assert.equal(resolveCanvasFill(0, true, 16 / 9), 1);
  assert.equal(resolveCanvasFill(0.3, true, 16 / 9), 0.3);
  const color = captionPlateBox({ ...box, bottomPad: 400 }, 0.20, { image: false, coverY: 0.90 });
  assert.equal(color.overlap, true);
  assert.equal(color.objectFit, 'cover');
  assert.ok(Math.abs(color.top - (box.offsetY + box.contentH - color.height)) < 1);
  assert.ok(color.top + color.height <= box.offsetY + box.contentH + 0.5);
});

test('color cover stays at the picture bottom even if coverY is mid-frame', () => {
  const box = { offsetX: 0, offsetY: 100, contentW: 1080, contentH: 600, plateH: 0 };
  const mid = captionPlateBox(box, 0.20, { image: false, coverY: 0.50 });
  assert.ok(Math.abs(mid.top - (100 + 600 - mid.height)) < 1);
});

test('color cover 22% is 22% of picture height, pinned to the bottom', () => {
  const portrait = { offsetX: 0, offsetY: 0, contentW: 1080, contentH: 1920, plateH: 0 };
  const a = captionPlateBox(portrait, 0.22, { image: false });
  assert.equal(Math.round(a.height), 422);
  assert.ok(Math.abs((a.top + a.height) - portrait.contentH) < 1);
  const landscape = { offsetX: 0, offsetY: 0, contentW: 1920, contentH: 1080, plateH: 0 };
  const b = captionPlateBox(landscape, 0.22, { image: false });
  assert.equal(Math.round(b.height), 238);
});

test('cover pad is ignored — color bar stays on the picture bottom', () => {
  const box = { offsetX: 0, offsetY: 100, contentW: 1080, contentH: 1920, plateH: 0 };
  const flush = captionPlateBox(box, 0.10, { image: false, coverPad: 0 });
  const lifted = captionPlateBox(box, 0.10, { image: false, coverPad: 0.12 });
  assert.equal(Math.round(lifted.top), Math.round(flush.top));
  assert.ok(Math.abs((flush.top + flush.height) - (box.offsetY + box.contentH)) < 1);
  assert.equal(clampCoverHeight(0.5), 0.36);
  assert.equal(clampCoverHeight(22), 0.22);
  assert.equal(clampCoverHeight(0), 0);
});

test('color cover 0% is off', () => {
  const box = { offsetX: 0, offsetY: 0, contentW: 1080, contentH: 1920, plateH: 0 };
  const off = captionPlateBox(box, 0, { image: false });
  assert.equal(off.height, 0);
  assert.equal(clampSubtitleBoxH(0), 0);
});

test('previewSubtitleY uses explicit drag, else auto near the cover band', () => {
  assert.equal(previewSubtitleY(0.5), 0.5);
  const autoCover = previewSubtitleY(0, 0.20, true);
  assert.ok(autoCover > 0.88 && autoCover < 0.95);
  assert.equal(previewSubtitleY(0, 0.20, false), 0.90);
});

test('vietsub background size is independent of cue and can cover hardsubs', () => {
  const box = { offsetX: 0, offsetY: 0, contentW: 1080, contentH: 1920 };
  const wide = subtitleBoxRect(box, 0.72, 0.90, 0.10);
  assert.equal(Math.round(wide.width), 972);
  assert.equal(Math.round(wide.height), 192);
  assert.ok(Math.abs(wide.top + wide.height / 2 - 0.72 * 1920) < 1);
  assert.equal(clampSubtitleBoxW(90), 0.90);
  assert.equal(clampSubtitleBoxH(10), 0.10);
  const noPlate = subtitleBoxRect(box, 0.72, 0.90, 0);
  assert.equal(noPlate.plateOff, true);
  assert.ok(noPlate.height > 0);
});

test('9:16 source pillarboxes into 16:9 (bars left/right)', () => {
  const fit = fitKeepIntoCanvas(fullKeep, 1080, 1920, 1920, 1080);
  assert.equal(Math.round(fit.fittedH), 1080);
  assert.ok(fit.fittedW < 1920 - 10);
  assert.ok(fit.padX > 400);
  assert.equal(Math.round(fit.padY), 0);
});

test('2% edge crop on 16:9 still fills 16:9', () => {
  const keep = { left: 0.02, top: 0.02, keepW: 0.96, keepH: 0.96 };
  const fit = fitKeepIntoCanvas(keep, 1920, 1080, 1920, 1080);
  assert.equal(Math.round(fit.fittedW), 1920);
  assert.equal(Math.round(fit.fittedH), 1080);
  assert.ok(Math.abs(fit.videoLeft + 0.02 * fit.videoW) < 0.5);
});

test('resolveCanvasAspect follows last pick when both families are on', () => {
  const both = ['tiktok', 'youtube_shorts', 'facebook', 'youtube'];
  assert.equal(resolveCanvasAspect(['tiktok'], null), '9:16');
  assert.equal(resolveCanvasAspect(['youtube'], null), '16:9');
  assert.equal(resolveCanvasAspect(both, '16:9'), '16:9');
  assert.equal(resolveCanvasAspect(both, '9:16'), '9:16');
  assert.equal(resolveCanvasAspect(both, null), '9:16');
  assert.equal(resolveCanvasAspect([], null), null);
});

test('platform family helpers', () => {
  assert.equal(platformsHaveVertical(['facebook']), true);
  assert.equal(platformsHaveHorizontal(['youtube']), true);
  assert.equal(platformsHaveHorizontal(['youtube_shorts']), false);
});

test('formatPixelAspect labels common ratios', () => {
  assert.equal(formatPixelAspect(1920, 1080), '16:9');
  assert.equal(formatPixelAspect(1080, 1920), '9:16');
  assert.equal(formatPixelAspect(1000, 1000), '1:1');
});

test('clampPreviewSpeed keeps the slider range', () => {
  assert.equal(clampPreviewSpeed(1.3), 1.3);
  assert.equal(clampPreviewSpeed(0.5), 0.8);
  assert.equal(clampPreviewSpeed(2), 1.5);
  assert.equal(clampPreviewSpeed('1.30'), 1.3);
});

test('applyVideoSpeed sets both playbackRate and defaultPlaybackRate', () => {
  const video = { playbackRate: 1, defaultPlaybackRate: 1 };
  assert.equal(applyVideoSpeed(video, 1.3), 1.3);
  assert.equal(video.playbackRate, 1.3);
  assert.equal(video.defaultPlaybackRate, 1.3);
});
