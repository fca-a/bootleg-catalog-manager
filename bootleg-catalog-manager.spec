# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Bootleg Catalog Manager 1.0.0.

Build command:
    pyinstaller bootleg-catalog-manager.spec

Output: dist/bootleg-catalog-manager/bootleg-catalog-manager.exe
"""

a = Analysis(
    ["src/bootleg_catalog/app/main.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=[
        # fpdf2's fpdf/sign.py does `from unittest.mock import patch` at
        # module level (reached from `from fpdf import FPDF` during PDF
        # export).  Without these the frozen app dies with
        # "No module named 'unittest'".  Note: hiddenimports is a no-op
        # while "unittest" is in excludes (excludes wins), so both the
        # exclude removal below and this list are required.
        "unittest",
        "unittest.mock",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude test/development modules.  "unittest" must NOT be
        # excluded: fpdf2 (fpdf/sign.py:16) imports unittest.mock at
        # module level, and excluding it broke PDF export in the .exe.
        "pytest",
        "doctest",
        # Exclude unused Qt modules to reduce size
        "PySide6.Qt3DCore",
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DExtras",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DRender",
        "PySide6.QtBluetooth",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtGraphs",
        "PySide6.QtHttpServer",
        "PySide6.QtLocation",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtNfc",
        "PySide6.QtPositioning",
        "PySide6.QtRemoteObjects",
        "PySide6.QtScxml",
        "PySide6.QtSensors",
        "PySide6.QtSerialBus",
        "PySide6.QtSerialPort",
        "PySide6.QtSpatialAudio",
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
        "PySide6.QtTest",
        "PySide6.QtTextToSpeech",
        "PySide6.QtUiTools",
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebSockets",
        "PySide6.Qt3DCore",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="bootleg-catalog-manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="resources/icons/bcm-icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="bootleg-catalog-manager",
)
