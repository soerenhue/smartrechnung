# PyInstaller-Konfiguration fuer SmartRechnung.
#
# Playwright+Chromium wird mitgepackt (ca. 150 MB). Dafuer keine
# Pango/Cairo/GLib-Konflikte mehr - PDF-Rendering laeuft via Chromium
# headless. Keine System-C-Libraries.

# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hidden = []
hidden += collect_submodules("facturx")
hidden += collect_submodules("uvicorn")
hidden += collect_submodules("app")
hidden += collect_submodules("webview")
hidden += collect_submodules("playwright")
hidden += ["fastapi", "starlette", "jinja2", "sqlalchemy", "pydantic",
           "lxml", "lxml.etree", "multipart", "email.mime", "email.mime.text",
           "segno", "fitz"]

if sys.platform == "darwin":
    hidden += collect_submodules("objc")
    hidden += collect_submodules("Foundation")
    hidden += collect_submodules("AppKit")
    hidden += collect_submodules("WebKit")
    hidden += ["webview.platforms.cocoa"]
elif sys.platform.startswith("win"):
    hidden += ["webview.platforms.edgechromium", "webview.platforms.winforms"]
    hidden += collect_submodules("clr_loader")

datas = []
datas += collect_data_files("facturx")
datas += collect_data_files("fastapi")
datas += collect_data_files("starlette")
datas += collect_data_files("playwright")
datas += [("app/templates", "app/templates")]

# Playwright-Browser-Binaries (Chromium) ins Bundle packen.
# Workflow laedt sie vorher mit "playwright install chromium" herunter.
_browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
if _browsers_path and os.path.isdir(_browsers_path):
    datas += [(_browsers_path, "ms-playwright")]
else:
    # Fallback: Standard-Playwright-Cache
    _default_cache = os.path.expanduser("~/Library/Caches/ms-playwright") \
        if sys.platform == "darwin" else \
        os.path.expanduser("~/.cache/ms-playwright") \
        if sys.platform.startswith("linux") else \
        os.path.expandvars("%USERPROFILE%/AppData/Local/ms-playwright")
    if os.path.isdir(_default_cache):
        datas += [(_default_cache, "ms-playwright")]

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
    excludes=[],
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas,
    name="SmartRechnung",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon_file,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="SmartRechnung.app",
        icon=icon_file,
        bundle_identifier="de.smartrechnung.app",
        info_plist={
            "CFBundleName": "SmartRechnung",
            "CFBundleDisplayName": "SmartRechnung",
            "CFBundleShortVersionString": "1.1.0",
            "CFBundleVersion": "1.1.0",
            "NSHighResolutionCapable": True,
        },
    )
