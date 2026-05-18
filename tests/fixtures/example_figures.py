"""Reusable figure factories for tests.

Each function returns a freshly-built ``matplotlib.figure.Figure`` so tests can
assume a clean slate.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402


def simple_line() -> "matplotlib.figure.Figure":
    fig, ax = plt.subplots(figsize=(4, 3))
    x = np.linspace(0, 5, 30)
    ax.plot(x, np.sin(x), label="sin")
    ax.set_title("Trig")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    return fig


def two_lines_with_legend() -> "matplotlib.figure.Figure":
    fig, ax = plt.subplots(figsize=(4, 3))
    x = np.linspace(0, 5, 30)
    ax.plot(x, np.sin(x), label="sin")
    ax.plot(x, np.cos(x), label="cos")
    ax.legend()
    return fig


def grid_of_kinds() -> "matplotlib.figure.Figure":
    rng = np.random.default_rng(0)
    fig, axes = plt.subplots(2, 2, figsize=(6, 4))
    x = np.linspace(0, 10, 50)
    axes[0, 0].plot(x, np.sin(x), label="sin")
    axes[0, 0].plot(x, np.cos(x), label="cos")
    axes[0, 0].legend()
    axes[0, 0].set_title("Lines")

    axes[0, 1].scatter(rng.random(20), rng.random(20), label="pts")
    axes[0, 1].set_title("Scatter")

    axes[1, 0].bar(["a", "b", "c"], [3, 1, 2])
    axes[1, 0].set_title("Bars")

    axes[1, 1].text(0.5, 0.5, "hi", ha="center")
    axes[1, 1].set_title("Text")
    return fig


def scatter_only() -> "matplotlib.figure.Figure":
    rng = np.random.default_rng(42)
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.scatter(rng.random(10), rng.random(10))
    return fig


def grouped_bars() -> "matplotlib.figure.Figure":
    fig, ax = plt.subplots(figsize=(4, 3))
    x = np.arange(3)
    w = 0.35
    ax.bar(x - w / 2, [3, 4, 5], w, label="A")
    ax.bar(x + w / 2, [2, 3, 4], w, label="B")
    ax.legend()
    return fig


def dual_axis() -> "matplotlib.figure.Figure":
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.bar(["a", "b", "c"], [1, 2, 3], color="steelblue")
    ax2 = ax.twinx()
    ax2.plot(["a", "b", "c"], [0.1, 0.5, 0.3], color="orange", marker="o")
    return fig
