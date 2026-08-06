#!/usr/bin/env bash
# Build a double-clickable HART_for_all.app for macOS (Apple Silicon or Intel).
#
# Produces dist/HART_for_all.app and dist/HART_for_all-macOS.zip.
# Requires Homebrew's python-tk (Tk 8.6+, matching the running Python's
# minor version) -- see README "macOS: blank window / no tabs".
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
REPO_ROOT="$(pwd)"

PYVER="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
if ! python3 -c "import tkinter; assert tkinter.TkVersion >= 8.6" >/dev/null 2>&1; then
    echo "Installing python-tk@${PYVER} (brings Tk 8.6+) ..."
    brew install "python-tk@${PYVER}"
fi

BUILD_VENV="${REPO_ROOT}/.build_venv_macos"
rm -rf "${BUILD_VENV}"
python3 -m venv "${BUILD_VENV}"
"${BUILD_VENV}/bin/pip" install -q --upgrade pip
"${BUILD_VENV}/bin/pip" install -q -r requirements.txt pyinstaller

rm -rf build dist HART_for_all.spec
"${BUILD_VENV}/bin/pyinstaller" --noconfirm --windowed --name HART_for_all \
    --icon packaging/icon.icns \
    --add-data "profiles:profiles" \
    --osx-bundle-identifier "com.builtdna.hartforall" \
    app.py

cd dist
ditto -c -k --sequesterRsrc --keepParent HART_for_all.app HART_for_all-macOS.zip
cd "${REPO_ROOT}"

rm -rf "${BUILD_VENV}"
echo
echo "Built dist/HART_for_all.app and dist/HART_for_all-macOS.zip"
