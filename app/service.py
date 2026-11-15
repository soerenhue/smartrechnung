"""Service-Schicht: Rechnungserstellung, immer scoped auf eine aktive Firma.

Hash-Chain laeuft pro Firma. Nummernkreis ebenso. Dateien landen unter
data/companies/<firma>/invoices/<Jahr>/<Nummer>.*
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from .config import Company
from .hashing import chain_hash, content_hash, file_sha256
from .models import AuditLog, Invoice, InvoiceItem
from .pdf import render_invoice_pdf
from .qrcode_gen import generate_payment_qr
from .schemas import InvoiceIn
from .zugferd import build_xml, embed_xml_in_pdf


class InvoiceNumberTakenError(ValueError):
    pass


class SellerNotConfiguredError(RuntimeError):
    pass


def _invoice_payload(inv: Invoice) -> dict:
    return {
        "number": inv.invoice_number,
        "issue_date": inv.issue_date,
        "delivery_date": inv.delivery_date,
        "due_date": inv.due_date,
        "currency": inv.currency,
        "seller": {"name": inv.seller_name, "tax_id": inv.seller_tax_id, "iban": inv.seller_iban},
        "buyer": {
            "name": inv.buyer_name, "street": inv.buyer_street,
            "postcode": inv.buyer_postcode, "city": inv.buyer_city,
            "country": inv.buyer_country, "tax_id": inv.buyer_tax_id,
            "reference": inv.buyer_reference,
        },
        "items": [
            {"pos": it.position, "description": it.description, "quantity": it.quantity,
             "unit": it.unit, "unit_price": it.unit_price, "tax_rate": it.tax_rate}
            for it in inv.items
        ],
        "net_total": inv.net_total, "tax_total": inv.tax_total, "gross_total": inv.gross_total,
    }


def _last_chain_hash_for(db: Session, company: Company) -> str:
    row = (
        db.query(Invoice)
        .filter(Invoice.company_slug == company.slug)
        .order_by(Invoice.id.desc())
        .first()
    )
    return row.chain_hash if row else ""


def _build_invoice_entity(data: InvoiceIn, company: Company) -> Invoice:
    if not company.is_configured():
        raise SellerNotConfiguredError(
            f"Firma '{company.display_name}' ist nicht vollstaendig eingerichtet."
        )

    inv = Invoice(
        company_slug=company.slug,
        invoice_number=data.invoice_number,
        issue_date=data.issue_date,
        delivery_date=data.delivery_date,
        due_date=data.due_date,
        currency=data.currency,
        seller_name=company.seller_name, seller_street=company.seller_street,
        seller_postcode=company.seller_postcode, seller_city=company.seller_city,
        seller_country=company.seller_country, seller_tax_id=company.seller_tax_id,
        seller_tax_number=company.seller_tax_number, seller_email=company.seller_email,
        seller_iban=company.seller_iban, seller_bic=company.seller_bic,
        seller_bank=company.seller_bank,
        buyer_name=data.buyer_name, buyer_street=data.buyer_street,
        buyer_postcode=data.buyer_postcode, buyer_city=data.buyer_city,
        buyer_country=data.buyer_country, buyer_tax_id=data.buyer_tax_id,
        buyer_email=data.buyer_email, buyer_reference=data.buyer_reference,
        notes=data.notes, payment_terms=data.payment_terms,
    )

    net_total = Decimal("0")
    tax_total = Decimal("0")
    for i, it in enumerate(data.items, start=1):
        line = InvoiceItem(
            position=i, description=it.description, quantity=it.quantity,
            unit=it.unit, unit_price=it.unit_price, tax_rate=it.tax_rate,
        )
        inv.items.append(line)
        net_total += line.net_amount
        tax_total += line.tax_amount
    inv.net_total = net_total.quantize(Decimal("0.01"))
    inv.tax_total = tax_total.quantize(Decimal("0.01"))
    inv.gross_total = (net_total + tax_total).quantize(Decimal("0.01"))
    return inv


def _finalize(db: Session, inv: Invoice, company: Company, pdf_bytes: bytes) -> Invoice:
    existing = (
        db.query(Invoice)
        .filter(Invoice.company_slug == company.slug,
                Invoice.invoice_number == inv.invoice_number)
        .first()
    )
    if existing:
        raise InvoiceNumberTakenError(
            f"Rechnungsnummer {inv.invoice_number} bereits vergeben in '{company.display_name}'."
        )

    ch = content_hash(_invoice_payload(inv))
    prev = _last_chain_hash_for(db, company)
    inv.content_hash = ch
    inv.prev_hash = prev
    inv.chain_hash = chain_hash(prev, ch)
    inv.created_at = datetime.utcnow()

    db.add(inv)
    db.flush()

    year_dir = company.dir / "invoices" / str(inv.issue_date.year)
    year_dir.mkdir(parents=True, exist_ok=True)
    safe_number = inv.invoice_number.replace("/", "-").replace(" ", "_").replace("\\", "-")
    pdf_path = year_dir / f"{safe_number}.pdf"
    xml_path = year_dir / f"{safe_number}.xml"

    xml_bytes = build_xml(inv)
    embed_xml_in_pdf(pdf_bytes, xml_bytes, pdf_path)
    xml_path.write_bytes(xml_bytes)

    from .config import DATA_DIR
    inv.pdf_path = str(pdf_path.relative_to(DATA_DIR))
    inv.xml_path = str(xml_path.relative_to(DATA_DIR))

    db.add(AuditLog(
        event="created", invoice_number=inv.invoice_number,
        detail=f"company={company.slug} pdf_sha256={file_sha256(str(pdf_path))} "
               f"xml_sha256={file_sha256(str(xml_path))}",
    ))
    db.commit()
    db.refresh(inv)

    company.bump_number_after(inv.invoice_number)
    return inv


def _maybe_make_qr(inv: Invoice, company: Company) -> None:
    """QR-Code zum Bezahlen erzeugen, wenn Firma das aktiviert hat.
    Wird VOR dem PDF-Render aufgerufen, damit das Template den QR einbetten kann."""
    if not getattr(company, "qr_code_enabled", True):
        return
    filename = generate_payment_qr(inv, company.assets_dir)
    if filename:
        inv.qr_code_filename = filename


def create_invoice(db: Session, data: InvoiceIn, company: Company) -> Invoice:
    inv = _build_invoice_entity(data, company)
    _maybe_make_qr(inv, company)
    pdf_bytes = render_invoice_pdf(inv, company)
    return _finalize(db, inv, company, pdf_bytes)


def create_invoice_from_pdf(db: Session, data: InvoiceIn, company: Company, pdf_bytes: bytes) -> Invoice:
    if not pdf_bytes or pdf_bytes[:4] != b"%PDF":
        raise ValueError("Die hochgeladene Datei ist kein gueltiges PDF.")
    inv = _build_invoice_entity(data, company)
    _maybe_make_qr(inv, company)
    return _finalize(db, inv, company, pdf_bytes)


def verify_chain(db: Session, company: Company) -> list[dict]:
    issues: list[dict] = []
    prev = ""
    rows = (
        db.query(Invoice)
        .filter(Invoice.company_slug == company.slug)
        .order_by(Invoice.id.asc())
        .all()
    )
    for inv in rows:
        ch = content_hash(_invoice_payload(inv))
        expected_chain = chain_hash(prev, ch)
        if inv.content_hash != ch:
            issues.append({"invoice": inv.invoice_number, "problem": "content_hash mismatch"})
        if inv.prev_hash != prev:
            issues.append({"invoice": inv.invoice_number, "problem": "prev_hash mismatch"})
        if inv.chain_hash != expected_chain:
            issues.append({"invoice": inv.invoice_number, "problem": "chain_hash mismatch"})
        prev = inv.chain_hash
    return issues
