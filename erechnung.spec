# PyInstaller-Konfiguration fuer die Desktop-App.
#
# Bauen:
#   pip install -r requirements.txt
#   pyinstaller erechnung.spec --clean
#
# Ergebnis liegt in dist/ als Einzel-Executable (Windows: E-Rechnung.exe,
# macOS: E-Rechnung.app, Linux: E-Rechnung).

# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = []
hidden += collect_submodules("weasyprint")
hidden += collect_submodules("facturx")
hidden += collect_submodules("uvicorn")
hidden += collect_submodules("app")

datas = []
datas += collect_data_files("weasyprint")
datas += collect_data_files("facturx")
# App-Templates mitnehmen (werden beim ersten Start nach data/ kopiert)
datas += [("app/templates", "app/templates")]


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
    name="E-Rechnung",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    console=False,          # kein Terminal-Fenster bei Doppelklick
    disable_windowed_traceback=False,
    icon=None,
)
