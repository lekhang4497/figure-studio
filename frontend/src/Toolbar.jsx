import React, { useEffect, useState } from 'react';
import { downloadUrl, resetSession, saveSession } from './api.js';

/**
 * Top bar: figure size + presets, export buttons, session controls, status.
 */

function fmt(n) {
  return Number.parseFloat(n).toFixed(2);
}

export default function Toolbar({ snapshot, presets, status, onApply, onToast, undoCount, onUndo }) {
  const figW = snapshot?.figure?.width_in ?? 6;
  const figH = snapshot?.figure?.height_in ?? 4;
  const [w, setW] = useState(figW);
  const [h, setH] = useState(figH);
  const [preset, setPreset] = useState('');

  useEffect(() => {
    setW(figW);
    setH(figH);
  }, [figW, figH]);

  const sendSize = (newW, newH) => {
    onApply({ op: 'set_figure_size', width_in: Number(newW), height_in: Number(newH) });
  };

  const onPresetChange = (key) => {
    setPreset(key);
    if (!key) return;
    const p = presets.find((pr) => pr.key === key);
    if (!p) return;
    setW(p.width_in);
    setH(p.height_in);
    sendSize(p.width_in, p.height_in);
    onToast(`Preset → ${p.label}`);
  };

  const exportPdf = () => downloadUrl('/api/export/pdf', 'figure.pdf');
  const exportPdfMain = () => downloadUrl('/api/export/pdf?only_visible=true', 'figure_main.pdf');
  const exportPng = () => downloadUrl('/api/export/png?dpi=300', 'figure.png');
  const exportCode = () => downloadUrl('/api/export/code', 'figure.py');

  const onSave = async () => {
    const r = await saveSession();
    onToast(r.path ? `Saved → ${r.path.split('/').pop()}` : 'Saved');
  };

  const onReset = async () => {
    if (!confirm('Clear the edit log? The figure stays as-is until you re-run your script.')) return;
    await resetSession();
    onToast('Edit log cleared');
  };

  const statusClass =
    status === 'open' ? 'status' : status === 'connecting' ? 'status warn' : 'status err';
  const statusText =
    status === 'open' ? 'Connected' : status === 'connecting' ? 'Connecting…' : 'Disconnected';

  return (
    <div className="topbar">
      <div className="brand">
        <span className="mark" aria-hidden="true">◆</span>
        figure-studio<small>v0.1</small>
      </div>
      <span className="divider" />

      <div className="group">
        <label className="tiny">width</label>
        <input
          type="number"
          step="0.05"
          min="0.5"
          max="20"
          style={{ width: 68 }}
          value={fmt(w)}
          onChange={(e) => setW(parseFloat(e.target.value))}
          onBlur={() => sendSize(w, h)}
          onKeyDown={(e) => e.key === 'Enter' && sendSize(w, h)}
        />
        <span className="tiny">×</span>
        <input
          type="number"
          step="0.05"
          min="0.5"
          max="20"
          style={{ width: 68 }}
          value={fmt(h)}
          onChange={(e) => setH(parseFloat(e.target.value))}
          onBlur={() => sendSize(w, h)}
          onKeyDown={(e) => e.key === 'Enter' && sendSize(w, h)}
        />
        <span className="tiny">in</span>
      </div>

      <div className="group">
        <label className="tiny">preset</label>
        <select value={preset} onChange={(e) => onPresetChange(e.target.value)}>
          <option value="">—</option>
          {presets.map((p) => (
            <option key={p.key} value={p.key}>
              {p.label} ({p.width_in}″)
            </option>
          ))}
        </select>
      </div>

      <span className="divider" />

      <div className="group">
        <button className="subtle" onClick={onUndo} disabled={!undoCount} title="Cmd/Ctrl-Z">
          ↺ Undo ({undoCount})
        </button>
        <button className="subtle" onClick={onSave} title="Cmd/Ctrl-S">Save</button>
        <button className="danger" onClick={onReset} title="Clear edit log">Reset</button>
      </div>

      <div className="grow" />

      <div className="group">
        <button className="subtle" onClick={exportCode}>.py</button>
        <button className="subtle" onClick={exportPng}>.png</button>
        <button
          className="subtle"
          onClick={exportPdfMain}
          title="PDF with axes flagged include_in_export=False hidden"
        >
          main.pdf
        </button>
        <button className="primary" onClick={exportPdf} title="Cmd/Ctrl-E">
          Export PDF
        </button>
      </div>

      <span className={statusClass}>{statusText}</span>
    </div>
  );
}
