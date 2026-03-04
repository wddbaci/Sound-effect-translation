# -*- mode: python ; coding: utf-8 -*-

import certifi  # 在文件开头导入 certifi

a = Analysis(
    ['音效翻译.py'],
    pathex=[],
    binaries=[],
    datas=[(certifi.where(), 'certifi')],  # 添加 certifi 的证书文件
    hiddenimports=[
        'tkinter',
        'concurrent.futures',
        'queue',
        'threading',
        'json',
        'urllib.request',
        're',
        'hashlib',
        'datetime',
        'pathlib',
        'platform',
        'subprocess',
        'atexit',
    ],
    hookspath=['.'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='音效翻译',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon-windowed.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='音效翻译',
)
app = BUNDLE(
    coll,
    name='音效翻译.app',
    icon='icon-windowed.icns',
    bundle_identifier=None,
    info_plist={
        'CFBundleShortVersionString': '2.0.5',
        'CFBundleVersion': '2.0.5',
    }
)