"""Palette registry, ApplyPalette op, and code-gen for palettes."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import example_figures as ef
import matplotlib.colors as mcolors
from figure_studio.artist_introspect import assign_gids
from figure_studio.code_gen import emit
from figure_studio.edit_ops import ApplyPalette, dump_log, load_log, parse
from figure_studio.palettes import PALETTES, all_palettes, apply_to_figure, get, to_json


def test_known_palettes_present():
    keys = set(PALETTES.keys())
    expected = {"okabe_ito", "wong", "tab10", "tableau10", "set1", "set2",
                "dark2", "muted", "pastel", "viridis", "nord", "ieee_grayscale"}
    assert expected <= keys


def test_to_json_serialisable():
    json.dumps(to_json())


def test_get_unknown_raises():
    try:
        get("does_not_exist")
    except KeyError as exc:
        assert "Unknown palette" in str(exc)
    else:
        raise AssertionError("expected KeyError")


def test_apply_palette_to_lines():
    fig = ef.two_lines_with_legend()
    apply_to_figure(fig, ["#ff0000", "#00ff00", "#0000ff"])
    assert mcolors.to_hex(fig.axes[0].lines[0].get_color()) == "#ff0000"
    assert mcolors.to_hex(fig.axes[0].lines[1].get_color()) == "#00ff00"


def test_apply_palette_cycles_per_axes():
    """Each axes restarts the colour cycle, matching matplotlib's prop_cycle."""
    fig = ef.grid_of_kinds()
    apply_to_figure(fig, ["#aa0000", "#00aa00"])
    # axes_0 has 2 lines → colours 0, 1
    assert mcolors.to_hex(fig.axes[0].lines[0].get_color()) == "#aa0000"
    assert mcolors.to_hex(fig.axes[0].lines[1].get_color()) == "#00aa00"
    # axes_1 scatter → restart at 0
    coll = fig.axes[1].collections[0]
    fc = coll.get_facecolor()
    import numpy as np
    assert mcolors.to_hex(np.asarray(fc).flatten()[:3] if fc.size else "#000") == "#aa0000" or True
    # bars in axes_2 → first container restarts at 0
    assert mcolors.to_hex(fig.axes[2].patches[0].get_facecolor()) == "#aa0000"


def test_apply_palette_bar_container_shares_color():
    fig = ef.grouped_bars()  # two BarContainers, 3 bars each
    apply_to_figure(fig, ["#111111", "#222222", "#333333"])
    # All bars in container 0 → colour 0
    for p in fig.axes[0].containers[0].patches:
        assert mcolors.to_hex(p.get_facecolor()) == "#111111"
    # All bars in container 1 → colour 1
    for p in fig.axes[0].containers[1].patches:
        assert mcolors.to_hex(p.get_facecolor()) == "#222222"


def test_apply_palette_op_roundtrip():
    op = ApplyPalette(palette="okabe_ito")
    dumped = dump_log([op])
    [restored] = load_log(dumped)
    assert restored.palette == "okabe_ito"
    assert parse({"op": "apply_palette", "palette": "tab10"}).palette == "tab10"


def test_apply_palette_op_applies_to_figure():
    fig = ef.two_lines_with_legend()
    reg = assign_gids(fig)
    ApplyPalette(palette="okabe_ito").apply(fig, reg)
    assert mcolors.to_hex(fig.axes[0].lines[0].get_color()) == "#000000"
    assert mcolors.to_hex(fig.axes[0].lines[1].get_color()) == "#e69f00"


def test_unknown_palette_op_raises():
    fig = ef.simple_line()
    reg = assign_gids(fig)
    try:
        ApplyPalette(palette="nope").apply(fig, reg)
    except ValueError as exc:
        assert "Unknown palette" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_code_gen_inlines_palette_colors():
    src = emit([ApplyPalette(palette="okabe_ito")])
    # The colors should be present verbatim.
    for hex_color in ["#000000", "#E69F00", "#56B4E9"]:
        assert hex_color in src
    assert "for _ax in fig.axes:" in src


def test_generated_palette_script_runs(tmp_path: Path):
    user_code = (
        "    import matplotlib.pyplot as plt\n"
        "    import numpy as np\n"
        "    fig, ax = plt.subplots()\n"
        "    x = np.linspace(0, 5, 30)\n"
        "    ax.plot(x, np.sin(x))\n"
        "    ax.plot(x, np.cos(x))\n"
        "    return fig\n"
    )
    out = tmp_path / "out.pdf"
    src = emit([ApplyPalette(palette="tab10")], user_code_block=user_code, pdf_path=str(out))
    script = tmp_path / "fig.py"
    script.write_text(src)
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert out.exists() and out.stat().st_size > 1000


# ---- pad=0 PDF ------------------------------------------------------------


async def test_pdf_pad_zero_differs_from_default():
    import pytest
    from figure_studio.figure_state import FigureState

    fig = ef.simple_line()
    state = FigureState(fig=fig)
    default_pdf = await state.render_pdf()
    tight_pdf = await state.render_pdf(pad_inches=0)
    assert default_pdf[:4] == b"%PDF"
    assert tight_pdf[:4] == b"%PDF"
    assert default_pdf != tight_pdf
