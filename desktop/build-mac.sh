#!/usr/bin/env bash
# Build the bigi macOS .app (+ .dmg) end to end:
#   frontend SPA -> PyInstaller sidecar -> stage into Tauri resources ->
#   tauri build -> deep ad-hoc sign.
#
# Internal/unsigned build. After copying the .app to /Applications, run once:
#   xattr -dr com.apple.quarantine "/Applications/bigi.app"
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # bigi/desktop
BIGI="$(cd "$ROOT/.." && pwd)"                          # bigi

# Make sure cargo is on PATH (rustup installs to ~/.cargo).
# shellcheck disable=SC1091
source "$HOME/.cargo/env" 2>/dev/null || true

echo "==> [1/5] Build frontend SPA"
( cd "$BIGI/frontend" && npm install --silent && npm run build )

echo "==> [2/5] Build Python sidecar (PyInstaller, onedir)"
( cd "$BIGI/backend" && rm -rf build dist && \
  ./.venv/bin/pyinstaller --noconfirm --onedir --name bigi-server \
    --collect-submodules uvicorn --collect-submodules anyio \
    --collect-submodules app \
    --collect-all pydantic --collect-all pydantic_core \
    --add-data "../frontend/dist:static" \
    desktop_main.py )

echo "==> [3/5] Stage sidecar into Tauri resources"
rm -rf "$ROOT/src-tauri/resources/bigi-server"
mkdir -p "$ROOT/src-tauri/resources"
cp -R "$BIGI/backend/dist/bigi-server" "$ROOT/src-tauri/resources/bigi-server"
chmod +x "$ROOT/src-tauri/resources/bigi-server/bigi-server"

echo "==> [4/5] Tauri build"
( cd "$ROOT" && npm install --silent && npx tauri build )

echo "==> [5/5] Deep ad-hoc sign the .app"
APP="$(/usr/bin/find "$ROOT/src-tauri/target/release/bundle/macos" -maxdepth 1 -name '*.app' 2>/dev/null | head -1)"
if [ -n "${APP:-}" ]; then
  /usr/bin/find "$APP" -name 'bigi-server' -type f -exec chmod +x {} \;
  codesign --force --deep -s - "$APP"
  echo ""
  echo "Built app : $APP"
fi
DMG="$(/usr/bin/find "$ROOT/src-tauri/target/release/bundle/dmg" -name '*.dmg' 2>/dev/null | head -1)"
[ -n "${DMG:-}" ] && echo "Built dmg : $DMG"
echo "Done."
