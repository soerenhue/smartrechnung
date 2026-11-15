"""Pre-Flight-Prüfung vor dem Erzeugen einer Rechnung.

Prüft die Pflichtangaben nach § 14 UStG und meldet alles, was fehlt oder
unplausibel ist. Gibt eine Liste menschenlesbarer Hinweise zurück - leere
Liste = alles OK.
"""

from __future__ import annotations

import re
from decimal import Decimal

from .config import Company
from .schemas import InvoiceIn


IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]+$")


def check_company(c: Company) -> list[str]:
    missing = []
    if not c.seller_name: missing.append("Firmenname (Absender)")
    if not c.seller_street: missing.append("Straße des Absenders")
    if not c.seller_postcode: missing.append("PLZ des Absenders")
    if not c.seller_city: missing.append("Ort des Absenders")
    if not (c.seller_tax_id or c.seller_tax_number):
        missing.append("USt-IdNr. ODER Steuernummer (Pflicht nach § 14 Abs. 4 UStG)")
    return [f"Firmendaten unvollständig: {m}." for m in missing]


def check_invoice(data: InvoiceIn, company: Company) -> list[str]:
    errors: list[str] = []

    if not data.invoice_number.strip():
        errors.append("Rechnungsnummer fehlt.")
    if not data.items:
        errors.append("Mindestens eine Rechnungsposition erforderlich.")

    # Empfaenger
    if not data.buyer_name:
        errors.append("Name des Rechnungsempfängers fehlt.")
    if not data.buyer_street or not data.buyer_postcode or not data.buyer_city:
        errors.append("Anschrift des Rechnungsempfängers unvollständig.")

    # Daten
    if data.due_date < data.issue_date:
        errors.append("Fälligkeitsdatum liegt vor dem Rechnungsdatum.")

    # Positionen
    for i, it in enumerate(data.items, start=1):
        if not it.description.strip():
            errors.append(f"Position {i}: Beschreibung fehlt.")
        if it.quantity <= 0:
            errors.append(f"Position {i}: Menge muss größer 0 sein.")
        if it.unit_price < 0:
            errors.append(f"Position {i}: Einzelpreis darf nicht negativ sein.")

    # Steuerregime
    rates = {Decimal(it.tax_rate) for it in data.items}
    if company.tax_regime == "small_business":
        if any(r > 0 for r in rates):
            errors.append(
                "Kleinunternehmer (§ 19 UStG): alle Positionen müssen 0% USt haben."
            )
    elif company.tax_regime == "vat_exempt":
        if any(r > 0 for r in rates):
            errors.append(
                "Firma ist als umsatzsteuerbefreit eingestellt: alle Positionen müssen 0% USt haben."
            )
    else:
        if data.reverse_charge and any(r > 0 for r in rates):
            errors.append(
                "Reverse-Charge: alle Positionen müssen 0% USt haben (Steuerschuld geht auf den Empfaenger ueber)."
            )

    # IBAN (nur Format-Check)
    iban = company.seller_iban.replace(" ", "").upper()
    if iban and not IBAN_RE.match(iban):
        errors.append(f"IBAN '{company.seller_iban}' scheint ungültig zu sein.")

    # Reverse-Charge grenzueberschreitend
    if data.reverse_charge and not data.buyer_tax_id:
        errors.append(
            "Reverse-Charge gesetzt, aber USt-IdNr. des Empfaengers fehlt."
        )

    return errors


def mandatory_notice(company: Company, reverse_charge: bool) -> str:
    """Pflichthinweis, der je nach Steuerregime automatisch auf die Rechnung kommt."""
    if company.tax_regime == "small_business":
        return ("Gemaess § 19 UStG wird keine Umsatzsteuer berechnet "
                "(Kleinunternehmerregelung).")
    if company.tax_regime == "vat_exempt":
        return "Umsatzsteuerbefreite Leistung."
    if reverse_charge:
        return "Steuerschuldnerschaft des Leistungsempfaengers (Reverse Charge)."
    return ""
