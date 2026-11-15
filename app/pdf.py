"""Erzeugt das Rechnungs-PDF per WeasyPrint aus der Firmenvorlage.

Mit Diagnose-Logging rund um den WeasyPrint-Call, damit native Crashes
wenigstens einen Checkpoint in der Log hinterlassen."""

from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import date
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .branding import load_branding
from .config import Company, DATA_DIR
from .models import Invoice


def _diag(msg: str) -> None:
    """Direkt in die startup.log schreiben - hilft, wenn der Prozess nativ stirbt."""
    try:
        log_file = DATA_DIR / "startup.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [PDF] {msg}\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


def _fmt_money(value: Decimal | float | int, currency: str = "EUR") -> str:
    v = Decimal(value).quantize(Decimal("0.01"))
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    symbol = {"EUR": "€", "USD": "$", "CHF": "CHF"}.get(currency, currency)
    return f"{s} {symbol}"


def _fmt_date(d: date) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


def _fmt_qty(v: Decimal) -> str:
    q = Decimal(v).normalize()
    if q == q.to_integral():
        return str(q.quantize(Decimal("1")))
    return f"{q:.3f}".rstrip("0").rstrip(".")


def render_invoice_pdf(inv: Invoice, company: Company) -> bytes:
    _diag(f"START render_invoice_pdf fuer {inv.invoice_number}")
    _diag(f"  FONTCONFIG_FILE = {os.environ.get('FONTCONFIG_FILE', '(nicht gesetzt)')}")
    _diag(f"  DYLD_FALLBACK_LIBRARY_PATH = {os.environ.get('DYLD_FALLBACK_LIBRARY_PATH', '(nicht gesetzt)')}")
    _diag(f"  templates_dir = {company.templates_dir}")
    _diag(f"  assets_dir = {company.assets_dir}")

    try:
        branding = load_branding(company)
        template_name = branding.get("template", "standard.html")
        _diag(f"  Vorlage: {template_name}")

        tpl_path = company.templates_dir / template_name
        if not tpl_path.exists():
            _diag(f"  FEHLER: Vorlage nicht gefunden: {tpl_path}")
            raise FileNotFoundError(f"Vorlage {template_name} nicht gefunden in {company.templates_dir}")

        env = Environment(
            loader=FileSystemLoader(str(company.templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        env.filters["money"] = _fmt_money
        env.filters["date"] = _fmt_date
        env.filters["qty"] = _fmt_qty

        template = env.get_template(template_name)
        html_text = template.render(inv=inv, items=inv.items, branding=branding)
        _diag(f"  Jinja-Render fertig, HTML {len(html_text)} Zeichen")

        # Import erst hier, damit das Diag-Logging davor steht
        _diag("  Importiere weasyprint...")
        from weasyprint import CSS, HTML  # noqa: PLC0415
        _diag("  weasyprint importiert")

        # Zusaetzliches Sicherheits-Stylesheet: zwingt bei JEDER Vorlage
        # eine Font-Fallback-Kette durch, damit auch alte/importierte
        # Vorlagen mit custom Font-Angaben nicht zum nativen Pango-Crash fuehren.
        safety_css = CSS(string="""
            html, body {
                font-family: "Helvetica Neue", Helvetica, Arial, "DejaVu Sans", sans-serif;
            }
        """)

        _diag("  Rufe HTML(...).write_pdf() auf")
        pdf_bytes = HTML(string=html_text, base_url=str(company.assets_dir)).write_pdf(
            stylesheets=[safety_css]
        )
        _diag(f"  PDF-Erzeugung fertig, {len(pdf_bytes)} Bytes")
        return pdf_bytes
    except Exception:
        _diag("PDF-FEHLER:\n" + traceback.format_exc())
        raise
