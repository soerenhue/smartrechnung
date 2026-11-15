"""Desktop-Launcher. Nutzt jetzt Playwright+Chromium fuer die PDF-Erzeugung
statt WeasyPrint - keine System-C-Libraries mehr noetig, keine Pango-
Konflikte."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path


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
STDERR_FILE = _log_dir() / "stderr.log"


# stderr/stdout in Datei umleiten, damit native Fehler lesbar sind
try:
    _err = open(STDERR_FILE, "a", buffering=1, encoding="utf-8")
    _err.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    _err.flush()
    os.dup2(_err.fileno(), 2)
    os.dup2(_err.fileno(), 1)
    sys.stderr = _err
    sys.stdout = _err
except Exception:
    pass


def _setup_playwright_browsers_path() -> None:
    """In der gepackten App (PyInstaller) liegt Chromium unter
    Resources/ms-playwright/. Diese Umgebungsvariable zeigt Playwright
    darauf, sodass es nicht versucht, Chromium nachzuinstallieren."""
    if not getattr(sys, "frozen", False):
        return
    # PyInstaller onedir: sys._MEIPASS zeigt auf das Extraktions-Verzeichnis.
    # macOS .app-Bundle: Contents/MacOS/SmartRechnung + Contents/Resources
    bundle_dir = Path(os.path.abspath(sys.executable)).parent
    candidates = [
        Path(getattr(sys, "_MEIPASS", str(bundle_dir))) / "ms-playwright",
        bundle_dir / ".." / "Resources" / "ms-playwright",
        bundle_dir / "ms-playwright",
    ]
    for p in candidates:
        if p.exists() and any(p.iterdir()):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(p.resolve())
            return


_setup_playwright_browsers_path()


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
        from app.main import app
        log("app.main importiert, starte Server ...")
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except Exception:
        log("SERVER-FEHLER:\n" + traceback.format_exc())


def _show_error_window(message: str):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("SmartRechnung konnte nicht starten", message)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


def main() -> int:
    log("=" * 60)
    log(f"SmartRechnung-Start | Python {sys.version} | Platform {sys.platform}")
    log(f"Executable: {sys.executable}")
    log(f"PLAYWRIGHT_BROWSERS_PATH = {os.environ.get('PLAYWRIGHT_BROWSERS_PATH', '(nicht gesetzt)')}")

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
            log("FEHLER: Server-Timeout.")
            _show_error_window(msg)
            return 1

        log(f"Server laeuft auf {url}")

        try:
            import webview
            log(f"pywebview geladen")
            webview.create_window(
                "SmartRechnung", url, width=1180, height=820, min_size=(800, 600),
            )
            gui = "cocoa" if sys.platform == "darwin" else ("edgechromium" if sys.platform.startswith("win") else None)
            log(f"Starte pywebview mit gui={gui}")
            webview.start(gui=gui, debug=False)
            log("Fenster geschlossen, beende App")
        except ImportError as e:
            log(f"pywebview nicht verfuegbar ({e}), oeffne Systembrowser")
            webbrowser.open(url)
            print(f"\nSmartRechnung laeuft auf {url}")
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
            f"SmartRechnung ist beim Start abgestuerzt.\n\n{tb}\n\nLog: {LOG_FILE}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
