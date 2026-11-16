from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Invoice(Base):
    """Rechnung. Nach dem ersten Festschreiben (status='issued') unveraenderlich -
    das wird in der Service-Schicht erzwungen (keine UPDATEs auf finalisierte Rechnungen)."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_slug: Mapped[str] = mapped_column(String(128), index=True, default="")
    invoice_number: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="issued")  # issued | cancelled

    # Rechnungsdaten
    issue_date: Mapped[date] = mapped_column(Date)
    delivery_date: Mapped[date] = mapped_column(Date)  # Leistungsdatum - Pflicht in DE
    due_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")

    # Verkaeufer (eingefroren zum Zeitpunkt der Rechnung)
    seller_name: Mapped[str] = mapped_column(String(255))
    seller_street: Mapped[str] = mapped_column(String(255))
    seller_postcode: Mapped[str] = mapped_column(String(16))
    seller_city: Mapped[str] = mapped_column(String(128))
    seller_country: Mapped[str] = mapped_column(String(2))
    seller_tax_id: Mapped[str] = mapped_column(String(32))
    seller_tax_number: Mapped[str] = mapped_column(String(32), default="")
    seller_email: Mapped[str] = mapped_column(String(255))
    seller_iban: Mapped[str] = mapped_column(String(34))
    seller_bic: Mapped[str] = mapped_column(String(11), default="")
    seller_bank: Mapped[str] = mapped_column(String(128), default="")

    # Kaeufer
    buyer_name: Mapped[str] = mapped_column(String(255))
    buyer_street: Mapped[str] = mapped_column(String(255))
    buyer_postcode: Mapped[str] = mapped_column(String(16))
    buyer_city: Mapped[str] = mapped_column(String(128))
    buyer_country: Mapped[str] = mapped_column(String(2), default="DE")
    buyer_tax_id: Mapped[str] = mapped_column(String(32), default="")
    buyer_email: Mapped[str] = mapped_column(String(255), default="")
    buyer_reference: Mapped[str] = mapped_column(String(64), default="")  # Leitweg-ID bei B2G

    # Summen (zur Zeit des Versands fixiert)
    net_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    tax_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    gross_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    notes: Mapped[str] = mapped_column(Text, default="")
    payment_terms: Mapped[str] = mapped_column(Text, default="Zahlbar innerhalb von 14 Tagen ohne Abzug.")
    tax_regime: Mapped[str] = mapped_column(String(32), default="regular")  # regular | small_business | vat_exempt
    reverse_charge: Mapped[bool] = mapped_column(default=False)

    # GoBD / Audit
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    content_hash: Mapped[str] = mapped_column(String(64))  # SHA-256 der normalisierten Rechnungsdaten
    prev_hash: Mapped[str] = mapped_column(String(64), default="")  # Hash der vorherigen Rechnung
    chain_hash: Mapped[str] = mapped_column(String(64))  # SHA-256(prev_hash + content_hash)

    # Dateipfade (relativ zu data_dir)
    pdf_path: Mapped[str] = mapped_column(String(512), default="")
    xml_path: Mapped[str] = mapped_column(String(512), default="")
    # EPC-QR-Code (Giro-Code) als PNG - optional, nur wenn Firma aktiviert hat
    qr_code_filename: Mapped[str] = mapped_column(String(256), default="")

    items: Mapped[list["InvoiceItem"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceItem.position"
    )

    __table_args__ = (
        UniqueConstraint("company_slug", "invoice_number", name="uq_company_number"),
    )


class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    position: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit: Mapped[str] = mapped_column(String(8), default="C62")  # UN/ECE Rec 20, C62 = "piece"
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))  # z. B. 19.00 oder 7.00

    invoice: Mapped[Invoice] = relationship(back_populates="items")

    @property
    def net_amount(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))

    @property
    def tax_amount(self) -> Decimal:
        return (self.net_amount * self.tax_rate / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def gross_amount(self) -> Decimal:
        return (self.net_amount + self.tax_amount).quantize(Decimal("0.01"))


class Customer(Base):
    """Gespeicherte Rechnungsempfaenger pro Firma. Beim Rechnung-Anlegen
    kann der Nutzer einen bestehenden Kunden auswaehlen statt alles neu
    einzutippen."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_slug: Mapped[str] = mapped_column(String(128), index=True, default="")
    display_name: Mapped[str] = mapped_column(String(255))  # nur fuer Anzeige im Dropdown

    name: Mapped[str] = mapped_column(String(255))
    street: Mapped[str] = mapped_column(String(255))
    postcode: Mapped[str] = mapped_column(String(16))
    city: Mapped[str] = mapped_column(String(128))
    country: Mapped[str] = mapped_column(String(2), default="DE")
    tax_id: Mapped[str] = mapped_column(String(32), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    reference: Mapped[str] = mapped_column(String(64), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)


class IncomingInvoice(Base):
    """Eingehende E-Rechnung (vom Kunden/Lieferanten empfangen). Wird
    GoBD-konform archiviert. Die XML wird gespeichert, geparst und die
    extrahierten Felder bleiben in der DB für Suche/Übersicht."""

    __tablename__ = "incoming_invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_slug: Mapped[str] = mapped_column(String(128), index=True, default="")

    source_kind: Mapped[str] = mapped_column(String(32), default="")  # pdf-hybrid | xml-pure
    format_profile: Mapped[str] = mapped_column(String(128), default="")
    is_valid: Mapped[bool] = mapped_column(default=False)
    issues: Mapped[str] = mapped_column(Text, default="")

    invoice_number: Mapped[str] = mapped_column(String(64), default="")
    issue_date_str: Mapped[str] = mapped_column(String(32), default="")
    seller_name: Mapped[str] = mapped_column(String(255), default="")
    buyer_name: Mapped[str] = mapped_column(String(255), default="")
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    net_total: Mapped[str] = mapped_column(String(32), default="")
    tax_total: Mapped[str] = mapped_column(String(32), default="")
    gross_total: Mapped[str] = mapped_column(String(32), default="")
    iban: Mapped[str] = mapped_column(String(34), default="")

    original_filename: Mapped[str] = mapped_column(String(255), default="")
    pdf_path: Mapped[str] = mapped_column(String(512), default="")
    xml_path: Mapped[str] = mapped_column(String(512), default="")
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """Append-only Log aller relevanten Ereignisse (GoBD: Nachvollziehbarkeit)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    event: Mapped[str] = mapped_column(String(64))  # created, cancelled, exported, ...
    invoice_number: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
