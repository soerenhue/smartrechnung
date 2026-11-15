"""Branding-Konfiguration pro Installation.

Farben, Schriftart, Logo, aktive Rechnungsvorlage, Fusszeile. Ueber die
Einstellungsseite im UI veraenderbar. Wird in data/branding.json gespeichert.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import DATA_DIR

BRANDING_FILE = DATA_DIR / "branding.json"
TEMPLATES_DIR = DATA_DIR / "templates"
BRANDING_ASSETS_DIR = DATA_DIR / "branding"

DEFAULT_BRANDING = {
    "template": "standard.html",
    "primary_color": "#1e3a8a",
    "accent_color": "#3b82f6",
    "text_color": "#111827",
    "muted_color": "#6b7280",
    "font_family": "DejaVu Sans, sans-serif",
    "logo_filename": "",
    "footer_line_1": "",
    "footer_line_2": "",
    "footer_line_3": "",
}


def _bundled_template_dir() -> Path:
    return Path(__file__).parent / "templates" / "invoice_pdf"


def ensure_initialized() -> None:
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
