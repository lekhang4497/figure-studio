"""In-memory state for a single ``launch()`` session.

Holds the live ``Figure``, an ordered edit log, an ``id -> artist`` registry, and
an ``asyncio.Lock`` that serialises all mutations. Matplotlib is not thread-safe;
every public mutator awaits the lock.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import matplotlib

# Use the Agg-compatible non-interactive backend explicitly so launch() works
# whether or not the user already imported pyplot with a GUI backend.
from matplotlib.figure import Figure

from . import artist_introspect
from .edit_ops import EditOp, dump_log, load_log, parse

logger = logging.getLogger("figure_studio")


def _embed_fonts_in_pdf() -> None:
    """Force PDF/PS backends to embed fonts as TrueType (Type 42) for LaTeX."""
    try:
        matplotlib.rcParams["pdf.fonttype"] = 42
        matplotlib.rcParams["ps.fonttype"] = 42
    except Exception:  # pragma: no cover
        pass


@dataclass
class FigureState:
    """All editor state for one figure."""

    fig: Figure
    session_path: Optional[Path] = None
    log: List[EditOp] = field(default_factory=list)
    registry: Dict[str, Any] = field(default_factory=dict)
    selected_id: Optional[str] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _save_pending: bool = False
    _save_task: Optional[asyncio.Task] = None
    _save_debounce_s: float = 0.5

    # ------------------------------------------------------------------ init

    def __post_init__(self) -> None:
        _embed_fonts_in_pdf()
        self._refresh_registry()

    def _refresh_registry(self) -> None:
        self.registry = artist_introspect.assign_gids(self.fig)

    # ------------------------------------------------------------------ apply

    async def apply(self, op: EditOp) -> None:
        """Apply a single op atomically, append to log, refresh GIDs."""
        async with self.lock:
            op.apply(self.fig, self.registry)
            self.log.append(op)
            self._refresh_registry()
        self._schedule_save()

    async def apply_many(self, ops: List[EditOp]) -> None:
        """Replay a sequence — used when loading a sidecar at launch."""
        async with self.lock:
            for op in ops:
                try:
                    op.apply(self.fig, self.registry)
                    self.log.append(op)
                    self._refresh_registry()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Skipping replay op %r: %s", op, exc)
        self._schedule_save()

    def replay_sync(self, ops: List[EditOp]) -> None:
        """Apply ops without taking the async lock. Use ONLY before the server starts."""
        for op in ops:
            try:
                op.apply(self.fig, self.registry)
                self.log.append(op)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping replay op %r: %s", op, exc)
        self._refresh_registry()

    async def reset_log(self) -> None:
        """Clear the edit log. Does NOT revert the live figure — re-run the script."""
        async with self.lock:
            self.log = []
            self._refresh_registry()
        if self.session_path and self.session_path.exists():
            try:
                self.session_path.unlink()
            except OSError as exc:
                logger.warning("Could not delete sidecar %s: %s", self.session_path, exc)

    async def set_selected(self, artist_id: Optional[str]) -> None:
        async with self.lock:
            self.selected_id = artist_id

    # ------------------------------------------------------------------ render

    async def render_svg(self) -> str:
        async with self.lock:
            buf = io.StringIO()
            self.fig.savefig(buf, format="svg", bbox_inches=None)
            return buf.getvalue()

    async def render_pdf(
        self,
        only_visible_axes: bool = False,
        pad_inches: Optional[float] = None,
    ) -> bytes:
        """Render the figure to a PDF.

        ``pad_inches`` controls the whitespace margin around the trimmed
        bounding box. ``None`` keeps matplotlib's default (0.1″). ``0``
        gives a truly flush PDF — useful when the figure will be embedded
        in a LaTeX figure environment that adds its own padding.
        """
        async with self.lock:
            if only_visible_axes:
                pdf_bytes = self._render_pdf_visible_axes_only(pad_inches=pad_inches)
            else:
                buf = io.BytesIO()
                kw: Dict[str, Any] = {"bbox_inches": "tight"}
                if pad_inches is not None:
                    kw["pad_inches"] = float(pad_inches)
                self.fig.savefig(buf, format="pdf", **kw)
                pdf_bytes = buf.getvalue()
            return pdf_bytes

    async def render_png(self, dpi: float = 200.0) -> bytes:
        async with self.lock:
            buf = io.BytesIO()
            self.fig.savefig(buf, format="png", dpi=float(dpi), bbox_inches="tight")
            return buf.getvalue()

    # ------------------------------------------------------------------ "appendix" export
    #
    # The killer feature from the plan: hide axes you don't want in the published
    # figure and export a tightened PDF with only the visible ones, repositioned
    # to fill the canvas. Implemented as a code-gen-time transform — we do NOT
    # mutate the live figure.

    def _render_pdf_visible_axes_only(self, pad_inches: Optional[float] = None) -> bytes:
        import copy

        from matplotlib import pyplot as plt

        save_kw: Dict[str, Any] = {"format": "pdf", "bbox_inches": "tight"}
        if pad_inches is not None:
            save_kw["pad_inches"] = float(pad_inches)

        visible = [
            ax for ax in self.fig.axes
            if bool(getattr(ax, "_figure_studio_include_in_export", True))
        ]
        if len(visible) == len(self.fig.axes):
            buf = io.BytesIO()
            self.fig.savefig(buf, **save_kw)
            return buf.getvalue()
        # Hide the excluded axes temporarily and re-tile the visible ones.
        hidden_states: List[tuple] = []
        original_positions: Dict[int, tuple] = {}
        try:
            for ax in self.fig.axes:
                original_positions[id(ax)] = ax.get_position().bounds
                if ax not in visible:
                    hidden_states.append((ax, ax.get_visible()))
                    ax.set_visible(False)
            n = len(visible)
            if n == 0:
                blank = Figure(figsize=self.fig.get_size_inches())
                buf = io.BytesIO()
                blank.savefig(buf, **save_kw)
                return buf.getvalue()
            cols = int(round(n ** 0.5))
            rows = (n + cols - 1) // cols
            margin = 0.08
            cell_w = (1.0 - margin * (cols + 1)) / cols
            cell_h = (1.0 - margin * (rows + 1)) / rows
            for i, ax in enumerate(visible):
                r, c = divmod(i, cols)
                x = margin + c * (cell_w + margin)
                y = 1.0 - margin - (r + 1) * cell_h - r * margin
                ax.set_position([x, y, cell_w, cell_h])
            buf = io.BytesIO()
            self.fig.savefig(buf, **save_kw)
            return buf.getvalue()
        finally:
            for ax, was_visible in hidden_states:
                ax.set_visible(was_visible)
            for ax in self.fig.axes:
                pos = original_positions.get(id(ax))
                if pos is not None:
                    ax.set_position(list(pos))

    # ------------------------------------------------------------------ snapshot

    async def snapshot(self) -> Dict[str, Any]:
        async with self.lock:
            tree = artist_introspect.inspector_tree(self.fig)
            w, h = self.fig.get_size_inches()
            return {
                "tree": tree,
                "figure": {
                    "width_in": float(w),
                    "height_in": float(h),
                    "dpi": float(self.fig.dpi),
                    "axes_count": len(self.fig.axes),
                },
                "selected_id": self.selected_id,
                "log_length": len(self.log),
                "session_path": str(self.session_path) if self.session_path else None,
                "saved_at": _file_mtime_iso(self.session_path),
            }

    # ------------------------------------------------------------------ session

    def session_dict(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "log": dump_log(self.log),
            "selected_id": self.selected_id,
            "saved_at": time.time(),
        }

    def write_session_now(self) -> None:
        if not self.session_path:
            return
        try:
            tmp = self.session_path.with_suffix(self.session_path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.session_dict(), indent=2))
            os.replace(tmp, self.session_path)
        except OSError as exc:
            logger.warning("Could not save sidecar %s: %s", self.session_path, exc)

    @classmethod
    def read_session(cls, path: Path) -> List[EditOp]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read sidecar %s: %s", path, exc)
            return []
        raw = data.get("log", [])
        ops: List[EditOp] = []
        for item in raw:
            try:
                ops.append(parse(item))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Dropping bad op %r: %s", item, exc)
        return ops

    def _schedule_save(self) -> None:
        if not self.session_path:
            return
        # Debounce: cancel a pending save and queue a fresh one.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._save_task and not self._save_task.done():
            self._save_task.cancel()
        self._save_task = loop.create_task(self._save_after_delay())

    async def _save_after_delay(self) -> None:
        try:
            await asyncio.sleep(self._save_debounce_s)
            self.write_session_now()
        except asyncio.CancelledError:
            pass

    @asynccontextmanager
    async def flush_on_exit(self) -> AsyncIterator[None]:
        try:
            yield
        finally:
            self.write_session_now()


def _file_mtime_iso(path: Optional[Path]) -> Optional[float]:
    if path and path.exists():
        try:
            return float(path.stat().st_mtime)
        except OSError:
            return None
    return None
