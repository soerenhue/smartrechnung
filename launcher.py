"""Desktop-Launcher.

Wird durch Doppelklick gestartet (nach PyInstaller-Verpackung ist das eine
.exe / .app / AppImage). Der Launcher:

  1. Startet die FastAPI-App auf einem freien localhost-Port (im Thread).
  2. Oeffnet ein natives Fenster (pywebview) auf diesem Port.
     Falls pywebview nicht geladen werden kann, wird stattdessen der
     Standard-Browser des Systems geoeffnet.
  3. Beendet den Server sauber, wenn das Fenster geschlossen wird.

Keine Terminal-Interaktion noetig - fuer den Endnutzer ein ganz normales
Fenster mit der E-Rechnungs-App drin.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser

import uvicorn


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


class _ServerThread(threading.Thread):
    def __init__(self, port: int):
        super().__init__(daemon=True)
        self.port = port
        self._config = uvicorn.Config(
            "app.main:app", host="127.0.0.1", port=port, log_level="warning",
        )
        self.server = uvicorn.Server(self._config)

    def run(self) -> None:
        self.server.run()

    def stop(self) -> None:
        self.server.should_exit = True


def _wait_for_server(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def main() -> int:
    port = _find_free_port()
    url = f"http://127.0.0.1:{port}/"

    server = _ServerThread(port)
    server.start()

    if not _wait_for_server(port):
        sys.stderr.write("Die App konnte nicht gestartet werden.\n")
        return 1

    try:
        import webview  # pywebview
        window = webview.create_window(
            "SmartRechnung", url, width=1180, height=820, min_size=(800, 600),
        )
        webview.start()
    except ImportError:
        # Fallback: System-Browser oeffnen und auf Strg+C warten
        webbrowser.open(url)
        print(f"SmartRechnung laeuft auf {url}")
        print("Dieses Fenster geschlossen lassen, solange du die App nutzt.")
        print("Zum Beenden: dieses Fenster schliessen oder Strg+C druecken.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
