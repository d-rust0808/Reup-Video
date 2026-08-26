import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
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
