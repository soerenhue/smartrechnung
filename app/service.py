"""Service-Schicht: Orchestriert die Erstellung einer Rechnung.

Zwei Wege:
  * create_invoice(db, data)                 -> PDF selbst rendern + XML einbetten
  * create_invoice_from_pdf(db, data, pdf)   -> PDF uebernehmen + XML einbetten

Beide schreiben die Rechnung mit SHA-256-Hash-Chain in die DB und legen die
Dateien unter data/invoices/<Jahr>/<Nummer>.* ab (GoBD).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from .config import DATA_DIR, load_seller
from .hashing import chain_hash, content_hash, file_sha256
from .models import AuditLog, Invoice, InvoiceItem
from .pdf import render_invoice_pdf
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


def _last_chain_hash(db: Session) -> str:
    row = db.query(Invoice).order_by(Invoice.id.desc()).first()
    return row.chain_hash if row else ""


def _build_invoice_entity(data: InvoiceIn) -> Invoice:
    s = load_seller()
    if not s["seller_name"]:
        raise SellerNotConfiguredError("Firmendaten fehlen - bitte zuerst Einrichtung abschliessen.")

    inv = Invoice(
        invoice_number=data.invoice_number,
        issue_date=data.issue_date,
        delivery_date=data.delivery_date,
        due_date=data.due_date,
        currency=data.currency,
        seller_name=s["seller_name"], seller_street=s["seller_street"],
        seller_postcode=s["seller_postcode"], seller_city=s["seller_city"],
        seller_country=s["seller_country"], seller_tax_id=s["seller_tax_id"],
        seller_tax_number=s["seller_tax_number"], seller_email=s["seller_email"],
        seller_iban=s["seller_iban"], seller_bic=s["seller_bic"], seller_bank=s["seller_bank"],
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


def _finalize(db: Session, inv: Invoice, pdf_bytes: bytes) -> Invoice:
    existing = db.query(Invoice).filter_by(invoice_number=inv.invoice_number).first()
    if existing:
        raise InvoiceNumberTakenError(f"Rechnungsnummer {inv.invoice_number} bereits vergeben.")

    ch = content_hash(_invoice_payload(inv))
    prev = _last_chain_hash(db)
    inv.content_hash = ch
    inv.prev_hash = prev
    inv.chain_hash = chain_hash(prev, ch)
    inv.created_at = datetime.utcnow()

    db.add(inv)
    db.flush()

    year_dir = Path(DATA_DIR) / "invoices" / str(inv.issue_date.year)
    year_dir.mkdir(parents=True, exist_ok=True)
    safe_number = inv.invoice_number.replace("/", "-").replace(" ", "_")
    pdf_path = year_dir / f"{safe_number}.pdf"
    xml_path = year_dir / f"{safe_number}.xml"

    xml_bytes = build_xml(inv)
    embed_xml_in_pdf(pdf_bytes, xml_bytes, pdf_path)
    xml_path.write_bytes(xml_bytes)

    inv.pdf_path = str(pdf_path.relative_to(DATA_DIR))
    inv.xml_path = str(xml_path.relative_to(DATA_DIR))

    db.add(AuditLog(
        event="created", invoice_number=inv.invoice_number,
        detail=f"pdf_sha256={file_sha256(str(pdf_path))} xml_sha256={file_sha256(str(xml_path))}",
    ))
    db.commit()
    db.refresh(inv)
    return inv


def create_invoice(db: Session, data: InvoiceIn) -> Invoice:
    """Variante A: PDF komplett in der App erzeugen."""
    inv = _build_invoice_entity(data)
    pdf_bytes = render_invoice_pdf(inv)
    return _finalize(db, inv, pdf_bytes)


def create_invoice_from_pdf(db: Session, data: InvoiceIn, pdf_bytes: bytes) -> Invoice:
    """Variante B: Bestehendes PDF uebernehmen und XML einbetten.

    Wichtig: der Nutzer traegt die Rechnungsdaten trotzdem im Formular ein -
    diese Daten landen im XML. Der Nutzer muss sicherstellen, dass das sichtbare
    PDF dem XML entspricht (sonst ist die E-Rechnung ungueltig)."""
    if not pdf_bytes or pdf_bytes[:4] != b"%PDF":
        raise ValueError("Die hochgeladene Datei ist kein gueltiges PDF.")
    inv = _build_invoice_entity(data)
    return _finalize(db, inv, pdf_bytes)


def verify_chain(db: Session) -> list[dict]:
    issues = []
    prev = ""
    for inv in db.query(Invoice).order_by(Invoice.id.asc()).all():
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
