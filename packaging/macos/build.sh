#!/usr/bin/env bash
# Build a double-clickable HART_for_all.app for macOS -- a single universal2
# binary that runs natively on both Apple Silicon and Intel.
#
# Produces dist/HART_for_all.app and dist/HART_for_all-macOS.zip.
#
# Homebrew's Python is single-arch (whatever chip you built it on), so a
# `brew install python-tk` build only runs on that same chip -- e.g. a build
# made on an Apple Silicon GitHub Actions runner fails with "not supported on
# this Mac" for anyone on an Intel Mac, and vice versa. python.org's own
# installer ships a universal2 framework build (arm64 + x86_64 in one binary,
# macOS 11+) with Tk 8.6 already bundled, so we use that instead -- one
# artifact that works everywhere, and no Homebrew dependency to boot.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
REPO_ROOT="$(pwd)"

PYTHON_VERSION="3.11.9"
PYTHON_MINOR="3.11"
FRAMEWORK_PY="/Library/Frameworks/Python.framework/Versions/${PYTHON_MINOR}/bin/python3"

if [ ! -x "${FRAMEWORK_PY}" ] || ! lipo -archs "${FRAMEWORK_PY}" 2>/dev/null | grep -q "x86_64" || ! lipo -archs "${FRAMEWORK_PY}" 2>/dev/null | grep -q "arm64"; then
    echo "Installing python.org's universal2 Python ${PYTHON_VERSION} (needs sudo) ..."
    PKG="${REPO_ROOT}/.python-universal2.pkg"
    curl -sL -o "${PKG}" \
        "https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-macos11.pkg"
    sudo installer -pkg "${PKG}" -target /
    rm -f "${PKG}"
fi
echo "Using $(lipo -archs "${FRAMEWORK_PY}") build at ${FRAMEWORK_PY}"

BUILD_VENV="${REPO_ROOT}/.build_venv_macos"
rm -rf "${BUILD_VENV}"
"${FRAMEWORK_PY}" -m venv "${BUILD_VENV}"
"${BUILD_VENV}/bin/pip" install -q --upgrade pip
"${BUILD_VENV}/bin/pip" install -q -r requirements.txt pyinstaller

rm -rf build dist HART_for_all.spec
"${BUILD_VENV}/bin/pyinstaller" --noconfirm --windowed --name HART_for_all \
    --icon packaging/icon.icns \
    --add-data "profiles:profiles" \
    --osx-bundle-identifier "com.builtdna.hartforall" \
    --target-architecture universal2 \
    --collect-submodules serial.urlhandler \
    app.py

echo "Bundle architectures: $(lipo -archs dist/HART_for_all.app/Contents/MacOS/HART_for_all)"

cd dist
ditto -c -k --sequesterRsrc --keepParent HART_for_all.app HART_for_all-macOS.zip
cd "${REPO_ROOT}"

rm -rf "${BUILD_VENV}"
echo
echo "Built dist/HART_for_all.app and dist/HART_for_all-macOS.zip"
