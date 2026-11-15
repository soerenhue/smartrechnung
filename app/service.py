"""Service-Schicht: Orchestriert die Erstellung einer Rechnung.

Flow:
  1. Validierte Eingaben -> Invoice-Objekt
  2. Summen berechnen
  3. content_hash + chain_hash (GoBD-Unveraenderlichkeit)
  4. In DB festschreiben
  5. PDF rendern
  6. ZUGFeRD-XML erzeugen und ins PDF einbetten
  7. Hybrid-PDF nach data/invoices/<jahr>/<nummer>.pdf schreiben
  8. XML zusaetzlich getrennt als Backup
  9. Audit-Log-Eintrag
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from .config import settings
from .hashing import chain_hash, content_hash, file_sha256
from .models import AuditLog, Invoice, InvoiceItem
from .pdf import render_invoice_pdf
from .schemas import InvoiceIn
from .zugferd import build_xml, embed_xml_in_pdf


class InvoiceNumberTakenError(ValueError):
    pass


def _invoice_payload(inv: Invoice) -> dict:
    """Normalisierte Repraesentation fuer den Hash. Dateinamen und Pfade
    bleiben bewusst drauss - nur der fachliche Inhalt zaehlt."""
    return {
        "number": inv.invoice_number,
        "issue_date": inv.issue_date,
        "delivery_date": inv.delivery_date,
        "due_date": inv.due_date,
        "currency": inv.currency,
        "seller": {
            "name": inv.seller_name,
            "tax_id": inv.seller_tax_id,
            "iban": inv.seller_iban,
        },
        "buyer": {
            "name": inv.buyer_name,
            "street": inv.buyer_street,
            "postcode": inv.buyer_postcode,
            "city": inv.buyer_city,
            "country": inv.buyer_country,
            "tax_id": inv.buyer_tax_id,
            "reference": inv.buyer_reference,
        },
        "items": [
            {
                "pos": it.position,
                "description": it.description,
                "quantity": it.quantity,
                "unit": it.unit,
                "unit_price": it.unit_price,
                "tax_rate": it.tax_rate,
            }
            for it in inv.items
        ],
        "net_total": inv.net_total,
        "tax_total": inv.tax_total,
        "gross_total": inv.gross_total,
    }


def _last_chain_hash(db: Session) -> str:
    row = db.query(Invoice).order_by(Invoice.id.desc()).first()
    return row.chain_hash if row else ""


def create_invoice(db: Session, data: InvoiceIn) -> Invoice:
    # Doppelte Nummer verhindern - Rechnungsnummern muessen eindeutig sein.
    existing = db.query(Invoice).filter_by(invoice_number=data.invoice_number).first()
    if existing:
        raise InvoiceNumberTakenError(f"Rechnungsnummer {data.invoice_number} bereits vergeben.")

    inv = Invoice(
        invoice_number=data.invoice_number,
        issue_date=data.issue_date,
        delivery_date=data.delivery_date,
        due_date=data.due_date,
        currency=data.currency,
        seller_name=settings.seller_name,
        seller_street=settings.seller_street,
        seller_postcode=settings.seller_postcode,
        seller_city=settings.seller_city,
        seller_country=settings.seller_country,
        seller_tax_id=settings.seller_tax_id,
        seller_tax_number=settings.seller_tax_number,
        seller_email=settings.seller_email,
        seller_iban=settings.seller_iban,
        seller_bic=settings.seller_bic,
        seller_bank=settings.seller_bank,
        buyer_name=data.buyer_name,
        buyer_street=data.buyer_street,
        buyer_postcode=data.buyer_postcode,
        buyer_city=data.buyer_city,
        buyer_country=data.buyer_country,
        buyer_tax_id=data.buyer_tax_id,
        buyer_email=data.buyer_email,
        buyer_reference=data.buyer_reference,
        notes=data.notes,
        payment_terms=data.payment_terms,
    )

    net_total = Decimal("0")
    tax_total = Decimal("0")
    for i, it in enumerate(data.items, start=1):
        line = InvoiceItem(
            position=i,
            description=it.description,
            quantity=it.quantity,
            unit=it.unit,
            unit_price=it.unit_price,
            tax_rate=it.tax_rate,
        )
        inv.items.append(line)
        net_total += line.net_amount
        tax_total += line.tax_amount

    inv.net_total = net_total.quantize(Decimal("0.01"))
    inv.tax_total = tax_total.quantize(Decimal("0.01"))
    inv.gross_total = (net_total + tax_total).quantize(Decimal("0.01"))

    # Hashes
    ch = content_hash(_invoice_payload(inv))
    prev = _last_chain_hash(db)
    inv.content_hash = ch
    inv.prev_hash = prev
    inv.chain_hash = chain_hash(prev, ch)
    inv.created_at = datetime.utcnow()

    db.add(inv)
    db.flush()  # vergibt inv.id

    # Dateien schreiben
    year_dir = Path(settings.data_dir) / "invoices" / str(inv.issue_date.year)
    year_dir.mkdir(parents=True, exist_ok=True)
    safe_number = inv.invoice_number.replace("/", "-").replace(" ", "_")
    pdf_path = year_dir / f"{safe_number}.pdf"
    xml_path = year_dir / f"{safe_number}.xml"

    pdf_bytes = render_invoice_pdf(inv)
    xml_bytes = build_xml(inv)
    embed_xml_in_pdf(pdf_bytes, xml_bytes, pdf_path)
    xml_path.write_bytes(xml_bytes)

    inv.pdf_path = str(pdf_path.relative_to(settings.data_dir))
    inv.xml_path = str(xml_path.relative_to(settings.data_dir))

    db.add(AuditLog(
        event="created",
        invoice_number=inv.invoice_number,
        detail=f"pdf_sha256={file_sha256(str(pdf_path))} xml_sha256={file_sha256(str(xml_path))}",
    ))
    db.commit()
    db.refresh(inv)
    return inv


def verify_chain(db: Session) -> list[dict]:
    """Prueft die Hash-Chain aller Rechnungen. Gibt eine Liste von Problemen zurueck
    (leere Liste = alles in Ordnung)."""
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
