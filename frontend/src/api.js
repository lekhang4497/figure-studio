// Per-figure WebSocket client + scoped REST helpers.
//
// All endpoints live under /api/figures/{name}/...; the client takes a
// `figureName` and rebuilds its connection if you call `switchFigure(name)`.

function encodeName(name) {
  return encodeURIComponent(name);
}

function wsUrl(figureName) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}/api/figures/${encodeName(figureName)}/ws`;
}

export class StudioClient {
  constructor({ figureName, onMessage, onStatus } = {}) {
    this.figureName = figureName;
    this.onMessage = onMessage || (() => {});
    this.onStatus = onStatus || (() => {});
    this.ws = null;
    this.reconnectDelay = 200;
    this._closedByUser = false;
    this._pingTimer = null;
    this._open();
  }

  _open() {
    if (!this.figureName) {
      this.onStatus('idle');
      return;
    }
    const ws = new WebSocket(wsUrl(this.figureName));
    this.ws = ws;
    this.onStatus('connecting');
    ws.onopen = () => {
      this.reconnectDelay = 200;
      this.onStatus('open');
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
      } catch (e) { /* ignore malformed */ }
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

  switchFigure(figureName) {
    // Tear down current socket and open a new one for the new figure.
    this._closedByUser = true;
    if (this.ws) try { this.ws.close(); } catch (e) { /* ignore */ }
    this._closedByUser = false;
    this.figureName = figureName;
    this._open();
  }

  close() {
    this._closedByUser = true;
    if (this._pingTimer) { clearInterval(this._pingTimer); this._pingTimer = null; }
    if (this.ws) this.ws.close();
  }
}

// ----- REST helpers -----

export async function fetchFigures() {
  const r = await fetch('/api/figures');
  return r.json();
}

export async function fetchPresets() {
  const r = await fetch('/api/presets');
  return r.json();
}

export async function fetchPalettes() {
  const r = await fetch('/api/palettes');
  return r.json();
}

export async function fetchFigureState(name) {
  const r = await fetch(`/api/figures/${encodeName(name)}/state`);
  if (!r.ok) throw new Error(`figure ${name} not found`);
  return r.json();
}

export async function fetchFigureSvg(name) {
  const r = await fetch(`/api/figures/${encodeName(name)}/figure.svg`);
  if (!r.ok) throw new Error(`figure ${name} svg fetch failed`);
  return r.text();
}

export async function resetSession(name) {
  return fetch(`/api/figures/${encodeName(name)}/session/reset`, { method: 'POST' }).then((r) => r.json());
}

export async function saveSession(name) {
  return fetch(`/api/figures/${encodeName(name)}/session/save`, { method: 'POST' }).then((r) => r.json());
}

export async function removeFigure(name) {
  return fetch(`/api/figures/${encodeName(name)}`, { method: 'DELETE' }).then((r) => r.json());
}

export async function extractAxes(name, axesIndex, asName) {
  const qs = asName ? `?as_name=${encodeName(asName)}` : '';
  const r = await fetch(`/api/figures/${encodeName(name)}/extract/${axesIndex}${qs}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  if (!r.ok) throw new Error(`extract failed: ${r.status}`);
  return r.json();
}

export function exportPdfUrl(name, { onlyVisible = false, pad = null } = {}) {
  const parts = [];
  if (onlyVisible) parts.push('only_visible=true');
  if (pad !== null && pad !== undefined) parts.push(`pad=${pad}`);
  const qs = parts.length ? `?${parts.join('&')}` : '';
  return `/api/figures/${encodeName(name)}/export/pdf${qs}`;
}

export function exportPngUrl(name, dpi = 300) {
  return `/api/figures/${encodeName(name)}/export/png?dpi=${dpi}`;
}

export function exportCodeUrl(name) {
  return `/api/figures/${encodeName(name)}/export/code`;
}

export async function fetchExportedCode(name) {
  const r = await fetch(exportCodeUrl(name));
  if (!r.ok) throw new Error(`code export failed: ${r.status}`);
  return r.text();
}

export async function copyToClipboard(text) {
  // navigator.clipboard requires a secure context; fall back to a hidden textarea.
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.focus(); ta.select();
  try { document.execCommand('copy'); } finally { ta.remove(); }
}

export function downloadUrl(url, filename) {
  const a = document.createElement('a');
  a.href = url;
  if (filename) a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ----- URL helpers -----

export function readFigureFromUrl() {
  try {
    const p = new URLSearchParams(location.search);
    return p.get('fig') || null;
  } catch (e) { return null; }
}

export function writeFigureToUrl(name) {
  try {
    const url = new URL(location.href);
    if (name) url.searchParams.set('fig', name);
    else url.searchParams.delete('fig');
    history.replaceState(null, '', url.toString());
  } catch (e) { /* ignore */ }
}
