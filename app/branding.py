"""Branding pro Firma.

Jede Firma hat ihre eigene branding.json in ihrem Ordner. Hier liegen Farben,
Schriftart, aktives Rechnungs-Layout, Logo-Dateiname und Fusszeilen.
"""

from __future__ import annotations

import json

from .config import Company


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


def load_branding(company: Company) -> dict:
    if not company.branding_file.exists():
        company.branding_file.parent.mkdir(parents=True, exist_ok=True)
        company.branding_file.write_text(
            json.dumps(DEFAULT_BRANDING, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    data = json.loads(company.branding_file.read_text(encoding="utf-8"))
    for k, v in DEFAULT_BRANDING.items():
        data.setdefault(k, v)
    return data


def save_branding(company: Company, data: dict) -> None:
    merged = load_branding(company)
    merged.update({k: v for k, v in data.items() if k in DEFAULT_BRANDING})
    company.branding_file.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def list_templates(company: Company) -> list[str]:
    if not company.templates_dir.exists():
        company.templates_dir.mkdir(parents=True, exist_ok=True)
    return sorted(p.name for p in company.templates_dir.glob("*.html"))
