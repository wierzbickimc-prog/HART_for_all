#!/usr/bin/env bash
# Build a double-clickable HART_for_all-x86_64.AppImage for Ubuntu/Debian.
#
# Produces dist/HART_for_all-x86_64.AppImage -- a single file with Python,
# Tkinter and pyserial bundled in. Users need no system Python or pip.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
REPO_ROOT="$(pwd)"

if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "python3-tk is required to build (it's what gets bundled)." >&2
    echo "  sudo apt install python3-tk python3-venv" >&2
    exit 1
fi

BUILD_VENV="${REPO_ROOT}/.build_venv_linux"
rm -rf "${BUILD_VENV}"
python3 -m venv "${BUILD_VENV}"
"${BUILD_VENV}/bin/pip" install -q --upgrade pip
"${BUILD_VENV}/bin/pip" install -q -r requirements.txt pyinstaller

rm -rf build dist HART_for_all.spec AppDir
"${BUILD_VENV}/bin/pyinstaller" --noconfirm --onefile --name HART_for_all \
    --add-data "profiles:profiles" \
    app.py

mkdir -p AppDir/usr/bin
cp dist/HART_for_all AppDir/usr/bin/HART_for_all
cp packaging/linux/HART_for_all.desktop AppDir/HART_for_all.desktop
cp packaging/icon.png AppDir/hart_for_all.png
cp packaging/icon.png AppDir/.DirIcon
cat > AppDir/AppRun <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
exec "${HERE}/usr/bin/HART_for_all" "$@"
EOF
chmod +x AppDir/AppRun

APPIMAGETOOL="${REPO_ROOT}/.appimagetool.AppImage"
if [ ! -x "${APPIMAGETOOL}" ]; then
    curl -sL -o "${APPIMAGETOOL}" \
        https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "${APPIMAGETOOL}"
fi
"${APPIMAGETOOL}" --appimage-extract-and-run AppDir dist/HART_for_all-x86_64.AppImage

rm -rf "${BUILD_VENV}" AppDir
echo
echo "Built dist/HART_for_all-x86_64.AppImage"
