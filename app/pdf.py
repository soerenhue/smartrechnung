"""Erzeugt das Rechnungs-PDF per WeasyPrint aus einer Jinja2-Vorlage.

Die Vorlage liegt austauschbar in data/templates/<name>.html. Jeder, der die App
betreibt, kann die Vorlage anpassen oder eine eigene daneben legen (z. B.
'onkel.html') und in der Einstellungsseite auswaehlen."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from .branding import BRANDING_ASSETS_DIR, TEMPLATES_DIR, load_branding
from .models import Invoice


def _fmt_money(value: Decimal | float | int, currency: str = "EUR") -> str:
    v = Decimal(value).quantize(Decimal("0.01"))
    s = f"{v:,.2f}"
    # Deutsche Notation: 1.234,56
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    symbol = {"EUR": "€", "USD": "$", "CHF": "CHF"}.get(currency, currency)
    return f"{s} {symbol}"


def _fmt_date(d: date) -> str:
    return d.strftime("%d.%m.%Y") if d else ""


def _fmt_qty(v: Decimal) -> str:
    q = Decimal(v).normalize()
    if q == q.to_integral():
        return str(q.quantize(Decimal("1")))
    return f"{q:.3f}".rstrip("0").rstrip(".")


def render_invoice_pdf(inv: Invoice) -> bytes:
    """Rendert das Rechnungs-PDF. Gibt die PDF-Bytes zurueck (noch ohne eingebettetes XML)."""
    branding = load_branding()
    template_name = branding.get("template", "standard.html")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["money"] = _fmt_money
    env.filters["date"] = _fmt_date
    env.filters["qty"] = _fmt_qty

    template = env.get_template(template_name)
    html_text = template.render(
        inv=inv,
        items=inv.items,
        branding=branding,
    )

    # base_url erlaubt WeasyPrint, Bilder (Logo) aus data/branding/ zu laden
    pdf_bytes = HTML(string=html_text, base_url=str(BRANDING_ASSETS_DIR)).write_pdf()
    return pdf_bytes


def logo_path() -> Path | None:
    branding = load_branding()
    if not branding.get("logo_filename"):
        return None
    p = BRANDING_ASSETS_DIR / branding["logo_filename"]
    return p if p.exists() else None
