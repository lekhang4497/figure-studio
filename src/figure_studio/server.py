"""FastAPI app: serves the built frontend and an edit-stream WebSocket.

The server is **multi-figure**: a single instance holds a registry of named
``FigureState`` objects. Each figure has its own WebSocket and edit log so
multiple charts can be edited side-by-side without contention. The legacy
``launch(fig)`` entry point simply registers one figure under the name
``default``.
"""
from __future__ import annotations

import asyncio
import importlib.resources as importlib_resources
import io
import json
import logging
import mimetypes
import pickle
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from . import artist_introspect, palettes, presets
from .code_gen import emit_for_session
from .edit_ops import parse as parse_op
from .figure_state import FigureState

logger = logging.getLogger("figure_studio")


DEFAULT_FIGURE_NAME = "default"


# ---------------------------------------------------------------------------
# Static asset resolution
# ---------------------------------------------------------------------------


def _static_dir() -> Path:
    try:
        ref = importlib_resources.files("figure_studio").joinpath("static")
        return Path(str(ref))
    except Exception:
        return Path(__file__).parent / "static"


_FALLBACK_INDEX = """<!doctype html>
<html><head><meta charset="utf-8"><title>figure-studio (frontend not built)</title></head>
<body style="font-family: ui-sans-serif, system-ui; padding: 2rem; line-height: 1.5;">
<h1>figure-studio backend is running</h1>
<p>The frontend bundle is missing. Build it with:</p>
<pre>cd frontend && npm install && npm run build</pre>
<p>Backend endpoints:</p>
<ul>
  <li><a href="/api/figures">/api/figures</a> &mdash; figure registry</li>
</ul>
</body></html>"""


# ---------------------------------------------------------------------------
# Connection broadcasting (per-figure)
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Tracks open WebSockets for a single figure; broadcasts state changes."""

    def __init__(self) -> None:
        self._sockets: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._sockets.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._sockets.discard(ws)

    async def broadcast_json(self, message: dict) -> None:
        async with self._lock:
            sockets = list(self._sockets)
        dead: List[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._sockets.discard(ws)


# ---------------------------------------------------------------------------
# Figure registry
# ---------------------------------------------------------------------------


_NAME_RE = re.compile(r"^[A-Za-z0-9_\-.]{1,64}$")


def _validate_name(name: str) -> str:
    if not _NAME_RE.match(name):
        raise HTTPException(
            422,
            detail=f"Figure name {name!r} is invalid. Use letters, digits, '_', '-', '.' (max 64 chars).",
        )
    return name


class FigureRegistry:
    """Holds the live ``FigureState`` objects keyed by name plus their connections."""

    def __init__(self) -> None:
        self._figures: Dict[str, FigureState] = {}
        self._managers: Dict[str, ConnectionManager] = {}
        self._created_at: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    def names(self) -> List[str]:
        return sorted(self._figures.keys())

    def has(self, name: str) -> bool:
        return name in self._figures

    def get(self, name: str) -> FigureState:
        if name not in self._figures:
            raise HTTPException(404, detail=f"Figure {name!r} not found.")
        return self._figures[name]

    def manager(self, name: str) -> ConnectionManager:
        if name not in self._managers:
            self._managers[name] = ConnectionManager()
        return self._managers[name]

    async def add(self, name: str, state: FigureState, *, overwrite: bool = True) -> str:
        async with self._lock:
            if not overwrite and name in self._figures:
                raise HTTPException(409, detail=f"Figure {name!r} already exists.")
            self._figures[name] = state
            self._created_at[name] = time.time()
            # Reset connections — old clients listening to the old figure under
            # this name should re-sync from the new live one.
            self._managers[name] = ConnectionManager()
        return name

    async def remove(self, name: str) -> bool:
        async with self._lock:
            existed = name in self._figures
            self._figures.pop(name, None)
            self._created_at.pop(name, None)
            self._managers.pop(name, None)
            return existed

    def meta(self) -> List[Dict[str, Any]]:
        out = []
        for name in self.names():
            state = self._figures[name]
            w, h = state.fig.get_size_inches()
            out.append(
                {
                    "name": name,
                    "edits": len(state.log),
                    "width_in": float(w),
                    "height_in": float(h),
                    "axes_count": len(state.fig.axes),
                    "created_at": self._created_at.get(name),
                }
            )
        return out


# ---------------------------------------------------------------------------
# Pickle helpers (figure transfer)
# ---------------------------------------------------------------------------


def _unpickle_figure(data: bytes):
    """Deserialise a matplotlib Figure and attach an Agg canvas so it can render."""
    import matplotlib
    matplotlib.use("Agg")  # ensure a non-interactive backend on the server
    fig = pickle.loads(data)
    try:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        FigureCanvasAgg(fig)
    except Exception:
        pass
    return fig


def _pickle_figure(fig) -> bytes:
    return pickle.dumps(fig)


# ---------------------------------------------------------------------------
# Combine multiple figures into a grid
# ---------------------------------------------------------------------------


def _copy_axes_content(src_ax, dest_ax) -> None:
    """Re-create the visible artists of ``src_ax`` inside ``dest_ax``.

    Direct axes-transplant (``ax.figure = new_fig``) fails because matplotlib
    caches transforms against the source figure's dpi/size. Instead we replay
    each artist via the high-level constructors (.plot, .scatter, .bar) so
    the dest axes computes its own transforms cleanly.

    Handles Line2D, PathCollection (scatter), BarContainer (preserves the
    container so figure-studio's BarGroup editing still works), lone
    Rectangles, Text, plus titles/labels/lims/scale/grid/facecolor/legend.
    More exotic artists (twinx axes, 3D, contours, custom collections) are
    not preserved.
    """
    from matplotlib.container import BarContainer
    from matplotlib.patches import Rectangle

    for line in src_ax.lines:
        dest_ax.plot(
            line.get_xdata(), line.get_ydata(),
            color=line.get_color(), linewidth=line.get_linewidth(),
            linestyle=line.get_linestyle(),
            marker=line.get_marker(), markersize=line.get_markersize(),
            alpha=line.get_alpha(), label=line.get_label(),
        )

    for coll in src_ax.collections:
        try:
            offsets = coll.get_offsets()
            if len(offsets) == 0:
                continue
            sizes = coll.get_sizes()
            fc = coll.get_facecolor()
            ec = coll.get_edgecolor()
            dest_ax.scatter(
                offsets[:, 0], offsets[:, 1],
                s=list(sizes) if len(sizes) > 0 else 36,
                c=list(fc) if len(fc) > 0 else None,
                edgecolors=list(ec) if len(ec) > 0 else None,
                alpha=coll.get_alpha(),
                label=coll.get_label(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not copy collection: %s", exc)

    consumed = set()
    for container in getattr(src_ax, "containers", []) or []:
        if not isinstance(container, BarContainer):
            continue
        xs, heights, widths, bottoms, colors = [], [], [], [], []
        for patch in container.patches:
            x, y = patch.get_xy()
            xs.append(x + patch.get_width() / 2)
            heights.append(patch.get_height())
            widths.append(patch.get_width())
            bottoms.append(y)
            colors.append(patch.get_facecolor())
            consumed.add(id(patch))
        if xs:
            label = container.get_label() if hasattr(container, "get_label") else None
            label = None if (label or "").startswith("_") else label
            dest_ax.bar(xs, heights, width=widths, bottom=bottoms, color=colors, label=label)

    for patch in src_ax.patches:
        if id(patch) in consumed:
            continue
        if patch is getattr(src_ax, "patch", None):
            continue
        if isinstance(patch, Rectangle):
            x, y = patch.get_xy()
            dest_ax.add_patch(Rectangle(
                (x, y), patch.get_width(), patch.get_height(),
                facecolor=patch.get_facecolor(),
                edgecolor=patch.get_edgecolor(),
                linewidth=patch.get_linewidth(),
                alpha=patch.get_alpha(),
            ))

    for text in src_ax.texts:
        dest_ax.text(
            *text.get_position(),
            text.get_text(),
            color=text.get_color(), fontsize=text.get_fontsize(),
            ha=text.get_horizontalalignment(), va=text.get_verticalalignment(),
            rotation=text.get_rotation(), alpha=text.get_alpha(),
        )

    if src_ax.get_title():
        dest_ax.set_title(src_ax.get_title())
    if src_ax.get_xlabel():
        dest_ax.set_xlabel(src_ax.get_xlabel())
    if src_ax.get_ylabel():
        dest_ax.set_ylabel(src_ax.get_ylabel())
    try: dest_ax.set_xlim(src_ax.get_xlim())
    except Exception: pass
    try: dest_ax.set_ylim(src_ax.get_ylim())
    except Exception: pass
    try: dest_ax.set_xscale(src_ax.get_xscale())
    except Exception: pass
    try: dest_ax.set_yscale(src_ax.get_yscale())
    except Exception: pass
    try:
        if any(g.get_visible() for g in src_ax.xaxis.get_gridlines()):
            dest_ax.grid(True)
    except Exception:
        pass
    try: dest_ax.set_facecolor(src_ax.get_facecolor())
    except Exception: pass
    if src_ax.get_legend() is not None:
        dest_ax.legend()


def _combine_figures(source_figs: List[Any], rows: int, cols: int):
    """Build a new ``Figure`` with a ``rows × cols`` subplot grid; copy each
    source figure's first axes into the matching cell.

    Uses ``plt.subplots`` for the grid so matplotlib handles positioning
    correctly, then replays each source axes' visible artists via
    :func:`_copy_axes_content`. Empty cells (when ``len(sources) < rows*cols``)
    are hidden via ``axis('off')``.

    The result is a fully editable matplotlib Figure — clicking lines/bars
    in figure-studio works exactly as on any other figure.
    """
    if rows < 1 or cols < 1:
        raise HTTPException(422, detail="rows and cols must be >= 1")
    if not source_figs:
        raise HTTPException(422, detail="combine requires at least one source figure")

    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    n_cells = rows * cols
    avg_w = sum(s.get_size_inches()[0] for s in source_figs) / len(source_figs)
    avg_h = sum(s.get_size_inches()[1] for s in source_figs) / len(source_figs)
    new_fig, dest_axes = plt.subplots(rows, cols, figsize=(avg_w * cols, avg_h * rows))
    FigureCanvasAgg(new_fig)
    flat = list(np.atleast_1d(dest_axes).flatten())

    for i, src_fig in enumerate(source_figs[:n_cells]):
        if not src_fig.axes:
            flat[i].axis("off")
            continue
        _copy_axes_content(src_fig.axes[0], flat[i])

    # Hide trailing empty cells
    for j in range(len(source_figs), n_cells):
        flat[j].axis("off")

    try:
        new_fig.tight_layout()
    except Exception:
        pass
    return new_fig


# ---------------------------------------------------------------------------
# Extract subplot into its own figure
# ---------------------------------------------------------------------------


def _extract_axes_as_figure(source_fig, axes_index: int):
    """Return a new Figure holding only the axes at ``axes_index``, repositioned to fill the canvas."""
    if axes_index < 0 or axes_index >= len(source_fig.axes):
        raise HTTPException(404, detail=f"axes index {axes_index} out of range")
    # Deep-copy via pickle (matplotlib has no clean axes-clone API).
    cloned = _unpickle_figure(_pickle_figure(source_fig))
    keep = cloned.axes[axes_index]
    for ax in list(cloned.axes):
        if ax is not keep:
            ax.remove()
    # Re-tile to fill most of the canvas with margins for ticks/labels.
    keep.set_position([0.12, 0.14, 0.82, 0.78])
    try:
        cloned.set_layout_engine("none")
    except Exception:
        pass
    return cloned


# ---------------------------------------------------------------------------
# WS message dispatch
# ---------------------------------------------------------------------------


def _resolve_select_target(state: FigureState, requested_id: Optional[str]) -> Optional[str]:
    """Promote a bar selection to its parent BarGroup so edits affect the group."""
    if not requested_id:
        return requested_id
    entries = artist_introspect.walk(state.fig)
    by_id = {e.id: e for e in entries}
    entry = by_id.get(requested_id)
    if entry and entry.parent_id:
        parent = by_id.get(entry.parent_id)
        if parent and parent.kind == "BarGroup":
            return parent.id
    return requested_id


async def _handle_ws_message(
    state: FigureState,
    manager: ConnectionManager,
    msg: dict,
    push_state,
) -> None:
    kind = msg.get("type")
    if kind == "apply":
        op = parse_op(msg["op"])
        await state.apply(op)
        await push_state()
    elif kind == "apply_many":
        from .edit_ops import load_log

        ops = load_log(msg["ops"])
        await state.apply_many(ops)
        await push_state()
    elif kind == "select":
        resolved = _resolve_select_target(state, msg.get("id"))
        await state.set_selected(resolved)
        snap = await state.snapshot()
        await manager.broadcast_json({"type": "selection", "selected_id": snap["selected_id"]})
    elif kind == "request_snapshot":
        await push_state()
    elif kind == "ping":
        pass
    else:
        raise ValueError(f"unknown ws message type: {kind!r}")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    registry: Optional[FigureRegistry] = None,
    *,
    legacy_single_state: Optional[FigureState] = None,
) -> FastAPI:
    """Build the multi-figure FastAPI app.

    ``legacy_single_state`` is a back-door for the original ``launch(fig)`` API:
    if provided, the figure is auto-registered under the name ``default`` and
    the un-scoped /api routes alias to it for backwards compat.
    """
    app = FastAPI(title="figure-studio", version="0.4.1")
    if registry is None:
        registry = FigureRegistry()
    app.state.registry = registry
    app.state.static_dir = _static_dir()
    app.state.upload_limit_mb = 64

    if legacy_single_state is not None:
        # Insert synchronously — no event loop yet.
        registry._figures[DEFAULT_FIGURE_NAME] = legacy_single_state  # noqa: SLF001
        registry._created_at[DEFAULT_FIGURE_NAME] = time.time()  # noqa: SLF001
        registry._managers[DEFAULT_FIGURE_NAME] = ConnectionManager()  # noqa: SLF001

    # ----- helpers -----

    async def _push_state(name: str) -> None:
        state = registry.get(name)
        snap = await state.snapshot()
        svg = await state.render_svg()
        await registry.manager(name).broadcast_json(
            {"type": "state", "state": snap, "svg": svg}
        )

    # ----- frontend (shared) -----

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        index_path = app.state.static_dir / "index.html"
        body = index_path.read_text() if index_path.exists() else _FALLBACK_INDEX
        return HTMLResponse(body, headers={"Cache-Control": "no-store, must-revalidate"})

    @app.get("/static/{path:path}")
    async def static_asset(path: str) -> Response:
        target = (app.state.static_dir / path).resolve()
        if not str(target).startswith(str(app.state.static_dir.resolve())):
            raise HTTPException(404)
        if not target.is_file():
            raise HTTPException(404)
        mime, _ = mimetypes.guess_type(str(target))
        return Response(target.read_bytes(), media_type=mime or "application/octet-stream")

    @app.get("/assets/{path:path}")
    async def vite_asset(path: str) -> Response:
        target = (app.state.static_dir / "assets" / path).resolve()
        if not target.is_file():
            raise HTTPException(404)
        mime, _ = mimetypes.guess_type(str(target))
        return Response(target.read_bytes(), media_type=mime or "application/octet-stream")

    @app.get("/api/presets")
    async def api_presets() -> JSONResponse:
        return JSONResponse(presets.to_json())

    @app.get("/api/palettes")
    async def api_palettes() -> JSONResponse:
        return JSONResponse(palettes.to_json())

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "version": "0.4.1", "figures": registry.names()})

    # ----- registry (figures list + add/remove) -----

    @app.get("/api/figures")
    async def api_figures_list() -> JSONResponse:
        return JSONResponse({"figures": registry.meta()})

    @app.post("/api/figures/{name}")
    async def api_figures_add(name: str, request: Request, overwrite: bool = True) -> JSONResponse:
        _validate_name(name)
        # body is a pickle of the figure
        body = await request.body()
        cap = app.state.upload_limit_mb * 1024 * 1024
        if len(body) > cap:
            raise HTTPException(413, detail=f"Upload exceeds {app.state.upload_limit_mb} MB cap.")
        try:
            fig = _unpickle_figure(body)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, detail=f"Could not unpickle figure: {exc}") from exc
        state = FigureState(fig=fig)
        await registry.add(name, state, overwrite=overwrite)
        # notify any clients already watching this name
        await _push_state(name)
        return JSONResponse({"name": name, "added": True, "meta": registry.meta()})

    @app.delete("/api/figures/{name}")
    async def api_figures_remove(name: str) -> JSONResponse:
        _validate_name(name)
        existed = await registry.remove(name)
        return JSONResponse({"name": name, "removed": existed})

    @app.get("/api/figures/{name}/pickle")
    async def api_figures_pickle(name: str) -> Response:
        """Return the live (edited) figure as a pickle so the client can ``session.get(name)``."""
        _validate_name(name)
        state = registry.get(name)
        async with state.lock:
            data = _pickle_figure(state.fig)
        return Response(data, media_type="application/octet-stream")

    @app.post("/api/figures/{name}/extract/{axes_index}")
    async def api_extract_axes(
        name: str, axes_index: int, as_name: Optional[str] = None
    ) -> JSONResponse:
        _validate_name(name)
        state = registry.get(name)
        async with state.lock:
            new_fig = _extract_axes_as_figure(state.fig, axes_index)
        new_name = as_name or _unique_name(registry, f"{name}_axes{axes_index}")
        _validate_name(new_name)
        await registry.add(new_name, FigureState(fig=new_fig), overwrite=True)
        return JSONResponse({"name": new_name, "extracted_from": name, "axes_index": axes_index})

    @app.post("/api/combine")
    async def api_combine(request: Request) -> JSONResponse:
        """Combine N session figures into a single ``rows × cols`` grid figure.

        Body: ``{"figures": ["name1", "name2", ...], "rows": 2, "cols": 2, "as_name": "combined"}``
        """
        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, detail=f"invalid JSON: {exc}") from exc
        names = list(body.get("figures") or [])
        rows = int(body.get("rows", 1))
        cols = int(body.get("cols", max(1, len(names))))
        as_name = body.get("as_name")
        if not names:
            raise HTTPException(422, detail="'figures' must be a non-empty list of figure names")
        if rows < 1 or cols < 1:
            raise HTTPException(422, detail="'rows' and 'cols' must be >= 1")
        sources = []
        missing = []
        for n in names:
            try:
                sources.append(registry.get(n).fig)
            except HTTPException:
                missing.append(n)
        if missing:
            raise HTTPException(404, detail=f"unknown figure(s): {missing}")
        new_fig = _combine_figures(sources, rows, cols)
        new_name = as_name or _unique_name(registry, "combined")
        _validate_name(new_name)
        await registry.add(new_name, FigureState(fig=new_fig), overwrite=True)
        return JSONResponse(
            {"name": new_name, "combined_from": names, "rows": rows, "cols": cols}
        )

    # ----- per-figure endpoints -----

    def _state_for(name: str) -> FigureState:
        _validate_name(name)
        return registry.get(name)

    @app.get("/api/figures/{name}/state")
    async def api_state(name: str) -> JSONResponse:
        state = _state_for(name)
        snap = await state.snapshot()
        return JSONResponse(
            {
                "name": name,
                "state": snap,
                "presets": presets.to_json(),
                "figures": registry.meta(),
            }
        )

    @app.get("/api/figures/{name}/figure.svg")
    async def api_svg(name: str) -> Response:
        state = _state_for(name)
        svg = await state.render_svg()
        return Response(svg, media_type="image/svg+xml")

    @app.get("/api/figures/{name}/export/pdf")
    async def api_export_pdf(
        name: str, only_visible: bool = False, pad: Optional[float] = None,
    ) -> Response:
        state = _state_for(name)
        pdf = await state.render_pdf(only_visible_axes=only_visible, pad_inches=pad)
        parts = [name]
        if only_visible: parts.append("main")
        if pad == 0: parts.append("tight")
        filename = "_".join(parts) + ".pdf"
        return Response(
            pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/figures/{name}/export/png")
    async def api_export_png(name: str, dpi: float = 300.0) -> Response:
        state = _state_for(name)
        png = await state.render_png(dpi=dpi)
        return Response(
            png,
            media_type="image/png",
            headers={"Content-Disposition": f'attachment; filename="{name}.png"'},
        )

    @app.get("/api/figures/{name}/export/code", response_class=PlainTextResponse)
    async def api_export_code(name: str) -> PlainTextResponse:
        state = _state_for(name)
        async with state.lock:
            log_copy = list(state.log)
        source = emit_for_session(log_copy)
        return PlainTextResponse(
            source,
            headers={"Content-Disposition": f'attachment; filename="{name}.py"'},
        )

    @app.post("/api/figures/{name}/session/save")
    async def api_session_save(name: str) -> JSONResponse:
        state = _state_for(name)
        state.write_session_now()
        return JSONResponse({"saved": True, "path": str(state.session_path) if state.session_path else None})

    @app.post("/api/figures/{name}/session/reset")
    async def api_session_reset(name: str) -> JSONResponse:
        state = _state_for(name)
        await state.reset_log()
        await _push_state(name)
        return JSONResponse({"reset": True})

    # ----- WebSocket per-figure -----

    @app.websocket("/api/figures/{name}/ws")
    async def ws_endpoint(websocket: WebSocket, name: str) -> None:
        if not _NAME_RE.match(name):
            await websocket.close(code=1003)
            return
        if not registry.has(name):
            await websocket.close(code=1003)
            return
        state = registry.get(name)
        manager = registry.manager(name)
        await manager.connect(websocket)
        try:
            snap = await state.snapshot()
            svg = await state.render_svg()
            await websocket.send_json(
                {"type": "state", "state": snap, "svg": svg, "name": name}
            )
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "invalid JSON"})
                    continue
                try:
                    await _handle_ws_message(
                        state, manager, msg, lambda: _push_state(name)
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("WS message failed")
                    await websocket.send_json({"type": "error", "message": str(exc)})
        except WebSocketDisconnect:
            pass
        finally:
            await manager.disconnect(websocket)

    # ----- backwards-compat aliases (un-scoped routes) -----
    # These mirror the v0.1 single-figure API onto the figure named ``default``.

    if legacy_single_state is not None or True:
        # Always expose them — they look up DEFAULT_FIGURE_NAME at call time.

        def _default_or_404() -> FigureState:
            if not registry.has(DEFAULT_FIGURE_NAME):
                raise HTTPException(404, detail="No default figure. Use /api/figures/{name}/state instead.")
            return registry.get(DEFAULT_FIGURE_NAME)

        @app.get("/api/state")
        async def api_state_legacy() -> JSONResponse:
            state = _default_or_404()
            snap = await state.snapshot()
            return JSONResponse(
                {
                    "state": snap,
                    "presets": presets.to_json(),
                    "figures": registry.meta(),
                }
            )

        @app.get("/api/figure.svg")
        async def api_svg_legacy() -> Response:
            state = _default_or_404()
            svg = await state.render_svg()
            return Response(svg, media_type="image/svg+xml")

        @app.get("/api/export/pdf")
        async def api_export_pdf_legacy(
            only_visible: bool = False, pad: Optional[float] = None,
        ) -> Response:
            state = _default_or_404()
            pdf = await state.render_pdf(only_visible_axes=only_visible, pad_inches=pad)
            suffix = "_main" if only_visible else ""
            return Response(
                pdf,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="figure{suffix}.pdf"'},
            )

        @app.get("/api/export/png")
        async def api_export_png_legacy(dpi: float = 300.0) -> Response:
            state = _default_or_404()
            png = await state.render_png(dpi=dpi)
            return Response(
                png,
                media_type="image/png",
                headers={"Content-Disposition": 'attachment; filename="figure.png"'},
            )

        @app.get("/api/export/code", response_class=PlainTextResponse)
        async def api_export_code_legacy() -> PlainTextResponse:
            state = _default_or_404()
            async with state.lock:
                log_copy = list(state.log)
            return PlainTextResponse(
                emit_for_session(log_copy),
                headers={"Content-Disposition": 'attachment; filename="figure.py"'},
            )

        @app.post("/api/session/save")
        async def api_session_save_legacy() -> JSONResponse:
            state = _default_or_404()
            state.write_session_now()
            return JSONResponse({"saved": True, "path": str(state.session_path) if state.session_path else None})

        @app.post("/api/session/reset")
        async def api_session_reset_legacy() -> JSONResponse:
            state = _default_or_404()
            await state.reset_log()
            await _push_state(DEFAULT_FIGURE_NAME)
            return JSONResponse({"reset": True})

        @app.websocket("/ws")
        async def ws_legacy(websocket: WebSocket) -> None:
            return await ws_endpoint(websocket, DEFAULT_FIGURE_NAME)

    return app


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------


def _unique_name(registry: FigureRegistry, base: str) -> str:
    if not registry.has(base):
        return base
    for i in range(2, 10000):
        cand = f"{base}_{i}"
        if not registry.has(cand):
            return cand
    return f"{base}_{int(time.time())}"
