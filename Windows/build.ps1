# Builds a single-file portable exe. Run from this directory:
#   .\build.ps1
# Output: dist\HomeLabManagement.exe (no installer, no runtime deps on the target PC).

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

& .\.venv\Scripts\python.exe -m PyInstaller `
    --noconfirm --clean --onefile --windowed `
    --name HomeLabManagement `
    --collect-submodules paramiko `
    app.py

Write-Host ""
Write-Host "Built: dist\HomeLabManagement.exe" -ForegroundColor Green
Write-Host "Copy the exe anywhere and run it directly - settings.json is created beside it on first run."
