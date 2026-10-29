# Build a double-clickable HART_for_all-Windows.exe for Windows.
#
# Produces dist\HART_for_all-Windows.exe -- a single file with Python, Tkinter and
# pyserial bundled in. Users need no system Python or pip.
#
# Run from a "python" on PATH that has Tkinter (the python.org installer
# ships it; see README -> Install from source -> Windows).
$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..\..")
$RepoRoot = Get-Location

python -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "tkinter not found. Install Python from python.org (it bundles Tk); the Microsoft Store build sometimes omits it."
    exit 1
}

$BuildVenv = Join-Path $RepoRoot ".build_venv_windows"
if (Test-Path $BuildVenv) { Remove-Item -Recurse -Force $BuildVenv }
python -m venv $BuildVenv
& "$BuildVenv\Scripts\pip.exe" install -q --upgrade pip
& "$BuildVenv\Scripts\pip.exe" install -q -r requirements.txt pyinstaller

foreach ($p in @("build", "dist", "HART_for_all.spec")) {
    if (Test-Path $p) { Remove-Item -Recurse -Force $p }
}

# ';' is the Windows --add-data separator (PyInstaller uses os.pathsep; ':' on macOS/Linux).
& "$BuildVenv\Scripts\pyinstaller.exe" --noconfirm --windowed --onefile --name HART_for_all `
    --icon packaging\icon.ico `
    --add-data "profiles;profiles" `
    --collect-submodules serial.urlhandler `
    app.py

Move-Item dist\HART_for_all.exe dist\HART_for_all-Windows.exe
Copy-Item packaging\windows\install_cp210x_driver.bat dist\Install_CP210x_Driver-Windows.bat

Remove-Item -Recurse -Force $BuildVenv
Write-Host ""
Write-Host "Built dist\HART_for_all-Windows.exe and dist\Install_CP210x_Driver-Windows.bat"
