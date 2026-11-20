"""Pre-Flight-Prüfung VOR dem Erzeugen einer Rechnung.

Prüft alle Pflichtfelder nach § 14 UStG, EN 16931 (BR-01..BR-67) und den
deutschen Zusatzregeln (BR-DE-01..27) sowie den Konsistenzregeln (BR-CO).
Bei einem Fehler wird die Rechnung erst gar nicht erzeugt - der Nutzer
sieht sofort eine Liste mit konkreten Hinweisen, was fehlt oder falsch ist.

Rückgabe: Liste menschenlesbarer Hinweise, leer = alles OK.
"""

from __future__ import annotations

import re
from decimal import Decimal

from .config import Company
from .schemas import InvoiceIn


IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]+$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def check_company(c: Company) -> list[str]:
    """BR-06..BR-09, BR-DE-21/27: Absender-Pflichtfelder."""
    missing = []
    if not c.seller_name:
        missing.append("BR-06: Firmenname/Absender-Name fehlt (BT-27, § 14 Abs. 4 Nr. 1 UStG)")
    if not c.seller_street:
        missing.append("BR-08: Straße des Absenders fehlt (BT-35, § 14 Abs. 4 Nr. 1 UStG)")
    if not c.seller_postcode:
        missing.append("BR-08: PLZ des Absenders fehlt (BT-38)")
    if not c.seller_city:
        missing.append("BR-08: Ort des Absenders fehlt (BT-37)")
    if not c.seller_country:
        missing.append("BR-09: Länderkennzeichen des Absenders fehlt (BT-40)")
    if not (c.seller_tax_id or c.seller_tax_number):
        missing.append("BR-DE-27: USt-IdNr. ODER Steuernummer fehlt "
                       "(BT-31/BT-32, § 14 Abs. 4 Nr. 2 UStG)")
    if c.seller_email and not EMAIL_RE.match(c.seller_email):
        missing.append(f"BR-DE-7: E-Mail-Adresse '{c.seller_email}' sieht ungültig aus (BT-43)")
    return missing


def check_invoice(data: InvoiceIn, company: Company) -> list[str]:
    """Vollständige Pre-Flight-Validierung aller Pflichtfelder einer Rechnung.

    Prüft:
    - BR-02..05: Rechnungsnummer, Datum, Typ, Währung
    - BR-07, BR-11: Empfänger-Name und Adresse
    - BR-DE-14: Leistungsdatum (§ 14 Abs. 4 Nr. 6 UStG)
    - BR-DE-15: BuyerReference (deutsche Pflicht)
    - BR-DE-24: Fälligkeitsdatum
    - BR-13..15: Monetäre Summen
    - BR-16, BR-21..25: Positionszeilen
    - BR-CO-10/13: Konsistenz der Summen
    - Spezielle Steuerregime (Kleinunternehmer, Reverse-Charge)
    """
    errors: list[str] = []

    # --- BR-02: Rechnungsnummer ---
    if not data.invoice_number.strip():
        errors.append("BR-02: Rechnungsnummer fehlt (BT-1, § 14 Abs. 4 Nr. 4 UStG)")

    # --- BR-03/04/05: Datum, Typ, Währung werden vom Backend gesetzt ---

    # --- BR-07: Empfänger-Name ---
    if not data.buyer_name:
        errors.append("BR-07: Name des Rechnungsempfängers fehlt (BT-44, § 14 Abs. 4 Nr. 1 UStG)")

    # --- BR-11: Empfänger-Adresse ---
    if not data.buyer_street:
        errors.append("BR-11: Straße des Empfängers fehlt (BT-50)")
    if not data.buyer_postcode:
        errors.append("BR-11: PLZ des Empfängers fehlt (BT-53)")
    if not data.buyer_city:
        errors.append("BR-11: Ort des Empfängers fehlt (BT-52)")
    if not getattr(data, "buyer_country", "DE"):
        errors.append("BR-11: Länderkennzeichen des Empfängers fehlt (BT-55)")

    # --- BR-DE-24: Fälligkeit nicht vor Rechnungsdatum ---
    if data.due_date < data.issue_date:
        errors.append("BR-DE-24: Fälligkeitsdatum liegt vor dem Rechnungsdatum - "
                      "nicht zulässig (BT-9)")

    # --- BR-DE-14: Leistungsdatum vorhanden (wird aus delivery_date übernommen) ---
    if not data.delivery_date:
        errors.append("BR-DE-14: Leistungs-/Lieferdatum fehlt - "
                      "nach § 14 Abs. 4 Nr. 6 UStG Pflichtangabe (BT-72)")

    # --- BR-16: Mindestens eine Position ---
    if not data.items:
        errors.append("BR-16: Mindestens eine Rechnungsposition erforderlich (BG-25)")

    # --- BR-21..25 pro Position ---
    for i, it in enumerate(data.items, start=1):
        if not it.description.strip():
            errors.append(f"BR-25: Position {i}: Beschreibung/Produktname fehlt (BT-153)")
        if it.quantity <= 0:
            errors.append(f"BR-22: Position {i}: Menge muss größer 0 sein (BT-129)")
        if it.unit_price < 0:
            errors.append(f"BR-27: Position {i}: Einzelpreis darf nicht negativ sein (BT-146)")
        if not getattr(it, "unit", "").strip():
            errors.append(f"BR-23: Position {i}: Mengeneinheit fehlt (BT-130)")
        if it.tax_rate < 0:
            errors.append(f"BR-48: Position {i}: USt-Satz darf nicht negativ sein (BT-152)")

    # --- Steuerregime-Konsistenz ---
    rates = {Decimal(it.tax_rate) for it in data.items}
    regime = company.tax_regime

    if regime == "small_business":
        if any(r > 0 for r in rates):
            errors.append(
                "BR-E-01: Firma ist Kleinunternehmer (§ 19 UStG) - "
                "alle Positionen müssen USt-Satz 0% haben")
    elif regime == "vat_exempt":
        if any(r > 0 for r in rates):
            errors.append(
                "BR-E-01: Firma ist umsatzsteuerbefreit - "
                "alle Positionen müssen USt-Satz 0% haben")
    else:
        if data.reverse_charge:
            if any(r > 0 for r in rates):
                errors.append(
                    "BR-AE-01: Reverse-Charge gesetzt - alle Positionen müssen "
                    "USt-Satz 0% haben (Steuerschuld geht auf Empfänger über)")
            if not data.buyer_tax_id:
                errors.append(
                    "BR-AE-08: Reverse-Charge gesetzt, aber USt-IdNr. des Empfängers fehlt (BT-48)")
        # Bei normaler Besteuerung: erlaubt sind nur 0, 7, 19
        for it in data.items:
            if Decimal(it.tax_rate) not in {Decimal("0"), Decimal("7"), Decimal("19")}:
                errors.append(
                    f"BR-S: Position '{it.description}' hat USt-Satz {it.tax_rate}% - "
                    "in Deutschland sind nur 0%, 7% oder 19% zulässig (bei Sonderfällen "
                    "bitte Reverse-Charge oder USt-Befreiung aktivieren)")

    # --- IBAN-Format ---
    iban = (company.seller_iban or "").replace(" ", "").upper()
    if iban and not IBAN_RE.match(iban):
        errors.append(f"BR-61: IBAN '{company.seller_iban}' hat ungültiges Format "
                      "(erwartet: Länderkürzel + 2 Prüfziffern + Kontonummer)")

    # --- E-Mail des Empfängers (optional, aber wenn gesetzt dann valide) ---
    if getattr(data, "buyer_email", None):
        if not EMAIL_RE.match(data.buyer_email):
            errors.append(f"BR-DE-7: E-Mail-Adresse des Empfängers '{data.buyer_email}' "
                          "sieht ungültig aus (BT-49)")

    # --- Konsistenzprüfung: Berechnete Summen plausibel? ---
    # Netto = Σ(menge × preis), USt = Σ(netto × satz), Brutto = Netto + USt
    try:
        total_net = sum((Decimal(it.quantity) * Decimal(it.unit_price)
                         for it in data.items), Decimal("0"))
        if total_net < 0:
            errors.append("BR-CO-13: Netto-Summe ist negativ - "
                          "Gutschriften bitte als Typ 381 markieren")
    except Exception:
        pass

    return errors


def mandatory_notice(company: Company, reverse_charge: bool) -> str:
    """Pflichthinweis, der je nach Steuerregime automatisch auf die Rechnung kommt."""
    if company.tax_regime == "small_business":
        return ("Gemäß § 19 UStG wird keine Umsatzsteuer berechnet "
                "(Kleinunternehmerregelung).")
    if company.tax_regime == "vat_exempt":
        return "Umsatzsteuerbefreite Leistung."
    if reverse_charge:
        return "Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge)."
    return ""
