"""Rendert das Rechnungs-PDF per Playwright (Chromium headless).

Warum nicht WeasyPrint?
WeasyPrint nutzt Pango/Cairo/GLib als C-Libraries via cffi. Auf macOS+
PyInstaller fuehrt das zu nicht behebbaren GObject-Double-Registration-
Crashes (cffi laedt Libs RTLD_LOCAL, libpango laedt libgobject neu).

Playwright steuert Chromium (das die PDF-Erzeugung macht). Chromium bringt
alles Noetige mit und ist vollstaendig unabhaengig vom System.
"""

from __future__ import annotations

import os
import sys
import time
import tempfile
import traceback
from datetime import date
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .branding import load_branding
from .config import Company, DATA_DIR
from .models import Invoice


def _diag(msg: str) -> None:
    try:
        log_file = DATA_DIR / "startup.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [PDF] {msg}\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


def _fmt_money(value, currency: str = "EUR") -> str:
    v = Decimal(value).quantize(Decimal("0.01"))
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    symbol = {"EUR": "€", "USD": "$", "CHF": "CHF"}.get(currency, currency)
    return f"{s} {symbol}"


def _fmt_date(d: date) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


def _fmt_qty(v) -> str:
    q = Decimal(v).normalize()
    if q == q.to_integral():
        return str(q.quantize(Decimal("1")))
    return f"{q:.3f}".rstrip("0").rstrip(".")


def render_invoice_pdf(inv: Invoice, company: Company) -> bytes:
    _diag(f"START render_invoice_pdf fuer {inv.invoice_number}")

    branding = load_branding(company)
    template_name = branding.get("template", "standard.html")
    _diag(f"  Vorlage: {template_name}")

    tpl_path = company.templates_dir / template_name
    if not tpl_path.exists():
        _diag(f"  FEHLER: Vorlage nicht gefunden: {tpl_path}")
        raise FileNotFoundError(f"Vorlage {template_name} nicht gefunden")

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

    # HTML temporaer in das Assets-Verzeichnis der Firma schreiben, damit
    # relative Pfade (Logo, QR-Code) aufgeloest werden koennen.
    tmp_html = company.assets_dir / f".invoice_render_{inv.invoice_number}.html"
    try:
        tmp_html.write_text(html_text, encoding="utf-8")
        _diag(f"  Temp-HTML: {tmp_html}")

        # Chromium via Playwright - erst hier importieren (lange Startzeit)
        _diag("  Importiere playwright...")
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
        _diag("  playwright importiert, starte Chromium...")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    _diag("  Chromium gestartet, oeffne HTML...")
                    page = browser.new_page()
                    file_url = tmp_html.resolve().as_uri()
                    page.goto(file_url, wait_until="networkidle", timeout=30000)
                    _diag("  Rendere PDF...")
                    pdf_bytes = page.pdf(
                        format="A4",
                        print_background=True,
                        margin={"top": "18mm", "right": "15mm",
                                "bottom": "22mm", "left": "15mm"},
                        prefer_css_page_size=True,
                    )
                    _diag(f"  PDF fertig: {len(pdf_bytes)} Bytes")
                    return pdf_bytes
                finally:
                    browser.close()
        except Exception:
            _diag("PDF-FEHLER (Playwright):\n" + traceback.format_exc())
            raise
    finally:
        try:
            tmp_html.unlink()
        except OSError:
            pass
