"""Curated color palettes for academic / professional figures.

Each palette is a fixed list of hex strings. Applying a palette assigns
colours to artists in walk order, restarting the cycle per axes (matching
matplotlib's default ``axes.prop_cycle`` semantics). Bars inside the same
``BarContainer`` share a color so a grouped bar chart still reads as one
series per palette entry.

Sources verified against published palettes (May 2026):
- **Okabe-Ito** — colourblind-safe 8-color palette from Okabe & Ito 2002,
  widely used in scientific publications.
- **Wong** — the Nature Methods recommendation (Wong 2011) — essentially
  the Okabe-Ito palette plus a sky-blue addition. Recognised as accessible.
- **Tableau 10 / 20** — corporate standard, good contrast.
- **ColorBrewer** Set1, Set2, Dark2 — Cynthia Brewer's perceptually-tuned
  qualitative palettes (cartographic standard).
- **matplotlib tab10** — the matplotlib >=2.0 default.
- **seaborn muted / pastel** — softer alternatives.
- **viridis** — perceptually uniform sequential (good for ordered data).
- **Nord** — popular "cool" editor palette, accessible on dark themes.
- **IEEE grayscale** — for monochrome publication.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class Palette:
    key: str
    label: str
    colors: List[str]
    notes: str = ""
    colorblind_safe: bool = False


PALETTES: Dict[str, Palette] = {
    "okabe_ito": Palette(
        "okabe_ito", "Okabe–Ito (colour-blind safe)",
        ["#000000", "#E69F00", "#56B4E9", "#009E73",
         "#F0E442", "#0072B2", "#D55E00", "#CC79A7"],
        notes="8-colour scheme, recommended for scientific publications.",
        colorblind_safe=True,
    ),
    "wong": Palette(
        "wong", "Wong (Nature Methods)",
        ["#000000", "#E69F00", "#56B4E9", "#009E73",
         "#F0E442", "#0072B2", "#D55E00", "#CC79A7"],
        notes="Recommended by Wong (2011) for accessibility.",
        colorblind_safe=True,
    ),
    "tab10": Palette(
        "tab10", "matplotlib tab10 (default)",
        ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
         "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"],
        notes="matplotlib's default since 2.0.",
    ),
    "tableau10": Palette(
        "tableau10", "Tableau 10",
        ["#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
         "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC"],
    ),
    "set1": Palette(
        "set1", "ColorBrewer Set1",
        ["#E41A1C", "#377EB8", "#4DAF4A", "#984EA3", "#FF7F00",
         "#FFFF33", "#A65628", "#F781BF", "#999999"],
        notes="Saturated, high contrast.",
    ),
    "set2": Palette(
        "set2", "ColorBrewer Set2 (soft)",
        ["#66C2A5", "#FC8D62", "#8DA0CB", "#E78AC3", "#A6D854",
         "#FFD92F", "#E5C494", "#B3B3B3"],
        notes="Muted pastels; good for many adjacent regions.",
    ),
    "dark2": Palette(
        "dark2", "ColorBrewer Dark2",
        ["#1B9E77", "#D95F02", "#7570B3", "#E7298A", "#66A61E",
         "#E6AB02", "#A6761D", "#666666"],
    ),
    "muted": Palette(
        "muted", "seaborn muted",
        ["#4878D0", "#EE854A", "#6ACC64", "#D65F5F", "#956CB4",
         "#8C613C", "#DC7EC0", "#797979", "#D5BB67", "#82C6E2"],
    ),
    "pastel": Palette(
        "pastel", "seaborn pastel",
        ["#A1C9F4", "#FFB482", "#8DE5A1", "#FF9F9B", "#D0BBFF",
         "#DEBB9B", "#FAB0E4", "#CFCFCF", "#FFFEA3", "#B9F2F0"],
    ),
    "viridis": Palette(
        "viridis", "viridis (sequential)",
        ["#440154", "#414487", "#2A788E", "#22A884", "#7AD151", "#FDE725"],
        notes="Perceptually uniform; good for ordered categories.",
        colorblind_safe=True,
    ),
    "nord": Palette(
        "nord", "Nord (cool)",
        ["#5E81AC", "#B48EAD", "#A3BE8C", "#EBCB8B", "#D08770",
         "#88C0D0", "#BF616A", "#81A1C1", "#8FBCBB"],
    ),
    "ieee_grayscale": Palette(
        "ieee_grayscale", "IEEE grayscale (mono)",
        ["#000000", "#444444", "#777777", "#AAAAAA", "#CCCCCC"],
        notes="For monochrome publication.",
    ),
}


def all_palettes() -> List[Palette]:
    return list(PALETTES.values())


def get(key: str) -> Palette:
    if key not in PALETTES:
        raise KeyError(f"Unknown palette {key!r}. Known: {sorted(PALETTES)}")
    return PALETTES[key]


def to_json() -> List[Dict[str, object]]:
    return [
        {
            "key": p.key,
            "label": p.label,
            "colors": p.colors,
            "notes": p.notes,
            "colorblind_safe": p.colorblind_safe,
        }
        for p in all_palettes()
    ]


# ---------------------------------------------------------------------------
# Application logic — applied at edit-op time AND in the generated code, so
# the same algorithm lives here as the single source of truth.
# ---------------------------------------------------------------------------


def apply_to_figure(fig, colors: List[str]) -> None:
    """Walk each axes and assign palette colours to its series in order.

    Bars inside the same ``BarContainer`` share a colour so a grouped bar
    chart reads as one series per palette entry. The cycle restarts per
    axes — matplotlib's default behaviour.
    """
    from matplotlib.container import BarContainer

    if not colors:
        return
    for axes in fig.axes:
        i = 0
        for line in axes.lines:
            line.set_color(colors[i % len(colors)])
            i += 1
        for coll in axes.collections:
            color = colors[i % len(colors)]
            try:
                coll.set_facecolor(color)
            except Exception:
                pass
            try:
                coll.set_edgecolor(color)
            except Exception:
                pass
            i += 1
        seen_patches = set()
        for container in getattr(axes, "containers", []) or []:
            if isinstance(container, BarContainer):
                color = colors[i % len(colors)]
                for patch in container.patches:
                    patch.set_facecolor(color)
                    seen_patches.add(id(patch))
                i += 1
        # Patches not in any container (rare — direct ax.add_patch usage).
        for patch in axes.patches:
            if id(patch) in seen_patches:
                continue
            if patch is getattr(axes, "patch", None):
                continue
            patch.set_facecolor(colors[i % len(colors)])
            i += 1
