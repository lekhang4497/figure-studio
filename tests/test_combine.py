"""Combine endpoint + Session.combine + helper edge cases."""
from __future__ import annotations

import pickle

import example_figures as ef
import httpx
import pytest
from figure_studio.server import (
    FigureRegistry,
    _combine_figures,
    create_app,
)


@pytest.fixture
def empty_app():
    registry = FigureRegistry()
    return create_app(registry), registry


def test_combine_helper_1x2():
    a = ef.simple_line()
    b = ef.scatter_only()
    combined = _combine_figures([a, b], 1, 2)
    assert len(combined.axes) == 2
    # Each axes' new position is in different cells (different x bounds).
    p0 = combined.axes[0].get_position().bounds
    p1 = combined.axes[1].get_position().bounds
    assert p0[0] < p1[0]
    # The combined figure renders to a valid PDF.
    import io
    buf = io.BytesIO()
    combined.savefig(buf, format="pdf")
    assert buf.getvalue()[:4] == b"%PDF"


def test_combine_helper_preserves_multi_axes_source():
    """A source with 2 axes contributes 2 axes to its cell."""
    a = ef.simple_line()
    b = ef.grid_of_kinds()   # 4 axes
    combined = _combine_figures([a, b], 1, 2)
    assert len(combined.axes) == 5   # 1 + 4


def test_combine_helper_drops_excess_sources():
    figs = [ef.simple_line() for _ in range(5)]
    combined = _combine_figures(figs, 2, 2)  # only 4 cells
    assert len(combined.axes) == 4


def test_combine_helper_rejects_zero_rows():
    try:
        _combine_figures([ef.simple_line()], 0, 1)
    except Exception as exc:
        assert "rows" in str(exc).lower() or ">=" in str(exc)
    else:
        raise AssertionError("expected HTTPException")


def test_combine_helper_rejects_empty():
    try:
        _combine_figures([], 1, 1)
    except Exception as exc:
        assert "source" in str(exc).lower() or "least" in str(exc).lower()
    else:
        raise AssertionError("expected HTTPException")


async def test_combine_endpoint_creates_new_figure(empty_app):
    app, registry = empty_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # Seed 3 figures
        for nm, builder in (
            ("a", ef.simple_line),
            ("b", ef.scatter_only),
            ("c", ef.two_lines_with_legend),
        ):
            await c.post(f"/api/figures/{nm}", content=pickle.dumps(builder()))
        r = await c.post(
            "/api/combine",
            json={"figures": ["a", "b", "c"], "rows": 1, "cols": 3, "as_name": "trio"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "trio"
        listing = await c.get("/api/figures")
        names = sorted(f["name"] for f in listing.json()["figures"])
        assert names == ["a", "b", "c", "trio"]
        # State endpoint reports 3 (or more) axes
        st = await c.get("/api/figures/trio/state")
        kinds = [e["kind"] for e in st.json()["state"]["tree"]]
        assert kinds.count("Axes") >= 3


async def test_combine_endpoint_404s_on_missing_figure(empty_app):
    app, registry = empty_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        await c.post("/api/figures/exists", content=pickle.dumps(ef.simple_line()))
        r = await c.post(
            "/api/combine",
            json={"figures": ["exists", "nope"], "rows": 1, "cols": 2},
        )
        assert r.status_code == 404


async def test_combine_endpoint_422_on_empty_figures(empty_app):
    app, _ = empty_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/combine", json={"figures": [], "rows": 1, "cols": 1})
        assert r.status_code == 422


async def test_combined_figure_exports_pdf(empty_app):
    app, _ = empty_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        await c.post("/api/figures/a", content=pickle.dumps(ef.simple_line()))
        await c.post("/api/figures/b", content=pickle.dumps(ef.scatter_only()))
        await c.post("/api/combine", json={"figures": ["a", "b"], "rows": 1, "cols": 2, "as_name": "pair"})
        pdf = await c.get("/api/figures/pair/export/pdf?pad=0")
        assert pdf.status_code == 200
        assert pdf.content[:4] == b"%PDF"
        assert len(pdf.content) > 1000


def test_session_client_exposes_combine():
    import figure_studio
    s = figure_studio.Session.__new__(figure_studio.Session)
    assert callable(s.combine)
