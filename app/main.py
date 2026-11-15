"""FastAPI-App - wird vom Desktop-Launcher gestartet.

Oeffnet beim ersten Start einen Einrichtungs-Assistenten; danach stehen zwei
Pfade bereit: Rechnung in der App erzeugen oder bestehendes PDF hochladen und
E-Rechnung daraus machen.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config
from .branding import (
    BRANDING_ASSETS_DIR, TEMPLATES_DIR, ensure_initialized,
    list_templates, load_branding, save_branding,
)
from .config import DATA_DIR, is_configured, load_seller, save_seller
from .db import Base, engine, get_db
from .models import Invoice
from .schemas import InvoiceIn, InvoiceItemIn
from .service import (
    InvoiceNumberTakenError, SellerNotConfiguredError,
    create_invoice, create_invoice_from_pdf, verify_chain,
)


Base.metadata.create_all(bind=engine)
ensure_initialized()

app = FastAPI(title="E-Rechnung", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/branding", StaticFiles(directory=str(BRANDING_ASSETS_DIR)), name="branding")


# --- Freundliche Fehlerseite (statt FastAPI-Standard-JSON) --------------
def _err_page(request: Request, title: str, message: str, status_code: int = 400):
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "title": title, "message": message, "branding": load_branding()},
        status_code=status_code,
    )


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
                     f"Bitte prufe das Feld: {loc}. ({msg})", 400)


@app.exception_handler(Exception)
async def unexpected_err(request: Request, exc: Exception):
    return _err_page(
        request, "Technischer Fehler",
        f"Etwas ist unerwartet schiefgelaufen: {type(exc).__name__}. "
        "Du kannst auf 'Zurueck' klicken und es nochmal versuchen. "
        "Deine bisherigen Rechnungen sind in jedem Fall sicher gespeichert.",
        500,
    )


# --- Einrichtungs-Zwang --------------------------------------------------
@app.middleware("http")
async def force_setup(request: Request, call_next):
    """Vor dem Haupt-UI wird ein Einrichtungs-Assistent erzwungen, solange die
    Firmendaten noch nicht gesetzt sind. Statische Dateien und der Assistent
    selbst duerfen durch."""
    path = request.url.path
    whitelisted = (
        path.startswith("/einrichtung")
        or path.startswith("/branding")
        or path.startswith("/favicon")
    )
    if not is_configured() and not whitelisted:
        return RedirectResponse("/einrichtung", status_code=303)
    return await call_next(request)


# --- Einrichtungs-Assistent ----------------------------------------------
@app.get("/einrichtung", response_class=HTMLResponse)
def setup_form(request: Request):
    return templates.TemplateResponse(
        "setup.html",
        {"request": request, "branding": load_branding(), "seller": load_seller(),
         "done": is_configured()},
    )


@app.post("/einrichtung")
async def setup_save(request: Request):
    form = await request.form()
    save_seller({
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
    })
    return RedirectResponse("/", status_code=303)


# --- Rechnungsliste ------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    invoices = db.query(Invoice).order_by(Invoice.issue_date.desc(), Invoice.id.desc()).all()
    chain_issues = verify_chain(db)
    return templates.TemplateResponse(
        "invoice_list.html",
        {"request": request, "invoices": invoices, "chain_issues": chain_issues,
         "branding": load_branding()},
    )


# --- Rechnung anlegen (zwei Modi) ----------------------------------------
def _suggest_next_number(db: Session) -> str:
    """Schlaegt die naechste Rechnungsnummer vor: 2026-001, 2026-002, ..."""
    year = date.today().year
    prefix = f"{year}-"
    last = (
        db.query(Invoice)
        .filter(Invoice.invoice_number.like(f"{prefix}%"))
        .order_by(Invoice.id.desc())
        .first()
    )
    if not last:
        return f"{prefix}001"
    try:
        n = int(last.invoice_number.split("-", 1)[1]) + 1
    except (ValueError, IndexError):
        n = db.query(Invoice).count() + 1
    return f"{prefix}{n:03d}"


@app.get("/neu", response_class=HTMLResponse)
def new_invoice_form(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "invoice_form.html",
        {"request": request, "branding": load_branding(), "seller": load_seller(),
         "suggested_number": _suggest_next_number(db)},
    )


def _parse_items(form) -> list[InvoiceItemIn]:
    items: list[InvoiceItemIn] = []
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
    )


@app.post("/neu")
async def new_invoice_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    mode = form.get("mode", "generate")

    items = _parse_items(form)
    if not items:
        raise HTTPException(400, "Mindestens eine Rechnungsposition erforderlich.")

    try:
        data = _parse_invoice_in(form)
    except Exception as e:
        raise HTTPException(400, f"Ungueltige Eingabe: {e}")

    try:
        if mode == "upload":
            upload = form.get("pdf_file")
            if not hasattr(upload, "filename") or not upload.filename:
                raise HTTPException(400, "Bitte eine PDF-Datei auswaehlen.")
            pdf_bytes = await upload.read()
            inv = create_invoice_from_pdf(db, data, pdf_bytes)
        else:
            inv = create_invoice(db, data)
    except InvoiceNumberTakenError as e:
        raise HTTPException(409, str(e))
    except SellerNotConfiguredError as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))

    return RedirectResponse(f"/rechnung/{inv.id}", status_code=303)


# --- Details, Downloads --------------------------------------------------
@app.get("/rechnung/{inv_id}", response_class=HTMLResponse)
def invoice_detail(inv_id: int, request: Request, db: Session = Depends(get_db)):
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(404)
    return templates.TemplateResponse(
        "invoice_detail.html",
        {"request": request, "inv": inv, "branding": load_branding()},
    )


@app.get("/rechnung/{inv_id}/pdf")
def invoice_pdf(inv_id: int, db: Session = Depends(get_db)):
    inv = db.get(Invoice, inv_id)
    if not inv or not inv.pdf_path:
        raise HTTPException(404)
    full = Path(DATA_DIR) / inv.pdf_path
    return FileResponse(full, media_type="application/pdf", filename=full.name)


@app.get("/rechnung/{inv_id}/xml")
def invoice_xml(inv_id: int, db: Session = Depends(get_db)):
    inv = db.get(Invoice, inv_id)
    if not inv or not inv.xml_path:
        raise HTTPException(404)
    full = Path(DATA_DIR) / inv.xml_path
    return FileResponse(full, media_type="application/xml", filename=full.name)


# --- Einstellungen (Branding + Firmendaten) ------------------------------
@app.get("/einstellungen", response_class=HTMLResponse)
def settings_page(request: Request):
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "branding": load_branding(),
         "templates": list_templates(), "seller": load_seller(),
         "data_dir": str(DATA_DIR)},
    )


@app.post("/einstellungen")
async def settings_save(request: Request):
    form = await request.form()

    # Branding
    save_branding({
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

    # Firmendaten
    save_seller({
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
    })

    # Logo-Upload
    logo = form.get("logo")
    if hasattr(logo, "filename") and logo.filename:
        safe = Path(logo.filename).name
        target = BRANDING_ASSETS_DIR / safe
        target.write_bytes(await logo.read())
        current = load_branding()
        current["logo_filename"] = safe
        save_branding(current)

    return RedirectResponse("/einstellungen", status_code=303)


# --- GoBD-Export ---------------------------------------------------------
@app.get("/export/gobd")
def gobd_export(db: Session = Depends(get_db)):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for inv in db.query(Invoice).order_by(Invoice.id.asc()).all():
            for rel in (inv.pdf_path, inv.xml_path):
                if rel:
                    full = Path(DATA_DIR) / rel
                    if full.exists():
                        zf.write(full, arcname=rel)
        lines = ["invoice_number;issue_date;content_hash;prev_hash;chain_hash;pdf_path;xml_path"]
        for inv in db.query(Invoice).order_by(Invoice.id.asc()).all():
            lines.append(";".join([
                inv.invoice_number, inv.issue_date.isoformat(),
                inv.content_hash, inv.prev_hash, inv.chain_hash,
                inv.pdf_path, inv.xml_path,
            ]))
        zf.writestr("hash-protokoll.csv", "\n".join(lines))
        zf.writestr("verfahrensdokumentation.md", _verfahrensdoku())
        issues = verify_chain(db)
        zf.writestr(
            "chain-verification.txt",
            "OK - alle Hashes konsistent\n" if not issues else "\n".join(str(i) for i in issues),
        )
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="gobd-export.zip"'},
    )


def _verfahrensdoku() -> str:
    s = load_seller()
    return f"""# Verfahrensdokumentation (GoBD-Kurzform)

## System
Selbst betriebene Desktop-Anwendung "E-Rechnung". Rechnungen werden als
Hybrid-PDF (PDF/A-3 mit eingebettetem factur-x.xml) nach ZUGFeRD 2.x /
EN 16931 erzeugt.

## Erfassung
Erfassung ueber Web-Formular (im lokalen Browserfenster der Desktop-App).
Pflichtangaben nach UStG § 14 werden technisch erzwungen; Rechnungsnummern
sind eindeutig.

## Unveraenderbarkeit
Jede Rechnung erhaelt einen SHA-256-Hash ueber ihren normalisierten Inhalt.
Dieser Hash wird mit dem Hash der vorherigen Rechnung verkettet (chain_hash).
Jede nachtraegliche Aenderung bricht die Kette und wird in der Oberflaeche
rot hervorgehoben.

## Aufbewahrung
PDF und XML pro Rechnung: data/invoices/<Jahr>/<Nummer>.*. Frist: 10 Jahre.

## Pruefbarkeit
Das ZIP-Archiv "gobd-export.zip" enthaelt alle Belege, das Hash-Protokoll und
diese Verfahrensdokumentation.

## Betreiber
Seller: {s['seller_name']}
Anschrift: {s['seller_street']}, {s['seller_postcode']} {s['seller_city']}
USt-IdNr: {s['seller_tax_id']}
"""
