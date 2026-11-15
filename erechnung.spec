# PyInstaller-Konfiguration fuer die Desktop-App.
#
# Bauen:
#   pip install -r requirements.txt
#   pyinstaller erechnung.spec --clean
#
# Ergebnis liegt in dist/ als Einzel-Executable
# (Windows: SmartRechnung.exe, macOS: SmartRechnung.app, Linux: SmartRechnung).

# -*- mode: python ; coding: utf-8 -*-

import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = []
hidden += collect_submodules("weasyprint")
hidden += collect_submodules("facturx")
hidden += collect_submodules("uvicorn")
hidden += collect_submodules("app")

datas = []
datas += collect_data_files("weasyprint")
datas += collect_data_files("facturx")
datas += [("app/templates", "app/templates")]

# Platform-spezifisches Icon
if sys.platform.startswith("win"):
    icon_file = "assets/icon.ico"
elif sys.platform == "darwin":
    icon_file = "assets/icon.icns"
else:
    icon_file = "assets/icon_512.png"


a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas,
    name="SmartRechnung",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    console=False,          # kein Terminal-Fenster bei Doppelklick
    disable_windowed_traceback=False,
    icon=icon_file,
)

# macOS: Bundle als .app
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="SmartRechnung.app",
        icon=icon_file,
        bundle_identifier="de.smartrechnung.app",
        info_plist={
            "CFBundleName": "SmartRechnung",
            "CFBundleDisplayName": "SmartRechnung",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
        },
    )
