import React, { useEffect, useMemo, useRef, useState } from 'react';

/**
 * Schema-driven property editor. Each property carries its own widget hints
 * (type, min, max, step, enum values, etc.) so this component is generic — to
 * support a new editable property, add it to the backend schema only.
 */

function debounce(fn, delay = 120) {
  let timer = null;
  return (...args) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

function hex6(value) {
  if (typeof value !== 'string') return '#000000';
  if (/^#[0-9a-fA-F]{8}$/.test(value)) return value.slice(0, 7);
  if (/^#[0-9a-fA-F]{6}$/.test(value)) return value;
  if (/^#[0-9a-fA-F]{3}$/.test(value)) {
    return '#' + value.slice(1).split('').map((c) => c + c).join('');
  }
  return '#000000';
}

function PropRow({ prop, entry, onApply }) {
  const [localValue, setLocalValue] = useState(prop.value);

  useEffect(() => {
    setLocalValue(prop.value);
  }, [prop.value, entry.id]);

  const send = useMemo(
    () =>
      debounce((value) => {
        onApply({
          op: 'set_property',
          artist_id: entry.id,
          kind: entry.kind,
          name: prop.name,
          value,
        });
      }, 90),
    [entry.id, entry.kind, prop.name, onApply],
  );

  const fire = (value) => {
    setLocalValue(value);
    send(value);
  };
  const fireImmediate = (value) => {
    setLocalValue(value);
    onApply({
      op: 'set_property',
      artist_id: entry.id,
      kind: entry.kind,
      name: prop.name,
      value,
    });
  };

  let control = null;
  if (prop.type === 'color') {
    const h = hex6(localValue || '#000000');
    control = (
      <div className="row">
        <input type="color" value={h} onChange={(e) => fire(e.target.value)} />
        <input
          type="text"
          value={localValue || ''}
          onChange={(e) => setLocalValue(e.target.value)}
          onBlur={(e) => fireImmediate(e.target.value)}
          spellCheck={false}
        />
      </div>
    );
  } else if (prop.type === 'float') {
    const min = prop.min ?? 0;
    const max = prop.max ?? 100;
    const step = prop.step ?? 0.1;
    control = (
      <div className="row">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={Number(localValue ?? 0)}
          onChange={(e) => fire(parseFloat(e.target.value))}
        />
        <input
          type="number"
          className="num"
          min={min}
          max={max}
          step={step}
          value={Number(localValue ?? 0)}
          onChange={(e) => fire(parseFloat(e.target.value))}
        />
      </div>
    );
  } else if (prop.type === 'int') {
    control = (
      <input
        type="number"
        step={prop.step ?? 1}
        min={prop.min}
        max={prop.max}
        value={Number(localValue ?? 0)}
        onChange={(e) => fire(parseInt(e.target.value, 10))}
      />
    );
  } else if (prop.type === 'bool') {
    control = (
      <input
        type="checkbox"
        checked={!!localValue}
        onChange={(e) => fireImmediate(e.target.checked)}
      />
    );
  } else if (prop.type === 'enum') {
    control = (
      <select value={String(localValue ?? '')} onChange={(e) => fireImmediate(e.target.value)}>
        {(prop.values || []).map((v) => (
          <option key={String(v)} value={String(v)}>
            {String(v)}
          </option>
        ))}
      </select>
    );
  } else if (prop.type === 'string') {
    control = (
      <input
        type="text"
        value={String(localValue ?? '')}
        onChange={(e) => setLocalValue(e.target.value)}
        onBlur={(e) => fireImmediate(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') fireImmediate(e.target.value);
        }}
      />
    );
  } else if (prop.type === 'tuple_float2' || prop.type === 'tuple_float4') {
    const arr = Array.isArray(localValue) ? localValue : [];
    const labels = prop.labels || ['', '', '', ''];
    control = (
      <div className="tuple">
        {arr.map((val, i) => (
          <input
            key={i}
            type="number"
            step={prop.step ?? 0.01}
            min={prop.min}
            max={prop.max}
            value={Number(val)}
            title={labels[i]}
            onChange={(e) => {
              const next = arr.slice();
              next[i] = parseFloat(e.target.value);
              fireImmediate(next);
            }}
          />
        ))}
      </div>
    );
  } else {
    control = <span>{String(localValue)}</span>;
  }

  return (
    <div className="prop">
      <label title={prop.type}>{prop.name}</label>
      {control}
    </div>
  );
}

// "Delete" for an artist is the kind-specific way to make it not appear in the
// figure: clearing the text for Text artists, hiding everything else. The change
// is undo-able via the existing inverse-op stack (Cmd-Z).
function deleteOpsFor(entry) {
  if (!entry) return [];
  if (entry.kind === 'Text') {
    return [{ op: 'set_property', artist_id: entry.id, kind: entry.kind, name: 'text', value: '' }];
  }
  // Axes deletion is the include_in_export workflow — skip a Delete button here.
  if (entry.kind === 'Axes') return [];
  const hasVisible = (entry.properties || []).some((p) => p.name === 'visible');
  if (!hasVisible) return [];
  return [{ op: 'set_property', artist_id: entry.id, kind: entry.kind, name: 'visible', value: false }];
}

function isDeleted(entry) {
  if (!entry) return false;
  if (entry.kind === 'Text') {
    const t = (entry.properties || []).find((p) => p.name === 'text');
    if (t && (t.value || '') === '') return true;
  }
  const v = (entry.properties || []).find((p) => p.name === 'visible');
  return v ? v.value === false : false;
}

function restoreOpsFor(entry) {
  if (!entry) return [];
  if (entry.kind === 'Text') {
    return [{ op: 'set_property', artist_id: entry.id, kind: entry.kind, name: 'text', value: entry.label || ' ' }];
  }
  return [{ op: 'set_property', artist_id: entry.id, kind: entry.kind, name: 'visible', value: true }];
}

function axesIndexFromId(id) {
  // axes_3 → 3; axes_3_line_0 → 3; etc.
  const m = /^axes_(\d+)/.exec(id || '');
  return m ? parseInt(m[1], 10) : null;
}

export default function Inspector({ entry, onApply, onToast, onExtractAxes }) {
  if (!entry) {
    return (
      <aside className="inspector-panel">
        <div className="empty">
          <strong>No artist selected.</strong>
          <p>
            Click anything on the figure (a line, a bar, an axis, a label) to edit its
            properties. Use the left sidebar to pick from the full artist tree.
          </p>
          <div className="shortcuts">
            <kbd>⌘Z</kbd> undo
            <kbd>⌘S</kbd> save
            <kbd>⌘E</kbd> export PDF
          </div>
        </div>
      </aside>
    );
  }
  const deleted = isDeleted(entry);
  const deleteOps = deleteOpsFor(entry);
  const handleDelete = () => {
    deleteOps.forEach((op) => onApply(op));
    if (onToast) onToast(`Deleted ${entry.id} — ⌘Z to undo`);
  };
  const handleRestore = () => {
    restoreOpsFor(entry).forEach((op) => onApply(op));
    if (onToast) onToast(`Restored ${entry.id}`);
  };
  const axesIdx = entry.kind === 'Axes' ? axesIndexFromId(entry.id) : null;
  const handleExtract = () => {
    if (axesIdx == null || !onExtractAxes) return;
    onExtractAxes(axesIdx);
  };
  const hasActions = deleted || deleteOps.length > 0 || axesIdx != null;
  return (
    <aside className="inspector-panel">
      <header>
        <div className="title">{entry.id}</div>
        <div className="kind">{entry.kind} · {entry.label}</div>
        {hasActions && (
          <div className="actions">
            {axesIdx != null && (
              <button
                className="subtle"
                onClick={handleExtract}
                title="Clone this subplot into a brand-new figure in the session"
              >
                ⤴ Extract as new plot
              </button>
            )}
            {deleted ? (
              <button className="subtle" onClick={handleRestore}>↺ Restore</button>
            ) : deleteOps.length > 0 ? (
              <button className="danger" onClick={handleDelete}>✕ Delete</button>
            ) : null}
          </div>
        )}
      </header>
      <div className="prop-grid">
        {entry.properties.map((p) => (
          <PropRow key={p.name} prop={p} entry={entry} onApply={onApply} />
        ))}
      </div>
    </aside>
  );
}
