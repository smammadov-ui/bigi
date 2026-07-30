"""FastAPI app entrypoint.

Wires CORS (open, dev), DB init on startup, /health, the four API routers,
and — last — serves a built SPA from ``bigi/backend/static`` when present
(optional single-process mode).
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import init_db
from .routers import declaration, jira, settings, webhook


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="bigi — Third-Party Declaration",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(settings.router)
app.include_router(declaration.router)
app.include_router(webhook.router)
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
