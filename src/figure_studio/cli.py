"""``figure-studio`` command-line entry point.

Subcommands:
    figure-studio serve [--port 8765] [--host 127.0.0.1] [--no-browser]
        Start the multi-figure editor server. The server is long-running and
        accepts figures from any client over ``Session.add(fig)``.

    figure-studio version
        Print the installed version.
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from typing import List, Optional


def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("figure_studio")
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("[figure-studio] %(message)s"))
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
    return logger


def _port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _is_headless() -> bool:
    if os.environ.get("FIGURE_STUDIO_HEADLESS", "").lower() in {"1", "true", "yes"}:
        return True
    if os.environ.get("SSH_CONNECTION"):
        return True
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return True
    return False


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import FigureRegistry, create_app

    log = _setup_logger()

    if not _port_is_free(args.port, args.host):
        log.error("Port %s on %s is already in use. Pass --port to pick a different one.", args.port, args.host)
        return 2

    registry = FigureRegistry()
    app = create_app(registry)
    url = f"http://{args.host}:{args.port}/"

    headless = _is_headless() or args.no_browser

    def _open_browser() -> None:
        deadline = time.time() + 6
        while time.time() < deadline:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.2)
                    s.connect((args.host, args.port))
                break
            except OSError:
                time.sleep(0.1)
        if not headless:
            try:
                webbrowser.open(url, new=2)
            except Exception:
                pass

    if not headless:
        threading.Thread(target=_open_browser, daemon=True, name="fs-opener").start()

    print(
        f"\n[figure-studio] serving multi-figure editor on {url}\n"
        f"[figure-studio] add figures from Python:  "
        f"session = figure_studio.connect(port={args.port}); session.add(fig)\n"
        f"[figure-studio] press Ctrl+C to stop.\n",
        flush=True,
    )

    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level=args.log_level,
            access_log=False,
        )
    except KeyboardInterrupt:
        return 0
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    from . import __version__
    print(__version__)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="figure-studio",
        description="Local web UI for visually editing matplotlib figures.",
    )
    sub = p.add_subparsers(dest="cmd")

    sp_serve = sub.add_parser(
        "serve",
        help="Start the multi-figure editor server.",
        description="Start a long-running figure-studio server. Add figures from Python with `figure_studio.connect()`.",
    )
    sp_serve.add_argument("--port", type=int, default=8765)
    sp_serve.add_argument("--host", default="127.0.0.1")
    sp_serve.add_argument("--no-browser", action="store_true", help="Don't try to open a browser.")
    sp_serve.add_argument("--log-level", default="warning", choices=["critical", "error", "warning", "info", "debug"])
    sp_serve.set_defaults(func=cmd_serve)

    sp_version = sub.add_parser("version", help="Print the installed version.")
    sp_version.set_defaults(func=cmd_version)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
