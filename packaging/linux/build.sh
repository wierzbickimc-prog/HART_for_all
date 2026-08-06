#!/usr/bin/env bash
# Build a double-clickable AppImage for Ubuntu/Debian.
#
# Produces dist/HART_for_all-<arch>.AppImage -- a single file with Python,
# Tkinter and pyserial bundled in. Users need no system Python or pip.
#
# The AppImage is a native binary, so it only runs on the architecture it was
# built on: build on x86_64 for an Intel/AMD machine, on aarch64 for an ARM
# one (a Raspberry Pi, or a Linux VM on an Apple Silicon Mac). There is no
# cross-compiling here -- CI runs this script once per architecture.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
REPO_ROOT="$(pwd)"

# appimagetool names its own releases x86_64 / aarch64, and needs $ARCH set
# because it cannot always infer the target from the AppDir alone.
case "$(uname -m)" in
    x86_64|amd64)   ARCH="x86_64" ;;
    aarch64|arm64)  ARCH="aarch64" ;;
    *)
        echo "Unsupported build architecture: $(uname -m)" >&2
        echo "This script builds x86_64 and aarch64 AppImages." >&2
        exit 1
        ;;
esac
export ARCH

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
    --collect-submodules serial.urlhandler \
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

OUTPUT="dist/HART_for_all-${ARCH}.AppImage"

# Cached per architecture: the wrong one would fail with "Exec format error".
APPIMAGETOOL="${REPO_ROOT}/.appimagetool-${ARCH}.AppImage"
if [ ! -x "${APPIMAGETOOL}" ]; then
    curl -fsSL -o "${APPIMAGETOOL}" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
    chmod +x "${APPIMAGETOOL}"
fi
"${APPIMAGETOOL}" --appimage-extract-and-run AppDir "${OUTPUT}"

rm -rf "${BUILD_VENV}" AppDir
echo
echo "Built ${OUTPUT}"
