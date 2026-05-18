"""Verify per-artist introspection: stable IDs, schemas, JSON-safe payloads."""
from __future__ import annotations

import json

import example_figures as ef
from figure_studio.artist_introspect import (
    AXES_PROPERTIES,
    LEGEND_PROPERTIES,
    LINE2D_PROPERTIES,
    PATHCOLLECTION_PROPERTIES,
    RECTANGLE_PROPERTIES,
    TEXT_PROPERTIES,
    assign_gids,
    inspector_tree,
    walk,
)


def test_walk_visits_every_kind_in_grid():
    fig = ef.grid_of_kinds()
    entries = walk(fig)
    kinds = {e.kind for e in entries}
    assert {"Axes", "Line2D", "PathCollection", "Rectangle", "Text", "Legend"} <= kinds


def test_stable_id_scheme():
    fig = ef.two_lines_with_legend()
    entries = walk(fig)
    ids = {e.id for e in entries}
    assert "axes_0" in ids
    assert "axes_0_line_0" in ids
    assert "axes_0_line_1" in ids
    assert "axes_0_legend" in ids


def test_assign_gids_sets_attrs_on_artists():
    fig = ef.two_lines_with_legend()
    registry = assign_gids(fig)
    for art_id, artist in registry.items():
        assert artist.get_gid() == art_id


def test_ids_are_stable_across_repeated_walks():
    fig = ef.grid_of_kinds()
    first = [e.id for e in walk(fig)]
    second = [e.id for e in walk(fig)]
    assert first == second


def test_legend_gid_survives_relegend():
    fig = ef.simple_line()
    fig.axes[0].plot([0, 1], [0, 1], label="extra")
    fig.axes[0].legend()
    registry1 = assign_gids(fig)
    # Re-call legend() — matplotlib re-creates the Legend artist.
    fig.axes[0].legend()
    registry2 = assign_gids(fig)
    assert "axes_0_legend" in registry1
    assert "axes_0_legend" in registry2
    assert registry2["axes_0_legend"].get_gid() == "axes_0_legend"


def test_inspector_tree_is_json_serialisable():
    fig = ef.grid_of_kinds()
    tree = inspector_tree(fig)
    # Will raise if any value is not JSON-able.
    json.dumps(tree)


def test_properties_match_schema_for_line2d():
    fig = ef.two_lines_with_legend()
    entries = {e.id: e for e in walk(fig)}
    from figure_studio.artist_introspect import serialize_properties

    props = {p["name"] for p in serialize_properties("Line2D", entries["axes_0_line_0"].artist)}
    assert props == set(LINE2D_PROPERTIES.keys())


def test_all_schemas_have_required_keys():
    for name, schema in [
        ("Line2D", LINE2D_PROPERTIES),
        ("PathCollection", PATHCOLLECTION_PROPERTIES),
        ("Rectangle", RECTANGLE_PROPERTIES),
        ("Text", TEXT_PROPERTIES),
        ("Legend", LEGEND_PROPERTIES),
        ("Axes", AXES_PROPERTIES),
    ]:
        for prop, spec in schema.items():
            assert "type" in spec, f"{name}.{prop} missing 'type'"
            assert "reader" in spec, f"{name}.{prop} missing 'reader'"
            assert "setter" in spec, f"{name}.{prop} missing 'setter'"


def test_alpha_reads_as_one_when_unset():
    """Previously: get_alpha() returns None → JSON null → inspector shows 0.
    Now: the reader substitutes the fallback (1.0) so visible artists display 1.0."""
    fig = ef.simple_line()
    from figure_studio.artist_introspect import serialize_properties
    line = fig.axes[0].lines[0]
    assert line.get_alpha() is None  # confirm matplotlib state
    props = {p["name"]: p["value"] for p in serialize_properties("Line2D", line)}
    assert props["alpha"] == 1.0


def test_bargroup_entries_created_for_each_container():
    fig = ef.grouped_bars()
    entries = walk(fig)
    groups = [e for e in entries if e.kind == "BarGroup"]
    assert len(groups) == 2
    assert {g.id for g in groups} == {"axes_0_bargroup_0", "axes_0_bargroup_1"}
    bars = [e for e in entries if e.kind == "Rectangle"]
    assert {b.parent_id for b in bars} == {"axes_0_bargroup_0", "axes_0_bargroup_1"}


def test_bargroup_registry_holds_container():
    fig = ef.grouped_bars()
    reg = assign_gids(fig)
    container = reg["axes_0_bargroup_0"]
    assert len(container.patches) == 3


def test_dual_axis_lists_both_axes():
    fig = ef.dual_axis()
    entries = walk(fig)
    axes_ids = sorted(e.id for e in entries if e.kind == "Axes")
    assert axes_ids == ["axes_0", "axes_1"]
    # The bar group lives on the primary axes; the line on the twin.
    assert any(e.id == "axes_0_bargroup_0" for e in entries)
    assert any(e.id == "axes_1_line_0" for e in entries)
