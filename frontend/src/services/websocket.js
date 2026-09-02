/**
 * Singleton WebSocket Manager for real-time job progress tracking
 */

import { DESKTOP_BACKEND_WS } from './backend';

function resolveJobsWsUrl() {
  if (typeof window === 'undefined') {
    return `${DESKTOP_BACKEND_WS}/ws/jobs`;
  }
  const hostName = window.location.hostname;
  const port = String(window.location.port || '');
  const loopback = hostName === '127.0.0.1' || hostName === 'localhost';
  // Dev Vite proxies /ws → backend. Same-origin avoids private-network WS failures.
  if (loopback && port === '6001') {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws/jobs`;
  }
  const desktop = !!window.electronAPI?.isDesktop || window.location.protocol === 'file:';
  const onBackend = loopback && port === '6000';
  if (desktop && !onBackend) {
    return `${DESKTOP_BACKEND_WS}/ws/jobs`;
  }
  const host = window.location.host;
  const protocol = window.location.protocol;
  if (protocol === 'file:' || !host) {
    return `${DESKTOP_BACKEND_WS}/ws/jobs`;
  }
  return `${protocol === 'https:' ? 'wss:' : 'ws:'}//${host}/ws/jobs`;
}

class WebSocketService {
  constructor() {
    this.listeners = new Set();
    this.statusListeners = new Set();
    this.ws = null;
    this.reconnectTimer = null;
    this.pingInterval = null;
    this.connected = false;
    this.isClosed = false;
  }

  onMessage(callback) {
    this.listeners.add(callback);
    return () => this.listeners.delete(callback);
  }

  onStatusChange(callback) {
    this.statusListeners.add(callback);
    const initial = this.connected ? 'connected' : this.ws ? 'connecting' : 'disconnected';
    callback(initial);
    return () => this.statusListeners.delete(callback);
  }

  connect() {
    if (this.isClosed) return;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const wsUrl = resolveJobsWsUrl();
    this.statusListeners.forEach((fn) => fn('connecting'));

    try {
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        this.connected = true;
        this.statusListeners.forEach((fn) => fn('connected'));

        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }

        // Heartbeat ping every 15s
        if (this.pingInterval) clearInterval(this.pingInterval);
        this.pingInterval = setInterval(() => {
          if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ action: 'ping', timestamp: Date.now() }));
          }
        }, 15000);
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.event === 'pong') return; // ignore pong response
          this.listeners.forEach((fn) => fn(data));
        } catch (e) {
          console.error('WS JSON parse error:', e);
        }
      };

      this.ws.onclose = () => {
        this.connected = false;
        this.statusListeners.forEach((fn) => fn('disconnected'));

        if (this.pingInterval) {
          clearInterval(this.pingInterval);
          this.pingInterval = null;
        }

        if (!this.isClosed) {
          this.scheduleReconnect();
        }
      };

      this.ws.onerror = () => {
        // onclose handles reconnect; closing here avoids a stuck CONNECTING socket.
        try {
          this.ws?.close();
        } catch {
          // ignore
        }
      };
    } catch {
      this.connected = false;
      this.statusListeners.forEach((fn) => fn('disconnected'));
      if (!this.isClosed) {
        this.scheduleReconnect();
      }
    }
  }

  scheduleReconnect() {
    if (this.isClosed || this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 3000);
  }

  subscribe(jobId) {
    if (this.ws && this.connected && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ action: 'subscribe', job_id: jobId }));
    }
  }

  close() {
    this.isClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        // ignore
      }
      this.ws = null;
    }
  }
}

export const wsClient = new WebSocketService();
export class WebSocketClient {
  constructor(onMessage, onStatusChange) {
    this.unsubMsg = onMessage ? wsClient.onMessage(onMessage) : null;
    this.unsubStatus = onStatusChange ? wsClient.onStatusChange(onStatusChange) : null;
  }
  connect() {
    wsClient.connect();
  }
  subscribe(jobId) {
    wsClient.subscribe(jobId);
  }
  close() {
    if (this.unsubMsg) this.unsubMsg();
    if (this.unsubStatus) this.unsubStatus();
  }
}
