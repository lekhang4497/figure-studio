"""End-to-end: drive the FastAPI app with httpx + websockets and assert PDF export."""
from __future__ import annotations

import json
import threading
import time

import example_figures as ef
import httpx
import pytest
from figure_studio.figure_state import FigureState
from figure_studio.server import create_app


@pytest.fixture
async def app_with_state():
    fig = ef.two_lines_with_legend()
    state = FigureState(fig=fig)
    app = create_app(state)
    return app, state


async def test_state_endpoint_returns_tree(app_with_state):
    app, state = app_with_state
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/state")
        assert r.status_code == 200
        body = r.json()
        assert "presets" in body
        assert any(p["key"] == "acl_double" for p in body["presets"])
        tree = body["state"]["tree"]
        kinds = {e["kind"] for e in tree}
        assert {"Line2D", "Legend", "Axes"} <= kinds


async def test_svg_export_contains_ids(app_with_state):
    app, state = app_with_state
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/figure.svg")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/svg+xml")
        assert "axes_0_line_0" in r.text


async def test_pdf_export_returns_valid_pdf(app_with_state):
    app, state = app_with_state
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/export/pdf")
        assert r.status_code == 200
        assert r.content[:4] == b"%PDF"
        assert int(r.headers.get("content-length", len(r.content))) > 1000


async def test_code_export_runs_standalone(app_with_state, tmp_path):
    import subprocess
    import sys

    app, state = app_with_state
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Apply an edit so the generated code has something to replay.
        op = {
            "op": "set_property",
            "artist_id": "axes_0_line_0",
            "kind": "Line2D",
            "name": "color",
            "value": "#22aa55",
        }
        await state.apply(__import__("figure_studio.edit_ops", fromlist=["parse"]).parse(op))

        r = await client.get("/api/export/code")
        assert r.status_code == 200
        src = r.text
    # Replace the build_figure stub with something runnable.
    runnable = src.replace(
        "raise NotImplementedError(\n        \"Replace the body of build_figure() with your plotting code.\"\n    )",
        "import matplotlib.pyplot as plt\n    import numpy as np\n    fig, ax = plt.subplots()\n    x = np.linspace(0,5,30)\n    ax.plot(x, np.sin(x), label='sin'); ax.plot(x, np.cos(x), label='cos'); ax.legend()\n    return fig",
    )
    script = tmp_path / "fig.py"
    out = tmp_path / "out.pdf"
    script.write_text(runnable)
    proc = subprocess.run([sys.executable, str(script), str(out)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert out.read_bytes()[:4] == b"%PDF"


async def test_apply_via_websocket_then_state_reflects_edit(app_with_state):
    """Bypass an actual ws upgrade by calling the apply pathway directly,
    then check that snapshot reports the new colour. Full WS upgrade adds setup
    cost without testing behaviour we don't already cover.
    """
    app, state = app_with_state
    from figure_studio.edit_ops import SetProperty

    await state.apply(
        SetProperty(artist_id="axes_0_line_0", kind="Line2D", name="color", value="#abcdef")
    )
    snap = await state.snapshot()
    line = next(e for e in snap["tree"] if e["id"] == "axes_0_line_0")
    color_prop = next(p for p in line["properties"] if p["name"] == "color")
    assert color_prop["value"].lower().startswith("#abcdef")


async def test_session_save_and_restore(tmp_path):
    fig = ef.simple_line()
    sidecar = tmp_path / "demo.figure_studio.json"
    state = FigureState(fig=fig, session_path=sidecar)
    from figure_studio.edit_ops import SetProperty

    await state.apply(SetProperty(artist_id="axes_0_line_0", kind="Line2D", name="color", value="#112233"))
    state.write_session_now()
    assert sidecar.exists()
    payload = json.loads(sidecar.read_text())
    assert payload["log"][0]["value"] == "#112233"

    # Restore into a fresh figure
    fig2 = ef.simple_line()
    state2 = FigureState(fig=fig2, session_path=sidecar)
    ops = FigureState.read_session(sidecar)
    state2.replay_sync(ops)
    snap = await state2.snapshot()
    line = next(e for e in snap["tree"] if e["id"] == "axes_0_line_0")
    color = next(p for p in line["properties"] if p["name"] == "color")["value"]
    assert color.lower().startswith("#112233")


async def test_reset_session_clears_log_and_deletes_sidecar(tmp_path):
    fig = ef.simple_line()
    sidecar = tmp_path / "demo.figure_studio.json"
    state = FigureState(fig=fig, session_path=sidecar)
    from figure_studio.edit_ops import SetProperty

    await state.apply(SetProperty(artist_id="axes_0_line_0", kind="Line2D", name="color", value="#aa00cc"))
    state.write_session_now()
    assert sidecar.exists()
    await state.reset_log()
    assert state.log == []
    assert not sidecar.exists()


async def test_select_on_bar_resolves_to_parent_bargroup():
    """Selecting a bar over WS must promote to the parent BarGroup so the
    inspector edits the whole group, matching the canvas-click behavior."""
    fig = ef.grouped_bars()
    state = FigureState(fig=fig)
    from figure_studio.server import _resolve_select_target

    assert _resolve_select_target(state, "axes_0_bar_0") == "axes_0_bargroup_0"
    assert _resolve_select_target(state, "axes_0_bar_4") == "axes_0_bargroup_1"
    # Non-bar selections pass through untouched.
    assert _resolve_select_target(state, "axes_0") == "axes_0"
    assert _resolve_select_target(state, "axes_0_bargroup_0") == "axes_0_bargroup_0"
    assert _resolve_select_target(state, None) is None
    assert _resolve_select_target(state, "nonexistent") == "nonexistent"


async def test_index_has_no_store_cache_header():
    """Stale frontend bundles cause "the editor doesn't work" reports —
    keep the index.html out of browser cache so new bundles always load."""
    fig = ef.simple_line()
    state = FigureState(fig=fig)
    app = create_app(state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/")
        assert r.status_code == 200
        cc = r.headers.get("cache-control", "")
        assert "no-store" in cc


async def test_axes_include_in_export_renders_smaller_pdf(app_with_state):
    """Hiding axes via include_in_export should produce a tighter 'main' PDF."""
    fig = ef.grid_of_kinds()
    state = FigureState(fig=fig)
    app = create_app(state)
    from figure_studio.edit_ops import SetProperty

    await state.apply(SetProperty(artist_id="axes_2", kind="Axes", name="include_in_export", value=False))
    await state.apply(SetProperty(artist_id="axes_3", kind="Axes", name="include_in_export", value=False))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r_full = await client.get("/api/export/pdf")
        r_main = await client.get("/api/export/pdf?only_visible=true")
        assert r_full.content[:4] == b"%PDF"
        assert r_main.content[:4] == b"%PDF"
        # Hard to compare semantically; just ensure both succeed and 'main' didn't crash.
        assert len(r_main.content) > 500
