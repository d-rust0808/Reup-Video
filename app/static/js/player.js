/**
 * VideoROIPlayer - Interactive HTML5 Video Player & Canvas ROI Selector Engine
 * Target Path: app/static/js/player.js
 */

export class VideoROIPlayer {
  constructor(options = {}) {
    this.container = typeof options.container === 'string'
      ? document.querySelector(options.container)
      : options.container;

    this.video = typeof options.video === 'string'
      ? document.querySelector(options.video)
      : options.video;

    this.canvas = typeof options.canvas === 'string'
      ? document.querySelector(options.canvas)
      : options.canvas;

    if (!this.canvas || !this.video) {
      console.warn('[VideoROIPlayer] Video or Canvas element not found during initialization.');
    }

    this.onCoordinatesChange = options.onCoordinatesChange || null;
    this.ctx = this.canvas ? this.canvas.getContext('2d') : null;

    // Geometry State (in displayed CSS canvas pixels)
    this.scale = 1.0;
    this.displayWidth = 0;
    this.displayHeight = 0;
    this.offsetX = 0;
    this.offsetY = 0;

    // Active ROI Box State (stored in displayed CSS canvas pixels: { x, y, w, h })
    this.roi = null;

    // Interaction State Machine: 'IDLE' | 'DRAWING' | 'DRAGGING' | 'RESIZING'
    this.interactionState = 'IDLE';
    this.activeHandle = null;
    this.dragStartOffset = { x: 0, y: 0 };
    this.drawStart = { x: 0, y: 0 };

    this.init();
  }

  init() {
    if (!this.canvas || !this.video) return;

    this.bindVideoEvents();
    this.bindMouseEvents();
    this.bindResizeObserver();

    // Initial geometry calculation
    this.updateGeometry();
    this.render();
  }

  bindVideoEvents() {
    const events = ['loadedmetadata', 'resize', 'seeked', 'play', 'pause', 'timeupdate'];
    events.forEach(evt => {
      this.video.addEventListener(evt, () => {
        this.updateGeometry();
        this.render();
      });
    });
  }

  bindResizeObserver() {
    if (this.container && window.ResizeObserver) {
      this.resizeObserver = new ResizeObserver(() => {
        this.updateGeometry();
        this.render();
      });
      this.resizeObserver.observe(this.container);
    } else {
      window.addEventListener('resize', () => {
        this.updateGeometry();
        this.render();
      });
    }
  }

  updateGeometry() {
    if (!this.canvas || !this.video) return;

    const dpr = window.devicePixelRatio || 1;
    const CW = this.canvas.clientWidth || this.container?.clientWidth || 800;
    const CH = this.canvas.clientHeight || this.container?.clientHeight || 450;

    // HiDPI Backing Store Scaling
    if (this.canvas.width !== Math.round(CW * dpr) || this.canvas.height !== Math.round(CH * dpr)) {
      this.canvas.width = Math.round(CW * dpr);
      this.canvas.height = Math.round(CH * dpr);
    }

    if (this.ctx) {
      this.ctx.resetTransform();
      this.ctx.scale(dpr, dpr);
    }

    const VW = this.video.videoWidth || CW;
    const VH = this.video.videoHeight || CH;

    // Uniform aspect ratio scaling factor with letterbox / pillarbox offset calculation
    this.scale = Math.min(CW / VW, CH / VH);
    this.displayWidth = VW * this.scale;
    this.displayHeight = VH * this.scale;
    this.offsetX = (CW - this.displayWidth) / 2;
    this.offsetY = (CH - this.displayHeight) / 2;
  }

  bindMouseEvents() {
    if (!this.canvas) return;

    // Mouse Events
    this.canvas.addEventListener('mousedown', (e) => this.onPointerDown(e));
    window.addEventListener('mousemove', (e) => this.onPointerMove(e));
    window.addEventListener('mouseup', (e) => this.onPointerUp(e));

    // Touch Events Support
    this.canvas.addEventListener('touchstart', (e) => {
      if (e.touches.length === 1) {
        this.onPointerDown(e.touches[0]);
        e.preventDefault();
      }
    }, { passive: false });

    window.addEventListener('touchmove', (e) => {
      if (this.interactionState !== 'IDLE' && e.touches.length === 1) {
        this.onPointerMove(e.touches[0]);
        e.preventDefault();
      }
    }, { passive: false });

    window.addEventListener('touchend', (e) => {
      if (this.interactionState !== 'IDLE') {
        this.onPointerUp(e.changedTouches[0] || e);
      }
    });
  }

  getPointerPos(e) {
    const rect = this.canvas.getBoundingClientRect();
    const rawX = e.clientX - rect.left;
    const rawY = e.clientY - rect.top;

    // Clamp coordinates strictly within displayed video frame boundaries [Ox, Oy, Ox + Dw, Oy + Dh]
    const clampedX = Math.max(this.offsetX, Math.min(this.offsetX + this.displayWidth, rawX));
    const clampedY = Math.max(this.offsetY, Math.min(this.offsetY + this.displayHeight, rawY));

    return { x: clampedX, y: clampedY, rawX, rawY };
  }

  onPointerDown(e) {
    const { x, y } = this.getPointerPos(e);
    const hit = this.getHitTarget(x, y);

    if (hit.type === 'HANDLE') {
      this.interactionState = 'RESIZING';
      this.activeHandle = hit.handle;
    } else if (hit.type === 'INTERIOR') {
      this.interactionState = 'DRAGGING';
      this.dragStartOffset = { x: x - this.roi.x, y: y - this.roi.y };
    } else {
      // Start drawing new ROI box
      this.interactionState = 'DRAWING';
      this.drawStart = { x, y };
      this.roi = { x, y, w: 0, h: 0 };
    }
  }

  onPointerMove(e) {
    const { x, y } = this.getPointerPos(e);

    if (this.interactionState === 'IDLE') {
      const hit = this.getHitTarget(x, y);
      this.updateCursor(hit);
      return;
    }

    if (this.interactionState === 'DRAWING') {
      const rx = Math.min(this.drawStart.x, x);
      const ry = Math.min(this.drawStart.y, y);
      const rw = Math.abs(x - this.drawStart.x);
      const rh = Math.abs(y - this.drawStart.y);
      this.roi = { x: rx, y: ry, w: rw, h: rh };
    } else if (this.interactionState === 'DRAGGING') {
      this.handleDrag(x, y);
    } else if (this.interactionState === 'RESIZING') {
      this.handleResize(x, y);
    }

    this.render();
    this.notifyCoordinatesChange();
  }

  onPointerUp(e) {
    if (this.interactionState === 'DRAWING') {
      // Discard tiny accidental click boxes (< 5px)
      if (this.roi && (this.roi.w < 5 || this.roi.h < 5)) {
        this.roi = null;
      }
    }

    this.interactionState = 'IDLE';
    this.activeHandle = null;
    this.render();
    this.notifyCoordinatesChange();
  }

  getHitTarget(mx, my) {
    if (!this.roi) return { type: 'NONE' };

    const { x, y, w, h } = this.roi;
    const hitRadius = 8; // 16x16 CSS pixel hit zone

    const handles = {
      TL: { x: x, y: y },
      TR: { x: x + w, y: y },
      BL: { x: x, y: y + h },
      BR: { x: x + w, y: y + h },
      T:  { x: x + w / 2, y: y },
      B:  { x: x + w / 2, y: y + h },
      L:  { x: x, y: y + h / 2 },
      R:  { x: x + w, y: y + h / 2 }
    };

    // 1. Check Handle Points first
    for (const [code, pt] of Object.entries(handles)) {
      if (Math.abs(mx - pt.x) <= hitRadius && Math.abs(my - pt.y) <= hitRadius) {
        return { type: 'HANDLE', handle: code };
      }
    }

    // 2. Check Box Interior
    if (mx >= x && mx <= x + w && my >= y && my <= y + h) {
      return { type: 'INTERIOR' };
    }

    return { type: 'NONE' };
  }

  updateCursor(hit) {
    if (!this.canvas) return;

    if (hit.type === 'HANDLE') {
      const cursorMap = {
        TL: 'nwse-resize', BR: 'nwse-resize',
        TR: 'nesw-resize', BL: 'nesw-resize',
        T: 'ns-resize',    B: 'ns-resize',
        L: 'ew-resize',    R: 'ew-resize'
      };
      this.canvas.style.cursor = cursorMap[hit.handle] || 'crosshair';
    } else if (hit.type === 'INTERIOR') {
      this.canvas.style.cursor = 'move';
    } else {
      this.canvas.style.cursor = 'crosshair';
    }
  }

  handleDrag(mx, my) {
    let newX = mx - this.dragStartOffset.x;
    let newY = my - this.dragStartOffset.y;

    // Clamp within displayed video frame bounds [Ox, Oy, Ox + Dw - w, Oy + Dh - h]
    newX = Math.max(this.offsetX, Math.min(this.offsetX + this.displayWidth - this.roi.w, newX));
    newY = Math.max(this.offsetY, Math.min(this.offsetY + this.displayHeight - this.roi.h, newY));

    this.roi.x = newX;
    this.roi.y = newY;
  }

  handleResize(mx, my) {
    const minSize = 10; // Minimum allowed ROI dimension in CSS pixels
    let { x, y, w, h } = this.roi;
    const right = x + w;
    const bottom = y + h;

    const clampX = (val) => Math.max(this.offsetX, Math.min(this.offsetX + this.displayWidth, val));
    const clampY = (val) => Math.max(this.offsetY, Math.min(this.offsetY + this.displayHeight, val));

    const cx = clampX(mx);
    const cy = clampY(my);

    switch (this.activeHandle) {
      case 'TL':
        x = Math.min(cx, right - minSize);
        y = Math.min(cy, bottom - minSize);
        w = right - x;
        h = bottom - y;
        break;
      case 'TR':
        y = Math.min(cy, bottom - minSize);
        w = Math.max(minSize, cx - x);
        h = bottom - y;
        break;
      case 'BL':
        x = Math.min(cx, right - minSize);
        w = right - x;
        h = Math.max(minSize, cy - y);
        break;
      case 'BR':
        w = Math.max(minSize, cx - x);
        h = Math.max(minSize, cy - y);
        break;
      case 'T':
        y = Math.min(cy, bottom - minSize);
        h = bottom - y;
        break;
      case 'B':
        h = Math.max(minSize, cy - y);
        break;
      case 'L':
        x = Math.min(cx, right - minSize);
        w = right - x;
        break;
      case 'R':
        w = Math.max(minSize, cx - x);
        break;
    }

    this.roi = { x, y, w, h };
  }

  render() {
    if (!this.ctx || !this.canvas) return;

    const CW = this.canvas.clientWidth || this.container?.clientWidth || 800;
    const CH = this.canvas.clientHeight || this.container?.clientHeight || 450;

    this.ctx.clearRect(0, 0, CW, CH);

    if (!this.roi) return;

    const { x, y, w, h } = this.roi;

    // 1. Semi-transparent dark overlay mask over outer areas
    this.ctx.fillStyle = 'rgba(0, 0, 0, 0.45)';
    // Top strip
    this.ctx.fillRect(this.offsetX, this.offsetY, this.displayWidth, Math.max(0, y - this.offsetY));
    // Bottom strip
    this.ctx.fillRect(this.offsetX, y + h, this.displayWidth, Math.max(0, (this.offsetY + this.displayHeight) - (y + h)));
    // Left strip
    this.ctx.fillRect(this.offsetX, y, Math.max(0, x - this.offsetX), h);
    // Right strip
    this.ctx.fillRect(x + w, y, Math.max(0, (this.offsetX + this.displayWidth) - (x + w)), h);

    // 2. Active ROI Bounding Box Stroke & Interior Fill Highlight
    this.ctx.fillStyle = 'rgba(59, 130, 246, 0.15)'; // Tailwind blue-500 tint
    this.ctx.fillRect(x, y, w, h);

    this.ctx.strokeStyle = '#3B82F6'; // Tailwind blue-500
    this.ctx.lineWidth = 2;
    this.ctx.strokeRect(x, y, w, h);

    // 3. Render 8 Anchor Handles (TL, TR, BL, BR, T, B, L, R)
    this.drawHandles();

    // 4. Render ROI Coordinate Readout Badge
    this.drawBadge();
  }

  drawHandles() {
    const { x, y, w, h } = this.roi;
    const handles = [
      { x: x, y: y },         { x: x + w / 2, y: y }, { x: x + w, y: y },
      { x: x, y: y + h / 2 },                         { x: x + w, y: y + h / 2 },
      { x: x, y: y + h },     { x: x + w / 2, y: y + h }, { x: x + w, y: y + h }
    ];

    this.ctx.fillStyle = '#FFFFFF';
    this.ctx.strokeStyle = '#2563EB'; // Blue 600
    this.ctx.lineWidth = 1.5;
    const size = 8;

    handles.forEach(pt => {
      this.ctx.fillRect(pt.x - size / 2, pt.y - size / 2, size, size);
      this.ctx.strokeRect(pt.x - size / 2, pt.y - size / 2, size, size);
    });
  }

  drawBadge() {
    const norm = this.getNormalizedROI();
    const text = `ROI: ${Math.round(norm.w_norm * 100)}% x ${Math.round(norm.h_norm * 100)}%`;
    this.ctx.font = '600 11px sans-serif';
    const textWidth = this.ctx.measureText(text).width;

    const badgeX = this.roi.x;
    const badgeY = Math.max(this.offsetY + 16, this.roi.y - 6);

    this.ctx.fillStyle = 'rgba(37, 99, 235, 0.9)'; // Blue-600 background
    this.ctx.fillRect(badgeX, badgeY - 14, textWidth + 10, 16);
    this.ctx.fillStyle = '#FFFFFF';
    this.ctx.fillText(text, badgeX + 5, badgeY - 2);
  }

  getNormalizedROI() {
    if (!this.roi || this.displayWidth === 0 || this.displayHeight === 0) {
      return { x_norm: 0, y_norm: 0, w_norm: 0, h_norm: 0 };
    }
    return {
      x_norm: Math.max(0, Math.min(1, (this.roi.x - this.offsetX) / this.displayWidth)),
      y_norm: Math.max(0, Math.min(1, (this.roi.y - this.offsetY) / this.displayHeight)),
      w_norm: Math.max(0, Math.min(1, this.roi.w / this.displayWidth)),
      h_norm: Math.max(0, Math.min(1, this.roi.h / this.displayHeight))
    };
  }

  setNormalizedROI(x_norm, y_norm, w_norm, h_norm) {
    this.updateGeometry();
    this.roi = {
      x: this.offsetX + (x_norm * this.displayWidth),
      y: this.offsetY + (y_norm * this.displayHeight),
      w: w_norm * this.displayWidth,
      h: h_norm * this.displayHeight
    };
    this.render();
    this.notifyCoordinatesChange();
  }

  applyPreset(presetKey) {
    const presets = {
      DOUYIN_TL:     { x: 0.03, y: 0.03, w: 0.25, h: 0.08 },
      KUAISHOU_BR:   { x: 0.70, y: 0.90, w: 0.28, h: 0.08 },
      XHS_TR:        { x: 0.65, y: 0.03, w: 0.32, h: 0.07 },
      SUBTITLE_BAND: { x: 0.05, y: 0.80, w: 0.90, h: 0.15 },
      CENTER_BOX:    { x: 0.25, y: 0.25, w: 0.50, h: 0.50 }
    };

    if (presetKey === 'CLEAR') {
      this.clearROI();
      return;
    }

    const p = presets[presetKey];
    if (p) {
      this.setNormalizedROI(p.x, p.y, p.w, p.h);
    }
  }

  clearROI() {
    this.roi = null;
    this.render();
    this.notifyCoordinatesChange();
  }

  stepFrame(direction) {
    if (!this.video) return;
    if (!this.video.paused) {
      this.video.pause();
    }
    const frameDuration = 1 / 30.0; // 30 FPS standard frame stepping
    let targetTime = this.video.currentTime + (direction * frameDuration);
    targetTime = Math.max(0, Math.min(this.video.duration || 0, targetTime));
    this.video.currentTime = targetTime;
  }

  notifyCoordinatesChange() {
    if (typeof this.onCoordinatesChange === 'function') {
      this.onCoordinatesChange(this.getNormalizedROI(), this.roi);
    }
  }
}

// Global window registration for non-module script tag fallbacks
if (typeof window !== 'undefined') {
  window.VideoROIPlayer = VideoROIPlayer;
}
