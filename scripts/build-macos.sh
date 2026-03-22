#!/bin/bash
# Build macOS .app using PyInstaller
# Requires: Python 3 with PyInstaller installed (on macOS)
# Usage: bash scripts/build-macos.sh

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "[build] Installing build dependencies..."
pip install PyInstaller --quiet

echo "[build] Cleaning previous builds..."
rm -rf build dist "L4 Proxy Test Server.app" 2>/dev/null || true

echo "[build] Building macOS .app with PyInstaller..."
pyinstaller pyinstaller-macos.spec

if [ -d "dist/L4 Proxy Test Server.app" ]; then
    echo "[build] ✓ macOS .app built successfully"
    echo "[build] Output: dist/L4 Proxy Test Server.app"
    du -sh "dist/L4 Proxy Test Server.app"
    
    # Create distributable .zip
    echo "[build] Creating distribution zip..."
    cd dist
    zip -r -q "L4-Proxy-Test-Server-macos.zip" "L4 Proxy Test Server.app"
    cd ..
    echo "[build] Distribution: dist/L4-Proxy-Test-Server-macos.zip"
else
    echo "[build] ✗ Build failed: .app not found in dist/"
    exit 1
fi
