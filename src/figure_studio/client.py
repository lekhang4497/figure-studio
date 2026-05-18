"""Client-side ``Session`` for talking to a running figure-studio server.

Typical usage from a script or notebook::

    import matplotlib.pyplot as plt
    import figure_studio

    session = figure_studio.connect(port=8765)   # auto-starts a local server if needed
    fig, ax = plt.subplots()
    ax.plot([0, 1], [1, 0])
    session.add(fig, name="lines")
    session.url()              # http://127.0.0.1:8765/?fig=lines
    edited = session.get("lines")   # fetch the edited Figure back
"""
from __future__ import annotations

import logging
import os
import pickle
import socket
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

if TYPE_CHECKING:
    from matplotlib.figure import Figure  # pragma: no cover

logger = logging.getLogger("figure_studio")


# ---------------------------------------------------------------------------
# Low-level HTTP helpers (no external deps — keep client lean)
# ---------------------------------------------------------------------------


def _http(method: str, url: str, *, data: Optional[bytes] = None, content_type: Optional[str] = None, timeout: float = 30.0) -> bytes:
    req = Request(url, data=data, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_json(method: str, url: str, *, json_body: Optional[dict] = None, timeout: float = 30.0) -> Any:
    import json as _json
    data = _json.dumps(json_body).encode() if json_body is not None else None
    raw = _http(method, url, data=data, content_type="application/json" if data else None, timeout=timeout)
    return _json.loads(raw) if raw else None


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class Session:
    """A handle to a figure-studio server. Add figures to it, get them back edited.

    Parameters
    ----------
    host, port:
        Where the server is reachable.
    autostart:
        If True (default) and no server answers at ``host:port``, spawn a
        detached local one. Set to False if you've already started ``figure-studio serve``.
    timeout:
        Seconds to wait for the autostarted server to come up.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        autostart: bool = True,
        timeout: float = 8.0,
    ) -> None:
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self._spawned_proc: Optional[subprocess.Popen] = None
        if not self._ping(timeout=0.4):
            if autostart:
                self._spawn_local_server()
                if not self._wait_until_up(timeout):
                    raise RuntimeError(
                        f"figure-studio server failed to start on {host}:{port} within {timeout}s"
                    )
            else:
                raise RuntimeError(
                    f"No figure-studio server reachable at {host}:{port}. "
                    "Start one with `figure-studio serve --port {port}` or pass autostart=True."
                )

    # -------- server probing & spawn --------

    def _ping(self, *, timeout: float = 0.5) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect((self.host, self.port))
            urlopen(self.base_url + "/healthz", timeout=timeout).read()
            return True
        except (OSError, URLError):
            return False

    def _wait_until_up(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ping(timeout=0.4):
                return True
            time.sleep(0.1)
        return False

    def _spawn_local_server(self) -> None:
        # Spawn the CLI in a detached process so the server outlives the calling script
        # / notebook kernel — that's the whole point of having a shared session.
        cmd = [
            sys.executable, "-m", "figure_studio.cli", "serve",
            "--port", str(self.port),
            "--host", self.host,
            "--no-browser",
        ]
        env = os.environ.copy()
        env.setdefault("FIGURE_STUDIO_HEADLESS", "1")
        kwargs: Dict[str, Any] = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        if os.name == "posix":
            kwargs["start_new_session"] = True
        else:  # pragma: no cover (windows)
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        self._spawned_proc = subprocess.Popen(cmd, **kwargs)
        logger.info("Spawned figure-studio server (pid %s) on %s:%s", self._spawned_proc.pid, self.host, self.port)

    # -------- figure management --------

    def add(self, fig: "Figure", name: Optional[str] = None, *, overwrite: bool = True) -> str:
        """Send ``fig`` to the server and return the registered name."""
        from matplotlib.figure import Figure
        if not isinstance(fig, Figure):
            raise TypeError(f"add() expects a matplotlib Figure, got {type(fig).__name__}")
        target_name = name or self._auto_name()
        data = pickle.dumps(fig)
        url = (
            f"{self.base_url}/api/figures/{urllib.parse.quote(target_name)}"
            f"?overwrite={'true' if overwrite else 'false'}"
        )
        _http("POST", url, data=data, content_type="application/octet-stream", timeout=30)
        return target_name

    def get(self, name: str) -> "Figure":
        """Fetch the live (edited) figure from the server. Returns a fresh ``Figure``."""
        url = f"{self.base_url}/api/figures/{urllib.parse.quote(name)}/pickle"
        raw = _http("GET", url, timeout=30)
        fig = pickle.loads(raw)
        # Re-attach an Agg canvas so the user can ``fig.savefig(...)`` immediately.
        try:
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            FigureCanvasAgg(fig)
        except Exception:  # pragma: no cover
            pass
        return fig

    def remove(self, name: str) -> bool:
        url = f"{self.base_url}/api/figures/{urllib.parse.quote(name)}"
        resp = _http_json("DELETE", url)
        return bool(resp.get("removed"))

    def list(self) -> List[str]:
        """Names of figures currently registered on the server."""
        data = _http_json("GET", f"{self.base_url}/api/figures")
        return [f["name"] for f in data.get("figures", [])]

    def list_meta(self) -> List[Dict[str, Any]]:
        data = _http_json("GET", f"{self.base_url}/api/figures")
        return data.get("figures", [])

    def extract_axes(self, name: str, axes_index: int, as_name: Optional[str] = None) -> str:
        """Server-side: clone an axes into a brand-new figure. Returns the new name."""
        qs = f"?as_name={urllib.parse.quote(as_name)}" if as_name else ""
        url = f"{self.base_url}/api/figures/{urllib.parse.quote(name)}/extract/{int(axes_index)}{qs}"
        resp = _http_json("POST", url, json_body={})
        return resp["name"]

    # -------- exports --------

    def export_pdf(self, name: str, path: str, *, only_visible: bool = False) -> str:
        qs = "?only_visible=true" if only_visible else ""
        url = f"{self.base_url}/api/figures/{urllib.parse.quote(name)}/export/pdf{qs}"
        with open(path, "wb") as f:
            f.write(_http("GET", url, timeout=60))
        return path

    def export_png(self, name: str, path: str, *, dpi: float = 300.0) -> str:
        url = f"{self.base_url}/api/figures/{urllib.parse.quote(name)}/export/png?dpi={dpi}"
        with open(path, "wb") as f:
            f.write(_http("GET", url, timeout=60))
        return path

    def export_code(self, name: str, path: str) -> str:
        url = f"{self.base_url}/api/figures/{urllib.parse.quote(name)}/export/code"
        with open(path, "wb") as f:
            f.write(_http("GET", url, timeout=30))
        return path

    # -------- URLs & browser open --------

    def url(self, name: Optional[str] = None) -> str:
        if name is None:
            return self.base_url + "/"
        return f"{self.base_url}/?fig={urllib.parse.quote(name)}"

    def open_browser(self, name: Optional[str] = None) -> None:
        webbrowser.open(self.url(name), new=2)

    # -------- notebook display --------

    def _repr_html_(self) -> str:
        url = self.url()
        return (
            f'<div style="border:1px solid #d6d6d6;border-radius:8px;overflow:hidden;">'
            f'  <div style="padding:6px 10px;background:#f3f3f3;font:12px system-ui;color:#5d5d5d;'
            f'              display:flex;justify-content:space-between;align-items:center;">'
            f'    <span><b>figure-studio</b> &middot; {len(self.list())} figure(s)</span>'
            f'    <a href="{url}" target="_blank" style="color:#0285ff;text-decoration:none;">'
            f'      open in new tab &#8599;</a>'
            f'  </div>'
            f'  <iframe src="{url}" width="100%" height="640" style="border:0;display:block;"></iframe>'
            f'</div>'
        )

    def __repr__(self) -> str:  # pragma: no cover
        names = self.list()
        return f"<figure_studio.Session at {self.base_url} figures={names}>"

    # -------- helpers --------

    def _auto_name(self) -> str:
        existing = set(self.list())
        for i in range(1, 10000):
            cand = f"figure_{i}"
            if cand not in existing:
                return cand
        return f"figure_{int(time.time())}"


# ---------------------------------------------------------------------------
# Module-level entry points
# ---------------------------------------------------------------------------


def connect(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    autostart: bool = True,
    timeout: float = 8.0,
) -> Session:
    """Open (or start) a figure-studio Session at ``host:port`` and return it."""
    return Session(host=host, port=port, autostart=autostart, timeout=timeout)


def show(fig: "Figure", name: Optional[str] = None, *, port: int = 8765) -> Session:
    """Convenience: ``connect()`` then ``session.add(fig)``. Returns the Session for notebook display."""
    session = connect(port=port)
    session.add(fig, name=name)
    return session
