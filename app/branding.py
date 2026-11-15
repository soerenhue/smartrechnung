"""Branding-Konfiguration pro Installation.

Jede laufende Instanz (also jeder Freund, der die App bei sich betreibt) hat
eine eigene branding.json im data-Verzeichnis. Darin: Farben, Schriftart,
Logo-Pfad, ausgewaehlte Rechnungsvorlage, Fusszeile. Veraenderbar ueber die
Einstellungsseite im UI."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import settings

BRANDING_FILE = Path(settings.data_dir) / "branding.json"
TEMPLATES_DIR = Path(settings.data_dir) / "templates"
BRANDING_ASSETS_DIR = Path(settings.data_dir) / "branding"

DEFAULT_BRANDING = {
    "template": "standard.html",
    "primary_color": "#1e3a8a",
    "accent_color": "#3b82f6",
    "text_color": "#111827",
    "muted_color": "#6b7280",
    "font_family": "DejaVu Sans, sans-serif",
    "logo_filename": "",            # z. B. "logo.png" (Datei liegt in data/branding/)
    "footer_line_1": "",            # z. B. "Meine Firma GmbH - Musterstr. 1 - 10115 Berlin"
    "footer_line_2": "",            # z. B. "Geschaeftsfuehrer: Max Mustermann - HRB 12345 Berlin"
    "footer_line_3": "",            # z. B. "USt-IdNr. DE123456789 - St.-Nr. 12/345/67890"
}


def _bundled_template_dir() -> Path:
    return Path(__file__).parent / "templates" / "invoice_pdf"


def ensure_initialized() -> None:
    """Beim ersten Start: Standardvorlagen ins data-Verzeichnis kopieren,
    branding.json anlegen. Bestehende Dateien werden NICHT ueberschrieben -
    der User kann sie frei anpassen."""
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    BRANDING_ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    for tpl in _bundled_template_dir().glob("*.html"):
        target = TEMPLATES_DIR / tpl.name
        if not target.exists():
            shutil.copy(tpl, target)

    if not BRANDING_FILE.exists():
        BRANDING_FILE.write_text(json.dumps(DEFAULT_BRANDING, indent=2, ensure_ascii=False), encoding="utf-8")


def load_branding() -> dict:
    ensure_initialized()
    data = json.loads(BRANDING_FILE.read_text(encoding="utf-8"))
    # Fallback fuer neue Keys bei Updates
    for k, v in DEFAULT_BRANDING.items():
        data.setdefault(k, v)
    return data


def save_branding(data: dict) -> None:
    merged = load_branding()
    merged.update({k: v for k, v in data.items() if k in DEFAULT_BRANDING})
    BRANDING_FILE.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")


def list_templates() -> list[str]:
    ensure_initialized()
    return sorted(p.name for p in TEMPLATES_DIR.glob("*.html"))
