# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for building macOS .app bundle
# Build with: pyinstaller pyinstaller-macos.spec
#
# Set the FFMPEG_BIN env var to the path of a static ffmpeg binary before building:
#   export FFMPEG_BIN="/path/to/ffmpeg"
#   pyinstaller pyinstaller-macos.spec

import os

ffmpeg_bin = os.environ.get('FFMPEG_BIN', '')
ffmpeg_binaries = []
if ffmpeg_bin and os.path.exists(ffmpeg_bin):
    ffmpeg_binaries = [(ffmpeg_bin, '.')]
else:
    print('WARNING: FFMPEG_BIN not set or file not found — ffmpeg will NOT be bundled.')

a = Analysis(
    ['../server.py'],
    pathex=[],
    binaries=ffmpeg_binaries,
    datas=[
        ('../assets/jsmpeg.min.js', 'assets'),
        ('../assets/bigbuckbunny.ts', 'assets'),
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
    name='l4-proxy-test-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # macOS: don't show console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

app = BUNDLE(
    exe,
    name='L4 Proxy Test Server.app',
    icon='icon.icns',
    bundle_identifier='com.example.l4-proxy-test-server',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSHighResolutionCapable': 'True',
    },
)
