import React, { useEffect, useState } from 'react';
import {
  copyToClipboard,
  downloadUrl,
  exportCodeUrl,
  exportPdfUrl,
  exportPngUrl,
  fetchExportedCode,
  fetchPalettes,
  resetSession,
  saveSession,
} from './api.js';

/**
 * Top bar: figure size + presets, palette, export buttons, session controls.
 * Operates on the currently active figure (`activeFigure`).
 */

function fmt(n) {
  return Number.parseFloat(n).toFixed(2);
}

export default function Toolbar({
  snapshot,
  presets,
  status,
  activeFigure,
  figures,
  onApply,
  onToast,
  undoCount,
  onUndo,
  onOpenCombine,
}) {
  const figW = snapshot?.figure?.width_in ?? 6;
  const figH = snapshot?.figure?.height_in ?? 4;
  const [w, setW] = useState(figW);
  const [h, setH] = useState(figH);
  const [preset, setPreset] = useState('');
  const [palettes, setPalettes] = useState([]);
  const [palette, setPalette] = useState('');

  useEffect(() => {
    setW(figW);
    setH(figH);
  }, [figW, figH]);

  useEffect(() => {
    fetchPalettes().then(setPalettes).catch(() => {});
  }, []);

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

  const onPaletteChange = (key) => {
    setPalette(key);
    if (!key) return;
    onApply({ op: 'apply_palette', palette: key });
    const p = palettes.find((q) => q.key === key);
    onToast(`Palette → ${p?.label || key}`);
  };

  const downloadFor = (urlFn, ext) => {
    if (!activeFigure) return;
    downloadUrl(urlFn(activeFigure), `${activeFigure}${ext}`);
  };

  const exportPdf = () => downloadFor((n) => exportPdfUrl(n, { pad: 0 }), '.pdf');
  const exportPdfPadded = () => downloadFor((n) => exportPdfUrl(n), '_padded.pdf');
  const exportPdfMain = () => downloadFor((n) => exportPdfUrl(n, { onlyVisible: true, pad: 0 }), '_main.pdf');
  const exportPng = () => downloadFor((n) => exportPngUrl(n, 300), '.png');
  const exportCode = () => downloadFor((n) => exportCodeUrl(n), '.py');

  const copyCode = async () => {
    if (!activeFigure) return;
    try {
      const code = await fetchExportedCode(activeFigure);
      await copyToClipboard(code);
      onToast(`Copied ${activeFigure}.py to clipboard`);
    } catch (e) {
      onToast(`Copy failed: ${e.message}`, 'err');
    }
  };

  const onSave = async () => {
    if (!activeFigure) return;
    const r = await saveSession(activeFigure);
    onToast(r.path ? `Saved → ${r.path.split('/').pop()}` : 'Saved');
  };

  const onReset = async () => {
    if (!activeFigure) return;
    if (!confirm('Clear the edit log? The figure stays as-is until you re-run your script.')) return;
    await resetSession(activeFigure);
    onToast('Edit log cleared');
  };

  const statusClass =
    status === 'open' ? 'status' :
    status === 'connecting' ? 'status warn' :
    status === 'idle' ? 'status idle' :
    'status err';
  const statusText =
    status === 'open' ? 'Connected' :
    status === 'connecting' ? 'Connecting…' :
    status === 'idle' ? 'No session' :
    'Disconnected';

  const showFigName = activeFigure && figures && figures.length > 1;

  return (
    <div className="topbar">
      <div className="brand">
        <span className="mark" aria-hidden="true">◆</span>
        figure-studio<small>v0.4</small>
      </div>
      {showFigName && (
        <>
          <span className="divider" />
          <span className="fig-chip" title={`Active figure: ${activeFigure}`}>
            <span className="tiny">figure</span>
            <span className="name">{activeFigure}</span>
          </span>
        </>
      )}
      <span className="divider" />

      <div className="group">
        <label className="tiny">size</label>
        <input
          type="number" step="0.05" min="0.5" max="20" style={{ width: 64 }}
          value={fmt(w)}
          onChange={(e) => setW(parseFloat(e.target.value))}
          onBlur={() => sendSize(w, h)}
          onKeyDown={(e) => e.key === 'Enter' && sendSize(w, h)}
          disabled={!activeFigure}
        />
        <span className="tiny">×</span>
        <input
          type="number" step="0.05" min="0.5" max="20" style={{ width: 64 }}
          value={fmt(h)}
          onChange={(e) => setH(parseFloat(e.target.value))}
          onBlur={() => sendSize(w, h)}
          onKeyDown={(e) => e.key === 'Enter' && sendSize(w, h)}
          disabled={!activeFigure}
        />
        <span className="tiny">in</span>
      </div>

      <div className="group">
        <label className="tiny">preset</label>
        <select
          value={preset}
          onChange={(e) => onPresetChange(e.target.value)}
          disabled={!activeFigure}
        >
          <option value="">—</option>
          {presets.map((p) => (
            <option key={p.key} value={p.key}>
              {p.label} ({p.width_in}″)
            </option>
          ))}
        </select>
      </div>

      <div className="group">
        <label className="tiny" title="Recolor every series with a curated palette">palette</label>
        <select
          value={palette}
          onChange={(e) => onPaletteChange(e.target.value)}
          disabled={!activeFigure || palettes.length === 0}
        >
          <option value="">—</option>
          {palettes.map((p) => (
            <option key={p.key} value={p.key}>
              {p.label}{p.colorblind_safe ? ' ★' : ''}
            </option>
          ))}
        </select>
      </div>

      <span className="divider" />

      <div className="group">
        <button className="subtle" onClick={onUndo} disabled={!undoCount} title="Cmd/Ctrl-Z">
          ↺ Undo ({undoCount})
        </button>
        <button className="subtle" onClick={onSave} disabled={!activeFigure} title="Cmd/Ctrl-S">Save</button>
        <button className="danger" onClick={onReset} disabled={!activeFigure} title="Clear edit log">Reset</button>
        {onOpenCombine && (
          <button
            className="subtle"
            onClick={onOpenCombine}
            disabled={!figures || figures.length < 1}
            title="Combine multiple figures into a grid"
          >
            ⊞ Combine
          </button>
        )}
      </div>

      <div className="grow" />

      <div className="group">
        <button
          className="subtle"
          onClick={copyCode}
          disabled={!activeFigure}
          title="Copy generated Python to clipboard"
        >
          📋 .py
        </button>
        <button className="subtle" onClick={exportCode} disabled={!activeFigure} title="Download generated Python">
          ⬇ .py
        </button>
        <button className="subtle" onClick={exportPng} disabled={!activeFigure}>.png</button>
        <button
          className="subtle"
          onClick={exportPdfPadded}
          disabled={!activeFigure}
          title="PDF with matplotlib's default 0.1″ margin"
        >
          padded.pdf
        </button>
        <button
          className="subtle"
          onClick={exportPdfMain}
          disabled={!activeFigure}
          title="PDF with axes flagged include_in_export=False hidden"
        >
          main.pdf
        </button>
        <button
          className="primary"
          onClick={exportPdf}
          disabled={!activeFigure}
          title="Cmd/Ctrl-E — tight PDF with zero padding (LaTeX-ready)"
        >
          Export PDF
        </button>
      </div>

      <span className={statusClass}>{statusText}</span>
    </div>
  );
}
