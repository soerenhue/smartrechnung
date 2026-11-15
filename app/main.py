"""FastAPI-App: Web-UI + Download-Endpoints.

Jede Installation ist eine isolierte Einzelplatz-App. Es gibt keinen geteilten
Server zwischen Freunden - jeder betreibt seinen eigenen Container mit
eigenem data-Volume."""

from __future__ import annotations

import io
import secrets
import zipfile
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .branding import (
    BRANDING_ASSETS_DIR,
    TEMPLATES_DIR,
    ensure_initialized,
    list_templates,
    load_branding,
    save_branding,
)
from .config import settings
from .db import Base, engine, get_db
from .models import Invoice
from .schemas import InvoiceIn, InvoiceItemIn
from .service import InvoiceNumberTakenError, create_invoice, verify_chain


Base.metadata.create_all(bind=engine)
ensure_initialized()

app = FastAPI(title="E-Rechnung", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app.mount("/branding", StaticFiles(directory=str(BRANDING_ASSETS_DIR)), name="branding")


# --- Optionaler Basic-Auth (fuer den Fall, dass die App exponiert wird) -------
_security = HTTPBasic()


def auth(credentials: HTTPBasicCredentials | None = Depends(_security) if False else None):
    """Deaktiviert, wenn REQUIRE_AUTH=false. Siehe require_auth-Dependency unten."""
    return None


def require_auth(request: Request):
    if not settings.require_auth:
        return
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    import base64
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
        user, _, pw = decoded.partition(":")
    except Exception:
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    if not (secrets.compare_digest(user, settings.basic_auth_user)
            and secrets.compare_digest(pw, settings.basic_auth_pass)):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


# --- Routen -----------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db), _=Depends(require_auth)):
    invoices = db.query(Invoice).order_by(Invoice.issue_date.desc(), Invoice.id.desc()).all()
    chain_issues = verify_chain(db)
    return templates.TemplateResponse(
        "invoice_list.html",
        {
            "request": request,
            "invoices": invoices,
            "chain_issues": chain_issues,
            "branding": load_branding(),
        },
    )


@app.get("/neu", response_class=HTMLResponse)
def new_invoice_form(request: Request, _=Depends(require_auth)):
    return templates.TemplateResponse(
        "invoice_form.html",
        {"request": request, "branding": load_branding(), "seller": settings.model_dump()},
    )


@app.post("/neu")
async def new_invoice_submit(request: Request, db: Session = Depends(get_db), _=Depends(require_auth)):
    form = await request.form()

    items: list[InvoiceItemIn] = []
    # Positionen werden als desc_1, qty_1, price_1, tax_1, desc_2, ... geliefert.
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

    if not items:
        raise HTTPException(400, "Mindestens eine Rechnungsposition erforderlich.")

    try:
        data = InvoiceIn(
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
            items=items,
            notes=form.get("notes") or "",
            payment_terms=form.get("payment_terms") or "Zahlbar innerhalb von 14 Tagen ohne Abzug.",
        )
    except Exception as e:
        raise HTTPException(400, f"Ungueltige Eingabe: {e}")

    try:
        inv = create_invoice(db, data)
    except InvoiceNumberTakenError as e:
        raise HTTPException(409, str(e))

    return RedirectResponse(f"/rechnung/{inv.id}", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/rechnung/{inv_id}", response_class=HTMLResponse)
def invoice_detail(inv_id: int, request: Request, db: Session = Depends(get_db), _=Depends(require_auth)):
    inv = db.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(404)
    return templates.TemplateResponse(
        "invoice_detail.html",
        {"request": request, "inv": inv, "branding": load_branding()},
    )


@app.get("/rechnung/{inv_id}/pdf")
def invoice_pdf(inv_id: int, db: Session = Depends(get_db), _=Depends(require_auth)):
    inv = db.get(Invoice, inv_id)
    if not inv or not inv.pdf_path:
        raise HTTPException(404)
    full = Path(settings.data_dir) / inv.pdf_path
    return FileResponse(full, media_type="application/pdf", filename=full.name)


@app.get("/rechnung/{inv_id}/xml")
def invoice_xml(inv_id: int, db: Session = Depends(get_db), _=Depends(require_auth)):
    inv = db.get(Invoice, inv_id)
    if not inv or not inv.xml_path:
        raise HTTPException(404)
    full = Path(settings.data_dir) / inv.xml_path
    return FileResponse(full, media_type="application/xml", filename=full.name)


@app.get("/einstellungen", response_class=HTMLResponse)
def settings_page(request: Request, _=Depends(require_auth)):
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "branding": load_branding(),
            "templates": list_templates(),
            "seller": settings.model_dump(),
        },
    )


@app.post("/einstellungen")
async def settings_save(request: Request, _=Depends(require_auth)):
    form = await request.form()
    save_branding({
        "template": form.get("template") or "standard.html",
        "primary_color": form.get("primary_color") or "#1e3a8a",
        "accent_color": form.get("accent_color") or "#3b82f6",
        "text_color": form.get("text_color") or "#111827",
        "muted_color": form.get("muted_color") or "#6b7280",
        "font_family": form.get("font_family") or "DejaVu Sans, sans-serif",
        "logo_filename": form.get("logo_filename") or "",
        "footer_line_1": form.get("footer_line_1") or "",
        "footer_line_2": form.get("footer_line_2") or "",
        "footer_line_3": form.get("footer_line_3") or "",
    })

    # Optional: Logo-Upload
    logo = form.get("logo")
    if hasattr(logo, "filename") and logo.filename:
        safe = Path(logo.filename).name
        target = BRANDING_ASSETS_DIR / safe
        target.write_bytes(await logo.read())
        current = load_branding()
        current["logo_filename"] = safe
        save_branding(current)

    return RedirectResponse("/einstellungen", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/export/gobd")
def gobd_export(db: Session = Depends(get_db), _=Depends(require_auth)):
    """Exportiert alle Rechnungen + XMLs + Hash-Protokoll + Verfahrensdokumentation
    als ZIP-Archiv - die typische Form fuer eine Betriebspruefung."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Rechnungsdateien
        for inv in db.query(Invoice).order_by(Invoice.id.asc()).all():
            for rel in (inv.pdf_path, inv.xml_path):
                if rel:
                    full = Path(settings.data_dir) / rel
                    if full.exists():
                        zf.write(full, arcname=rel)

        # Hash-Protokoll
        lines = ["invoice_number;issue_date;content_hash;prev_hash;chain_hash;pdf_path;xml_path"]
        for inv in db.query(Invoice).order_by(Invoice.id.asc()).all():
            lines.append(";".join([
                inv.invoice_number, inv.issue_date.isoformat(),
                inv.content_hash, inv.prev_hash, inv.chain_hash,
                inv.pdf_path, inv.xml_path,
            ]))
        zf.writestr("hash-protokoll.csv", "\n".join(lines))

        # Verfahrensdokumentation (statische Erklaerung des Systems)
        zf.writestr("verfahrensdokumentation.md", _verfahrensdoku())

        # Kettenpruefung
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
    return f"""# Verfahrensdokumentation (GoBD-Kurzform)

Dieses Dokument beschreibt das verwendete E-Rechnungssystem.

## 1. System
Selbst betriebene Open-Source-Anwendung "E-Rechnung". Jede Rechnung wird in
einem Hybrid-PDF nach ZUGFeRD 2.x / EN 16931 erzeugt (PDF/A-3 mit eingebettetem
factur-x.xml).

## 2. Erfassung
Rechnungsdaten werden ueber ein Web-Formular erfasst. Pflichtangaben nach
UStG § 14 Abs. 4 werden technisch erzwungen. Die Rechnungsnummer ist eindeutig.

## 3. Unveraenderlichkeit (GoBD Rz. 110 ff.)
Jede festgeschriebene Rechnung wird nicht mehr veraendert. Jede Rechnung erhaelt
einen SHA-256-Hash ueber ihren normalisierten Inhalt. Dieser Hash wird mit dem
Hash der vorherigen Rechnung verkettet (chain_hash). Jede nachtraegliche
Aenderung wird dadurch erkannt.

## 4. Aufbewahrung
PDF und XML pro Rechnung liegen unter data/invoices/<Jahr>/<Nummer>.*. Die
Aufbewahrungsfrist betraegt 10 Jahre. Der Betreiber ist verpflichtet, das
data-Verzeichnis entsprechend zu sichern (Backup, WORM oder revisionssichere
Ablage empfohlen).

## 5. Pruefbarkeit
Das ZIP-Archiv "gobd-export.zip" enthaelt alle Belege, das Hash-Protokoll
sowie das Ergebnis einer Ketten-Pruefung. Diese Datei ist einem Pruefer zu
uebergeben.

## 6. Betreiber
Seller: {settings.seller_name}
Anschrift: {settings.seller_street}, {settings.seller_postcode} {settings.seller_city}
USt-IdNr: {settings.seller_tax_id}
Stand: automatisch erzeugt beim Export
"""
