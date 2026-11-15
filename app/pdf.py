"""Erzeugt das Rechnungs-PDF per WeasyPrint aus der Firmenvorlage."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from .branding import load_branding
from .config import Company
from .models import Invoice


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
    branding = load_branding(company)
    template_name = branding.get("template", "standard.html")

    env = Environment(
        loader=FileSystemLoader(str(company.templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["money"] = _fmt_money
    env.filters["date"] = _fmt_date
    env.filters["qty"] = _fmt_qty

    template = env.get_template(template_name)
    html_text = template.render(inv=inv, items=inv.items, branding=branding)
    return HTML(string=html_text, base_url=str(company.assets_dir)).write_pdf()
