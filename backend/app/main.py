"""FastAPI app entrypoint.

Wires CORS (locked to local origins), DB init on startup, /health, the API
routers, and — last — serves a built SPA from ``bigi/backend/static`` when
present (optional single-process mode).

bigi is a LOCAL single-operator tool: the desktop shell binds 127.0.0.1 on a
random port and the dev server defaults to localhost. CORS is restricted to
local origins (no wildcard, no credentials) so a malicious web page the
operator happens to have open cannot drive the API. The Docker image binds
0.0.0.0 for container port-mapping — run it only on a trusted interface.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import init_db
from .routers import declaration, jira, settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="bigi — Third-Party Declaration",
    version="1.0.0",
    lifespan=lifespan,
)

# Local origins only. The bundled SPA is served same-origin (mounted at "/");
# the vite dev server proxies /api server-side; the Tauri webview is
# same-origin. No cross-site browser client legitimately needs credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",   # vite dev server
        "http://localhost:8000", "http://127.0.0.1:8000",   # single-process
        "tauri://localhost", "https://tauri.localhost",      # desktop webview
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(settings.router)
app.include_router(declaration.router)
app.include_router(jira.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# Serve the built SPA LAST (only when present), so it never shadows the API.
# When frozen by PyInstaller the bundled static/ lives under sys._MEIPASS.
if getattr(sys, "frozen", False):
    _static_dir = Path(sys._MEIPASS) / "static"
else:
    _static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
