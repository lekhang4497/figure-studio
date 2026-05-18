# figure-studio — Project Plan

A local web UI for visually editing matplotlib figures and exporting both PDF and reproducible Python code. Built for researchers preparing paper figures.

## 1. Product spec (what we are building)

**The user flow:**
1. User writes normal matplotlib code that creates a `Figure`.
2. User wraps the final `plt.show()` (or equivalent) with `figure_studio.launch(fig)`.
3. Running the script opens `http://localhost:PORT` in the browser.
4. The user sees the figure rendered and an inspector panel on the side.
5. User can:
   - Click any element (axes, line, scatter, bar, text, legend) → see editable properties.
   - Drag subplots, legends, text annotations to reposition.
   - Resize subplots by dragging handles.
   - Edit colors, linewidths, markersize, barwidth, fontsize, legend location, axis labels, ticks, limits, log/linear scale, grid on/off.
   - Change overall figure size (with presets for common LaTeX column widths: ACL/NeurIPS/IEEE single/double column).
   - Toggle visibility of individual subplots (useful for "main paper vs appendix" decisions).
6. User clicks **Export PDF** → downloads `figure.pdf` (vector, fonts embedded).
7. User clicks **Export Code** → downloads a self-contained `figure.py` that reproduces the styled figure with pure matplotlib (no `figure_studio` dependency at runtime).
8. User clicks **Save Session** → writes a `.figure_studio.json` next to their script so reopening picks up where they left off.

**Explicit non-goals (cut to keep scope sane):**
- Not a general plotting tool — input must already be a matplotlib `Figure`.
- Not multi-user / not deployed — strictly localhost, single session.
- Not real-time collaborative editing.
- Not a replacement for Illustrator-level vector editing (no path editing, no gradients beyond what matplotlib supports).
- v1 supports `Line2D`, `PathCollection` (scatter), `Rectangle` patches (bar charts), `Text`, `Legend`, `Axes`. Not heatmaps, 3D, contours — those come in v2.

## 2. Architecture decisions

**Backend: Python + FastAPI + WebSockets.**
- FastAPI serves the static frontend and a small REST + WS API.
- WebSocket carries edit events from frontend to backend; backend mutates the live `Figure` object, re-renders, sends back PNG (for preview) or SVG (for richer interactions).
- Keep the `Figure` object alive in memory — do not pickle/unpickle on every edit. State of truth is the live matplotlib Figure plus a sidecar dict of "edits applied" used for code generation.

**Frontend: plain HTML + vanilla JS or lightweight React (Vite).**
- Recommend Vite + React for the inspector panel because it has many small controlled inputs and React handles that better than vanilla. But the canvas/preview is just an `<img>` or `<svg>` element — no Canvas/WebGL.
- Drag interactions: use pointer events on the SVG. When the user drops, send `{element_id, new_position}` to backend, which calls `set_position` on the matplotlib artist and re-renders.

**Rendering strategy: SVG preview + PNG fallback.**
- Use matplotlib's SVG backend for the live preview. SVG gives us per-artist `id` attributes (via `set_gid` on artists) so the frontend can map clicks to artists.
- On every edit, backend re-renders SVG and sends it over the WebSocket. SVGs of typical paper figures are <500KB; this is fast enough for snappy feedback.
- Final export uses matplotlib's PDF backend, not converted from SVG.

**Artist identification: stable IDs.**
- On first load, walk the figure tree (`fig.get_children()` recursively) and assign each editable artist a stable ID like `axes_0_line_2`. Store in a `dict[str, Artist]`.
- Use `artist.set_gid(id)` so the SVG output carries the ID. Frontend reads `<g id="axes_0_line_2">` and uses it for click targeting.

**Code generation: replay log, not AST rewriting.**
- Do NOT try to parse and modify the user's original Python file. That's pylustrator's approach and it's fragile.
- Instead: maintain an ordered list of edit operations (`[("set_color", "axes_0_line_2", "#ff0000"), ("set_position", "axes_0", [0.1, 0.1, 0.8, 0.8]), ...]`).
- On code export, emit a Python file that:
  1. Has a placeholder `# === Your original figure code goes here ===` block (user pastes their code in, or we copy it from the source file if available).
  2. Appends a `def _apply_figure_studio_edits(fig):` function that walks the figure, finds artists by the same ID scheme, and applies each operation.
  3. Calls `_apply_figure_studio_edits(fig)` before `plt.savefig(...)`.
- This is robust: the user can change their plotting code freely; as long as the artist tree shape is similar, the edits replay correctly.

**Session persistence: JSON sidecar.**
- `<script_name>.figure_studio.json` next to the user's script.
- Contains: edit operations list, figure size, last-selected element, panel collapse state.
- Auto-saved on every edit (debounced 500ms) and on clean shutdown.

## 3. Repository layout

```
figure-studio/
├── pyproject.toml
├── README.md
├── src/figure_studio/
│   ├── __init__.py          # exports launch()
│   ├── server.py            # FastAPI app
│   ├── figure_state.py      # holds the Figure, applies edits, generates IDs
│   ├── artist_introspect.py # walks figure tree, extracts editable properties
│   ├── edit_ops.py          # operation classes: SetColor, SetPosition, etc.
│   ├── code_gen.py          # emits standalone Python from edit log
│   ├── presets.py           # LaTeX column-width presets (ACL, NeurIPS, IEEE, etc.)
│   └── static/              # built frontend (committed for pip install)
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── App.jsx
│       ├── Canvas.jsx       # SVG preview + click/drag handlers
│       ├── Inspector.jsx    # property editor for selected artist
│       ├── Toolbar.jsx      # figure size, export buttons, presets
│       └── api.js           # WebSocket client
└── tests/
    ├── test_artist_introspect.py
    ├── test_edit_ops.py
    ├── test_code_gen.py
    └── fixtures/
        └── example_figures.py
```

## 4. Build phases (this is the order for the coding agent)

### Phase 0: Skeleton (half day)
- [ ] `pyproject.toml` with deps: `matplotlib>=3.7`, `fastapi`, `uvicorn`, `websockets`, `pydantic>=2`.
- [ ] Empty FastAPI app that serves a "Hello figure-studio" page on `localhost:8765`.
- [ ] `figure_studio.launch(fig)` function: starts uvicorn in a thread, opens browser via `webbrowser.open()`, blocks until Ctrl+C.

### Phase 1: Read-only preview (1 day)
- [ ] `artist_introspect.walk(fig)` → returns list of `(id, artist, type, properties)`.
- [ ] On `launch()`, assign GIDs to all editable artists.
- [ ] Render figure to SVG via `fig.savefig(buf, format='svg')`; serve at `/figure.svg`.
- [ ] Frontend shows the SVG and lists all artist IDs in a sidebar.
- [ ] Test: load a 2x2 subplot figure, confirm every line/scatter/bar appears in the sidebar.

### Phase 2: Click-to-select + property editing (1-2 days)
- [ ] Frontend: clicking an SVG element looks up its `id` and tells backend "selected".
- [ ] Backend returns the artist's editable properties as JSON (color, linewidth, alpha, label, etc.) — use a hand-written per-artist-type schema (Line2D, PathCollection, Rectangle, Text, Legend, Axes). Avoid auto-introspecting all matplotlib properties; the list is too noisy.
- [ ] Inspector renders the right input for each property: color picker for colors, number input for sizes, dropdown for line styles, etc.
- [ ] Edit → WebSocket message → backend applies via `artist.set_*()` → re-renders SVG → pushes back to frontend.
- [ ] **Acceptance test:** change a line color, see it update in <300ms.

### Phase 3: Drag positioning (1 day)
- [ ] On selecting an Axes, Legend, or Text artist, show drag handles in the SVG overlay (rendered by frontend, not matplotlib).
- [ ] Dragging sends `set_position` for axes (figure-fraction coords), `set_bbox_to_anchor` for legends, `set_position` for text.
- [ ] Snap-to-grid optional (hold Shift to snap to 0.05 increments).

### Phase 4: Figure-level controls + LaTeX presets (half day)
- [ ] Toolbar with figure width/height inputs.
- [ ] Preset dropdown: "ACL single column (3.25in)", "ACL double column (6.75in)", "NeurIPS (5.5in)", "IEEE single column (3.5in)", "IEEE double column (7.16in)". Verify these against current style guides at build time.
- [ ] DPI control for preview (export DPI is separate).

### Phase 5: Export (1 day)
- [ ] PDF export: `fig.savefig('figure.pdf', bbox_inches='tight', backend='pdf')` with `pdf.fonttype=42` and `ps.fonttype=42` for embedded fonts.
- [ ] Code export: implement `code_gen.emit(edit_log) -> str`. The output should pass `python -c "exec(open('figure.py').read())"` standalone.
- [ ] PNG export at user-chosen DPI as a bonus.

### Phase 6: Session save/restore (half day)
- [ ] Auto-save edit log to `<script>.figure_studio.json` every 500ms (debounced).
- [ ] On `launch()`, if sidecar exists, replay the edit log before showing UI.
- [ ] Manual "Clear all edits" button.

### Phase 7: Polish & ship (1-2 days)
- [ ] Keyboard shortcuts: Cmd-Z undo (maintain undo stack of edit ops), Cmd-S save, Cmd-E export PDF.
- [ ] Error toasts when an edit fails (e.g., invalid color string).
- [ ] README with GIF demo.
- [ ] `pip install figure-studio` works — frontend is pre-built and committed under `src/figure_studio/static/`.

## 5. Critical design details the agent must get right

**Subplot visibility for "main vs appendix."** Add a per-axes "include in export" toggle. When OFF, the export logic should produce a *separate* PDF with only the visible axes, repositioned to fill the figure. Implement this as a code-gen-time transformation, not by mutating the live figure. This is the killer feature for your workflow — make sure it's not an afterthought.

**Edit operations must be idempotent and order-independent where possible.** `set_color` is fine. `set_position` is fine if absolute. Avoid relative ops like "move by dx, dy" because replay becomes order-dependent. When the user drags, compute the final absolute position and store that.

**Don't try to support every matplotlib property in v1.** The supported-property schema lives in `artist_introspect.py` as explicit dicts:
```python
LINE2D_PROPERTIES = {
    "color": {"type": "color", "getter": "get_color", "setter": "set_color"},
    "linewidth": {"type": "float", "min": 0.1, "max": 10, "getter": "get_linewidth", "setter": "set_linewidth"},
    "linestyle": {"type": "enum", "values": ["-", "--", "-.", ":"], ...},
    "alpha": {"type": "float", "min": 0, "max": 1, ...},
    "marker": {"type": "enum", "values": [...], ...},
    "markersize": {"type": "float", ...},
    "label": {"type": "string", ...},
}
```
Adding new editable properties is then a one-line change. This is the right abstraction — push back on the agent if it tries to do something cleverer (e.g., reflection on matplotlib's `Artist.properties()`).

**SVG IDs survive re-renders.** Matplotlib re-creates artists on some operations (e.g., `legend()` called twice). Re-assign GIDs after every edit, using the same walk order, so IDs stay stable across re-renders. Test this explicitly.

**Threading.** Matplotlib is not thread-safe. Run all `Figure` mutations on a single asyncio task (use `asyncio.Lock` around the figure). The uvicorn server runs in a thread, but figure edits are serialized.

**Don't auto-open a browser in headless environments.** Check `DISPLAY` / `os.environ.get("SSH_CONNECTION")` and just print the URL if remote.

## 6. Testing strategy

- Snapshot tests on `code_gen.emit()`: given a known edit log, the output Python file matches a golden file.
- For each `Artist` type, a fixture figure + a test that introspect returns the expected property list.
- One end-to-end test using `httpx` + `websockets` test client that simulates: launch → select line → change color → export PDF → assert PDF has the right color (use `pdfplumber` or just check the file is valid and non-empty).
- No Selenium / browser automation in v1 — too slow for what it catches.

## 7. Open questions to resolve in week 1

1. **How does the user invoke it from a notebook (not a script)?** First option: `figure_studio.launch(fig)` in a cell blocks the kernel — acceptable, since they're editing. Second option: a Jupyter widget that embeds the iframe. Defer the widget to v2.
2. **What if their figure uses `constrained_layout` or `tight_layout`?** Both reflow on every render and will fight with manual position edits. Decision: on first edit to any axes position, disable both layout managers and emit a warning. Document this clearly.
3. **Pickle-based save vs JSON edit log?** JSON wins because edits replay onto a freshly-generated figure, surviving changes to the user's plotting code. Confirmed.

## 8. What success looks like

After ~6-8 focused days of agent work, you should be able to:
- Run `pip install figure-studio` (or `pip install -e .` from local clone).
- Add `import figure_studio; figure_studio.launch(fig)` to any of your existing paper figure scripts.
- Visually tune the figure in the browser in <5 minutes.
- Export both `figure.pdf` (ready for LaTeX) and `figure_styled.py` (reproducible).
- Close the browser, never need figure_studio again to regenerate the figure.

If after this the only thing missing is a feature you actually need (heatmaps, twin axes, broken axes), the architecture supports adding it without a rewrite. If the architecture itself is fighting you at this point, stop and reassess before adding more features.
