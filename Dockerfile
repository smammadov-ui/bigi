# syntax=docker/dockerfile:1
#
# One image that runs the whole bigi app: the FastAPI backend (SQLite, zero
# external services) ALSO serves the built React SPA, so a tester just runs the
# container and opens http://localhost:8000. No secrets are baked in — the LLM /
# Back-Office (Finom) / Jira credentials are entered at runtime in the Settings
# tab and persisted to the /data volume.

# ---- stage 1: build the React/Vite frontend ----
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build          # -> /fe/dist

# ---- stage 2: Python backend (also serves the built SPA) ----
FROM python:3.13-slim AS app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BIGI_DB=sqlite:////data/bigi.db
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/ backend/
# The backend serves this dir at "/" when present (see app/main.py).
COPY --from=frontend /fe/dist backend/static

RUN mkdir -p /data
WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
