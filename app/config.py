"""Multi-Firma-Konfiguration.

Pro Installation koennen beliebig viele Firmen angelegt werden. Jede Firma hat
ihren eigenen Ordner unter data/companies/<slug>/ mit:
  seller.json      -> Firmendaten + Nummernformat + Naechster-Zaehler
  branding.json    -> Farben, Schrift, Logo, aktive Vorlage
  templates/       -> HTML-Vorlagen fuer die PDF-Rechnung (editierbar)
  branding/        -> Logo-Dateien

Die aktive Firma wird pro Browsersitzung in einem Cookie gehalten.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Optional


# --- Datenverzeichnis ---------------------------------------------------
def default_data_dir() -> Path:
    env = os.environ.get("ERECHNUNG_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("USERPROFILE", "~")) / "Documents"
    elif sys.platform == "darwin":
        base = Path.home() / "Documents"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return (base / "SmartRechnung").resolve()


DATA_DIR: Path = default_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

COMPANIES_DIR = DATA_DIR / "companies"
COMPANIES_DIR.mkdir(parents=True, exist_ok=True)


# --- Firma --------------------------------------------------------------
DEFAULT_SELLER = {
    "display_name": "",            # Anzeigename im UI, kann Leerzeichen etc. enthalten
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
    # Nummernkreis
    "number_format": "{year}-{nn:03d}",
    "next_number": 1,
    # Umsatzsteuer-Regime (bestimmt Pflichthinweise auf der Rechnung)
    "tax_regime": "regular",   # regular | small_business | vat_exempt
    "reverse_charge_default": False,
}


def _slugify(name: str) -> str:
    """URL-tauglicher Ordnername aus einem Firmen-Anzeigenamen."""
    s = name.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s)
    s = s.strip("-")
    return s or "firma"


class Company:
    def __init__(self, slug: str, data: dict):
        self.slug = slug
        self._data = {**DEFAULT_SELLER, **data}

    @property
    def dir(self) -> Path:
        return COMPANIES_DIR / self.slug

    @property
    def templates_dir(self) -> Path:
        return self.dir / "templates"

    @property
    def assets_dir(self) -> Path:
        return self.dir / "branding"

    @property
    def seller_file(self) -> Path:
        return self.dir / "seller.json"

    @property
    def branding_file(self) -> Path:
        return self.dir / "branding.json"

    @property
    def display_name(self) -> str:
        return self._data["display_name"] or self._data["seller_name"] or self.slug

    def __getattr__(self, name: str):
        if name in DEFAULT_SELLER:
            return self._data[name]
        raise AttributeError(name)

    def to_dict(self) -> dict:
        return {"slug": self.slug, **self._data, "display_name": self.display_name}

    def is_configured(self) -> bool:
        d = self._data
        minimum = [d["seller_name"], d["seller_street"], d["seller_postcode"],
                   d["seller_city"], d["seller_email"]]
        has_tax = d["seller_tax_id"] or d["seller_tax_number"]
        return all(minimum) and bool(has_tax)

    def format_next_number(self, when: Optional[date] = None, n: Optional[int] = None) -> str:
        """Erzeugt die naechste Rechnungsnummer anhand des Firmen-Formats.
        Platzhalter: {year}, {yy}, {month}, {mm}, {nn} (unterstuetzt :0Nd-Breite)."""
        when = when or date.today()
        n = n if n is not None else int(self._data["next_number"])
        vars_ = {
            "year": when.year,
            "yy": when.year % 100,
            "month": when.month,
            "mm": when.month,  # das Format :02d legt die Breite fest, mm/month sind gleich
            "nn": n,
        }
        try:
            return self._data["number_format"].format(**vars_)
        except (KeyError, IndexError, ValueError):
            # Fallback auf Standardformat bei fehlerhaftem Muster
            return f"{when.year}-{n:03d}"

    def bump_number_after(self, used_number: str) -> None:
        """Nach einer erfolgreichen Rechnung: next_number auf den naechsten Wert
        bringen. Extrahiert die hinterste Ziffernfolge aus used_number."""
        m = re.search(r"(\d+)(?!.*\d)", used_number)
        if m:
            used = int(m.group(1))
            self._data["next_number"] = max(int(self._data["next_number"]), used + 1)
            self.save()

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.seller_file.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def update(self, data: dict) -> None:
        for k in DEFAULT_SELLER:
            if k in data and data[k] is not None:
                v = data[k]
                if isinstance(v, str):
                    v = v.strip()
                if k == "next_number":
                    try:
                        v = max(1, int(v))
                    except (TypeError, ValueError):
                        v = self._data["next_number"]
                self._data[k] = v
        self.save()


# --- CompanyManager -----------------------------------------------------
def list_companies() -> list[Company]:
    out = []
    for p in sorted(COMPANIES_DIR.iterdir() if COMPANIES_DIR.exists() else []):
        if p.is_dir() and (p / "seller.json").exists():
            data = json.loads((p / "seller.json").read_text(encoding="utf-8"))
            out.append(Company(p.name, data))
    return out


def get_company(slug: str) -> Optional[Company]:
    p = COMPANIES_DIR / slug
    seller = p / "seller.json"
    if not seller.exists():
        return None
    data = json.loads(seller.read_text(encoding="utf-8"))
    return Company(slug, data)


def create_company(form_data: dict) -> Company:
    display = (form_data.get("display_name") or form_data.get("seller_name") or "").strip()
    if not display:
        raise ValueError("Firmenname fehlt.")
    base_slug = _slugify(display)
    slug = base_slug
    n = 2
    while (COMPANIES_DIR / slug).exists():
        slug = f"{base_slug}-{n}"
        n += 1
    c = Company(slug, {})
    c._data["display_name"] = display
    c.update(form_data)
    _init_company_files(c)
    return c


def delete_company(slug: str) -> None:
    p = COMPANIES_DIR / slug
    if p.exists():
        shutil.rmtree(p)


def _bundled_template_dir() -> Path:
    return Path(__file__).parent / "templates" / "invoice_pdf"


def _init_company_files(c: Company) -> None:
    """Kopiert Standardvorlagen und schreibt ein Default-Branding - nur einmal
    pro Firma. Bestehende Dateien werden nie ueberschrieben."""
    c.templates_dir.mkdir(parents=True, exist_ok=True)
    c.assets_dir.mkdir(parents=True, exist_ok=True)
    for tpl in _bundled_template_dir().glob("*.html"):
        target = c.templates_dir / tpl.name
        if not target.exists():
            shutil.copy(tpl, target)
    if not c.branding_file.exists():
        from .branding import DEFAULT_BRANDING
        c.branding_file.write_text(
            json.dumps(DEFAULT_BRANDING, indent=2, ensure_ascii=False), encoding="utf-8"
        )


# --- Legacy-Migration: alte seller.json -> erste Firma ------------------
def _migrate_legacy() -> None:
    legacy = DATA_DIR / "seller.json"
    legacy_branding = DATA_DIR / "branding.json"
    legacy_templates = DATA_DIR / "templates"
    if legacy.exists() and not any(COMPANIES_DIR.iterdir()):
        data = json.loads(legacy.read_text(encoding="utf-8"))
        display = data.get("seller_name") or "Meine Firma"
        slug = _slugify(display)
        c = Company(slug, {})
        c._data["display_name"] = display
        c.update(data)
        _init_company_files(c)
        if legacy_branding.exists():
            shutil.copy(legacy_branding, c.branding_file)
        if legacy_templates.exists():
            for tpl in legacy_templates.glob("*.html"):
                target = c.templates_dir / tpl.name
                if not target.exists():
                    shutil.copy(tpl, target)


_migrate_legacy()


# --- Legacy-Interface fuer DB-URL ---------------------------------------
class _SettingsProxy:
    @property
    def database_url(self) -> str:
        return f"sqlite:///{DATA_DIR}/erechnung.db"

    @property
    def data_dir(self) -> str:
        return str(DATA_DIR)


settings = _SettingsProxy()
