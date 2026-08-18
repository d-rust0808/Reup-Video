/**
 * WebSocket Manager for real-time job progress tracking
 */

export class WebSocketClient {
  constructor(onMessage, onStatusChange) {
    this.onMessage = onMessage;
    this.onStatusChange = onStatusChange;
    this.ws = null;
    this.reconnectTimer = null;
    this.connected = false;
  }

  connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/ws/jobs`;

    try {
      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        this.connected = true;
        this.onStatusChange?.('connected');
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this.onMessage?.(data);
        } catch (e) {
          console.error('WS JSON parse error:', e);
        }
      };

      this.ws.onclose = () => {
        this.connected = false;
        this.onStatusChange?.('disconnected');
        this.scheduleReconnect();
      };

      this.ws.onerror = (err) => {
        console.warn('WS Error:', err);
        this.ws?.close();
      };
    } catch {
      this.connected = false;
      this.onStatusChange?.('disconnected');
      this.scheduleReconnect();
    }
  }

  scheduleReconnect() {
    if (!this.reconnectTimer) {
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this.connect();
      }, 3000);
    }
  }

  subscribe(jobId) {
    if (this.ws && this.connected) {
      this.ws.send(JSON.stringify({ action: 'subscribe', job_id: jobId }));
    }
  }

  close() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.ws) this.ws.close();
  }
}
