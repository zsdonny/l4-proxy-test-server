#!/bin/bash
# Build macOS .app using PyInstaller (bundles a static ffmpeg)
# Requires: Python 3 with PyInstaller installed (on macOS), curl
# Usage: bash scripts/build-macos.sh

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Download static ffmpeg for macOS (evermeet.cx - GPL static build)
# ---------------------------------------------------------------------------
VENDOR_PATH="$REPO_ROOT/vendor/ffmpeg/macos/ffmpeg"
FFMPEG_DIR="build/ffmpeg-macos"
FFMPEG_DL_URL="https://evermeet.cx/ffmpeg/getrelease/zip"

if [ -f "$VENDOR_PATH" ]; then
    echo "[ffmpeg] Using vendored binary (airgapped)"
    chmod +x "$VENDOR_PATH"
    export FFMPEG_BIN="$VENDOR_PATH"
elif [ ! -f "$FFMPEG_DIR/ffmpeg" ]; then
    echo "[ffmpeg] Downloading static macOS build..."
    mkdir -p "$FFMPEG_DIR"
    curl -L --retry 3 -o "$FFMPEG_DIR/ffmpeg.zip" "$FFMPEG_DL_URL"
    unzip -q -o "$FFMPEG_DIR/ffmpeg.zip" -d "$FFMPEG_DIR/"
    chmod +x "$FFMPEG_DIR/ffmpeg"
    export FFMPEG_BIN="$REPO_ROOT/$FFMPEG_DIR/ffmpeg"
else
    export FFMPEG_BIN="$REPO_ROOT/$FFMPEG_DIR/ffmpeg"
fi
echo "[ffmpeg] Using: $FFMPEG_BIN"

echo "[build] Installing build dependencies..."
pip install PyInstaller --quiet

echo "[build] Cleaning previous builds..."
rm -rf build/pyinstaller dist "L4 Proxy Test Server.app" 2>/dev/null || true

echo "[build] Building macOS .app with PyInstaller..."
pyinstaller pyinstaller-macos.spec

if [ -d "dist/L4 Proxy Test Server.app" ]; then
    echo "[build] macOS .app built successfully"
    echo "[build] Output: dist/L4 Proxy Test Server.app"
    du -sh "dist/L4 Proxy Test Server.app"

    # Create distributable .zip
    echo "[build] Creating distribution zip..."
    cd dist
    zip -r -q "L4-Proxy-Test-Server-macos.zip" "L4 Proxy Test Server.app"
    cd ..
    echo "[build] Distribution: dist/L4-Proxy-Test-Server-macos.zip"
else
    echo "[build] Build failed: .app not found in dist/"
    exit 1
fi