"""`chess-mistake-coach serve` -- flag parsing and uvicorn bootstrap for the local
web app. Deliberately its own small argparse setup, not a subparser bolted
onto cli.py's build_parser(): serve's flags (--host, --port, --no-browser)
share nothing with the fetch/report flags, and build_parser() is directly
unit-tested with --user as a required top-level flag.

Built to be started from a desktop shortcut with no console window: if the
app is already running it just opens a window onto it, a busy port moves to
the next free one, output goes to a log file next to the database, and a
failure to start is shown in a message box rather than lost.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import urllib.request
import webbrowser

from ..analysis import backfill_clock_moves
from ..cli import DEFAULT_DB
from ..db import open_db
from ..engine import find_engine
from .browser import open_app_window

APP_ID = "chess-mistake-coach"
DEFAULT_PORT = 8000
# How far past the default port to look for a free one.
PORT_SEARCH = 10
LOG_MAX_BYTES = 1_000_000

# Set when started without a console (from a shortcut): only then is a
# startup failure shown in a message box.
_no_console = False


def build_serve_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chess-mistake-coach serve",
                                description="Run the local web app")
    p.add_argument("--host", default="127.0.0.1",
                   help="Interface to bind. Defaults to 127.0.0.1 -- this is "
                        "a single-user local tool, not meant to be exposed "
                        "on the network.")
    p.add_argument("--port", type=int, default=None,
                   help=f"Port to use (default {DEFAULT_PORT}, or the next free one).")
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite database path")
    p.add_argument("--engine", default=None,
                   help="Path to the Stockfish binary. Auto-detected if omitted.")
    p.add_argument("--no-browser", action="store_true",
                   help="Don't automatically open a browser window")
    return p


def _backfill_clock_moves_once(db_path: str) -> None:
    """Count moves per clock band for games analysed before the dashboard
    could rate time pressure properly. A one-off per database (a few minutes
    for tens of thousands of games, no engine), run in the background so the
    app is usable meanwhile; the dashboard shows how far along it is. New
    games store their counts when they're analysed."""
    conn = open_db(db_path)
    try:
        for (user,) in conn.execute("SELECT DISTINCT username FROM games").fetchall():
            backfill_clock_moves(conn, user)
    except Exception:  # a background nicety: never take the app down with it
        pass
    finally:
        conn.close()


def running_here(port: int, timeout: float = 1.0) -> bool:
    """Is Chess Mistake Coach itself already answering on this port?"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == APP_ID
    except Exception:
        return False


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host if host != "0.0.0.0" else "", port))
            return True
        except OSError:
            return False


def choose_port(host: str, requested: int | None) -> tuple[int | None, bool]:
    """(port, already_running). With an explicit --port only that port is
    considered; otherwise the first of DEFAULT_PORT.. that is either already
    serving this app (reuse it) or free."""
    candidates = [requested] if requested else range(DEFAULT_PORT, DEFAULT_PORT + PORT_SEARCH)
    for port in candidates:
        if running_here(port):
            return port, True
        if port_free(host, port):
            return port, False
    return None, False


def _open_window(url: str) -> None:
    if not open_app_window(url):
        webbrowser.open(url)


def _log_to_file_if_no_console(db_path: str) -> None:
    """Started from the Windows shortcut (pythonw) there is no console to
    print to, so keep output in a small log next to the database instead of
    losing it."""
    global _no_console
    if sys.stdout is not None:
        return
    _no_console = True
    path = os.path.join(os.path.dirname(os.path.abspath(db_path)), APP_ID + ".log")
    try:
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".old")
        log = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stdout = sys.stderr = log


def _fail(message: str) -> None:
    """Report a startup failure where the person will actually see it."""
    if sys.platform == "win32" and _no_console:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Chess Mistake Coach", 0x10)
        except Exception:
            pass
    sys.exit(message)


def serve_main(argv: list[str]) -> None:
    args = build_serve_parser().parse_args(argv)
    _log_to_file_if_no_console(args.db)

    try:
        import uvicorn
    except ImportError:
        _fail('The web app needs extra dependencies: pip install -e ".[web]"')

    port, already = choose_port(args.host, args.port)
    if port is None:
        _fail(f"Couldn't find a free port to start Chess Mistake Coach on "
              f"({args.port or f'{DEFAULT_PORT}-{DEFAULT_PORT + PORT_SEARCH - 1}'}). "
              f"Close whatever is using it, or pass --port.")
    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{port}/"

    if already:
        print(f"Chess Mistake Coach is already running at {url}")
        if not args.no_browser:
            _open_window(url)
        return

    from .app import create_app

    engine_path = args.engine or find_engine()
    app = create_app(args.db, engine_path)
    threading.Thread(target=_backfill_clock_moves_once, args=(args.db,),
                     name="clock-backfill", daemon=True).start()

    if not args.no_browser:
        threading.Timer(1.0, _open_window, args=(url,)).start()

    server = uvicorn.Server(uvicorn.Config(app, host=args.host, port=port, log_level="warning"))
    # The in-app Stop button (/api/shutdown) asks this server to exit.
    app.state.server = server
    print(f"Chess Mistake Coach is running at {url}")
    try:
        server.run()
    except OSError as exc:
        _fail(f"Couldn't start the server on {args.host}:{port} ({exc}).")
