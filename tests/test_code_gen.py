"""code_gen produces self-contained Python that recreates the styled figure."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import matplotlib.colors as mcolors
from figure_studio.code_gen import artist_access, emit
from figure_studio.edit_ops import (
    DisableAutoLayout,
    SetFigureSize,
    SetProperty,
)


def test_artist_access_handles_known_patterns():
    assert artist_access("axes_0") == "fig.axes[0]"
    assert artist_access("axes_0_line_2") == "fig.axes[0].lines[2]"
    assert artist_access("axes_3_scatter_0") == "fig.axes[3].collections[0]"
    assert artist_access("axes_1_bar_4") == "fig.axes[1].patches[4]"
    assert artist_access("axes_2_bargroup_1") == "fig.axes[2].containers[1]"
    assert artist_access("axes_0_text_1") == "fig.axes[0].texts[1]"
    assert artist_access("axes_2_title") == "fig.axes[2].title"
    assert artist_access("axes_0_xlabel_text") == "fig.axes[0].xaxis.label"
    assert artist_access("axes_0_legend") == "fig.axes[0].get_legend()"
    assert artist_access("fig_text_0") == "fig.texts[0]"


def test_bargroup_emits_loop_over_patches():
    src = emit([
        SetProperty(artist_id="axes_0_bargroup_0", kind="BarGroup", name="facecolor", value="#cc3344"),
    ])
    assert "for _p in fig.axes[0].containers[0].patches:" in src
    assert '_p.set_facecolor("#cc3344")' in src


def test_emit_is_deterministic():
    log = [
        SetProperty(artist_id="axes_0_line_0", kind="Line2D", name="color", value="#ff0000"),
        SetFigureSize(width_in=6.0, height_in=4.0),
    ]
    a = emit(log, user_code_block="    import matplotlib.pyplot as plt\n    fig, ax = plt.subplots()\n    ax.plot([0,1],[0,1])\n    return fig\n")
    b = emit(log, user_code_block="    import matplotlib.pyplot as plt\n    fig, ax = plt.subplots()\n    ax.plot([0,1],[0,1])\n    return fig\n")
    assert a == b


def test_generated_script_runs_standalone(tmp_path: Path):
    user_code = (
        "    import matplotlib.pyplot as plt\n"
        "    import numpy as np\n"
        "    fig, ax = plt.subplots()\n"
        "    x = np.linspace(0, 5, 30)\n"
        "    ax.plot(x, np.sin(x), label='sin')\n"
        "    ax.plot(x, np.cos(x), label='cos')\n"
        "    ax.legend()\n"
        "    return fig\n"
    )
    log = [
        SetProperty(artist_id="axes_0_line_0", kind="Line2D", name="color", value="#ff0000"),
        SetProperty(artist_id="axes_0_line_1", kind="Line2D", name="linewidth", value=2.5),
        SetProperty(artist_id="axes_0", kind="Axes", name="title", value="Generated"),
        SetProperty(artist_id="axes_0_legend", kind="Legend", name="loc", value="lower right"),
        SetFigureSize(width_in=6.0, height_in=4.0),
        DisableAutoLayout(),
    ]
    src = emit(log, user_code_block=user_code, pdf_path=str(tmp_path / "out.pdf"))
    script_path = tmp_path / "figure.py"
    script_path.write_text(src)
    proc = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)
    assert proc.returncode == 0, f"script failed:\n{proc.stdout}\n{proc.stderr}"
    pdf_path = tmp_path / "out.pdf"
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 1000
    # PDF magic bytes
    with open(pdf_path, "rb") as f:
        assert f.read(4) == b"%PDF"


def test_legend_loc_emits_numeric_code():
    log = [SetProperty(artist_id="axes_0_legend", kind="Legend", name="loc", value="lower right")]
    src = emit(log)
    # lower right is code 4
    assert "_leg._loc = 4" in src


def test_include_in_export_helper_present():
    log = []
    src = emit(log)
    assert "_save_main_only" in src
    assert "_figure_studio_include_in_export" in src


def test_emit_with_no_edits_still_runs(tmp_path: Path):
    user_code = (
        "    import matplotlib.pyplot as plt\n"
        "    fig, ax = plt.subplots()\n"
        "    ax.plot([0,1],[1,0])\n"
        "    return fig\n"
    )
    src = emit([], user_code_block=user_code, pdf_path=str(tmp_path / "empty.pdf"))
    script_path = tmp_path / "empty.py"
    script_path.write_text(src)
    proc = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "empty.pdf").exists()
