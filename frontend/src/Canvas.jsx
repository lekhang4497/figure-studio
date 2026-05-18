import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

/**
 * Renders the figure SVG inline so we can hit-test artists by [id], overlay a
 * selection rectangle, and drag axes positions in figure-fraction coords.
 *
 * The component never owns selected_id — that comes from props so the rest of
 * the UI (inspector, sidebar) stays in lockstep.
 */

const KNOWN_ID_RE = /^(axes_\d+(_(line|scatter|bar|text|title|xlabel_text|ylabel_text|legend)(_\d+)?)?|fig_(text|legend)_\d+)$/;

function closestArtistId(target, root) {
  let el = target;
  while (el && el !== root) {
    const id = el.getAttribute && el.getAttribute('id');
    if (id && KNOWN_ID_RE.test(id)) return id;
    el = el.parentNode;
  }
  return null;
}

function findEntry(tree, id) {
  if (!id) return null;
  return tree.find((e) => e.id === id) || null;
}

function readProp(entry, name) {
  if (!entry) return undefined;
  const p = entry.properties.find((p) => p.name === name);
  return p ? p.value : undefined;
}

function svgPixelRect(svgRoot) {
  return svgRoot.getBoundingClientRect();
}

/** Convert axes position [x,y,w,h] (fig fraction) to a CSS rect *relative to the wrap*. */
function axesOverlayRect(svgEl, wrapEl, position) {
  if (!svgEl || !wrapEl || !position) return null;
  const svgR = svgEl.getBoundingClientRect();
  const wrapR = wrapEl.getBoundingClientRect();
  const [x, y, w, h] = position;
  const left = svgR.left - wrapR.left + x * svgR.width;
  const top = svgR.top - wrapR.top + (1.0 - y - h) * svgR.height;
  return { left, top, width: w * svgR.width, height: h * svgR.height };
}

export default function Canvas({ svg, tree, selectedId, onSelect, onApply, onToast }) {
  const wrapRef = useRef(null);
  const svgWrapRef = useRef(null);
  const [_resizeTick, setResizeTick] = useState(0);
  const [dragInfo, setDragInfo] = useState(null);
  // Drag preview overrides the position from the inspector tree while the user is dragging.
  const [previewPos, setPreviewPos] = useState(null);

  // Inline the SVG into the page. Keep matplotlib's intrinsic width/height in pt
  // (they give the SVG a real box) and let CSS scale it responsively. Strip the
  // XML prologue + DOCTYPE — they're noise once we're inside an HTML document.
  const processedSvg = useMemo(() => {
    if (!svg) return '';
    let s = svg.replace(/^\s*<\?xml[^?]*\?>\s*/, '').replace(/^\s*<!DOCTYPE[^>]*>\s*/, '');
    s = s.replace(/<svg\b([^>]*)>/, (m, attrs) => {
      const hasAR = /preserveAspectRatio=/.test(attrs);
      const extraAR = hasAR ? '' : ' preserveAspectRatio="xMidYMid meet"';
      return `<svg${attrs}${extraAR} style="display:block;max-width:100%;height:auto;">`;
    });
    return s;
  }, [svg]);

  // Re-run overlay positioning on resize so the selection rect tracks the SVG.
  useEffect(() => {
    const obs = new ResizeObserver(() => setResizeTick((t) => t + 1));
    if (wrapRef.current) obs.observe(wrapRef.current);
    return () => obs.disconnect();
  }, []);

  const handleClick = useCallback(
    (e) => {
      const root = svgWrapRef.current;
      if (!root) return;
      const id = closestArtistId(e.target, root);
      if (!id) return;
      // If the click landed on an artist whose parent is a container (e.g.
      // BarGroup), prefer the container so edits affect the whole group.
      // Shift-click keeps the original single-artist selection.
      const entry = tree && tree.find((t) => t.id === id);
      if (entry && entry.parent_id && !e.shiftKey) {
        const parent = tree.find((t) => t.id === entry.parent_id);
        if (parent && parent.kind === 'BarGroup') {
          onSelect(parent.id);
          return;
        }
      }
      onSelect(id);
    },
    [onSelect, tree],
  );

  // Selected entry / its overlay rect
  const selectedEntry = findEntry(tree, selectedId);
  const selectedAxesId = selectedEntry
    ? selectedEntry.kind === 'Axes'
      ? selectedEntry.id
      : selectedEntry.parent_id
    : null;
  const axesEntry = findEntry(tree, selectedAxesId);

  const axesPos = previewPos || readProp(axesEntry, 'position');
  const axesRect =
    axesEntry && axesPos
      ? axesOverlayRect(svgWrapRef.current?.querySelector('svg'), wrapRef.current, axesPos)
      : null;

  // ---------------------------------------------------------------- drag
  const startDrag = useCallback(
    (mode, e) => {
      if (!axesEntry) return;
      e.preventDefault();
      e.stopPropagation();
      const svgEl = svgWrapRef.current?.querySelector('svg');
      if (!svgEl) return;
      const svgR = svgPixelRect(svgEl);
      const startPos = readProp(axesEntry, 'position') || [0, 0, 1, 1];
      setDragInfo({
        mode,
        startX: e.clientX,
        startY: e.clientY,
        startPos,
        svgW: svgR.width,
        svgH: svgR.height,
      });
      setPreviewPos(startPos);
    },
    [axesEntry],
  );

  useEffect(() => {
    if (!dragInfo) return undefined;
    const onMove = (e) => {
      const dx = (e.clientX - dragInfo.startX) / dragInfo.svgW;
      const dy = -(e.clientY - dragInfo.startY) / dragInfo.svgH; // svg y is inverted vs fig
      const [x0, y0, w0, h0] = dragInfo.startPos;
      const snap = e.shiftKey ? 0.05 : 0.0;
      let next;
      const snapTo = (v) => (snap > 0 ? Math.round(v / snap) * snap : v);
      if (dragInfo.mode === 'move') {
        next = [snapTo(x0 + dx), snapTo(y0 + dy), w0, h0];
      } else if (dragInfo.mode === 'resize-tr') {
        next = [x0, snapTo(y0 + dy), Math.max(0.05, snapTo(w0 + dx)), Math.max(0.05, snapTo(h0 - dy))];
      } else if (dragInfo.mode === 'resize-br') {
        next = [x0, y0, Math.max(0.05, snapTo(w0 + dx)), Math.max(0.05, snapTo(h0 - dy))];
        next[1] = snapTo(y0 + dy); // bottom-right keeps left fixed
      } else if (dragInfo.mode === 'resize-tl') {
        next = [snapTo(x0 + dx), snapTo(y0 + dy), Math.max(0.05, snapTo(w0 - dx)), Math.max(0.05, snapTo(h0 - dy))];
      } else if (dragInfo.mode === 'resize-bl') {
        next = [snapTo(x0 + dx), snapTo(y0 + dy), Math.max(0.05, snapTo(w0 - dx)), Math.max(0.05, snapTo(h0 + dy))];
      } else {
        next = dragInfo.startPos;
      }
      // Clamp to [0,1].
      next = next.map((v, i) => (i < 2 ? Math.max(0, Math.min(1 - 0.05, v)) : Math.max(0.05, Math.min(1, v))));
      setPreviewPos(next);
    };
    const onUp = () => {
      if (previewPos && axesEntry) {
        // First time we drag, also disable auto-layout — that fight between manual
        // positions and constrained_layout shows up as positions snapping back.
        onApply({ op: 'disable_auto_layout' });
        onApply({
          op: 'set_property',
          artist_id: axesEntry.id,
          kind: 'Axes',
          name: 'position',
          value: previewPos.map((v) => Number(v.toFixed(4))),
        });
      }
      setDragInfo(null);
      setPreviewPos(null);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [dragInfo, previewPos, axesEntry, onApply]);

  // Repaint overlay each frame while dragging, in case getBoundingClientRect changes.
  useLayoutEffect(() => {
    if (dragInfo) setResizeTick((t) => t + 1);
  }, [dragInfo, previewPos]);

  return (
    <div className="canvas-panel">
      <div className="canvas-wrap" ref={wrapRef}>
        <div
          ref={svgWrapRef}
          onClick={handleClick}
          dangerouslySetInnerHTML={{ __html: processedSvg }}
        />
        {axesRect && axesEntry && (
          <>
            <div
              className="selection-outline"
              style={{
                left: axesRect.left,
                top: axesRect.top,
                width: axesRect.width,
                height: axesRect.height,
              }}
            />
            {/* drag handles only when an Axes is selected. Resize from corners, move from center. */}
            {selectedEntry?.kind === 'Axes' && (
              <>
                <div
                  className={`drag-handle ${dragInfo?.mode === 'move' ? 'active' : ''}`}
                  title="Drag to move (Shift = snap)"
                  style={{
                    left: axesRect.left + axesRect.width / 2 - 5,
                    top: axesRect.top + axesRect.height / 2 - 5,
                    width: 14,
                    height: 14,
                    borderRadius: '50%',
                  }}
                  onPointerDown={(e) => startDrag('move', e)}
                />
                <div
                  className="drag-handle"
                  title="Resize"
                  style={{ left: axesRect.left - 5, top: axesRect.top - 5 }}
                  onPointerDown={(e) => startDrag('resize-tl', e)}
                />
                <div
                  className="drag-handle"
                  title="Resize"
                  style={{ left: axesRect.left + axesRect.width - 5, top: axesRect.top - 5 }}
                  onPointerDown={(e) => startDrag('resize-tr', e)}
                />
                <div
                  className="drag-handle"
                  title="Resize"
                  style={{ left: axesRect.left - 5, top: axesRect.top + axesRect.height - 5 }}
                  onPointerDown={(e) => startDrag('resize-bl', e)}
                />
                <div
                  className="drag-handle"
                  title="Resize"
                  style={{
                    left: axesRect.left + axesRect.width - 5,
                    top: axesRect.top + axesRect.height - 5,
                  }}
                  onPointerDown={(e) => startDrag('resize-br', e)}
                />
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
