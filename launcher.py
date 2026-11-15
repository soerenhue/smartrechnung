"""Desktop-Launcher mit Fehler-Logging.

Alles, was schiefgeht, wird in eine Log-Datei geschrieben:
  Windows: %USERPROFILE%\\Documents\\SmartRechnung\\startup.log
  macOS:   ~/Documents/SmartRechnung/startup.log
  Linux:   ~/.local/share/SmartRechnung/startup.log

Wenn die App nicht startet, schick mir diese Datei - da steht der Grund drin.
"""

from __future__ import annotations

import ctypes
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path


def _bootstrap_macos_libs() -> None:
    """Vor WeasyPrint-Import:
    - gebuendelte dylibs via ctypes vorladen (gobject/pango/cairo/...)
    - Fontconfig auf eine mitgelieferte fonts.conf zeigen lassen, die
      auf macOS-System-Fontpfade verweist statt auf Homebrew
    - FONTCONFIG_PATH/FONTCONFIG_FILE und DYLD_FALLBACK_LIBRARY_PATH setzen
    """
    if not (getattr(sys, "frozen", False) and sys.platform == "darwin"):
        return
    bundle_dir = Path(os.path.abspath(sys.executable)).parent  # Contents/MacOS/
    contents = bundle_dir.parent  # Contents/
    frameworks = (contents / "Frameworks").resolve()
    resources = (contents / "Resources").resolve()

    if frameworks.is_dir():
        current = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = (
            f"{frameworks}:{current}" if current else str(frameworks)
        )
        for p in sorted(frameworks.glob("*.dylib")):
            try:
                ctypes.CDLL(str(p), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass

    # Fontconfig: wir schreiben beim Start eine fonts.conf MIT ABSOLUTEN PFADEN
    # ins User-Verzeichnis. Grund: Fontconfig mag keine relativen Pfade in
    # <dir>-Eintraegen, besonders nicht wenn es an verschiedenen Orten laeuft
    # (AppTranslocation!). Darum bauen wir die Konfig live.
    bundled_fonts = resources / "fonts"
    user_data_dir = Path.home() / "Documents" / "SmartRechnung"
    user_data_dir.mkdir(parents=True, exist_ok=True)
    user_fonts_conf = user_data_dir / "fonts.conf"

    fonts_conf_text = f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <dir>{bundled_fonts}</dir>
  <dir>/System/Library/Fonts</dir>
  <dir>/Library/Fonts</dir>
  <dir>{Path.home() / "Library" / "Fonts"}</dir>
  <cachedir>{Path.home() / ".cache" / "fontconfig"}</cachedir>

  <alias><family>serif</family><prefer>
    <family>DejaVu Serif</family><family>Helvetica</family><family>Arial</family>
  </prefer></alias>
  <alias><family>sans-serif</family><prefer>
    <family>DejaVu Sans</family><family>Helvetica Neue</family>
    <family>Helvetica</family><family>Arial</family>
  </prefer></alias>
  <alias><family>monospace</family><prefer>
    <family>DejaVu Sans Mono</family><family>Menlo</family><family>Monaco</family>
  </prefer></alias>

  <!-- CATCH-ALL: Jeder Font-Lookup (egal welcher Name!) bekommt am Ende
       eine stilistisch passende Kette angehaengt, damit Pango IMMER
       eine Font findet - und eine die stilistisch passt.

       Die Reihenfolge ist absichtlich: zuerst Serif-Fallbacks, dann
       Sans-Fallbacks, dann Mono-Fallbacks. Fontconfig waehlt davon
       die erste tatsaechlich vorhandene. -->
  <match target="pattern">
    <edit name="family" mode="append_last" binding="weak">
      <string>Georgia</string>
      <string>Times New Roman</string>
      <string>DejaVu Serif</string>
      <string>Helvetica Neue</string>
      <string>Helvetica</string>
      <string>Arial</string>
      <string>DejaVu Sans</string>
      <string>Menlo</string>
      <string>DejaVu Sans Mono</string>
    </edit>
  </match>
</fontconfig>
"""
    user_fonts_conf.write_text(fonts_conf_text, encoding="utf-8")
    os.environ["FONTCONFIG_FILE"] = str(user_fonts_conf)
    os.environ["FONTCONFIG_PATH"] = str(resources)

    # Fontconfig-Cache
    (Path.home() / ".cache" / "fontconfig").mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("XDG_CACHE_HOME", str(Path.home() / ".cache"))


_bootstrap_macos_libs()


def _log_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("USERPROFILE", "~")) / "Documents"
    elif sys.platform == "darwin":
        base = Path.home() / "Documents"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / "SmartRechnung"
    d.mkdir(parents=True, exist_ok=True)
    return d


LOG_FILE = _log_dir() / "startup.log"


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="", flush=True)


def _find_free_port(preferred: int = 8765) -> int:
    for port in (preferred, 8766, 8767, 8080, 8081):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _run_server(port: int):
    try:
        log("Starte uvicorn ...")
        import uvicorn
        log("uvicorn importiert, importiere app.main ...")
        from app.main import app  # Hier kracht es, wenn Deps fehlen
        log("app.main importiert, starte Server ...")
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except Exception:
        log("SERVER-FEHLER:\n" + traceback.format_exc())


def _show_error_window(message: str):
    """Letzter Rettungsanker: Fehlermeldung in nativem Fenster anzeigen."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("SmartRechnung konnte nicht starten", message)
        root.destroy()
    except Exception:
        # Wenn selbst tkinter nicht geht: stdout/stderr
        print(message, file=sys.stderr)


def main() -> int:
    log("=" * 60)
    log(f"SmartRechnung-Start | Python {sys.version} | Platform {sys.platform}")
    log(f"Executable: {sys.executable}")
    log(f"Log-Datei: {LOG_FILE}")

    try:
        port = _find_free_port()
        log(f"Freier Port: {port}")
        url = f"http://127.0.0.1:{port}/"

        server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
        server_thread.start()

        if not _wait_for_server(port, timeout=30):
            msg = (f"Der eingebaute Server konnte nicht starten.\n\n"
                   f"Details in: {LOG_FILE}\n\n"
                   f"Bitte diese Datei an den Entwickler schicken.")
            log("FEHLER: Server-Timeout. Datei-Log siehe oben.")
            _show_error_window(msg)
            return 1

        log(f"Server laeuft auf {url}")

        # Bevorzugt pywebview, Fallback auf Browser
        try:
            import webview
            log(f"pywebview geladen (Version: {getattr(webview, '__version__', '?')})")
            window = webview.create_window(
                "SmartRechnung", url, width=1180, height=820, min_size=(800, 600),
            )
            # Backend je Plattform explizit - sonst kann pywebview auf macOS in
            # bestimmten PyInstaller-Builds nicht entscheiden, welches Backend es nimmt.
            gui = "cocoa" if sys.platform == "darwin" else ("edgechromium" if sys.platform.startswith("win") else None)
            log(f"Starte pywebview mit gui={gui}")
            webview.start(gui=gui, debug=False)
            log("Fenster geschlossen, beende App")
        except ImportError as e:
            log(f"pywebview nicht verfuegbar ({e}), oeffne Systembrowser")
            webbrowser.open(url)
            print(f"\nSmartRechnung laeuft auf {url}")
            print("Dieses Fenster geschlossen lassen, solange du die App nutzt.")
            print("Zum Beenden: Strg+C oder Fenster schliessen.\n")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        except Exception:
            tb = traceback.format_exc()
            log(f"pywebview-Fehler, Fallback auf Browser:\n{tb}")
            webbrowser.open(url)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

        return 0

    except Exception:
        tb = traceback.format_exc()
        log("STARTUP-FEHLER:\n" + tb)
        _show_error_window(
            f"SmartRechnung ist beim Start abgestuerzt.\n\n"
            f"{tb}\n\nLog: {LOG_FILE}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
