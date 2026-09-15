# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

packages = ['yt_dlp', 'yt_dlp_ejs', 'flask', 'fpdf']
datas = []
binaries = []
hiddenimports = []
for pkg in packages:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass
hiddenimports += collect_submodules('yt_dlp')

from pathlib import Path
import os
PROJECT_ROOT = Path(SPECPATH).resolve().parent

datas += [
    (str(PROJECT_ROOT / 'frontend'), 'frontend'),
    (str(PROJECT_ROOT / 'assets'), 'assets'),
]

deno_path = PROJECT_ROOT / 'build' / 'windows' / 'deno' / 'deno.exe'
if deno_path.exists():
    binaries.append((str(deno_path), '.'))


a = Analysis(
    [str(PROJECT_ROOT / 'app.py')],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='YouScraperBackend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='YouScraperBackend',
)
