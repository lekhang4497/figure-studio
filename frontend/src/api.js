// Single WebSocket connection + a few REST helpers. The connection auto-reconnects
// with exponential backoff; consumers attach an `onMessage` callback.

export function backendBase() {
  // In dev, vite proxies /api and /ws to the FastAPI server. In production
  // we're served from the same origin as the backend.
  return '';
}

function wsUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}/ws`;
}

export class StudioClient {
  constructor({ onMessage, onStatus } = {}) {
    this.onMessage = onMessage || (() => {});
    this.onStatus = onStatus || (() => {});
    this.ws = null;
    this.reconnectDelay = 200;
    this._closedByUser = false;
    this._open();
  }

  _open() {
    const ws = new WebSocket(wsUrl());
    this.ws = ws;
    this.onStatus('connecting');
    ws.onopen = () => {
      this.reconnectDelay = 200;
      this.onStatus('open');
      // Tiny keepalive so idle proxies / browsers don't close us out.
      if (this._pingTimer) clearInterval(this._pingTimer);
      this._pingTimer = setInterval(() => {
        try {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }));
        } catch (e) { /* ignore */ }
      }, 20000);
    };
    ws.onclose = () => {
      if (this._pingTimer) { clearInterval(this._pingTimer); this._pingTimer = null; }
      this.onStatus('closed');
      if (!this._closedByUser) setTimeout(() => this._open(), this.reconnectDelay);
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 4000);
    };
    ws.onerror = () => this.onStatus('error');
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        this.onMessage(msg);
      } catch (e) {
        // ignore malformed
      }
    };
  }

  send(msg) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  apply(op) { this.send({ type: 'apply', op }); }
  applyMany(ops) { this.send({ type: 'apply_many', ops }); }
  select(id) { this.send({ type: 'select', id }); }
  requestSnapshot() { this.send({ type: 'request_snapshot' }); }

  close() {
    this._closedByUser = true;
    if (this.ws) this.ws.close();
  }
}

export async function fetchInitialState() {
  const r = await fetch('/api/state');
  return r.json();
}

export async function resetSession() {
  return fetch('/api/session/reset', { method: 'POST' }).then((r) => r.json());
}

export async function saveSession() {
  return fetch('/api/session/save', { method: 'POST' }).then((r) => r.json());
}

export function downloadUrl(url, filename) {
  const a = document.createElement('a');
  a.href = url;
  if (filename) a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}
