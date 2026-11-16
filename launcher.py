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


_LOCK_FILE = _log_dir() / "smartrechnung.lock"


def _acquire_startup_lock() -> bool:
    """Lock-Datei verhindert, dass mehrere parallele Doppel-Klicks mehrere
    Instanzen starten. Der erste Start schreibt die PID rein, weitere
    Starts sehen die frische Lock-Datei und beenden sich sofort mit
    einem Hinweis, dass die App gerade hochfährt.

    Lock gilt als 'frisch' wenn er weniger als 60 Sekunden alt ist - nach
    einem Crash soll die App ja wieder starten können."""
    try:
        if _LOCK_FILE.exists():
            age = time.time() - _LOCK_FILE.stat().st_mtime
            if age < 60:
                return False
        _LOCK_FILE.write_text(str(os.getpid()))
        return True
    except Exception:
        return True  # Im Zweifel starten


def _release_startup_lock() -> None:
    try:
        if _LOCK_FILE.exists():
            _LOCK_FILE.unlink()
    except Exception:
        pass


def _already_running(port: int = 8765) -> bool:
    """Prüft, ob auf dem Standard-Port schon eine SmartRechnung-Instanz
    läuft. Mehrere Versuche mit kurzen Pausen, weil beim Start der Server
    ein paar Sekunden braucht, bevor er antwortet."""
    import urllib.request
    for _ in range(3):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as r:
                body = r.read(512).decode("utf-8", errors="ignore")
                if "SmartRechnung" in body:
                    return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


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
        # Single-Instance-Check: Wenn SmartRechnung schon läuft, einfach
        # den Browser auf die bestehende Instanz richten und beenden
        if _already_running(8765):
            log("SmartRechnung läuft bereits - öffne bestehendes Fenster")
            webbrowser.open("http://127.0.0.1:8765/")
            return 0

        # Lock-Datei verhindert mehrere parallele Starts durch ungeduldiges
        # Doppelklicken. Wenn ein Start läuft, beenden wir uns leise.
        if not _acquire_startup_lock():
            log("Startup-Lock aktiv (App fährt gerade hoch) - beende diese Instanz")
            # Sanfter Hinweis für den User, dass seine erste Instanz noch kommt
            try:
                import tkinter as tk
                root = tk.Tk(); root.withdraw()
                from tkinter import messagebox
                messagebox.showinfo(
                    "SmartRechnung startet",
                    "SmartRechnung fährt gerade hoch. "
                    "Bitte einen Moment Geduld - das Fenster öffnet sich gleich.")
                root.destroy()
            except Exception:
                pass
            return 0

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

        # Lock freigeben, Server ist bereit - weitere Klicks finden die App
        _release_startup_lock()

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
        _release_startup_lock()
        _show_error_window(
            f"SmartRechnung ist beim Start abgestuerzt.\n\n{tb}\n\nLog: {LOG_FILE}"
        )
        return 1
    finally:
        _release_startup_lock()


if __name__ == "__main__":
    raise SystemExit(main())
