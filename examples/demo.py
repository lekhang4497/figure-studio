"""Demo script for figure-studio.

Run it:

    python examples/demo.py

Opens http://localhost:8765/ in your browser. Edit the figure visually:

- Click any line, bar, scatter, or text to edit it in the right-hand inspector.
- Click a bar to edit the WHOLE bar group at once. Shift-click to edit a single
  bar inside the group; the group's individual bars are also listed under it in
  the left sidebar.
- Use the inspector's **delete** button to clear text or hide an artist; Cmd/Ctrl-Z
  restores it.
- Use the toolbar to set the figure size or pick a LaTeX column-width preset.
- Export the final PDF and/or a self-contained `figure.py` that reproduces it.

A sidecar ``demo.figure_studio.json`` appears next to this file once you make edits;
re-running this script picks up where you left off.
"""
import matplotlib.pyplot as plt
import numpy as np

import figure_studio


def build_figure():
    rng = np.random.default_rng(0)
    fig, axes = plt.subplots(2, 3, figsize=(11, 6))
    fig.suptitle("figure-studio demo")

    # (0,0) Trig — two lines + legend
    ax = axes[0, 0]
    x = np.linspace(0, 4 * np.pi, 200)
    ax.plot(x, np.sin(x), label="sin")
    ax.plot(x, np.cos(x), label="cos")
    ax.set_title("Trig")
    ax.set_xlabel("x"); ax.set_ylabel("y")
    ax.legend()

    # (0,1) Scatter with colored groups
    ax = axes[0, 1]
    n = 60
    ax.scatter(rng.standard_normal(n), rng.standard_normal(n),
               s=18, alpha=0.7, label="A")
    ax.scatter(rng.standard_normal(n) + 1.4, rng.standard_normal(n) - 0.6,
               s=22, alpha=0.7, label="B")
    ax.set_title("Scatter (two clusters)")
    ax.legend()

    # (0,2) Grouped bar chart — three series, four categories
    ax = axes[0, 2]
    categories = ["Q1", "Q2", "Q3", "Q4"]
    a = [3.2, 4.1, 2.9, 3.7]
    b = [2.4, 3.5, 3.1, 4.2]
    c = [1.7, 2.6, 2.8, 3.4]
    xp = np.arange(len(categories))
    w = 0.25
    ax.bar(xp - w, a, w, label="Model A")
    ax.bar(xp,     b, w, label="Model B")
    ax.bar(xp + w, c, w, label="Model C")
    ax.set_xticks(xp); ax.set_xticklabels(categories)
    ax.set_title("Grouped bars")
    ax.set_ylabel("accuracy")
    ax.legend()

    # (1,0) Dual axis — bar (revenue) + line (growth) on twin axes
    ax = axes[1, 0]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
    revenue = [12.0, 19.0, 15.0, 22.0, 28.0, 31.0]
    growth_pct = [10.0, 58.0, -21.0, 47.0, 27.0, 11.0]
    ax.bar(months, revenue, color="steelblue", alpha=0.85, label="Revenue")
    ax.set_ylabel("Revenue ($M)", color="steelblue")
    ax.tick_params(axis="y", labelcolor="steelblue")
    ax2 = ax.twinx()
    ax2.plot(months, growth_pct, color="darkorange", marker="o", linewidth=2.0, label="MoM %")
    ax2.set_ylabel("Growth (%)", color="darkorange")
    ax2.tick_params(axis="y", labelcolor="darkorange")
    ax.set_title("Dual axis — bar + line")

    # (1,1) Damped oscillator
    ax = axes[1, 1]
    ax.plot(x, np.exp(-x / 5.0) * np.sin(x))
    ax.set_title("Damped oscillator")
    ax.set_xlabel("t")

    # (1,2) Stacked bar
    ax = axes[1, 2]
    labels = ["A", "B", "C", "D"]
    low = np.array([3, 1, 4, 2])
    mid = np.array([2, 3, 1, 2])
    high = np.array([1, 2, 2, 3])
    ax.bar(labels, low, label="Low")
    ax.bar(labels, mid, bottom=low, label="Mid")
    ax.bar(labels, high, bottom=low + mid, label="High")
    ax.set_title("Stacked bars")
    ax.legend()

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return fig


if __name__ == "__main__":
    fig = build_figure()
    figure_studio.launch(fig)
