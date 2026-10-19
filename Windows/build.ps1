# Builds a portable folder + zip. Run from this directory:
#   .\build.ps1
# Output: dist\HomeLabManagement\HomeLabManagement.exe and dist\HomeLabManagement-<version>-win64.zip
# (one-folder rather than one-file: no 60 MB self-extraction on every launch, so it starts much faster).
# Unzip anywhere; settings.json, history.db and homelab.log are created beside the exe.

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    # PySide6 wheels lag the newest Python, so prefer 3.12 when the py launcher has it.
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3.12 -m venv .venv } else { python -m venv .venv }
    if ($LASTEXITCODE -ne 0) { python -m venv .venv }
}
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --upgrade pip | Out-Null
$reqs = if (Test-Path "requirements.lock") { "requirements.lock" } else { "requirements.txt" }
& $py -m pip install -r $reqs

& $py -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed - not building." }

& $py make_version_info.py
& $py -m PyInstaller `
    --noconfirm --clean --onedir --windowed `
    --name HomeLabManagement `
    --icon app_icon.ico `
    --version-file version_info.txt `
    --add-data "app_icon.ico;." `
    --collect-submodules paramiko `
    --copy-metadata keyring `
    --hidden-import keyring.backends.Windows `
    --exclude-module tkinter `
    app.py

$version = & $py -c "from version import __version__; print(__version__)"
$zip = "dist\HomeLabManagement-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "dist\HomeLabManagement" -DestinationPath $zip

Write-Host ""
Write-Host "Built: dist\HomeLabManagement\HomeLabManagement.exe" -ForegroundColor Green
Write-Host "Zip:   $zip"
