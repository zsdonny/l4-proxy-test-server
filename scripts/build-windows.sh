#!/bin/bash
# Build Windows .exe using PyInstaller
# Requires: Python 3 with PyInstaller installed
# Usage: bash scripts/build-windows.sh

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "[build] Installing build dependencies..."
pip install PyInstaller --quiet

echo "[build] Cleaning previous builds..."
rm -rf build dist "L4-Proxy-Test-Server.exe" 2>/dev/null || true

echo "[build] Building Windows .exe with PyInstaller..."
pyinstaller pyinstaller-windows.spec

if [ -f "dist/L4-Proxy-Test-Server/L4-Proxy-Test-Server.exe" ]; then
    echo "[build] ✓ Windows .exe built successfully"
    echo "[build] Output: dist/L4-Proxy-Test-Server/L4-Proxy-Test-Server.exe"
    du -sh "dist/L4-Proxy-Test-Server.exe" 2>/dev/null || du -sh "dist/L4-Proxy-Test-Server" || true
else
    echo "[build] ✗ Build failed: .exe not found in dist/"
    exit 1
fi
