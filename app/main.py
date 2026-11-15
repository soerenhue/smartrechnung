"""FastAPI-App - Multi-Firma-Desktop-Variante."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import validator as preflight
from .branding import load_branding, save_branding, list_templates
from .config import (
    COMPANIES_DIR, Company, create_company, delete_company,
    get_company, list_companies, settings as _cfg,
)
from .pdf_analyzer import analyze_pdf, save_logo
from .template_generator import generate_template
from .db import Base, engine, get_db
from .models import Customer, Invoice
from .schemas import InvoiceIn, InvoiceItemIn
from .service import (
    InvoiceNumberTakenError, SellerNotConfiguredError,
    create_invoice, create_invoice_from_pdf, verify_chain,
)


Base.metadata.create_all(bind=engine)

# Legacy-Schema-Migration: fehlende Spalten nachtragen (einmaliger Upgrade-Pfad)
with engine.begin() as conn:
    cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(invoices)").fetchall()}
    if "company_slug" not in cols:
        conn.exec_driver_sql("ALTER TABLE invoices ADD COLUMN company_slug VARCHAR(128) DEFAULT ''")
    if "tax_regime" not in cols:
        conn.exec_driver_sql("ALTER TABLE invoices ADD COLUMN tax_regime VARCHAR(32) DEFAULT 'regular'")
    if "reverse_charge" not in cols:
        conn.exec_driver_sql("ALTER TABLE invoices ADD COLUMN reverse_charge BOOLEAN DEFAULT 0")
    if "qr_code_filename" not in cols:
        conn.exec_driver_sql("ALTER TABLE invoices ADD COLUMN qr_code_filename VARCHAR(256) DEFAULT ''")

    # Kunden-Tabelle wird durch Base.metadata.create_all automatisch angelegt,
    # falls sie noch nicht existiert (Upgrade-Pfad fuer bestehende DBs).


app = FastAPI(title="E-Rechnung", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Logos & Assets aller Firmen bereitstellen
app.mount("/data", StaticFiles(directory=str(COMPANIES_DIR)), name="data")


# --- Aktive Firma (Cookie) ----------------------------------------------
ACTIVE_COOKIE = "active_company"


def active_company(request: Request) -> Optional[Company]:
    slug = request.cookies.get(ACTIVE_COOKIE)
    if slug:
        c = get_company(slug)
        if c:
            return c
    cs = list_companies()
    return cs[0] if cs else None


def _render(request: Request, template: str, ctx: dict, status_code: int = 200):
    ctx.setdefault("request", request)
    ctx.setdefault("active", active_company(request))
    ctx.setdefault("companies", list_companies())
    ctx.setdefault("branding", load_branding(ctx["active"]) if ctx["active"] else {})
    return templates.TemplateResponse(template, ctx, status_code=status_code)


# --- Fehlerseiten -------------------------------------------------------
def _err_page(request: Request, title: str, message: str, code: int = 400):
    return _render(request, "error.html",
                   {"title": title, "message": message}, status_code=code)


@app.exception_handler(StarletteHTTPException)
async def http_err(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _err_page(request, "Seite nicht gefunden",
                         "Die aufgerufene Rechnung oder Seite existiert nicht.", 404)
    return _err_page(request, "Ups, da ging etwas schief",
                     str(exc.detail or "Unbekannter Fehler."), exc.status_code or 400)


@app.exception_handler(RequestValidationError)
async def validation_err(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    loc = " - ".join(str(x) for x in first.get("loc", []))
    msg = first.get("msg", "Eingabe ungueltig.")
    return _err_page(request, "Eingabe nicht vollstaendig",
                     f"Bitte pruefe das Feld: {loc}. ({msg})", 400)


@app.exception_handler(Exception)
async def unexpected_err(request: Request, exc: Exception):
    return _err_page(
        request, "Technischer Fehler",
        f"Etwas ist unerwartet schiefgelaufen: {type(exc).__name__}. "
        "Deine bisherigen Rechnungen sind sicher gespeichert.",
        500,
    )


# --- Startseite / Weiche --------------------------------------------------
@app.get("/")
def index(request: Request, db: Session = Depends(get_db)):
    companies = list_companies()
    if not companies:
        return RedirectResponse("/firmen/neu?first=1", status_code=303)
    c = active_company(request) or companies[0]
    if not c.is_configured():
        return RedirectResponse(f"/firmen/{c.slug}", status_code=303)

    invoices = (
        db.query(Invoice)
        .filter(Invoice.company_slug == c.slug)
        .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
        .all()
    )
    chain_issues = verify_chain(db, c)
    return _render(request, "invoice_list.html",
                   {"invoices": invoices, "chain_issues": chain_issues})


# --- Firmen-Umschalter ---------------------------------------------------
@app.post("/firma/wechseln")
async def switch_company(request: Request):
    form = await request.form()
    slug = form.get("slug", "")
    resp = RedirectResponse("/", status_code=303)
    if get_company(slug):
        resp.set_cookie(ACTIVE_COOKIE, slug, max_age=60 * 60 * 24 * 365, httponly=True)
    return resp


# --- Firmen-Verwaltung ---------------------------------------------------
@app.get("/firmen")
def companies_page(request: Request):
    return _render(request, "companies.html", {})


@app.get("/firmen/neu")
def company_new_form(request: Request):
    return _render(
        request, "company_form.html",
        {"form_action": "/firmen/neu", "company": None,
         "is_first": request.query_params.get("first") == "1"},
    )


@app.post("/firmen/neu")
async def company_new_save(request: Request):
    form = await request.form()
    data = {k: form.get(k) for k in form}
    data["next_number"] = form.get("next_number") or 1
    try:
        c = create_company(data)
    except ValueError as e:
        return _err_page(request, "Firma konnte nicht angelegt werden", str(e), 400)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(ACTIVE_COOKIE, c.slug, max_age=60 * 60 * 24 * 365, httponly=True)
    return resp


@app.get("/firmen/{slug}")
def company_edit_form(slug: str, request: Request):
    c = get_company(slug)
    if not c:
        raise HTTPException(404)
    return _render(
        request, "company_form.html",
        {"form_action": f"/firmen/{slug}", "company": c, "is_first": False,
         "template_list": list_templates(c),
         "branding_for_form": load_branding(c)},
    )


@app.post("/firmen/{slug}")
async def company_edit_save(slug: str, request: Request):
    c = get_company(slug)
    if not c:
        raise HTTPException(404)
    form = await request.form()
    c.update({
        "display_name": form.get("display_name"),
        "seller_name": form.get("seller_name"),
        "seller_street": form.get("seller_street"),
        "seller_postcode": form.get("seller_postcode"),
        "seller_city": form.get("seller_city"),
        "seller_country": form.get("seller_country") or "DE",
        "seller_tax_id": form.get("seller_tax_id"),
        "seller_tax_number": form.get("seller_tax_number"),
        "seller_email": form.get("seller_email"),
        "seller_iban": form.get("seller_iban"),
        "seller_bic": form.get("seller_bic"),
        "seller_bank": form.get("seller_bank"),
        "number_format": form.get("number_format") or "{year}-{nn:03d}",
        "next_number": form.get("next_number") or 1,
        "tax_regime": form.get("tax_regime") or "regular",
        "reverse_charge_default": bool(form.get("reverse_charge_default")),
        "qr_code_enabled": bool(form.get("qr_code_enabled")),
    })

    # Branding speichern
    save_branding(c, {
        "template": form.get("template") or "standard.html",
        "primary_color": form.get("primary_color") or "#1e3a8a",
        "accent_color": form.get("accent_color") or "#3b82f6",
        "text_color": form.get("text_color") or "#111827",
        "muted_color": form.get("muted_color") or "#6b7280",
        "font_family": form.get("font_family") or "DejaVu Sans, sans-serif",
        "footer_line_1": form.get("footer_line_1") or "",
        "footer_line_2": form.get("footer_line_2") or "",
        "footer_line_3": form.get("footer_line_3") or "",
    })

    logo = form.get("logo")
    if hasattr(logo, "filename") and logo.filename:
        safe = Path(logo.filename).name
        (c.assets_dir / safe).write_bytes(await logo.read())
        current = load_branding(c)
        current["logo_filename"] = safe
        save_branding(c, current)

    return RedirectResponse(f"/firmen/{slug}", status_code=303)


@app.get("/firmen/{slug}/vorlage-aus-pdf")
def template_wizard(slug: str, request: Request):
    c = get_company(slug)
    if not c:
        raise HTTPException(404)
    return _render(request, "template_wizard.html",
                   {"company": c, "layout": None, "saved": False})


@app.post("/firmen/{slug}/vorlage-aus-pdf")
async def template_wizard_save(slug: str, request: Request):
    c = get_company(slug)
    if not c:
        raise HTTPException(404)
    form = await request.form()
    upload = form.get("pdf_file")
    if not hasattr(upload, "filename") or not upload.filename:
        return _err_page(request, "Keine PDF-Datei gewaehlt",
                         "Bitte lade eine PDF-Rechnung hoch, aus der die Vorlage gebaut werden soll.", 400)
    pdf_bytes = await upload.read()
    if not pdf_bytes.startswith(b"%PDF"):
        return _err_page(request, "Keine gueltige PDF",
                         "Die Datei konnte nicht als PDF gelesen werden.", 400)

    try:
        layout = analyze_pdf(pdf_bytes)
    except Exception as e:
        return _err_page(request, "PDF-Analyse fehlgeschlagen",
                         f"Konnte das Layout nicht ermitteln: {e}", 400)

    # Logo speichern (falls erkannt)
    logo_filename = save_logo(layout, c.assets_dir, name_stem="logo-imported")

    # HTML-Vorlage erzeugen
    html = generate_template(layout)
    template_name = form.get("template_name") or "importiert.html"
    if not template_name.endswith(".html"):
        template_name += ".html"
    # Sicherheit: nur Dateinamen, keine Pfade
    template_name = Path(template_name).name
    (c.templates_dir / template_name).write_text(html, encoding="utf-8")

    # Branding aktualisieren: neue Vorlage aktiv + Farben uebernehmen
    br = load_branding(c)
    br["template"] = template_name
    br["primary_color"] = layout.primary_color
    br["accent_color"] = layout.accent_color
    br["text_color"] = layout.text_color
    br["font_family"] = f'"{layout.font_family}", DejaVu Sans, sans-serif'
    if logo_filename:
        br["logo_filename"] = logo_filename
    save_branding(c, br)

    return _render(request, "template_wizard.html",
                   {"company": c, "layout": layout, "saved": True,
                    "template_name": template_name})


@app.post("/firmen/{slug}/loeschen")
def company_delete(slug: str, request: Request):
    delete_company(slug)
    resp = RedirectResponse("/firmen", status_code=303)
    if request.cookies.get(ACTIVE_COOKIE) == slug:
        resp.delete_cookie(ACTIVE_COOKIE)
    return resp


# --- Rechnung anlegen ----------------------------------------------------
def _suggest_next_number(db: Session, c: Company) -> str:
    return c.format_next_number()


@app.get("/neu")
def new_invoice_form(request: Request, db: Session = Depends(get_db)):
    c = active_company(request)
    if not c:
        return RedirectResponse("/firmen/neu?first=1", status_code=303)
    if not c.is_configured():
        return RedirectResponse(f"/firmen/{c.slug}", status_code=303)
    # Gespeicherte Kunden fuer das Dropdown, sortiert nach letzter Nutzung
    customers = (
        db.query(Customer)
        .filter(Customer.company_slug == c.slug)
        .order_by(Customer.use_count.desc(), Customer.last_used_at.desc().nullslast())
        .all()
    )
    return _render(
        request, "invoice_form.html",
        {"suggested_number": _suggest_next_number(db, c),
         "customers": customers},
    )


def _parse_items(form) -> list[InvoiceItemIn]:
    items = []
    i = 1
    while f"desc_{i}" in form:
        desc = (form.get(f"desc_{i}") or "").strip()
        if desc:
            items.append(InvoiceItemIn(
                description=desc,
                quantity=form.get(f"qty_{i}") or "0",
                unit=form.get(f"unit_{i}") or "C62",
                unit_price=form.get(f"price_{i}") or "0",
                tax_rate=form.get(f"tax_{i}") or "19",
            ))
        i += 1
    return items


def _parse_invoice_in(form) -> InvoiceIn:
    return InvoiceIn(
        invoice_number=form["invoice_number"],
        issue_date=form["issue_date"],
        delivery_date=form.get("delivery_date") or form["issue_date"],
        due_date=form["due_date"],
        buyer_name=form["buyer_name"],
        buyer_street=form["buyer_street"],
        buyer_postcode=form["buyer_postcode"],
        buyer_city=form["buyer_city"],
        buyer_country=form.get("buyer_country") or "DE",
        buyer_tax_id=form.get("buyer_tax_id") or "",
        buyer_email=form.get("buyer_email") or "",
        buyer_reference=form.get("buyer_reference") or "",
        items=_parse_items(form),
        notes=form.get("notes") or "",
        payment_terms=form.get("payment_terms") or "Zahlbar innerhalb von 14 Tagen ohne Abzug.",
        reverse_charge=bool(form.get("reverse_charge")),
    )


@app.post("/neu")
async def new_invoice_submit(request: Request, db: Session = Depends(get_db)):
    c = active_company(request)
    if not c:
        raise HTTPException(400, "Keine Firma ausgewaehlt.")

    form = await request.form()
    mode = form.get("mode", "generate")

    items = _parse_items(form)
    if not items:
        return _err_page(request, "Rechnung unvollstaendig",
                         "Bitte mindestens eine Rechnungsposition eintragen.", 400)

    try:
        data = _parse_invoice_in(form)
    except Exception as e:
        return _err_page(request, "Eingabe ungueltig", str(e), 400)

    # Pflicht-Check VOR dem Erzeugen
    issues = preflight.check_company(c) + preflight.check_invoice(data, c)
    if issues:
        return _render(request, "error.html", {
            "title": "Diese Rechnung waere nicht rechtskonform",
            "message": "Bitte korrigiere die folgenden Punkte:",
            "issues": issues,
        }, status_code=400)

    # Pflichthinweis automatisch in notes ergaenzen (falls nicht schon enthalten)
    notice = preflight.mandatory_notice(c, data.reverse_charge)
    if notice and notice not in data.notes:
        data.notes = (data.notes + ("\n\n" if data.notes else "") + notice).strip()

    try:
        if mode == "upload":
            upload = form.get("pdf_file")
            if not hasattr(upload, "filename") or not upload.filename:
                return _err_page(request, "PDF fehlt", "Bitte eine PDF-Datei auswaehlen.", 400)
            pdf_bytes = await upload.read()
            inv = create_invoice_from_pdf(db, data, c, pdf_bytes)
        else:
            inv = create_invoice(db, data, c)

        # Steuerregime + Reverse-Charge auf der Rechnung mitspeichern
        inv.tax_regime = c.tax_regime
        inv.reverse_charge = data.reverse_charge
        db.commit()

    except InvoiceNumberTakenError as e:
        return _err_page(request, "Rechnungsnummer bereits vergeben", str(e), 409)
    except SellerNotConfiguredError as e:
        return _err_page(request, "Firma nicht vollstaendig eingerichtet", str(e), 400)
    except ValueError as e:
        return _err_page(request, "Ungueltige Eingabe", str(e), 400)

    return RedirectResponse(f"/rechnung/{inv.id}", status_code=303)


# --- Details, Downloads --------------------------------------------------
@app.get("/kunden")
def customers_list(request: Request, db: Session = Depends(get_db)):
    c = active_company(request)
    if not c:
        return RedirectResponse("/", status_code=303)
    rows = (
        db.query(Customer)
        .filter(Customer.company_slug == c.slug)
        .order_by(Customer.display_name.asc())
        .all()
    )
    return _render(request, "customers.html", {"customers_list": rows})


@app.post("/kunden/{cust_id}/loeschen")
def customer_delete(cust_id: int, request: Request, db: Session = Depends(get_db)):
    c = active_company(request)
    cust = db.get(Customer, cust_id)
    if cust and c and cust.company_slug == c.slug:
        db.delete(cust)
        db.commit()
    return RedirectResponse("/kunden", status_code=303)


@app.get("/kunden/{cust_id}.json")
def customer_json(cust_id: int, request: Request, db: Session = Depends(get_db)):
    """Wird vom Rechnungsformular aufgerufen, wenn ein Kunde im Dropdown
    ausgewaehlt wird - fuellt die Felder per JavaScript."""
    c = active_company(request)
    cust = db.get(Customer, cust_id)
    if not cust or not c or cust.company_slug != c.slug:
        raise HTTPException(404)
    return {
        "name": cust.name, "street": cust.street,
        "postcode": cust.postcode, "city": cust.city,
        "country": cust.country, "tax_id": cust.tax_id,
        "email": cust.email, "reference": cust.reference,
    }


@app.get("/rechnung/{inv_id}")
def invoice_detail(inv_id: int, request: Request, db: Session = Depends(get_db)):
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(404)
    return _render(request, "invoice_detail.html", {"inv": inv})


@app.get("/rechnung/{inv_id}/pdf")
def invoice_pdf(inv_id: int, db: Session = Depends(get_db)):
    inv = db.get(Invoice, inv_id)
    if not inv or not inv.pdf_path:
        raise HTTPException(404)
    full = Path(_cfg.data_dir) / inv.pdf_path
    return FileResponse(full, media_type="application/pdf", filename=full.name)


@app.get("/rechnung/{inv_id}/xml")
def invoice_xml(inv_id: int, db: Session = Depends(get_db)):
    inv = db.get(Invoice, inv_id)
    if not inv or not inv.xml_path:
        raise HTTPException(404)
    full = Path(_cfg.data_dir) / inv.xml_path
    return FileResponse(full, media_type="application/xml", filename=full.name)


# --- GoBD-Export (pro Firma) ---------------------------------------------
@app.get("/export/gobd")
def gobd_export(request: Request, db: Session = Depends(get_db)):
    c = active_company(request)
    if not c:
        raise HTTPException(400, "Keine Firma ausgewaehlt.")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        rows = db.query(Invoice).filter(Invoice.company_slug == c.slug).order_by(Invoice.id.asc()).all()
        for inv in rows:
            for rel in (inv.pdf_path, inv.xml_path):
                if rel:
                    full = Path(_cfg.data_dir) / rel
                    if full.exists():
                        zf.write(full, arcname=rel)
        lines = ["invoice_number;issue_date;content_hash;prev_hash;chain_hash;pdf_path;xml_path"]
        for inv in rows:
            lines.append(";".join([
                inv.invoice_number, inv.issue_date.isoformat(),
                inv.content_hash, inv.prev_hash, inv.chain_hash,
                inv.pdf_path, inv.xml_path,
            ]))
        zf.writestr("hash-protokoll.csv", "\n".join(lines))
        zf.writestr("verfahrensdokumentation.md", _verfahrensdoku(c))
        issues = verify_chain(db, c)
        zf.writestr("chain-verification.txt",
                    "OK - alle Hashes konsistent\n" if not issues else "\n".join(str(i) for i in issues))
    return Response(
        content=buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="gobd-export-{c.slug}.zip"'},
    )


def _verfahrensdoku(c: Company) -> str:
    return f"""# Verfahrensdokumentation (GoBD-Kurzform)

## System
Selbst betriebene Desktop-Anwendung "E-Rechnung". Rechnungen werden als
Hybrid-PDF (PDF/A-3 mit eingebettetem factur-x.xml) nach ZUGFeRD 2.x /
EN 16931 erzeugt.

## Erfassung
Erfassung ueber Web-Formular im lokalen Browserfenster. Pflichtangaben nach
UStG § 14 werden technisch vor dem Erzeugen geprueft; Rechnungsnummern sind
pro Firma eindeutig.

## Unveraenderbarkeit
Jede Rechnung erhaelt einen SHA-256-Hash ueber ihren normalisierten Inhalt.
Dieser Hash wird mit dem Hash der vorherigen Rechnung derselben Firma
verkettet. Jede nachtraegliche Aenderung bricht die Kette und wird in der
Oberflaeche rot hervorgehoben.

## Aufbewahrung
PDF und XML pro Rechnung: data/companies/{c.slug}/invoices/<Jahr>/<Nummer>.*.
Frist: 10 Jahre.

## Betreiber dieser Firma
Firma: {c.seller_name}
Anschrift: {c.seller_street}, {c.seller_postcode} {c.seller_city}
USt-IdNr: {c.seller_tax_id}
Steuernummer: {c.seller_tax_number}
Steuerregime: {c.tax_regime}
"""
