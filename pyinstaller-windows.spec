# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for building Windows .exe
# Build with: pyinstaller pyinstaller-windows.spec
#
# Set the FFMPEG_BIN env var to the path of a static ffmpeg.exe before building:
#   $env:FFMPEG_BIN = "path\to\ffmpeg.exe"
#   pyinstaller pyinstaller-windows.spec

import os
from PyInstaller.utils.hooks import get_module_file_attribute

ffmpeg_bin = os.environ.get('FFMPEG_BIN', '')
ffmpeg_binaries = []
if ffmpeg_bin and os.path.exists(ffmpeg_bin):
    ffmpeg_binaries = [(ffmpeg_bin, '.')]
else:
    print('WARNING: FFMPEG_BIN not set or file not found — ffmpeg will NOT be bundled.')

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=ffmpeg_binaries,
    datas=[
        ('jsmpeg.min.js', '.'),
        ('bigbuckbunny.ts', '.'),
    ],
    hiddenimports=[
        'queue',
        'struct',
        'threading',
        'subprocess',
        'signal',
        'socket',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='L4-Proxy-Test-Server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Show console window to display connection info and logs
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='L4-Proxy-Test-Server',
)
