import React, { useEffect, useMemo, useState } from 'react';
import { combineFigures } from './api.js';

/**
 * Modal for combining N session figures into a rows × cols grid.
 *
 * The user picks rows + cols, then adds figures in the order they should
 * populate the grid (row-major). Click "Combine" → calls /api/combine and
 * the parent App switches to the resulting figure.
 */
export default function CombineModal({ figures, onClose, onCreated, onToast }) {
  const [rows, setRows] = useState(2);
  const [cols, setCols] = useState(2);
  const [picked, setPicked] = useState([]);   // ordered list of figure names
  const [asName, setAsName] = useState('');
  const [busy, setBusy] = useState(false);

  // Default suggestion: 1xN if you have N <= 4, else 2 × ceil(N/2)
  useEffect(() => {
    const n = figures.length;
    if (n === 0) return;
    if (n <= 4) { setRows(1); setCols(n); }
    else { setRows(2); setCols(Math.ceil(n / 2)); }
  }, [figures.length]);

  const available = useMemo(
    () => figures.filter((f) => !picked.includes(f.name)),
    [figures, picked],
  );
  const cells = rows * cols;
  const tooMany = picked.length > cells;
  const tooFew = picked.length === 0;

  const addFigure = (name) => {
    setPicked((p) => [...p, name]);
  };
  const removeAt = (i) => {
    setPicked((p) => p.filter((_, j) => j !== i));
  };
  const moveUp = (i) => {
    if (i === 0) return;
    setPicked((p) => {
      const next = p.slice();
      [next[i - 1], next[i]] = [next[i], next[i - 1]];
      return next;
    });
  };
  const moveDown = (i) => {
    setPicked((p) => {
      if (i >= p.length - 1) return p;
      const next = p.slice();
      [next[i], next[i + 1]] = [next[i + 1], next[i]];
      return next;
    });
  };
  const useAll = () => setPicked(figures.map((f) => f.name));
  const clear = () => setPicked([]);

  const submit = async () => {
    if (busy || tooFew) return;
    setBusy(true);
    try {
      const result = await combineFigures({
        figures: picked, rows, cols, asName: asName.trim(),
      });
      onToast?.(`Combined ${picked.length} figure(s) → "${result.name}"`);
      onCreated?.(result.name);
      onClose?.();
    } catch (e) {
      onToast?.(`Combine failed: ${e.message}`, 'err');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header>
          <div className="title">Combine figures into a grid</div>
          <button className="subtle close" onClick={onClose}>✕</button>
        </header>

        <section>
          <label className="tiny">Layout</label>
          <div className="row layout">
            <input
              type="number" min="1" max="6" step="1"
              value={rows}
              onChange={(e) => setRows(Math.max(1, parseInt(e.target.value || '1', 10)))}
              style={{ width: 64 }}
            />
            <span>rows ×</span>
            <input
              type="number" min="1" max="6" step="1"
              value={cols}
              onChange={(e) => setCols(Math.max(1, parseInt(e.target.value || '1', 10)))}
              style={{ width: 64 }}
            />
            <span>cols = <b>{cells}</b> cell(s)</span>
          </div>
        </section>

        <section>
          <div className="row spread">
            <label className="tiny">Figures to combine (row-major order)</label>
            <div>
              <button className="subtle" onClick={useAll} disabled={busy}>add all</button>
              <button className="subtle" onClick={clear} disabled={busy || picked.length === 0}>clear</button>
            </div>
          </div>

          <div className="combine-grid">
            <div className="combine-col">
              <div className="combine-col-head">Available</div>
              {available.length === 0 && (
                <div className="empty-list">All session figures are already in the order list.</div>
              )}
              {available.map((f) => (
                <div key={f.name} className="combine-item available" onClick={() => addFigure(f.name)}>
                  <span className="dot" /> <span className="name">{f.name}</span>
                  <span className="meta">{f.axes_count}ax</span>
                  <button className="subtle">+</button>
                </div>
              ))}
            </div>

            <div className="combine-col">
              <div className="combine-col-head">
                Order ({picked.length}/{cells}){tooMany && <span className="warn"> · only first {cells} will fit</span>}
              </div>
              {picked.length === 0 && (
                <div className="empty-list">Click a figure on the left to add it.</div>
              )}
              {picked.map((name, i) => (
                <div key={name} className={`combine-item picked ${i >= cells ? 'overflow' : ''}`}>
                  <span className="cell-idx">cell {i + 1}</span>
                  <span className="name">{name}</span>
                  <button className="subtle" disabled={i === 0} onClick={() => moveUp(i)} title="Move up">↑</button>
                  <button className="subtle" disabled={i === picked.length - 1} onClick={() => moveDown(i)} title="Move down">↓</button>
                  <button className="subtle" onClick={() => removeAt(i)} title="Remove">✕</button>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section>
          <label className="tiny">Result name (optional)</label>
          <input
            type="text"
            placeholder="combined"
            value={asName}
            onChange={(e) => setAsName(e.target.value)}
          />
        </section>

        <footer>
          <button className="subtle" onClick={onClose} disabled={busy}>Cancel</button>
          <button
            className="primary"
            onClick={submit}
            disabled={busy || tooFew}
            title={tooFew ? 'Pick at least one figure' : ''}
          >
            {busy ? 'Combining…' : `Combine ${Math.min(picked.length, cells)} figure(s)`}
          </button>
        </footer>
      </div>
    </div>
  );
}
