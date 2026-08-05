# Build the bigi Windows installer (.exe) end to end:
#   frontend SPA -> PyInstaller sidecar -> stage into Tauri resources ->
#   tauri build (NSIS setup exe, per tauri.windows.conf.json).
#
# Run on Windows (stock PowerShell 5.1 blocks scripts by default, so pass an
# execution-policy override for the process):
#   powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
#
# Prerequisites (one-time):
#   - Rust (rustup) with the MSVC toolchain + VS Build Tools "Desktop
#     development with C++" workload
#   - Node 18+
#   - Python 3.13 with the backend venv + PyInstaller:
#       cd ..\backend
#       py -3.13 -m venv .venv
#       .\.venv\Scripts\pip install -r requirements.txt "pyinstaller>=6.11"
$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot                       # bigi\desktop
$Bigi = Split-Path -Parent $Root            # bigi

Write-Host "==> [1/4] Build frontend SPA"
Push-Location "$Bigi\frontend"
try {
    npm install --silent
    if ($LASTEXITCODE -ne 0) { throw "npm install (frontend) failed" }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "frontend build failed" }
} finally { Pop-Location }

Write-Host "==> [2/4] Build Python sidecar (PyInstaller, onedir)"
Push-Location "$Bigi\backend"
try {
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
    # Console-mode build on purpose: a --noconsole bootloader leaves
    # sys.stdout as None, which breaks uvicorn's default logging. The Tauri
    # shell hides the console via CREATE_NO_WINDOW instead.
    & .\.venv\Scripts\pyinstaller.exe --noconfirm --onedir --name bigi-server `
        --collect-submodules uvicorn --collect-submodules anyio `
        --collect-submodules app `
        --collect-all pydantic --collect-all pydantic_core `
        --add-data "..\frontend\dist;static" `
        desktop_main.py
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }
} finally { Pop-Location }

Write-Host "==> [3/4] Stage sidecar into Tauri resources"
$Staged = "$Root\src-tauri\resources\bigi-server"
if (Test-Path $Staged) {
    # No -ErrorAction here on purpose: a locked file (e.g. a still-running
    # bigi-server.exe) must abort the build, or Copy-Item would nest the new
    # sidecar inside the stale directory and the installer would ship the
    # old backend.
    Remove-Item -Recurse -Force $Staged
}
New-Item -ItemType Directory -Force -Path "$Root\src-tauri\resources" | Out-Null
Copy-Item -Recurse "$Bigi\backend\dist\bigi-server" $Staged

Write-Host "==> [4/4] Tauri build (NSIS installer)"
Push-Location $Root
try {
    npm install --silent
    if ($LASTEXITCODE -ne 0) { throw "npm install (desktop) failed" }
    npx tauri build
    if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
} finally { Pop-Location }

$Setup = Get-ChildItem "$Root\src-tauri\target\release\bundle\nsis\*.exe" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($Setup) {
    Write-Host ""
    Write-Host "Built installer : $($Setup.FullName)"
}
Write-Host "Done."
