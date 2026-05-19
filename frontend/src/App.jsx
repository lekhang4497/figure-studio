import React, { useCallback, useEffect, useMemo, useReducer, useRef } from 'react';
import {
  StudioClient,
  downloadUrl,
  exportPdfUrl,
  extractAxes,
  fetchFigureState,
  fetchFigureSvg,
  fetchFigures,
  fetchPresets,
  readFigureFromUrl,
  removeFigure,
  resetSession,
  saveSession,
  writeFigureToUrl,
} from './api.js';
import Canvas from './Canvas.jsx';
import Inspector from './Inspector.jsx';
import Toolbar from './Toolbar.jsx';

/**
 * Multi-figure editor shell.
 *
 * The server holds a registry of named figures; this component tracks the
 * active figure, fetches its state over HTTP, then subscribes to its
 * per-figure WebSocket for live updates. Switching figures reconnects the
 * socket and refetches state.
 */

function makeInverse(op, snapshot) {
  if (op.op === 'set_property') {
    const entry = snapshot.tree.find((e) => e.id === op.artist_id);
    if (!entry) return null;
    const prop = entry.properties.find((p) => p.name === op.name);
    if (!prop) return null;
    return {
      op: 'set_property',
      artist_id: op.artist_id,
      kind: op.kind,
      name: op.name,
      value: prop.value,
    };
  }
  if (op.op === 'set_figure_size') {
    return {
      op: 'set_figure_size',
      width_in: snapshot.figure.width_in,
      height_in: snapshot.figure.height_in,
    };
  }
  if (op.op === 'set_figure_dpi') {
    return { op: 'set_figure_dpi', dpi: snapshot.figure.dpi };
  }
  return null;
}

function FigurePicker({ figures, active, onSelect, onRemove }) {
  if (!figures || figures.length <= 1) return null;
  return (
    <div className="figure-picker">
      <h3>Figures ({figures.length})</h3>
      {figures.map((f) => (
        <div
          key={f.name}
          className={`figure-row ${active === f.name ? 'active' : ''}`}
          onClick={() => onSelect(f.name)}
          title={`${f.name} · ${f.axes_count} axes · ${f.edits} edits`}
        >
          <span className="dot" />
          <span className="name">{f.name}</span>
          <span className="meta">{f.axes_count}ax</span>
          <button
            className="subtle close"
            onClick={(e) => { e.stopPropagation(); onRemove(f.name); }}
            title="Remove this figure from the session"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}

function TreeSidebar({ tree, selectedId, onSelect, figures, activeFigure, onSelectFigure, onRemoveFigure }) {
  const byParent = useMemo(() => {
    const m = new Map();
    for (const entry of tree) {
      const key = entry.parent_id || '__root__';
      const list = m.get(key) || [];
      list.push(entry);
      m.set(key, list);
    }
    return m;
  }, [tree]);

  const renderNode = (entry, depth) => {
    const children = byParent.get(entry.id) || [];
    const isDeleted =
      (entry.properties || []).some(
        (p) => p.name === 'visible' && p.value === false,
      ) ||
      (entry.kind === 'Text' &&
        (entry.properties || []).some((p) => p.name === 'text' && (p.value || '') === ''));
    return (
      <React.Fragment key={entry.id}>
        <div
          className={`tree-row ${selectedId === entry.id ? 'selected' : ''} ${isDeleted ? 'deleted' : ''}`}
          style={{ paddingLeft: 14 + depth * 14 }}
          onClick={() => onSelect(entry.id)}
          title={entry.id}
        >
          <span className="kind">{entry.kind}</span>
          <span className="label">{entry.label}</span>
        </div>
        {children.map((c) => renderNode(c, depth + 1))}
      </React.Fragment>
    );
  };

  const roots = byParent.get('__root__') || [];
  return (
    <nav className="tree-panel">
      <FigurePicker
        figures={figures}
        active={activeFigure}
        onSelect={onSelectFigure}
        onRemove={onRemoveFigure}
      />
      <h3>Artists ({tree.length})</h3>
      {roots.map((r) => renderNode(r, 0))}
    </nav>
  );
}

const initialState = {
  activeFigure: null,
  figures: [],
  snapshot: null,
  presets: [],
  svg: '',
  status: 'connecting',
  selectedId: null,
  undoStack: [],
  toast: null,
};

function reducer(state, action) {
  switch (action.type) {
    case 'ACTIVE_FIGURE':
      return {
        ...state,
        activeFigure: action.name,
        snapshot: null,
        svg: '',
        selectedId: null,
        undoStack: [],
        status: 'connecting',
      };
    case 'FIGURES':
      return { ...state, figures: action.figures };
    case 'STATE':
      return {
        ...state,
        snapshot: action.snapshot === undefined ? state.snapshot : action.snapshot,
        svg: action.svg ? action.svg : state.svg,
        selectedId:
          (action.snapshot && action.snapshot.selected_id) || state.selectedId,
      };
    case 'PRESETS':
      return { ...state, presets: action.presets };
    case 'STATUS':
      return { ...state, status: action.status };
    case 'SELECT':
      return { ...state, selectedId: action.id };
    case 'PUSH_UNDO':
      return { ...state, undoStack: [...state.undoStack, action.op] };
    case 'POP_UNDO':
      return { ...state, undoStack: state.undoStack.slice(0, -1) };
    case 'TOAST':
      return { ...state, toast: action.toast };
    default:
      return state;
  }
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const clientRef = useRef(null);
  const toastTimer = useRef(null);
  const pollTimer = useRef(null);

  const showToast = useCallback((msg, kind = 'ok') => {
    dispatch({ type: 'TOAST', toast: { msg, kind } });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => dispatch({ type: 'TOAST', toast: null }), 2200);
  }, []);

  // ----- Discover figures and pick the active one ---------------------------

  const refreshFigures = useCallback(async () => {
    try {
      const data = await fetchFigures();
      dispatch({ type: 'FIGURES', figures: data.figures || [] });
      return data.figures || [];
    } catch (e) {
      return [];
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const presets = await fetchPresets();
        if (!cancelled) dispatch({ type: 'PRESETS', presets });
      } catch (e) { /* ignore */ }
      const figures = await refreshFigures();
      if (cancelled) return;
      const fromUrl = readFigureFromUrl();
      const pick =
        (fromUrl && figures.find((f) => f.name === fromUrl)?.name) ||
        figures[0]?.name ||
        null;
      if (pick) dispatch({ type: 'ACTIVE_FIGURE', name: pick });
    })();
    // Poll the figures list periodically so newly-pushed figures appear.
    pollTimer.current = setInterval(refreshFigures, 4000);
    return () => {
      cancelled = true;
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, [refreshFigures]);

  // ----- Whenever active figure changes, refetch + reconnect WS -------------

  useEffect(() => {
    if (!state.activeFigure) return;
    writeFigureToUrl(state.activeFigure);
    let cancelled = false;
    // Seed snapshot via HTTP so the canvas paints before the WS opens.
    fetchFigureState(state.activeFigure)
      .then((data) => {
        if (cancelled) return;
        if (data.state) dispatch({ type: 'STATE', snapshot: data.state, svg: '' });
        if (data.presets) dispatch({ type: 'PRESETS', presets: data.presets });
        if (data.figures) dispatch({ type: 'FIGURES', figures: data.figures });
      })
      .catch(() => {});
    fetchFigureSvg(state.activeFigure)
      .then((svg) => {
        if (!cancelled && svg) dispatch({ type: 'STATE', snapshot: undefined, svg });
      })
      .catch(() => {});

    if (!clientRef.current) {
      clientRef.current = new StudioClient({
        figureName: state.activeFigure,
        onStatus: (s) => dispatch({ type: 'STATUS', status: s }),
        onMessage: (msg) => {
          if (msg.type === 'state') {
            dispatch({ type: 'STATE', snapshot: msg.state, svg: msg.svg });
          } else if (msg.type === 'selection') {
            dispatch({ type: 'SELECT', id: msg.selected_id });
          } else if (msg.type === 'error') {
            showToast(msg.message, 'err');
          }
        },
      });
    } else {
      clientRef.current.switchFigure(state.activeFigure);
    }
    return () => {
      cancelled = true;
    };
  }, [state.activeFigure, showToast]);

  // ----- Edit / select / undo ----------------------------------------------

  const apply = useCallback(
    (op, { recordUndo = true } = {}) => {
      if (!clientRef.current) return;
      if (recordUndo && state.snapshot) {
        const inv = makeInverse(op, state.snapshot);
        if (inv) dispatch({ type: 'PUSH_UNDO', op: inv });
      }
      clientRef.current.apply(op);
    },
    [state.snapshot],
  );

  const onSelect = useCallback((id) => {
    dispatch({ type: 'SELECT', id });
    if (clientRef.current) clientRef.current.select(id);
  }, []);

  const undo = useCallback(() => {
    const top = state.undoStack[state.undoStack.length - 1];
    if (!top) return;
    dispatch({ type: 'POP_UNDO' });
    if (clientRef.current) clientRef.current.apply(top);
    showToast('Undone');
  }, [state.undoStack, showToast]);

  const onExtractAxes = useCallback(
    async (axesIndex) => {
      if (!state.activeFigure) return;
      try {
        const result = await extractAxes(state.activeFigure, axesIndex);
        await refreshFigures();
        dispatch({ type: 'ACTIVE_FIGURE', name: result.name });
        showToast(`Extracted to "${result.name}"`);
      } catch (e) {
        showToast(`Extract failed: ${e.message}`, 'err');
      }
    },
    [state.activeFigure, refreshFigures, showToast],
  );

  const onSelectFigure = useCallback((name) => {
    if (name !== state.activeFigure) {
      dispatch({ type: 'ACTIVE_FIGURE', name });
    }
  }, [state.activeFigure]);

  const onRemoveFigure = useCallback(
    async (name) => {
      if (!confirm(`Remove "${name}" from the session?`)) return;
      await removeFigure(name);
      const figs = await refreshFigures();
      if (state.activeFigure === name) {
        const next = figs[0]?.name || null;
        if (next) dispatch({ type: 'ACTIVE_FIGURE', name: next });
        else dispatch({ type: 'ACTIVE_FIGURE', name: null });
      }
      showToast(`Removed "${name}"`);
    },
    [state.activeFigure, refreshFigures, showToast],
  );

  // ----- Keyboard shortcuts -------------------------------------------------

  useEffect(() => {
    const handler = (e) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      const k = e.key.toLowerCase();
      if (k === 'z') {
        e.preventDefault();
        undo();
      } else if (k === 's') {
        e.preventDefault();
        if (state.activeFigure) {
          saveSession(state.activeFigure).then((r) =>
            showToast(`Saved → ${r.path?.split('/').pop() || 'session'}`),
          );
        }
      } else if (k === 'e') {
        e.preventDefault();
        if (state.activeFigure) {
          downloadUrl(exportPdfUrl(state.activeFigure, { pad: 0 }), `${state.activeFigure}.pdf`);
          showToast(`Exported ${state.activeFigure}.pdf`);
        }
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [undo, showToast, state.activeFigure]);

  const selectedEntry = useMemo(() => {
    if (!state.snapshot || !state.selectedId) return null;
    return state.snapshot.tree.find((e) => e.id === state.selectedId) || null;
  }, [state.snapshot, state.selectedId]);

  // ----- Empty states -------------------------------------------------------

  if (!state.activeFigure && state.figures.length === 0) {
    return (
      <div className="app">
        <Toolbar
          snapshot={null}
          presets={state.presets}
          status="idle"
          activeFigure={null}
          figures={state.figures}
          onApply={apply}
          onToast={showToast}
          undoCount={0}
          onUndo={undo}
        />
        <div className="empty" style={{ gridColumn: '1 / -1', padding: '48px 24px', textAlign: 'center' }}>
          <strong>No figures in this session yet.</strong>
          <p style={{ marginTop: 12, color: 'var(--text-secondary)' }}>
            From a Python script or notebook:
          </p>
          <pre style={{
            display: 'inline-block', textAlign: 'left', padding: '12px 16px',
            background: 'var(--bg-secondary)', borderRadius: 8, fontSize: 12,
            color: 'var(--text-primary)', marginTop: 8,
          }}>
{`import matplotlib.pyplot as plt
import figure_studio

fig, ax = plt.subplots()
ax.plot([0,1,2], [1,3,2])

session = figure_studio.connect(port=${location.port || '8765'})
session.add(fig, name="my_plot")`}
          </pre>
        </div>
      </div>
    );
  }

  if (!state.snapshot) {
    return (
      <div className="app">
        <Toolbar
          snapshot={null}
          presets={state.presets}
          status={state.status}
          activeFigure={state.activeFigure}
          figures={state.figures}
          onApply={apply}
          onToast={showToast}
          undoCount={0}
          onUndo={undo}
        />
        <div className="empty" style={{ gridColumn: '1 / -1' }}>
          Loading {state.activeFigure}… ({state.status})
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <Toolbar
        snapshot={state.snapshot}
        presets={state.presets}
        status={state.status}
        activeFigure={state.activeFigure}
        figures={state.figures}
        onApply={apply}
        onToast={showToast}
        undoCount={state.undoStack.length}
        onUndo={undo}
      />
      <TreeSidebar
        tree={state.snapshot.tree}
        selectedId={state.selectedId}
        onSelect={onSelect}
        figures={state.figures}
        activeFigure={state.activeFigure}
        onSelectFigure={onSelectFigure}
        onRemoveFigure={onRemoveFigure}
      />
      <Canvas
        svg={state.svg}
        tree={state.snapshot.tree}
        selectedId={state.selectedId}
        onSelect={onSelect}
        onApply={apply}
        onToast={showToast}
      />
      <Inspector
        entry={selectedEntry}
        onApply={apply}
        onToast={showToast}
        onExtractAxes={onExtractAxes}
      />
      {state.toast && <div className={`toast ${state.toast.kind}`}>{state.toast.msg}</div>}
    </div>
  );
}
