"""Multi-figure server + Session client + extract endpoint."""
from __future__ import annotations

import pickle

import example_figures as ef
import httpx
import pytest
from figure_studio.server import (
    DEFAULT_FIGURE_NAME,
    FigureRegistry,
    _extract_axes_as_figure,
    _pickle_figure,
    _unpickle_figure,
    create_app,
)
from figure_studio.figure_state import FigureState


@pytest.fixture
def empty_app():
    registry = FigureRegistry()
    return create_app(registry), registry


@pytest.fixture
def app_with_default():
    fig = ef.two_lines_with_legend()
    return create_app(legacy_single_state=FigureState(fig=fig))


async def test_figures_list_starts_empty(empty_app):
    app, _ = empty_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/figures")
        assert r.status_code == 200
        assert r.json() == {"figures": []}


async def test_legacy_launch_registers_default_figure(app_with_default):
    transport = httpx.ASGITransport(app=app_with_default)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/figures")
        names = [f["name"] for f in r.json()["figures"]]
        assert names == [DEFAULT_FIGURE_NAME]


async def test_legacy_routes_serve_default_figure(app_with_default):
    transport = httpx.ASGITransport(app=app_with_default)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # un-scoped /api/state hits default
        r = await c.get("/api/state")
        assert r.status_code == 200
        assert "tree" in r.json()["state"]
        r2 = await c.get("/api/figures/default/state")
        assert r2.status_code == 200
        assert r2.json()["state"]["tree"] == r.json()["state"]["tree"]


async def test_post_pickled_figure_registers_it(empty_app):
    app, registry = empty_app
    fig = ef.simple_line()
    body = pickle.dumps(fig)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/figures/lines", content=body)
        assert r.status_code == 200
        assert r.json()["name"] == "lines"
        listing = await c.get("/api/figures")
        names = [f["name"] for f in listing.json()["figures"]]
        assert names == ["lines"]
        # the figure is now editable
        state_resp = await c.get("/api/figures/lines/state")
        assert any(e["id"] == "axes_0_line_0" for e in state_resp.json()["state"]["tree"])


async def test_get_pickle_returns_live_figure(empty_app):
    app, registry = empty_app
    fig = ef.simple_line()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        await c.post("/api/figures/lines", content=pickle.dumps(fig))
        r = await c.get("/api/figures/lines/pickle")
        assert r.status_code == 200
        got = _unpickle_figure(r.content)
        assert len(got.axes) == 1
        assert got.axes[0].get_title() == fig.axes[0].get_title()


async def test_delete_removes_figure(empty_app):
    app, registry = empty_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        await c.post("/api/figures/foo", content=pickle.dumps(ef.simple_line()))
        r = await c.delete("/api/figures/foo")
        assert r.status_code == 200
        assert r.json()["removed"] is True
        listing = await c.get("/api/figures")
        assert listing.json()["figures"] == []


async def test_extract_axes_creates_new_figure(empty_app):
    app, registry = empty_app
    fig = ef.grid_of_kinds()  # 4 axes
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        await c.post("/api/figures/grid", content=pickle.dumps(fig))
        r = await c.post("/api/figures/grid/extract/2")
        assert r.status_code == 200
        new_name = r.json()["name"]
        assert new_name.startswith("grid_axes")
        listing = await c.get("/api/figures")
        names = sorted(f["name"] for f in listing.json()["figures"])
        assert names == ["grid", new_name]
        # The extracted figure has exactly one axes
        new_state = await c.get(f"/api/figures/{new_name}/state")
        tree = new_state.json()["state"]["tree"]
        assert sum(1 for e in tree if e["kind"] == "Axes") == 1


async def test_extract_unit_helper_preserves_titles():
    fig = ef.grid_of_kinds()
    titles = [a.get_title() for a in fig.axes]
    cloned = _extract_axes_as_figure(fig, 1)
    assert len(cloned.axes) == 1
    assert cloned.axes[0].get_title() == titles[1]


async def test_invalid_figure_name_returns_422(empty_app):
    app, registry = empty_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        bad = "../etc/passwd"
        r = await c.post(f"/api/figures/{bad}", content=b"not even pickle")
        assert r.status_code in (404, 422)


async def test_bad_pickle_returns_400(empty_app):
    app, registry = empty_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/figures/foo", content=b"\x00not a real pickle\x00")
        assert r.status_code == 400


async def test_overwrite_false_rejects_duplicate(empty_app):
    app, registry = empty_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        body = pickle.dumps(ef.simple_line())
        await c.post("/api/figures/foo", content=body)
        r = await c.post("/api/figures/foo?overwrite=false", content=body)
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# Session client (light unit tests against the ASGI transport via monkey-patch)
# ---------------------------------------------------------------------------


def test_client_module_exposes_connect_and_show():
    import figure_studio
    assert callable(figure_studio.connect)
    assert callable(figure_studio.show)
    assert figure_studio.Session is not None


def test_session_repr_html_contains_iframe():
    import figure_studio
    # Build a Session manually that doesn't autostart anything (we override _ping).
    s = figure_studio.Session.__new__(figure_studio.Session)
    s.host = "127.0.0.1"
    s.port = 8765
    s.base_url = "http://127.0.0.1:8765"
    s._spawned_proc = None
    # _repr_html_ calls .list() — patch it to return a fixed value
    s.list = lambda: ["a", "b"]
    html = s._repr_html_()
    assert "<iframe" in html
    assert "http://127.0.0.1:8765" in html
    assert "2 figure" in html
