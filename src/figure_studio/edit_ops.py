"""Edit operations.

Operations are pydantic models so they validate at the boundary and serialise to
JSON for the session sidecar. They are **idempotent** and **absolute** — replaying
an edit log in order produces a deterministic figure regardless of how many times
the same property was edited at the source. The frontend computes the final
position when the user drops a drag handle; we never store ``dx, dy`` deltas.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.legend import Legend
from pydantic import BaseModel, Field

from .artist_introspect import KIND_SCHEMAS

# ---------------------------------------------------------------------------
# Custom setters dispatched by ``_set_*`` names in the schema
# ---------------------------------------------------------------------------

_LEGEND_LOC_CODE = {
    "best": 0, "upper right": 1, "upper left": 2, "lower left": 3, "lower right": 4,
    "right": 5, "center left": 6, "center right": 7, "lower center": 8, "upper center": 9,
    "center": 10,
}


def _set_legend_loc(legend: Legend, value: str) -> None:
    code = _LEGEND_LOC_CODE.get(str(value), 0)
    legend._loc = code  # noqa: SLF001  (private but stable for years)


def _set_legend_ncol(legend: Legend, value: int) -> None:
    # ``_ncols`` is the modern attribute (matplotlib >=3.6); ``_ncol`` is the legacy one.
    n = max(1, int(value))
    if hasattr(legend, "_ncols"):
        legend._ncols = n  # noqa: SLF001
    legend._ncol = n  # noqa: SLF001


def _set_legend_title(legend: Legend, value: str) -> None:
    legend.set_title(str(value))


def _set_axes_grid(axes: Axes, value: bool) -> None:
    axes.grid(bool(value))


def _set_axes_include_in_export(axes: Axes, value: bool) -> None:
    axes._figure_studio_include_in_export = bool(value)  # noqa: SLF001


_CUSTOM_SETTERS: Dict[str, Any] = {
    "_set_loc": _set_legend_loc,
    "_set_ncol": _set_legend_ncol,
    "_set_title": _set_legend_title,
    "_set_grid": _set_axes_grid,
    "_set_include_in_export": _set_axes_include_in_export,
}


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------


def _coerce(prop_type: str, value: Any) -> Any:
    """Coerce JSON-typed values into the form the matplotlib setter expects."""
    if prop_type == "float":
        return float(value)
    if prop_type == "int":
        return int(value)
    if prop_type == "bool":
        if isinstance(value, str):
            return value.lower() in {"true", "1", "yes", "on"}
        return bool(value)
    if prop_type == "color":
        return str(value)
    if prop_type in {"string", "enum"}:
        return str(value)
    if prop_type == "tuple_float2":
        a, b = value
        return [float(a), float(b)]
    if prop_type == "tuple_float4":
        a, b, c, d = value
        return [float(a), float(b), float(c), float(d)]
    return value


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


class SetProperty(BaseModel):
    """Set a single editable property on an artist by id."""

    op: Literal["set_property"] = "set_property"
    artist_id: str
    kind: str          # repeats artist kind so we can validate against the schema offline
    name: str
    value: Any

    def apply(self, fig: Figure, registry: Dict[str, Any]) -> None:
        artist = registry.get(self.artist_id)
        if artist is None:
            # Artist disappeared (figure shape changed). Skip rather than crash;
            # the user's underlying code is the source of truth.
            return
        schema = KIND_SCHEMAS.get(self.kind, {})
        spec = schema.get(self.name)
        if spec is None:
            raise ValueError(f"Unknown property '{self.name}' for kind '{self.kind}'.")
        coerced = _coerce(spec.get("type", "string"), self.value)

        # BarGroup is a synthetic kind that wraps a BarContainer; fan the write
        # out to each Rectangle in the container using its standard setter.
        if self.kind == "BarGroup":
            rect_spec = KIND_SCHEMAS["Rectangle"].get(self.name)
            if rect_spec is None:
                raise ValueError(f"BarGroup cannot set '{self.name}': not a Rectangle property.")
            rect_setter = rect_spec.get("setter")
            for patch in getattr(artist, "patches", []) or []:
                if rect_setter in _CUSTOM_SETTERS:
                    _CUSTOM_SETTERS[rect_setter](patch, coerced)
                else:
                    getattr(patch, rect_setter)(coerced)
            return

        setter_name = spec.get("setter")
        if setter_name is None:
            raise ValueError(f"Property '{self.name}' on '{self.kind}' is read-only.")
        if setter_name in _CUSTOM_SETTERS:
            _CUSTOM_SETTERS[setter_name](artist, coerced)
            return
        # Special case: sizes for PathCollection — broadcast scalar to a uniform array
        if self.kind == "PathCollection" and self.name == "sizes":
            getattr(artist, setter_name)([coerced])
            return
        getattr(artist, setter_name)(coerced)


class SetFigureSize(BaseModel):
    op: Literal["set_figure_size"] = "set_figure_size"
    width_in: float = Field(gt=0)
    height_in: float = Field(gt=0)

    def apply(self, fig: Figure, registry: Dict[str, Any]) -> None:
        fig.set_size_inches(float(self.width_in), float(self.height_in), forward=True)


class SetFigureDpi(BaseModel):
    op: Literal["set_figure_dpi"] = "set_figure_dpi"
    dpi: float = Field(gt=0)

    def apply(self, fig: Figure, registry: Dict[str, Any]) -> None:
        fig.set_dpi(float(self.dpi))


class DisableAutoLayout(BaseModel):
    """Disable constrained/tight layout so manual axes positions stick.

    The frontend emits this once before any axes-position edit, matching the plan's
    decision: 'on first edit to any axes position, disable both layout managers and
    emit a warning'. Idempotent — safe to replay.
    """

    op: Literal["disable_auto_layout"] = "disable_auto_layout"

    def apply(self, fig: Figure, registry: Dict[str, Any]) -> None:
        try:
            fig.set_layout_engine("none")
        except Exception:
            # Older matplotlib: poke the underlying attrs.
            try:
                fig.set_constrained_layout(False)
            except Exception:
                pass
            try:
                fig.set_tight_layout(False)
            except Exception:
                pass


class ApplyPalette(BaseModel):
    """Assign a colour palette to every series in the figure.

    The palette is identified by ``key`` (see :mod:`figure_studio.palettes`).
    Replays at code-gen time as an inlined loop, so the exported script has
    no figure_studio runtime dependency.
    """

    op: Literal["apply_palette"] = "apply_palette"
    palette: str

    def apply(self, fig: Figure, registry: Dict[str, Any]) -> None:
        from .palettes import apply_to_figure, get
        try:
            pal = get(self.palette)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        apply_to_figure(fig, pal.colors)


EditOp = Union[
    SetProperty, SetFigureSize, SetFigureDpi, DisableAutoLayout, ApplyPalette,
]


_OP_BY_NAME: Dict[str, Any] = {
    "set_property": SetProperty,
    "set_figure_size": SetFigureSize,
    "set_figure_dpi": SetFigureDpi,
    "disable_auto_layout": DisableAutoLayout,
    "apply_palette": ApplyPalette,
}


def parse(payload: Dict[str, Any]) -> EditOp:
    """Decode a raw dict (e.g. from JSON) into the right concrete op."""
    op_name = payload.get("op")
    cls = _OP_BY_NAME.get(op_name)
    if cls is None:
        raise ValueError(f"Unknown edit op '{op_name}'.")
    return cls(**payload)


def dump_log(log: List[EditOp]) -> List[Dict[str, Any]]:
    return [op.model_dump(mode="json") for op in log]


def load_log(raw: List[Dict[str, Any]]) -> List[EditOp]:
    return [parse(item) for item in raw]
