# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent.parent

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "config" / "prompts.json"), "config"),
        (str(ROOT / "config" / "models.json"), "config"),
        (str(ROOT / "config" / "theme.qss"), "config"),
        (str(ROOT / "config" / "theme_light.qss"), "config"),
        (str(ROOT / "config" / "theme_space_blue.qss"), "config"),
        (str(ROOT / ".env.example"), "."),
    ],
    hiddenimports=[
        "PyQt6",
        "PyQt6.QtMultimedia",
        "PyQt6.QtMultimediaWidgets",
        "edge_tts",
        "langdetect",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["torch", "ultralytics", "kokoro"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RecapAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RecapAI",
)
