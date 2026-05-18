import React, { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { StudioClient, downloadUrl, fetchInitialState, saveSession } from './api.js';
import Canvas from './Canvas.jsx';
import Inspector from './Inspector.jsx';
import Toolbar from './Toolbar.jsx';

/**
 * Glue. Holds the editor's global state (snapshot, svg, selection, undo stack,
 * connection status, toast). All edits go through `onApply` so we can record the
 * inverse for undo before broadcasting to the backend.
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
  // disable_auto_layout has no clean inverse; skip.
  return null;
}

function TreeSidebar({ tree, selectedId, onSelect }) {
  // Build a parent -> children map; render recursively so BarGroup → bars (and
  // any future container kinds) nest correctly under their parent.
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
          <span
            style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              textDecoration: isDeleted ? 'line-through' : 'none',
            }}
          >
            {entry.label}
          </span>
        </div>
        {children.map((c) => renderNode(c, depth + 1))}
      </React.Fragment>
    );
  };

  const roots = byParent.get('__root__') || [];
  return (
    <nav className="tree-panel">
      <h3>Artists ({tree.length})</h3>
      {roots.map((r) => renderNode(r, 0))}
    </nav>
  );
}

const initialState = {
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
    case 'STATE':
      return {
        ...state,
        // undefined means "leave snapshot alone" (HTTP svg-only update),
        // explicit null clears it.
        snapshot: action.snapshot === undefined ? state.snapshot : action.snapshot,
        // Empty string means "no change" (allowed so HTTP state can land without
        // clobbering an SVG we already have).
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
    case 'POP_UNDO': {
      const next = state.undoStack.slice(0, -1);
      return { ...state, undoStack: next };
    }
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

  const showToast = useCallback((msg, kind = 'ok') => {
    dispatch({ type: 'TOAST', toast: { msg, kind } });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => dispatch({ type: 'TOAST', toast: null }), 2200);
  }, []);

  // Populate snapshot + presets via HTTP immediately so the UI has something to
  // show even before the WebSocket opens. Also fetch the current SVG so the
  // canvas is non-blank from the first paint. The WebSocket then takes over
  // for live edits.
  useEffect(() => {
    let cancelled = false;
    fetchInitialState()
      .then((data) => {
        if (cancelled) return;
        dispatch({ type: 'PRESETS', presets: data.presets });
        if (data.state) {
          dispatch({ type: 'STATE', snapshot: data.state, svg: '' });
        }
      })
      .catch(() => {});
    fetch('/api/figure.svg')
      .then((r) => r.text())
      .then((svg) => {
        if (!cancelled && svg) {
          dispatch({ type: 'STATE', snapshot: undefined, svg });
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // Open the WebSocket and route messages into the reducer.
  useEffect(() => {
    const client = new StudioClient({
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
    clientRef.current = client;
    return () => client.close();
  }, [showToast]);

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

  const onSelect = useCallback(
    (id) => {
      dispatch({ type: 'SELECT', id });
      if (clientRef.current) clientRef.current.select(id);
    },
    [],
  );

  const undo = useCallback(() => {
    const top = state.undoStack[state.undoStack.length - 1];
    if (!top) return;
    dispatch({ type: 'POP_UNDO' });
    if (clientRef.current) clientRef.current.apply(top);
    showToast('Undone');
  }, [state.undoStack, showToast]);

  // Keyboard shortcuts: Cmd/Ctrl-Z, Cmd/Ctrl-S, Cmd/Ctrl-E.
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
        saveSession().then((r) => showToast(`Saved → ${r.path?.split('/').pop() || 'session'}`));
      } else if (k === 'e') {
        e.preventDefault();
        downloadUrl('/api/export/pdf', 'figure.pdf');
        showToast('Exported figure.pdf');
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [undo, showToast]);

  const selectedEntry = useMemo(() => {
    if (!state.snapshot || !state.selectedId) return null;
    return state.snapshot.tree.find((e) => e.id === state.selectedId) || null;
  }, [state.snapshot, state.selectedId]);

  if (!state.snapshot) {
    return (
      <div className="app">
        <Toolbar
          snapshot={null}
          presets={state.presets}
          status={state.status}
          onApply={apply}
          onToast={showToast}
          undoCount={state.undoStack.length}
          onUndo={undo}
        />
        <div className="empty" style={{ gridColumn: '1 / -1' }}>
          Waiting for backend… ({state.status})
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
        onApply={apply}
        onToast={showToast}
        undoCount={state.undoStack.length}
        onUndo={undo}
      />
      <TreeSidebar tree={state.snapshot.tree} selectedId={state.selectedId} onSelect={onSelect} />
      <Canvas
        svg={state.svg}
        tree={state.snapshot.tree}
        selectedId={state.selectedId}
        onSelect={onSelect}
        onApply={apply}
        onToast={showToast}
      />
      <Inspector entry={selectedEntry} onApply={apply} onToast={showToast} />
      {state.toast && <div className={`toast ${state.toast.kind}`}>{state.toast.msg}</div>}
    </div>
  );
}
