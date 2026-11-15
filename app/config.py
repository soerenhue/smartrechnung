"""Konfiguration pro Installation.

Alles liegt im data-Verzeichnis:
  - seller.json       -> Firmendaten (durch Einrichtungs-Assistenten gesetzt)
  - branding.json     -> Farben, Schrift, Logo, Vorlagen-Auswahl
  - templates/        -> Rechnungs-Layouts (HTML/CSS, editierbar)
  - branding/         -> Logo-Dateien
  - erechnung.db      -> SQLite-Datenbank
  - invoices/         -> erzeugte PDFs und XMLs

Der data-Ordner liegt auf dem Rechner des Nutzers in einem portablen Pfad,
z. B. unter Windows: C:\\Users\\<User>\\Documents\\E-Rechnung
       unter macOS:   ~/Documents/E-Rechnung
       unter Linux:   ~/.local/share/E-Rechnung
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def default_data_dir() -> Path:
    """Platform-spezifisch sinnvolles Daten-Verzeichnis. Kann per
    Umgebungsvariable ERECHNUNG_DATA_DIR ueberschrieben werden."""
    env = os.environ.get("ERECHNUNG_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("USERPROFILE", "~")) / "Documents"
    elif sys.platform == "darwin":
        base = Path.home() / "Documents"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return (base / "E-Rechnung").resolve()


DATA_DIR: Path = default_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

SELLER_FILE = DATA_DIR / "seller.json"


DEFAULT_SELLER = {
    "seller_name": "",
    "seller_street": "",
    "seller_postcode": "",
    "seller_city": "",
    "seller_country": "DE",
    "seller_tax_id": "",
    "seller_tax_number": "",
    "seller_email": "",
    "seller_iban": "",
    "seller_bic": "",
    "seller_bank": "",
}


def load_seller() -> dict:
    if SELLER_FILE.exists():
        data = json.loads(SELLER_FILE.read_text(encoding="utf-8"))
        return {**DEFAULT_SELLER, **data}
    return dict(DEFAULT_SELLER)


def save_seller(data: dict) -> None:
    merged = load_seller()
    for k in DEFAULT_SELLER:
        if k in data:
            merged[k] = (data[k] or "").strip()
    SELLER_FILE.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")


def is_configured() -> bool:
    """True sobald mindestens Name, Adresse und USt-IdNr./Steuernr. gesetzt sind."""
    s = load_seller()
    minimum = [s["seller_name"], s["seller_street"], s["seller_postcode"],
               s["seller_city"], s["seller_email"]]
    has_tax = s["seller_tax_id"] or s["seller_tax_number"]
    return all(minimum) and bool(has_tax)


# Legacy-Interface fuer bestehenden Code (settings.seller_name etc.)
class _SettingsProxy:
    @property
    def database_url(self) -> str:
        return f"sqlite:///{DATA_DIR}/erechnung.db"

    @property
    def data_dir(self) -> str:
        return str(DATA_DIR)

    def __getattr__(self, name: str):
        s = load_seller()
        if name in s:
            return s[name]
        raise AttributeError(name)

    def model_dump(self) -> dict:
        return {**load_seller(), "data_dir": str(DATA_DIR)}


settings = _SettingsProxy()
