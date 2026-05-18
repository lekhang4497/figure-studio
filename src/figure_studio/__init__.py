"""figure-studio: a local web UI for visually editing matplotlib figures.

Public surface:
    >>> import figure_studio
    >>> figure_studio.launch(fig)

See ``figure_studio_plan.md`` for the design.
"""
from __future__ import annotations

import inspect
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from matplotlib.figure import Figure

from .figure_state import FigureState
from .server import create_app

__all__ = ["launch", "FigureState", "create_app"]
__version__ = "0.1.0"

logger = logging.getLogger("figure_studio")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[figure-studio] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _caller_script_path() -> Optional[Path]:
    """Walk up the stack and find the first frame that isn't our own package."""
    pkg_root = str(Path(__file__).parent.resolve())
    for frame in inspect.stack():
        fname = frame.filename
        if not fname or fname.startswith("<"):
            continue
        resolved = str(Path(fname).resolve())
        if resolved.startswith(pkg_root):
            continue
        if "site-packages" in resolved and "figure_studio" in resolved:
            continue
        return Path(fname).resolve()
    return None


def _default_session_path(fig: Figure, override: Optional[Path]) -> Optional[Path]:
    if override is not None:
        return Path(override).expanduser().resolve()
    script = _caller_script_path()
    if script is None:
        return None
    # <script>.figure_studio.json next to the calling script
    return script.with_name(script.stem + ".figure_studio.json")


def _is_headless() -> bool:
    if os.environ.get("FIGURE_STUDIO_HEADLESS", "").lower() in {"1", "true", "yes"}:
        return True
    if os.environ.get("SSH_CONNECTION"):
        return True
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return True
    return False


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _pick_port(preferred: int) -> int:
    for candidate in [preferred, *range(preferred + 1, preferred + 20)]:
        if _port_is_free(candidate):
            return candidate
    raise RuntimeError(f"No free port near {preferred}")


def _wait_until_listening(host: str, port: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            try:
                s.connect((host, port))
                return True
            except OSError:
                time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------


def launch(
    fig: Figure,
    *,
    port: int = 8765,
    host: str = "127.0.0.1",
    open_browser: bool = True,
    session_path: Optional[Path] = None,
    log_level: str = "warning",
) -> None:
    """Start the figure-studio editor for ``fig`` and block until Ctrl+C.

    Args:
        fig: the live matplotlib :class:`Figure` to edit. Stays in memory.
        port: preferred TCP port; if busy, the next free one nearby is used.
        host: bind address. ``127.0.0.1`` (default) restricts to localhost.
        open_browser: try to open a browser tab unless the env looks headless.
        session_path: where to read/write the ``.figure_studio.json`` sidecar; default
            is ``<calling-script>.figure_studio.json``.
        log_level: uvicorn log level.
    """
    import uvicorn  # local import keeps cold-start fast for non-launch usage

    if not isinstance(fig, Figure):
        raise TypeError(f"launch() expects a matplotlib Figure, got {type(fig).__name__}")

    chosen_port = _pick_port(port)
    sidecar = _default_session_path(fig, session_path)
    state = FigureState(fig=fig, session_path=sidecar)

    # Replay sidecar before the server starts so the first /api/state already reflects edits.
    if sidecar and sidecar.exists():
        ops = FigureState.read_session(sidecar)
        if ops:
            state.replay_sync(ops)
            logger.info("Replayed %d edit(s) from %s", len(ops), sidecar)

    app = create_app(state)
    url = f"http://{host}:{chosen_port}/"

    headless = _is_headless() or not open_browser

    def _maybe_open() -> None:
        if not _wait_until_listening(host, chosen_port, timeout=8.0):
            logger.warning("Backend did not become ready in time; skipping browser open.")
            return
        if headless:
            return
        try:
            opened = webbrowser.open(url, new=2)
        except Exception:
            opened = False
        if not opened:
            print(f"[figure-studio] open in browser: {url}", flush=True)

    opener_thread = threading.Thread(target=_maybe_open, daemon=True, name="figure-studio-opener")
    opener_thread.start()

    banner = (
        f"\n[figure-studio] editing {len(state.registry)} artist(s) on {fig}\n"
        f"[figure-studio] open: {url}\n"
        f"[figure-studio] session: {sidecar if sidecar else '(not persisted — caller is interactive)'}\n"
        f"[figure-studio] press Ctrl+C to stop.\n"
    )
    print(banner, flush=True)

    try:
        uvicorn.run(
            app,
            host=host,
            port=chosen_port,
            log_level=log_level,
            access_log=False,
        )
    except KeyboardInterrupt:
        pass
    finally:
        state.write_session_now()
