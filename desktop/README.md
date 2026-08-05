# bigi — desktop app (macOS + Windows)

A native desktop app that wraps the existing `bigi` web app (all 11 seizure scenarios). A thin **Tauri
(Rust)** shell launches the FastAPI/uvicorn backend as a bundled **Python
sidecar** on a random local port, points its SQLite DB at the app's per-user
data directory, waits for `/health`, then shows the SPA in a native window.
The sidecar is killed when the app quits.

Nothing in the backend or frontend is rewritten — the shell reuses the same
single-process mode the Docker image uses (the server serves the SPA + API on
one origin).

## Layout

```
desktop/
├── build-mac.sh            # one-shot macOS build: SPA → sidecar → bundle → sign
├── build-windows.ps1       # one-shot Windows build: SPA → sidecar → NSIS setup exe
├── ui/index.html           # tiny "Starting…" splash (frontendDist)
└── src-tauri/
    ├── Cargo.toml
    ├── tauri.conf.json           # window, resources, ad-hoc signing
    ├── tauri.windows.conf.json   # Windows overrides: NSIS target, WebView2 bootstrapper
    ├── src/main.rs          # spawn sidecar, pick port, health-poll, navigate, kill on exit
    ├── icons/               # app icon set (committed)
    └── resources/           # PyInstaller output, staged at build time (gitignored)
```

The Python sidecar entry point lives in the backend: `../backend/desktop_main.py`.

## Prerequisites (one-time)

- **Rust** — macOS: `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`;
  Windows: [rustup](https://rustup.rs) with the MSVC toolchain + VS Build Tools
  "Desktop development with C++" workload
- **Node 18+** (for the Vite build and the Tauri CLI)
- **Python 3.13** with the backend venv + PyInstaller:
  ```sh
  # macOS
  cd ../backend
  python3.13 -m venv .venv
  ./.venv/bin/pip install -r requirements.txt "pyinstaller>=6.11"
  ```
  ```powershell
  # Windows
  cd ..\backend
  py -3.13 -m venv .venv
  .\.venv\Scripts\pip install -r requirements.txt "pyinstaller>=6.11"
  ```

Neither build cross-compiles: the PyInstaller sidecar must be built on the OS
it targets, so build the macOS app on a Mac and the Windows installer on
Windows (or use the CI job below).

## Build — macOS

```sh
./build-mac.sh
```

Outputs:
- `src-tauri/target/release/bundle/macos/bigi.app`
- `src-tauri/target/release/bundle/dmg/bigi_<version>_aarch64.dmg`

The build targets **Apple Silicon** (`aarch64`). The app is **ad-hoc signed**,
not notarized (internal use).

## Build — Windows

On a Windows machine (the `-ExecutionPolicy` override is needed because stock
Windows PowerShell blocks scripts by default):

```powershell
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
```

Output:
- `src-tauri\target\release\bundle\nsis\bigi_<version>_x64-setup.exe`

Or without a Windows machine: the GitHub Actions workflow
`.github/workflows/build-windows.yml` builds the installer on a
`windows-latest` runner. Trigger it from GitHub → Actions → *Build Windows
installer* → *Run workflow* (it also runs on `v*` tags) and download the
`bigi-windows-setup` artifact.

The installer is **unsigned**, so SmartScreen shows "Windows protected your
PC" on first run — click *More info* → *Run anyway*. It installs per-user (no
admin rights needed) and bootstraps the WebView2 runtime if missing.

## Install / run

**macOS** — copy `bigi.app` to `/Applications`, then clear the
quarantine flag once (only needed if the app was downloaded/AirDropped —
macOS 15.1+ no longer offers right-click → Open for unsigned apps):

```sh
xattr -dr com.apple.quarantine "/Applications/bigi.app"
```

**Windows** — run the setup exe.

Then launch the app. Credentials (LLM / Finom Back-Office / Jira) are entered
in the in-app **Settings** tab and persisted to:

```
macOS   : ~/Library/Application Support/com.smammadov.bigi/bigi.db
Windows : %APPDATA%\com.smammadov.bigi\bigi.db
```

## Regenerating the icon

```sh
npx tauri icon icon-source.png
```

## Notes

- Icons are committed so the app builds without regenerating them.
- To later **notarize** for wider distribution, the bundled sidecar binary must
  be deep-signed with a Developer ID before notarization (an unsigned nested
  binary invalidates the parent signature). Out of scope for internal use.
