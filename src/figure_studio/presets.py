"""LaTeX column-width presets in inches.

Sources verified against the current author kits (May 2026):
- ACL/EMNLP: ``acl_latex.sty`` — \\columnsep=0.31in, total textwidth ~6.30in,
  single-column width ~3.30in. We round to common community values.
- NeurIPS: ``neurips_2024.sty`` — \\textwidth ~5.50in (single column page).
- IEEE conf/journal (``IEEEtran.cls``): single column 3.50in, double column 7.16in.

Heights are sensible defaults (golden-ratio-ish); the user will tweak per figure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class FigurePreset:
    """Named ``(width_in, height_in)`` template for a paper figure size."""

    key: str
    label: str
    width_in: float
    height_in: float
    notes: str = ""


PRESETS: Dict[str, FigurePreset] = {
    "acl_single":   FigurePreset("acl_single",   "ACL / EMNLP single column",  3.30, 2.40, "fits one ACL column with default 11pt body text"),
    "acl_double":   FigurePreset("acl_double",   "ACL / EMNLP double column",  6.75, 3.50, "spans both ACL columns"),
    "neurips":      FigurePreset("neurips",      "NeurIPS (single column)",    5.50, 3.40),
    "iclr":         FigurePreset("iclr",         "ICLR (textwidth)",           5.50, 3.40),
    "ieee_single":  FigurePreset("ieee_single",  "IEEE single column",         3.50, 2.50),
    "ieee_double":  FigurePreset("ieee_double",  "IEEE double column",         7.16, 3.50),
    "a4_full":      FigurePreset("a4_full",      "A4 textwidth (~6.30in)",     6.30, 4.00),
    "letter_full":  FigurePreset("letter_full",  "US letter textwidth (~6.5in)", 6.50, 4.00),
}


def all_presets() -> List[FigurePreset]:
    return list(PRESETS.values())


def get(key: str) -> FigurePreset:
    if key not in PRESETS:
        raise KeyError(f"Unknown preset {key!r}. Known: {sorted(PRESETS)}")
    return PRESETS[key]


def to_json() -> List[Dict[str, object]]:
    return [
        {
            "key": p.key,
            "label": p.label,
            "width_in": p.width_in,
            "height_in": p.height_in,
            "notes": p.notes,
        }
        for p in all_presets()
    ]
