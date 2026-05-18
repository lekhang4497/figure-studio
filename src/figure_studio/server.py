"""FastAPI app: serves the built frontend and an edit-stream WebSocket."""
from __future__ import annotations

import asyncio
import importlib.resources as importlib_resources
import json
import logging
import mimetypes
from pathlib import Path
from typing import List, Optional, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from . import artist_introspect, presets
from .code_gen import emit_for_session
from .edit_ops import parse as parse_op
from .figure_state import FigureState

logger = logging.getLogger("figure_studio")


# ---------------------------------------------------------------------------
# Static asset resolution — built frontend lives at src/figure_studio/static/
# ---------------------------------------------------------------------------


def _static_dir() -> Path:
    """Locate the static dir whether we're editable-installed or zip-installed."""
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
<p>or use the dev server: <code>npm run dev</code> (proxies to this backend).</p>
<p>Backend endpoints (handy for poking with curl):</p>
<ul>
  <li><a href="/api/state">/api/state</a> — JSON snapshot</li>
  <li><a href="/api/figure.svg">/api/figure.svg</a> — current SVG</li>
  <li><a href="/api/export/pdf">/api/export/pdf</a> — export PDF</li>
  <li><a href="/api/export/code">/api/export/code</a> — export Python</li>
</ul>
</body></html>"""


# ---------------------------------------------------------------------------
# Connection broadcasting
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Tracks open WebSockets; broadcasts state changes to all of them."""

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
# App factory
# ---------------------------------------------------------------------------


def create_app(state: FigureState) -> FastAPI:
    app = FastAPI(title="figure-studio", version="0.1.0")
    manager = ConnectionManager()

    app.state.figure_state = state
    app.state.connections = manager
    app.state.static_dir = _static_dir()

    # ----- helpers -----

    async def _push_state() -> None:
        snap = await state.snapshot()
        svg = await state.render_svg()
        await manager.broadcast_json({"type": "state", "state": snap, "svg": svg})

    # ----- frontend -----

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        index_path = app.state.static_dir / "index.html"
        body = index_path.read_text() if index_path.exists() else _FALLBACK_INDEX
        # Bundle filenames are content-hashed so they cache forever, but index.html
        # must always re-fetch — otherwise users get stuck on stale JS and the
        # editor seems "broken" after we ship a new build.
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

    # Vite builds reference assets at /assets/* — alias to the same dir.
    @app.get("/assets/{path:path}")
    async def vite_asset(path: str) -> Response:
        target = (app.state.static_dir / "assets" / path).resolve()
        if not target.is_file():
            raise HTTPException(404)
        mime, _ = mimetypes.guess_type(str(target))
        return Response(target.read_bytes(), media_type=mime or "application/octet-stream")

    # ----- REST -----

    @app.get("/api/state")
    async def api_state() -> JSONResponse:
        snap = await state.snapshot()
        return JSONResponse(
            {
                "state": snap,
                "presets": presets.to_json(),
            }
        )

    @app.get("/api/figure.svg")
    async def api_svg() -> Response:
        svg = await state.render_svg()
        return Response(svg, media_type="image/svg+xml")

    @app.get("/api/export/pdf")
    async def api_export_pdf(only_visible: bool = False) -> Response:
        pdf = await state.render_pdf(only_visible_axes=only_visible)
        suffix = "_main" if only_visible else ""
        return Response(
            pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="figure{suffix}.pdf"'},
        )

    @app.get("/api/export/png")
    async def api_export_png(dpi: float = 300.0) -> Response:
        png = await state.render_png(dpi=dpi)
        return Response(
            png,
            media_type="image/png",
            headers={"Content-Disposition": 'attachment; filename="figure.png"'},
        )

    @app.get("/api/export/code", response_class=PlainTextResponse)
    async def api_export_code() -> PlainTextResponse:
        async with state.lock:
            log_copy = list(state.log)
        source = emit_for_session(log_copy)
        return PlainTextResponse(
            source,
            headers={"Content-Disposition": 'attachment; filename="figure.py"'},
        )

    @app.post("/api/session/save")
    async def api_session_save() -> JSONResponse:
        state.write_session_now()
        return JSONResponse({"saved": True, "path": str(state.session_path) if state.session_path else None})

    @app.post("/api/session/reset")
    async def api_session_reset() -> JSONResponse:
        await state.reset_log()
        await _push_state()
        return JSONResponse({"reset": True})

    @app.get("/api/presets")
    async def api_presets() -> JSONResponse:
        return JSONResponse(presets.to_json())

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    # ----- WebSocket -----

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await manager.connect(ws)
        try:
            # On connect: send the latest state + svg
            snap = await state.snapshot()
            svg = await state.render_svg()
            await ws.send_json({"type": "state", "state": snap, "svg": svg})

            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "message": "invalid JSON"})
                    continue
                try:
                    await _handle_ws_message(state, manager, msg, _push_state)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("WS message failed")
                    await ws.send_json({"type": "error", "message": str(exc)})
        except WebSocketDisconnect:
            pass
        finally:
            await manager.disconnect(ws)

    return app


def _resolve_select_target(state: FigureState, requested_id: Optional[str]) -> Optional[str]:
    """Promote a selection to its container if one wraps the clicked artist.

    The canvas only ships individual bars (matplotlib's BarContainer isn't an
    Artist and has no SVG presence), so clicks land on a single bar even when
    the user wants to edit the whole group. We resolve that on the server so
    the behavior is consistent even if the frontend bundle is stale.
    """
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
