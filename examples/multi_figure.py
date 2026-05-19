"""Multi-figure session demo.

Run this in two terminals.

Terminal 1 — start the long-running server::

    figure-studio serve --port 8765

Terminal 2 — push figures into the session::

    python examples/multi_figure.py
"""
import matplotlib.pyplot as plt
import numpy as np

import figure_studio


def main() -> None:
    rng = np.random.default_rng(0)

    fig1, ax = plt.subplots(figsize=(5, 3))
    x = np.linspace(0, 4 * np.pi, 200)
    ax.plot(x, np.sin(x), label="sin")
    ax.plot(x, np.cos(x), label="cos")
    ax.set_title("Trig")
    ax.legend()

    fig2, ax = plt.subplots(figsize=(5, 3))
    ax.bar(["Q1", "Q2", "Q3", "Q4"], [3.2, 4.1, 2.9, 3.7])
    ax.set_title("Quarterly accuracy")

    fig3, axes = plt.subplots(2, 2, figsize=(7, 4.5))
    axes[0, 0].plot(x, np.sin(x)); axes[0, 0].set_title("sin")
    axes[0, 1].scatter(rng.standard_normal(40), rng.standard_normal(40), s=14, alpha=0.7)
    axes[0, 1].set_title("scatter")
    axes[1, 0].bar(["a", "b", "c"], [3, 1, 2]); axes[1, 0].set_title("bars")
    axes[1, 1].plot(x, np.exp(-x / 5) * np.sin(x)); axes[1, 1].set_title("damped")
    fig3.tight_layout()

    # autostart=True spins up a local server on port 8766 if you haven't already.
    session = figure_studio.connect(port=8766)
    session.add(fig1, name="trig")
    session.add(fig2, name="bars")
    session.add(fig3, name="grid")

    print(f"open in browser: {session.url()}")
    print(f"figures: {session.list()}")


if __name__ == "__main__":
    main()
