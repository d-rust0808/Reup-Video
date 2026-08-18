/**
 * JobWebSocketSubscriber - Real-time WebSocket subscriber for video processing pipeline progress
 * Target Path: app/static/js/websocket.js
 */

export class JobWebSocketSubscriber {
  constructor(options = {}) {
    this.url = options.url || this.getDefaultWsUrl();
    this.reconnectAttempts = 0;
    this.maxReconnectDelay = 30000;
    this.baseDelay = 1000;
    this.pingIntervalMs = 15000;

    this.ws = null;
    this.pingTimer = null;
    this.listeners = new Map();
    this.autoReconnect = true;
    this.subscribedJobs = new Set();
  }

  getDefaultWsUrl() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws/jobs`;
  }

  connect() {
    try {
      this.updateStatusBadge('Connecting...', 'bg-yellow-500');
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this.reconnectAttempts = 0;
        this.updateStatusBadge('Connected', 'bg-green-500');
        this.startHeartbeat();
        this.emit('connection_change', { status: 'connected' });

        // Resubscribe active jobs upon reconnect
        this.subscribedJobs.forEach((jobId) => {
          this.sendSubscribe(jobId);
        });
      };

      this.ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          this.handlePayload(payload);
        } catch (e) {
          console.error('[WS] Error parsing JSON payload:', e);
        }
      };

      this.ws.onclose = (event) => {
        this.stopHeartbeat();
        this.updateStatusBadge('Disconnected', 'bg-red-500');
        this.emit('connection_change', { status: 'disconnected', code: event.code });

        if (this.autoReconnect) {
          this.scheduleReconnect();
        }
      };

      this.ws.onerror = (error) => {
        console.error('[WS] WebSocket error:', error);
      };
    } catch (err) {
      console.error('[WS] Connection attempt exception:', err);
      this.scheduleReconnect();
    }
  }

  scheduleReconnect() {
    this.reconnectAttempts++;
    // Exponential backoff + jitter
    const delay = Math.min(
      this.baseDelay * Math.pow(1.8, this.reconnectAttempts - 1) + Math.random() * 500,
      this.maxReconnectDelay
    );
    this.updateStatusBadge(`Reconnecting in ${Math.ceil(delay / 1000)}s...`, 'bg-yellow-600');
    setTimeout(() => this.connect(), delay);
  }

  startHeartbeat() {
    this.stopHeartbeat();
    this.pingTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ action: 'ping', timestamp: Date.now() }));
      }
    }, this.pingIntervalMs);
  }

  stopHeartbeat() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  sendSubscribe(jobId) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ action: 'subscribe', job_id: jobId }));
    }
  }

  subscribeJob(jobId) {
    this.subscribedJobs.add(jobId);
    this.sendSubscribe(jobId);
  }

  handlePayload(payload) {
    const eventName = payload.event;
    if (eventName) {
      this.emit(eventName, payload);
      this.emit('*', payload); // Universal wildcard listener
    }
  }

  on(event, callback) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event).add(callback);
  }

  off(event, callback) {
    if (this.listeners.has(event)) {
      this.listeners.get(event).delete(callback);
    }
  }

  emit(event, data) {
    if (this.listeners.has(event)) {
      this.listeners.get(event).forEach((cb) => cb(data));
    }
  }

  updateStatusBadge(text, bgClass) {
    const badges = [
      document.getElementById('wsStatusBadge'),
      document.getElementById('ws-status-badge')
    ];

    badges.forEach((badge) => {
      if (badge) {
        badge.textContent = text;
        badge.className = `px-2.5 py-1 text-xs font-semibold rounded-full text-white transition-all ${bgClass}`;
      }
    });
  }
}

export const wsSubscriber = new JobWebSocketSubscriber();

if (typeof window !== 'undefined') {
  window.JobWebSocketSubscriber = JobWebSocketSubscriber;
  window.wsSubscriber = wsSubscriber;
}
