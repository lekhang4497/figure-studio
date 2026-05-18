"""Emit a self-contained Python file from an edit log.

The output has **no runtime dependency on figure_studio** — it imports only matplotlib.
The artist IDs we use during editing are deterministic from walk order, so we
translate each ``axes_0_line_2`` style id directly into a positional access path
like ``fig.axes[0].lines[2]``. That keeps the generated file readable instead of
shipping a copy of the introspection walker.

The generator is deterministic and snapshot-testable: same edit log + same
``user_code_block`` produces byte-identical output.
"""
from __future__ import annotations

import json
import re
from textwrap import indent
from typing import Any, Dict, List, Optional, Tuple

from .edit_ops import (
    DisableAutoLayout,
    EditOp,
    SetFigureDpi,
    SetFigureSize,
    SetProperty,
)

# ---------------------------------------------------------------------------
# ID → access path
# ---------------------------------------------------------------------------

_ID_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"^axes_(\d+)$"),                "fig.axes[{0}]"),
    (re.compile(r"^axes_(\d+)_title$"),          "fig.axes[{0}].title"),
    (re.compile(r"^axes_(\d+)_xlabel_text$"),    "fig.axes[{0}].xaxis.label"),
    (re.compile(r"^axes_(\d+)_ylabel_text$"),    "fig.axes[{0}].yaxis.label"),
    (re.compile(r"^axes_(\d+)_line_(\d+)$"),     "fig.axes[{0}].lines[{1}]"),
    (re.compile(r"^axes_(\d+)_scatter_(\d+)$"),  "fig.axes[{0}].collections[{1}]"),
    (re.compile(r"^axes_(\d+)_bargroup_(\d+)$"), "fig.axes[{0}].containers[{1}]"),
    (re.compile(r"^axes_(\d+)_bar_(\d+)$"),      "fig.axes[{0}].patches[{1}]"),
    (re.compile(r"^axes_(\d+)_text_(\d+)$"),     "fig.axes[{0}].texts[{1}]"),
    (re.compile(r"^axes_(\d+)_legend$"),         "fig.axes[{0}].get_legend()"),
    (re.compile(r"^fig_text_(\d+)$"),            "fig.texts[{0}]"),
    (re.compile(r"^fig_legend_(\d+)$"),          "fig.legends[{0}]"),
]


def artist_access(artist_id: str) -> str:
    for pattern, template in _ID_PATTERNS:
        m = pattern.match(artist_id)
        if m:
            return template.format(*m.groups())
    raise ValueError(f"Cannot translate artist id {artist_id!r} into an access path.")


# ---------------------------------------------------------------------------
# Property → setter call
# ---------------------------------------------------------------------------

_LEGEND_LOC_CODE = {
    "best": 0, "upper right": 1, "upper left": 2, "lower left": 3, "lower right": 4,
    "right": 5, "center left": 6, "center right": 7, "lower center": 8, "upper center": 9,
    "center": 10,
}


def _format_value(value: Any) -> str:
    """JSON-safe literal repr — round-trips through json.dumps so dicts/lists are clean."""
    return json.dumps(value)


def _emit_set_property(op: SetProperty) -> List[str]:
    access = artist_access(op.artist_id)
    val = _format_value(op.value)

    if op.kind == "BarGroup":
        # Fan a single property write out to every bar in the container.
        setter_name = f"set_{op.name}"
        return [
            f"for _p in {access}.patches:",
            f"    _p.{setter_name}({val})",
        ]

    if op.kind == "Legend" and op.name == "loc":
        code = _LEGEND_LOC_CODE.get(str(op.value), 0)
        return [f"_leg = {access}", f"if _leg is not None:", f"    _leg._loc = {code}"]
    if op.kind == "Legend" and op.name == "ncol":
        n = max(1, int(op.value))
        return [
            f"_leg = {access}",
            f"if _leg is not None:",
            f"    if hasattr(_leg, '_ncols'): _leg._ncols = {n}",
            f"    _leg._ncol = {n}",
        ]
    if op.kind == "Legend" and op.name == "title":
        return [f"_leg = {access}", f"if _leg is not None:", f"    _leg.set_title({val})"]
    if op.kind == "Axes" and op.name == "grid":
        return [f"{access}.grid({val})"]
    if op.kind == "Axes" and op.name == "include_in_export":
        return [f"{access}._figure_studio_include_in_export = {val}"]
    if op.kind == "PathCollection" and op.name == "sizes":
        return [f"{access}.set_sizes([{val}])"]
    # Fall through — generic setter name from the schema is "set_<name>".
    setter_name = f"set_{op.name}"
    return [f"{access}.{setter_name}({val})"]


def _emit_op(op: EditOp) -> List[str]:
    if isinstance(op, SetProperty):
        return _emit_set_property(op)
    if isinstance(op, SetFigureSize):
        return [f"fig.set_size_inches({op.width_in}, {op.height_in}, forward=True)"]
    if isinstance(op, SetFigureDpi):
        return [f"fig.set_dpi({op.dpi})"]
    if isinstance(op, DisableAutoLayout):
        return [
            "try:",
            "    fig.set_layout_engine('none')",
            "except Exception:",
            "    pass",
        ]
    raise ValueError(f"Unknown op type: {type(op).__name__}")


# ---------------------------------------------------------------------------
# File assembly
# ---------------------------------------------------------------------------

_DEFAULT_BUILD_STUB = """    # === Your original figure code goes here ===
    # Paste in (or import from your script) the matplotlib code that creates `fig`.
    raise NotImplementedError(
        "Replace the body of build_figure() with your plotting code."
    )
    # return fig
"""

_HEADER = '''"""Generated by figure-studio.

Re-creates the styled figure by replaying the edits captured in the editor onto a
freshly built figure. The only runtime dependency is matplotlib.

Usage:
    python figure.py              # writes figure.pdf
    python figure.py out.pdf      # writes the given path

If any axes are marked ``include_in_export=False`` a second file
``figure_main.pdf`` is also written, with those axes hidden and the rest re-tiled.
"""
import sys

import matplotlib
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"] = 42

import matplotlib.pyplot as plt
'''


_HIDE_AND_TILE = '''
def _save_main_only(fig, path):
    """Hide axes flagged ``_figure_studio_include_in_export = False`` and re-tile the rest."""
    hidden = [
        ax for ax in fig.axes
        if not bool(getattr(ax, "_figure_studio_include_in_export", True))
    ]
    if not hidden:
        return
    visible = [ax for ax in fig.axes if ax not in hidden]
    if not visible:
        return
    original_positions = {id(ax): ax.get_position().bounds for ax in fig.axes}
    original_visibility = {id(ax): ax.get_visible() for ax in hidden}
    try:
        for ax in hidden:
            ax.set_visible(False)
        n = len(visible)
        cols = max(1, int(round(n ** 0.5)))
        rows = (n + cols - 1) // cols
        margin = 0.08
        cell_w = (1.0 - margin * (cols + 1)) / cols
        cell_h = (1.0 - margin * (rows + 1)) / rows
        for i, ax in enumerate(visible):
            r, c = divmod(i, cols)
            x = margin + c * (cell_w + margin)
            y = 1.0 - margin - (r + 1) * cell_h - r * margin
            ax.set_position([x, y, cell_w, cell_h])
        fig.savefig(path, bbox_inches="tight")
    finally:
        for ax in hidden:
            ax.set_visible(original_visibility[id(ax)])
        for ax in fig.axes:
            pos = original_positions.get(id(ax))
            if pos is not None:
                ax.set_position(list(pos))
'''


def emit(
    log: List[EditOp],
    user_code_block: Optional[str] = None,
    *,
    pdf_path: str = "figure.pdf",
) -> str:
    """Return the full Python source for a script that reproduces the edited figure."""
    build_body = user_code_block if user_code_block is not None else _DEFAULT_BUILD_STUB

    lines: List[str] = []
    for op in log:
        lines.extend(_emit_op(op))
    apply_body = indent("\n".join(lines) or "pass", "    ")

    sections: List[str] = [
        _HEADER,
        "def build_figure():",
        build_body.rstrip() + "\n",
        "",
        "def _apply_figure_studio_edits(fig):",
        f'    """Generated body — {len(log)} edit(s)."""',
        apply_body,
        "",
        _HIDE_AND_TILE.strip(),
        "",
        "",
        "def main(pdf_path=" + repr(pdf_path) + "):",
        "    fig = build_figure()",
        "    _apply_figure_studio_edits(fig)",
        "    fig.savefig(pdf_path, bbox_inches='tight')",
        '    main_path = pdf_path.replace(".pdf", "_main.pdf") if pdf_path.endswith(".pdf") else pdf_path + "_main.pdf"',
        "    _save_main_only(fig, main_path)",
        "    return pdf_path",
        "",
        "",
        'if __name__ == "__main__":',
        "    main(sys.argv[1] if len(sys.argv) > 1 else " + repr(pdf_path) + ")",
        "",
    ]
    return "\n".join(sections)


def emit_for_session(
    log: List[EditOp],
    *,
    pdf_path: str = "figure.pdf",
    user_code_block: Optional[str] = None,
) -> str:
    """Convenience wrapper used by the server."""
    return emit(log, user_code_block=user_code_block, pdf_path=pdf_path)
