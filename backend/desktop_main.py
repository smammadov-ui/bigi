"""PyInstaller entry point for the desktop (Tauri) build.

Runs the existing FastAPI app under a single-process uvicorn server, bound to a
port chosen by the Tauri shell (passed as ``--port``). The DB location is taken
from the ``BIGI_DB`` env var, which the shell points at the app's per-user data
directory (macOS: Application Support, Windows: %APPDATA%).

A small watchdog thread exits the process if it is ever orphaned (parent died),
so the Python server can never outlive the app window.
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import threading
import time


def _watch_parent(ppid: int) -> None:
    """Exit if the parent process dies — belt-and-suspenders cleanup."""
    if os.name == "nt":
        # Windows never reparents, so getppid() keeps returning the dead
        # parent's PID; block on the parent's process handle instead.
        import ctypes

        SYNCHRONIZE = 0x0010_0000
        INFINITE = 0xFFFF_FFFF
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, ppid)
        if handle:
            kernel32.WaitForSingleObject(handle, INFINITE)
        os._exit(0)
    while True:
        if os.getppid() != ppid:
            os._exit(0)
        time.sleep(2)


def main() -> None:
    multiprocessing.freeze_support()  # no-op unless workers are ever used

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args, _ = ap.parse_known_args()

    threading.Thread(
        target=_watch_parent, args=(os.getppid(),), daemon=True
    ).start()

    import uvicorn

    # Import the app object (and models) statically so PyInstaller's analysis
    # follows the full import graph; init_db()'s function-local model import is
    # otherwise invisible to the bundler.
    from app.main import app  # noqa: F401
    from app import models  # noqa: F401

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
