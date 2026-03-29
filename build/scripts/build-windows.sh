#!/bin/bash
# Build Windows .exe using PyInstaller (bundles a static ffmpeg.exe)
# Requires: Python 3 with PyInstaller installed, curl, unzip
# Usage: bash scripts/build-windows.sh

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Download static ffmpeg for Windows (BtbN builds - GPL, static, 64-bit)
# ---------------------------------------------------------------------------
FFMPEG_DIR="build/ffmpeg-win64"
FFMPEG_ZIP="build/ffmpeg-win64.zip"
FFMPEG_DL_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

if [ ! -f "$FFMPEG_DIR/bin/ffmpeg.exe" ]; then
    echo "[ffmpeg] Downloading static Windows build..."
    mkdir -p build
    curl -L --retry 3 -o "$FFMPEG_ZIP" "$FFMPEG_DL_URL"
    unzip -q -o "$FFMPEG_ZIP" -d build/
    # The zip extracts to a versioned folder name - rename it to a predictable path
    mv build/ffmpeg-master-latest-win64-gpl "$FFMPEG_DIR" 2>/dev/null || true
    export FFMPEG_BIN="$REPO_ROOT/$FFMPEG_DIR/bin/ffmpeg.exe"
else
    export FFMPEG_BIN="$REPO_ROOT/$FFMPEG_DIR/bin/ffmpeg.exe"
fi
echo "[ffmpeg] Using: $FFMPEG_BIN"

echo "[build] Installing build dependencies..."
pip install PyInstaller --quiet

echo "[build] Cleaning previous builds..."
rm -rf build/pyinstaller dist "L4-Proxy-Test-Server.exe" 2>/dev/null || true

echo "[build] Building Windows .exe with PyInstaller..."
pyinstaller build/pyinstaller-windows.spec

if [ -f "dist/L4-Proxy-Test-Server.exe" ]; then
    echo "[build] Windows .exe built successfully"
    echo "[build] Output: dist/L4-Proxy-Test-Server.exe"
    du -sh "dist/L4-Proxy-Test-Server.exe" || true
else
    echo "[build] Build failed: .exe not found in dist/"
    exit 1
fi