from datetime import date
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field


class InvoiceItemIn(BaseModel):
    description: str
    quantity: Decimal = Field(gt=0)
    unit: str = "C62"  # UN/ECE Rec 20 - C62 = "piece/stueck", HUR = "hour", DAY, MTR usw.
    unit_price: Decimal = Field(ge=0)
    tax_rate: Decimal = Field(ge=0, le=100)


class InvoiceIn(BaseModel):
    invoice_number: str
    issue_date: date
    delivery_date: date
    due_date: date
    currency: str = "EUR"

    buyer_name: str
    buyer_street: str
    buyer_postcode: str
    buyer_city: str
    buyer_country: str = "DE"
    buyer_tax_id: str = ""
    buyer_email: str = ""
    buyer_reference: str = ""

    items: list[InvoiceItemIn]
    notes: str = ""
    payment_terms: str = "Zahlbar innerhalb von 14 Tagen ohne Abzug."
