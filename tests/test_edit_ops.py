"""Verify edit ops apply correctly and roundtrip cleanly through JSON."""
from __future__ import annotations

import json

import example_figures as ef
import matplotlib.colors as mcolors
from figure_studio.artist_introspect import assign_gids
from figure_studio.edit_ops import (
    DisableAutoLayout,
    SetFigureDpi,
    SetFigureSize,
    SetProperty,
    dump_log,
    load_log,
    parse,
)


def test_set_color_line2d():
    fig = ef.simple_line()
    reg = assign_gids(fig)
    op = SetProperty(artist_id="axes_0_line_0", kind="Line2D", name="color", value="#0033ff")
    op.apply(fig, reg)
    assert mcolors.to_hex(reg["axes_0_line_0"].get_color()) == "#0033ff"


def test_set_linewidth_line2d():
    fig = ef.simple_line()
    reg = assign_gids(fig)
    op = SetProperty(artist_id="axes_0_line_0", kind="Line2D", name="linewidth", value=2.5)
    op.apply(fig, reg)
    assert reg["axes_0_line_0"].get_linewidth() == 2.5


def test_set_axes_position():
    fig = ef.simple_line()
    reg = assign_gids(fig)
    op = SetProperty(artist_id="axes_0", kind="Axes", name="position", value=[0.2, 0.2, 0.6, 0.6])
    op.apply(fig, reg)
    bounds = reg["axes_0"].get_position().bounds
    assert tuple(round(v, 4) for v in bounds) == (0.2, 0.2, 0.6, 0.6)


def test_set_axes_grid_toggles():
    fig = ef.simple_line()
    reg = assign_gids(fig)
    SetProperty(artist_id="axes_0", kind="Axes", name="grid", value=True).apply(fig, reg)
    assert any(line.get_visible() for line in reg["axes_0"].xaxis.get_gridlines())
    SetProperty(artist_id="axes_0", kind="Axes", name="grid", value=False).apply(fig, reg)
    assert not any(line.get_visible() for line in reg["axes_0"].xaxis.get_gridlines())


def test_legend_loc_changes_position_code():
    fig = ef.two_lines_with_legend()
    reg = assign_gids(fig)
    SetProperty(artist_id="axes_0_legend", kind="Legend", name="loc", value="lower right").apply(fig, reg)
    assert reg["axes_0_legend"]._loc == 4


def test_axes_include_in_export_flag():
    fig = ef.grid_of_kinds()
    reg = assign_gids(fig)
    SetProperty(artist_id="axes_2", kind="Axes", name="include_in_export", value=False).apply(fig, reg)
    assert reg["axes_2"]._figure_studio_include_in_export is False


def test_set_figure_size_changes_size():
    fig = ef.simple_line()
    reg = assign_gids(fig)
    SetFigureSize(width_in=7.0, height_in=4.5).apply(fig, reg)
    w, h = fig.get_size_inches()
    assert (round(float(w), 2), round(float(h), 2)) == (7.0, 4.5)


def test_disable_auto_layout_idempotent():
    fig = ef.simple_line()
    reg = assign_gids(fig)
    DisableAutoLayout().apply(fig, reg)
    DisableAutoLayout().apply(fig, reg)  # second call must not raise


def test_roundtrip_json():
    ops = [
        SetProperty(artist_id="axes_0_line_0", kind="Line2D", name="color", value="#ff0000"),
        SetFigureSize(width_in=6.5, height_in=4.0),
        SetFigureDpi(dpi=300),
        DisableAutoLayout(),
    ]
    dumped = dump_log(ops)
    blob = json.dumps(dumped)
    parsed = load_log(json.loads(blob))
    assert [type(o).__name__ for o in parsed] == [
        "SetProperty", "SetFigureSize", "SetFigureDpi", "DisableAutoLayout",
    ]


def test_unknown_property_raises():
    fig = ef.simple_line()
    reg = assign_gids(fig)
    op = SetProperty(artist_id="axes_0_line_0", kind="Line2D", name="bogus", value=1)
    try:
        op.apply(fig, reg)
    except ValueError as exc:
        assert "Unknown property" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_parse_rejects_unknown_op():
    try:
        parse({"op": "nope"})
    except ValueError as exc:
        assert "Unknown edit op" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_missing_artist_does_not_crash():
    # Replay safety: if the user's code changed and the artist no longer exists,
    # apply should silently skip rather than crash.
    fig = ef.simple_line()
    reg = assign_gids(fig)
    op = SetProperty(artist_id="axes_99_line_99", kind="Line2D", name="color", value="#ff0000")
    op.apply(fig, reg)  # must not raise


def test_bargroup_edit_fans_out_to_all_bars():
    fig = ef.grouped_bars()
    reg = assign_gids(fig)
    SetProperty(
        artist_id="axes_0_bargroup_0", kind="BarGroup", name="facecolor", value="#cc3344",
    ).apply(fig, reg)
    # Group 0 has 3 bars in it
    container = reg["axes_0_bargroup_0"]
    assert len(container.patches) == 3
    for patch in container.patches:
        assert mcolors.to_hex(patch.get_facecolor()) == "#cc3344"
    # Group 1's bars are untouched
    container2 = reg["axes_0_bargroup_1"]
    for patch in container2.patches:
        assert mcolors.to_hex(patch.get_facecolor()) != "#cc3344"


def test_bargroup_edit_hides_all_bars():
    fig = ef.grouped_bars()
    reg = assign_gids(fig)
    SetProperty(
        artist_id="axes_0_bargroup_0", kind="BarGroup", name="visible", value=False,
    ).apply(fig, reg)
    for patch in reg["axes_0_bargroup_0"].patches:
        assert patch.get_visible() is False
    for patch in reg["axes_0_bargroup_1"].patches:
        assert patch.get_visible() is True


def test_individual_bar_edit_still_works():
    """Editing one bar (Rectangle, not BarGroup) only changes that bar."""
    fig = ef.grouped_bars()
    reg = assign_gids(fig)
    SetProperty(
        artist_id="axes_0_bar_0", kind="Rectangle", name="facecolor", value="#0000ff",
    ).apply(fig, reg)
    assert mcolors.to_hex(reg["axes_0_bar_0"].get_facecolor()) == "#0000ff"
    assert mcolors.to_hex(reg["axes_0_bar_1"].get_facecolor()) != "#0000ff"
