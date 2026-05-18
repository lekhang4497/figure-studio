"""Walk a matplotlib Figure, assign stable IDs, expose a hand-written property schema.

The schema is intentionally narrow — only properties researchers actually tweak when
preparing paper figures. Adding a new editable property is a one-line change to the
relevant ``*_PROPERTIES`` dict. We do NOT reflect on ``Artist.properties()``; that
list is too noisy and produces unsafe setters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import matplotlib.colors as mcolors
from matplotlib.axes import Axes
from matplotlib.collections import PathCollection
from matplotlib.container import BarContainer
from matplotlib.figure import Figure
from matplotlib.legend import Legend
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from matplotlib.text import Text


# ---------------------------------------------------------------------------
# Property schemas
# ---------------------------------------------------------------------------

# Each entry: {prop_name: {type, getter, setter, ...metadata}}
# "type" drives the inspector widget. The optional "reader" callable normalises the
# raw return value of the getter for transport over JSON.

LINESTYLES = ["-", "--", "-.", ":", "None"]
MARKERS = [
    "None", ".", ",", "o", "v", "^", "<", ">",
    "1", "2", "3", "4", "s", "p", "*", "h", "H",
    "+", "x", "D", "d", "|", "_",
]
FONTWEIGHTS = ["normal", "bold", "light", "medium", "semibold", "black"]
HALIGNMENTS = ["left", "center", "right"]
VALIGNMENTS = ["top", "center", "bottom", "baseline"]
SCALES = ["linear", "log", "symlog", "logit"]
LEGEND_LOCS = [
    "best", "upper right", "upper left", "lower left", "lower right",
    "right", "center left", "center right", "lower center", "upper center", "center",
]


def _to_hex(value: Any) -> str:
    """Best-effort colour normaliser. Returns ``#rrggbb`` or ``#rrggbbaa``."""
    try:
        return mcolors.to_hex(value, keep_alpha=True)
    except Exception:
        return "#000000ff"


def _read_color(getter_name: str) -> Callable[[Any], str]:
    def reader(artist: Any) -> str:
        value = getattr(artist, getter_name)()
        # PathCollection.get_facecolor returns an array per-point; pick first.
        try:
            import numpy as np

            arr = np.asarray(value)
            if arr.ndim == 2 and len(arr) > 0:
                return _to_hex(arr[0])
        except Exception:
            pass
        return _to_hex(value)

    return reader


def _read_first_array_value(getter_name: str, default: float = 1.0) -> Callable[[Any], float]:
    def reader(artist: Any) -> float:
        value = getattr(artist, getter_name)()
        try:
            import numpy as np

            arr = np.asarray(value, dtype=float).flatten()
            if arr.size:
                return float(arr[0])
        except Exception:
            pass
        try:
            return float(value)
        except Exception:
            return default

    return reader


def _read_attr(getter_name: str, fallback: Any = None) -> Callable[[Any], Any]:
    def reader(artist: Any) -> Any:
        try:
            value = getattr(artist, getter_name)()
        except Exception:
            return fallback
        # matplotlib returns None for never-explicitly-set properties (alpha,
        # markersize, etc.); display the documented default instead so the
        # inspector doesn't show ``0`` for a visible artist.
        return fallback if value is None else value

    return reader


def _read_axes_position(artist: Axes) -> List[float]:
    box = artist.get_position()
    return [float(box.x0), float(box.y0), float(box.width), float(box.height)]


def _read_lim(getter_name: str) -> Callable[[Axes], List[float]]:
    def reader(artist: Axes) -> List[float]:
        lo, hi = getattr(artist, getter_name)()
        return [float(lo), float(hi)]

    return reader


def _read_grid(artist: Axes) -> bool:
    # Heuristic: any major gridline that is visible counts as "grid on".
    for axis in (artist.xaxis, artist.yaxis):
        for line in axis.get_gridlines():
            if line.get_visible():
                return True
    return False


def _read_legend_loc(artist: Legend) -> str:
    # Legend stores the location code on _loc; reverse-map to string.
    code_to_str = {
        0: "best", 1: "upper right", 2: "upper left", 3: "lower left", 4: "lower right",
        5: "right", 6: "center left", 7: "center right", 8: "lower center", 9: "upper center",
        10: "center",
    }
    try:
        code = artist._loc  # noqa: SLF001
    except Exception:
        return "best"
    return code_to_str.get(int(code), "best")


def _read_marker(artist: Line2D) -> str:
    m = artist.get_marker()
    if m is None:
        return "None"
    return str(m)


def _read_linestyle(artist: Line2D) -> str:
    ls = artist.get_linestyle()
    if not ls:
        return "-"
    return str(ls)


LINE2D_PROPERTIES: Dict[str, Dict[str, Any]] = {
    "color": {"type": "color", "getter": "get_color", "setter": "set_color", "reader": _read_color("get_color")},
    "linewidth": {"type": "float", "min": 0.0, "max": 10.0, "step": 0.25, "getter": "get_linewidth", "setter": "set_linewidth", "reader": _read_attr("get_linewidth", 1.0)},
    "linestyle": {"type": "enum", "values": LINESTYLES, "getter": "get_linestyle", "setter": "set_linestyle", "reader": _read_linestyle},
    "alpha": {"type": "float", "min": 0.0, "max": 1.0, "step": 0.05, "getter": "get_alpha", "setter": "set_alpha", "reader": _read_attr("get_alpha", 1.0)},
    "marker": {"type": "enum", "values": MARKERS, "getter": "get_marker", "setter": "set_marker", "reader": _read_marker},
    "markersize": {"type": "float", "min": 0.0, "max": 30.0, "step": 0.5, "getter": "get_markersize", "setter": "set_markersize", "reader": _read_attr("get_markersize", 6.0)},
    "markerfacecolor": {"type": "color", "getter": "get_markerfacecolor", "setter": "set_markerfacecolor", "reader": _read_color("get_markerfacecolor")},
    "markeredgecolor": {"type": "color", "getter": "get_markeredgecolor", "setter": "set_markeredgecolor", "reader": _read_color("get_markeredgecolor")},
    "label": {"type": "string", "getter": "get_label", "setter": "set_label", "reader": _read_attr("get_label", "")},
    "visible": {"type": "bool", "getter": "get_visible", "setter": "set_visible", "reader": _read_attr("get_visible", True)},
    "zorder": {"type": "float", "min": -5.0, "max": 50.0, "step": 1.0, "getter": "get_zorder", "setter": "set_zorder", "reader": _read_attr("get_zorder", 2.0)},
}

PATHCOLLECTION_PROPERTIES: Dict[str, Dict[str, Any]] = {
    "facecolor": {"type": "color", "getter": "get_facecolor", "setter": "set_facecolor", "reader": _read_color("get_facecolor")},
    "edgecolor": {"type": "color", "getter": "get_edgecolor", "setter": "set_edgecolor", "reader": _read_color("get_edgecolor")},
    "linewidth": {"type": "float", "min": 0.0, "max": 5.0, "step": 0.1, "getter": "get_linewidth", "setter": "set_linewidth", "reader": _read_first_array_value("get_linewidth", 1.0)},
    "alpha": {"type": "float", "min": 0.0, "max": 1.0, "step": 0.05, "getter": "get_alpha", "setter": "set_alpha", "reader": _read_attr("get_alpha", 1.0)},
    "sizes": {"type": "float", "min": 1.0, "max": 500.0, "step": 5.0, "getter": "get_sizes", "setter": "set_sizes", "reader": _read_first_array_value("get_sizes", 36.0), "applies_as": "uniform_sizes"},
    "label": {"type": "string", "getter": "get_label", "setter": "set_label", "reader": _read_attr("get_label", "")},
    "visible": {"type": "bool", "getter": "get_visible", "setter": "set_visible", "reader": _read_attr("get_visible", True)},
    "zorder": {"type": "float", "min": -5.0, "max": 50.0, "step": 1.0, "getter": "get_zorder", "setter": "set_zorder", "reader": _read_attr("get_zorder", 1.0)},
}

RECTANGLE_PROPERTIES: Dict[str, Dict[str, Any]] = {
    "facecolor": {"type": "color", "getter": "get_facecolor", "setter": "set_facecolor", "reader": _read_color("get_facecolor")},
    "edgecolor": {"type": "color", "getter": "get_edgecolor", "setter": "set_edgecolor", "reader": _read_color("get_edgecolor")},
    "linewidth": {"type": "float", "min": 0.0, "max": 5.0, "step": 0.1, "getter": "get_linewidth", "setter": "set_linewidth", "reader": _read_attr("get_linewidth", 1.0)},
    "alpha": {"type": "float", "min": 0.0, "max": 1.0, "step": 0.05, "getter": "get_alpha", "setter": "set_alpha", "reader": _read_attr("get_alpha", 1.0)},
    "hatch": {"type": "string", "getter": "get_hatch", "setter": "set_hatch", "reader": _read_attr("get_hatch", "")},
    "label": {"type": "string", "getter": "get_label", "setter": "set_label", "reader": _read_attr("get_label", "")},
    "visible": {"type": "bool", "getter": "get_visible", "setter": "set_visible", "reader": _read_attr("get_visible", True)},
    "zorder": {"type": "float", "min": -5.0, "max": 50.0, "step": 1.0, "getter": "get_zorder", "setter": "set_zorder", "reader": _read_attr("get_zorder", 1.0)},
}

TEXT_PROPERTIES: Dict[str, Dict[str, Any]] = {
    "text": {"type": "string", "getter": "get_text", "setter": "set_text", "reader": _read_attr("get_text", "")},
    "color": {"type": "color", "getter": "get_color", "setter": "set_color", "reader": _read_color("get_color")},
    "fontsize": {"type": "float", "min": 4.0, "max": 48.0, "step": 0.5, "getter": "get_fontsize", "setter": "set_fontsize", "reader": _read_attr("get_fontsize", 10.0)},
    "fontweight": {"type": "enum", "values": FONTWEIGHTS, "getter": "get_fontweight", "setter": "set_fontweight", "reader": _read_attr("get_fontweight", "normal")},
    "alpha": {"type": "float", "min": 0.0, "max": 1.0, "step": 0.05, "getter": "get_alpha", "setter": "set_alpha", "reader": _read_attr("get_alpha", 1.0)},
    "rotation": {"type": "float", "min": -180.0, "max": 180.0, "step": 5.0, "getter": "get_rotation", "setter": "set_rotation", "reader": _read_attr("get_rotation", 0.0)},
    "horizontalalignment": {"type": "enum", "values": HALIGNMENTS, "getter": "get_horizontalalignment", "setter": "set_horizontalalignment", "reader": _read_attr("get_horizontalalignment", "left")},
    "verticalalignment": {"type": "enum", "values": VALIGNMENTS, "getter": "get_verticalalignment", "setter": "set_verticalalignment", "reader": _read_attr("get_verticalalignment", "baseline")},
    "visible": {"type": "bool", "getter": "get_visible", "setter": "set_visible", "reader": _read_attr("get_visible", True)},
}

LEGEND_PROPERTIES: Dict[str, Dict[str, Any]] = {
    "loc": {"type": "enum", "values": LEGEND_LOCS, "getter": None, "setter": "_set_loc", "reader": _read_legend_loc},
    "frameon": {"type": "bool", "getter": "get_frame_on", "setter": "set_frame_on", "reader": _read_attr("get_frame_on", True)},
    "ncol": {"type": "int", "min": 1, "max": 8, "step": 1, "getter": None, "setter": "_set_ncol", "reader": lambda a: int(getattr(a, "_ncols", getattr(a, "_ncol", 1)))},
    "title": {"type": "string", "getter": None, "setter": "_set_title", "reader": lambda a: a.get_title().get_text() if a.get_title() else ""},
    "visible": {"type": "bool", "getter": "get_visible", "setter": "set_visible", "reader": _read_attr("get_visible", True)},
}

AXES_PROPERTIES: Dict[str, Dict[str, Any]] = {
    "position": {"type": "tuple_float4", "labels": ["x", "y", "w", "h"], "min": 0.0, "max": 1.0, "step": 0.01, "getter": None, "setter": "set_position", "reader": _read_axes_position},
    "title": {"type": "string", "getter": "get_title", "setter": "set_title", "reader": _read_attr("get_title", "")},
    "xlabel": {"type": "string", "getter": "get_xlabel", "setter": "set_xlabel", "reader": _read_attr("get_xlabel", "")},
    "ylabel": {"type": "string", "getter": "get_ylabel", "setter": "set_ylabel", "reader": _read_attr("get_ylabel", "")},
    "xlim": {"type": "tuple_float2", "labels": ["min", "max"], "getter": "get_xlim", "setter": "set_xlim", "reader": _read_lim("get_xlim")},
    "ylim": {"type": "tuple_float2", "labels": ["min", "max"], "getter": "get_ylim", "setter": "set_ylim", "reader": _read_lim("get_ylim")},
    "xscale": {"type": "enum", "values": SCALES, "getter": "get_xscale", "setter": "set_xscale", "reader": _read_attr("get_xscale", "linear")},
    "yscale": {"type": "enum", "values": SCALES, "getter": "get_yscale", "setter": "set_yscale", "reader": _read_attr("get_yscale", "linear")},
    "grid": {"type": "bool", "getter": None, "setter": "_set_grid", "reader": _read_grid},
    "facecolor": {"type": "color", "getter": "get_facecolor", "setter": "set_facecolor", "reader": _read_color("get_facecolor")},
    "include_in_export": {"type": "bool", "getter": None, "setter": "_set_include_in_export", "reader": lambda a: bool(getattr(a, "_figure_studio_include_in_export", True))},
    "visible": {"type": "bool", "getter": "get_visible", "setter": "set_visible", "reader": _read_attr("get_visible", True)},
}


# BarGroup wraps a matplotlib BarContainer (the return value of ``ax.bar(...)``).
# Properties read from the first patch as a representative; SetProperty's apply()
# fans the write out to every patch in the container so editing one bar visually
# edits the whole group — the common case for paper figures.

def _bargroup_reader(prop_name: str, fallback: Any) -> Callable[[Any], Any]:
    def reader(container: Any) -> Any:
        patches = getattr(container, "patches", []) or []
        if not patches:
            return fallback
        spec = RECTANGLE_PROPERTIES.get(prop_name, {})
        rd = spec.get("reader")
        if rd is None:
            return fallback
        return rd(patches[0])

    return reader


BARGROUP_PROPERTIES: Dict[str, Dict[str, Any]] = {}
for _name in ("facecolor", "edgecolor", "linewidth", "alpha", "hatch", "label", "visible"):
    _base = dict(RECTANGLE_PROPERTIES[_name])
    _base["reader"] = _bargroup_reader(_name, _base.get("default"))
    BARGROUP_PROPERTIES[_name] = _base


KIND_SCHEMAS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "Line2D": LINE2D_PROPERTIES,
    "PathCollection": PATHCOLLECTION_PROPERTIES,
    "Rectangle": RECTANGLE_PROPERTIES,
    "Text": TEXT_PROPERTIES,
    "Legend": LEGEND_PROPERTIES,
    "Axes": AXES_PROPERTIES,
    "BarGroup": BARGROUP_PROPERTIES,
}


# ---------------------------------------------------------------------------
# Walking the tree
# ---------------------------------------------------------------------------


@dataclass
class ArtistEntry:
    """A single editable artist discovered while walking the figure tree."""

    id: str
    kind: str            # "Line2D", "PathCollection", "Rectangle", "Text", "Legend", "Axes"
    label: str           # short human-readable identifier
    parent_id: Optional[str]
    artist: Any = field(repr=False)


def _clean_label(raw: Any, fallback: str) -> str:
    """Strip matplotlib's sentinel labels (``_nolegend_``, ``_line0``) for display."""
    s = "" if raw is None else str(raw)
    if not s or s.startswith("_"):
        return fallback
    return s


def _is_axes_background_patch(patch: Any, axes: Axes) -> bool:
    # Axes.patch is the rectangle behind the data area — not user-editable here.
    return patch is getattr(axes, "patch", None)


def _is_axes_spine_artist(artist: Any, axes: Axes) -> bool:
    spines = getattr(axes, "spines", {})
    for spine in spines.values():
        if artist is spine:
            return True
    return False


def walk(fig: Figure) -> List[ArtistEntry]:
    """Walk ``fig`` and return all editable artists with stable IDs.

    IDs are deterministic from walk order so they survive re-renders. After every
    edit the figure is re-walked with the same logic; IDs stay stable provided the
    artist tree shape stays the same.
    """
    entries: List[ArtistEntry] = []

    for ax_idx, axes in enumerate(fig.axes):
        ax_id = f"axes_{ax_idx}"
        entries.append(
            ArtistEntry(
                id=ax_id,
                kind="Axes",
                label=f"Axes #{ax_idx}",
                parent_id=None,
                artist=axes,
            )
        )

        title = axes.title
        if isinstance(title, Text) and title.get_text():
            entries.append(
                ArtistEntry(
                    id=f"{ax_id}_title",
                    kind="Text",
                    label=f"{ax_id} · title",
                    parent_id=ax_id,
                    artist=title,
                )
            )

        for tname, tartist in (
            ("xlabel", axes.xaxis.label),
            ("ylabel", axes.yaxis.label),
        ):
            if isinstance(tartist, Text) and tartist.get_text():
                entries.append(
                    ArtistEntry(
                        id=f"{ax_id}_{tname}_text",
                        kind="Text",
                        label=f"{ax_id} · {tname}",
                        parent_id=ax_id,
                        artist=tartist,
                    )
                )

        for li, line in enumerate(axes.lines):
            entries.append(
                ArtistEntry(
                    id=f"{ax_id}_line_{li}",
                    kind="Line2D",
                    label=_clean_label(line.get_label(), f"line {li}"),
                    parent_id=ax_id,
                    artist=line,
                )
            )

        for ci, coll in enumerate(axes.collections):
            if isinstance(coll, PathCollection):
                entries.append(
                    ArtistEntry(
                        id=f"{ax_id}_scatter_{ci}",
                        kind="PathCollection",
                        label=_clean_label(coll.get_label(), f"scatter {ci}"),
                        parent_id=ax_id,
                        artist=coll,
                    )
                )

        # Iterate containers first so we can tag bars with their group parent.
        # ax.bar(...) produces a BarContainer whose .patches are also in ax.patches;
        # we surface the container as a "BarGroup" so editing it edits all bars at once.
        patch_to_group: Dict[int, str] = {}
        for ci, container in enumerate(getattr(axes, "containers", []) or []):
            if not isinstance(container, BarContainer):
                continue
            group_id = f"{ax_id}_bargroup_{ci}"
            entries.append(
                ArtistEntry(
                    id=group_id,
                    kind="BarGroup",
                    label=_clean_label(container.get_label(), f"bars ({len(container.patches)})"),
                    parent_id=ax_id,
                    artist=container,
                )
            )
            for patch in container.patches:
                patch_to_group[id(patch)] = group_id

        for pi, patch in enumerate(axes.patches):
            if not isinstance(patch, Rectangle):
                continue
            if _is_axes_background_patch(patch, axes):
                continue
            parent = patch_to_group.get(id(patch), ax_id)
            entries.append(
                ArtistEntry(
                    id=f"{ax_id}_bar_{pi}",
                    kind="Rectangle",
                    label=_clean_label(patch.get_label(), f"bar {pi}"),
                    parent_id=parent,
                    artist=patch,
                )
            )

        for ti, text in enumerate(axes.texts):
            entries.append(
                ArtistEntry(
                    id=f"{ax_id}_text_{ti}",
                    kind="Text",
                    label=(text.get_text() or f"text {ti}")[:40],
                    parent_id=ax_id,
                    artist=text,
                )
            )

        legend = axes.get_legend()
        if legend is not None:
            entries.append(
                ArtistEntry(
                    id=f"{ax_id}_legend",
                    kind="Legend",
                    label=f"{ax_id} · legend",
                    parent_id=ax_id,
                    artist=legend,
                )
            )

    # Figure-level artists last so axes-children IDs always win for overlapping clicks.
    for ti, text in enumerate(fig.texts):
        entries.append(
            ArtistEntry(
                id=f"fig_text_{ti}",
                kind="Text",
                label=(text.get_text() or f"fig text {ti}")[:40],
                parent_id=None,
                artist=text,
            )
        )

    if getattr(fig, "legends", None):
        for li, legend in enumerate(fig.legends):
            entries.append(
                ArtistEntry(
                    id=f"fig_legend_{li}",
                    kind="Legend",
                    label=f"figure · legend {li}",
                    parent_id=None,
                    artist=legend,
                )
            )

    return entries


def assign_gids(fig: Figure) -> Dict[str, Any]:
    """Walk the figure, ``set_gid`` on every editable artist, return the registry.

    Call this after every edit that may have re-created artists (e.g. ``ax.legend()``).
    Stable IDs come from deterministic walk order, not from any artist identity.
    """
    entries = walk(fig)
    registry: Dict[str, Any] = {}
    for entry in entries:
        # Containers (BarGroup) aren't matplotlib Artists and have no set_gid;
        # they live in the registry by synthetic id only.
        if hasattr(entry.artist, "set_gid"):
            entry.artist.set_gid(entry.id)
        registry[entry.id] = entry.artist
    return registry


# ---------------------------------------------------------------------------
# Inspector payloads
# ---------------------------------------------------------------------------


def _safe_read(reader: Callable[[Any], Any], artist: Any, default: Any) -> Any:
    try:
        value = reader(artist)
    except Exception:
        return default
    # Make sure the value is JSON-able.
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return list(value)
    try:
        return list(value)
    except Exception:
        return str(value)


def schema_for(kind: str) -> Dict[str, Dict[str, Any]]:
    return KIND_SCHEMAS.get(kind, {})


def serialize_properties(kind: str, artist: Any) -> List[Dict[str, Any]]:
    """Return the JSON-able list of properties for one artist."""
    schema = schema_for(kind)
    out: List[Dict[str, Any]] = []
    for prop, spec in schema.items():
        reader = spec.get("reader")
        default = spec.get("default")
        if reader is None:
            continue
        value = _safe_read(reader, artist, default)
        meta = {k: v for k, v in spec.items() if k not in {"getter", "setter", "reader"}}
        out.append({"name": prop, "value": value, **meta})
    return out


def inspector_tree(fig: Figure) -> List[Dict[str, Any]]:
    """Tree of artists for the sidebar — id, kind, label, parent, properties."""
    out: List[Dict[str, Any]] = []
    for entry in walk(fig):
        out.append(
            {
                "id": entry.id,
                "kind": entry.kind,
                "label": entry.label,
                "parent_id": entry.parent_id,
                "properties": serialize_properties(entry.kind, entry.artist),
            }
        )
    return out
