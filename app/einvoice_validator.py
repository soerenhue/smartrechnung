"""Umfassende EN-16931-Validierung für eingehende und ausgehende
E-Rechnungen. Prüft die offiziellen Business Rules der EU-Norm plus
die deutschen Zusatzregeln (BR-DE) plus Konsistenz (BR-CO).

Kein Ersatz für die offizielle KoSIT-Validierung mit XSD+Schematron, aber
eine praktische Kurzform, die alle für Buchhaltungssysteme relevanten
Checks durchführt und verständliche Fehlermeldungen liefert.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

from lxml import etree

_NS_CII = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}
_NS_UBL = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


@dataclass
class ValidationResult:
    level: str      # "ok" | "warn" | "error"
    rule: str       # z.B. "BR-DE-15"
    title: str      # kurzer Titel
    detail: str = ""  # längere Erklärung

    @property
    def is_error(self) -> bool:
        return self.level == "error"


@dataclass
class ValidationReport:
    results: list[ValidationResult] = field(default_factory=list)
    is_valid: bool = True

    def add(self, level: str, rule: str, title: str, detail: str = ""):
        r = ValidationResult(level, rule, title, detail)
        self.results.append(r)
        if r.is_error:
            self.is_valid = False

    def ok(self, rule: str, title: str): self.add("ok", rule, title)
    def warn(self, rule: str, title: str, detail: str = ""): self.add("warn", rule, title, detail)
    def error(self, rule: str, title: str, detail: str = ""): self.add("error", rule, title, detail)

    @property
    def errors(self) -> list[ValidationResult]:
        return [r for r in self.results if r.level == "error"]

    @property
    def warnings(self) -> list[ValidationResult]:
        return [r for r in self.results if r.level == "warn"]

    @property
    def passed(self) -> list[ValidationResult]:
        return [r for r in self.results if r.level == "ok"]


def _text(root, xpath: str, ns: dict) -> str:
    try:
        els = root.xpath(xpath, namespaces=ns)
        if els:
            if hasattr(els[0], "text"):
                return (els[0].text or "").strip()
            return str(els[0]).strip()
    except Exception:
        pass
    return ""


def _all(root, xpath: str, ns: dict) -> list:
    try:
        return root.xpath(xpath, namespaces=ns)
    except Exception:
        return []


def _decimal(s: str) -> Optional[Decimal]:
    if not s:
        return None
    try:
        return Decimal(s.replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _validate_cii(root, rep: ValidationReport, pdf_a3: Optional[bool] = None) -> None:
    """Validiert eine ZUGFeRD/Factur-X Cross-Industry-Invoice XML."""
    ns = _NS_CII

    # --- Guideline / Profil ---
    guideline = _text(root, ".//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID", ns)
    if not guideline:
        rep.error("BR-01", "Format-Profil fehlt",
                  "<ram:ID> unter GuidelineSpecifiedDocumentContextParameter ist leer. "
                  "Erwartet z.B. urn:cen.eu:en16931:2017 (EN-16931-Profil).")
    else:
        if "en16931" in guideline or "xrechnung" in guideline.lower():
            rep.ok("BR-01", f"Format-Profil erkannt: {guideline}")
        else:
            rep.warn("BR-01", f"Unbekanntes Profil: {guideline}",
                     "Dieses Profil ist weder EN 16931 noch XRechnung. "
                     "Manche Empfänger können es nicht verarbeiten.")

    # --- BR-02: Rechnungsnummer (BT-1) ---
    inv_num = _text(root, "rsm:ExchangedDocument/ram:ID", ns)
    if not inv_num:
        rep.error("BR-02", "Rechnungsnummer (BT-1) fehlt",
                  "Jede Rechnung muss eine eindeutige Rechnungsnummer tragen (§ 14 Abs. 4 Nr. 4 UStG).")
    else:
        rep.ok("BR-02", f"Rechnungsnummer: {inv_num}")

    # --- BR-03: Rechnungsdatum (BT-2) ---
    issue_date = _text(root, "rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString", ns)
    if not issue_date:
        rep.error("BR-03", "Rechnungsdatum (BT-2) fehlt",
                  "Ohne Ausstellungsdatum keine gültige Rechnung (§ 14 Abs. 4 Nr. 3 UStG).")
    elif len(issue_date) == 8 and issue_date.isdigit():
        y, m, d = issue_date[:4], issue_date[4:6], issue_date[6:8]
        rep.ok("BR-03", f"Rechnungsdatum: {d}.{m}.{y}")
    else:
        rep.warn("BR-03", f"Datumsformat ungewöhnlich: {issue_date}")

    # --- BR-04: Rechnungstyp-Code (BT-3) ---
    type_code = _text(root, "rsm:ExchangedDocument/ram:TypeCode", ns)
    if type_code not in ("380", "381", "384", "389", "751", "875", "876", "877"):
        rep.error("BR-04", f"Rechnungstyp-Code ungültig: {type_code}",
                  "Nach EN 16931 erlaubt: 380 (Rechnung), 381 (Gutschrift), 384 (Korrekturrechnung), …")
    else:
        names = {"380": "Rechnung", "381": "Gutschrift", "384": "Korrekturrechnung",
                 "389": "Selbst-ausgestellte Rechnung"}
        rep.ok("BR-04", f"Typ: {type_code} ({names.get(type_code, 'Sonstige')})")

    # --- BR-05: Währung (BT-5) ---
    tx = "rsm:SupplyChainTradeTransaction"
    settle = f"{tx}/ram:ApplicableHeaderTradeSettlement"
    currency = _text(root, f"{settle}/ram:InvoiceCurrencyCode", ns)
    if not currency:
        rep.error("BR-05", "Währung (BT-5) fehlt")
    elif not re.fullmatch(r"[A-Z]{3}", currency):
        rep.error("BR-05", f"Währungscode ungültig: {currency}",
                  "Erwartet: ISO-4217 Drei-Buchstaben-Code (EUR, USD, …).")
    else:
        rep.ok("BR-05", f"Währung: {currency}")

    # --- BR-06 / BR-DE-21: Absender-Name (BT-27) ---
    agr = f"{tx}/ram:ApplicableHeaderTradeAgreement"
    seller_name = _text(root, f"{agr}/ram:SellerTradeParty/ram:Name", ns)
    if not seller_name:
        rep.error("BR-06", "Absender-Name (BT-27) fehlt")
    else:
        rep.ok("BR-06", f"Absender: {seller_name}")

    # --- BR-08: Absender-Adresse ---
    seller_addr = _all(root, f"{agr}/ram:SellerTradeParty/ram:PostalTradeAddress", ns)
    if not seller_addr:
        rep.error("BR-08", "Absender-Adresse (BG-5) fehlt")
    else:
        country = _text(root, f"{agr}/ram:SellerTradeParty/ram:PostalTradeAddress/ram:CountryID", ns)
        if not country:
            rep.error("BR-09", "Absender-Länderkennzeichen (BT-40) fehlt")
        else:
            rep.ok("BR-09", f"Absender-Land: {country}")

    # --- BR-DE-6/7: Absender-Kontakt (Telefon/E-Mail) ---
    seller_email = _text(root, f"{agr}/ram:SellerTradeParty/ram:URIUniversalCommunication/ram:URIID", ns)
    seller_phone = _text(root, f"{agr}/ram:SellerTradeParty/ram:DefinedTradeContact/ram:TelephoneUniversalCommunication/ram:CompleteNumber", ns)
    if not seller_email and not seller_phone:
        rep.warn("BR-DE-06/07", "Absender-Kontakt (BT-42/BT-43) fehlt",
                 "Nach deutschen Regeln sollte mindestens Telefon oder E-Mail des "
                 "Absenders in der Rechnung stehen.")
    else:
        rep.ok("BR-DE-06/07", f"Absender-Kontakt: {seller_email or seller_phone}")

    # --- BR-DE-27 / BR-21: Steuernummer / USt-IdNr des Absenders ---
    vat_id = _text(root, f"{agr}/ram:SellerTradeParty/ram:SpecifiedTaxRegistration[ram:ID/@schemeID='VA']/ram:ID", ns)
    tax_no = _text(root, f"{agr}/ram:SellerTradeParty/ram:SpecifiedTaxRegistration[ram:ID/@schemeID='FC']/ram:ID", ns)
    if not vat_id and not tax_no:
        rep.error("BR-DE-27", "Steuernummer oder USt-IdNr. fehlt",
                  "§ 14 Abs. 4 Nr. 2 UStG: Pflichtangabe auf jeder Rechnung.")
    elif vat_id:
        rep.ok("BR-DE-27", f"USt-IdNr.: {vat_id}")
    else:
        rep.ok("BR-DE-27", f"Steuernummer: {tax_no}")

    # --- BR-07: Empfänger-Name (BT-44) ---
    buyer_name = _text(root, f"{agr}/ram:BuyerTradeParty/ram:Name", ns)
    if not buyer_name:
        rep.error("BR-07", "Empfänger-Name (BT-44) fehlt")
    else:
        rep.ok("BR-07", f"Empfänger: {buyer_name}")

    # --- BR-DE-15 / BR-10: BuyerReference oder PurchaseOrderReference ---
    buyer_ref = _text(root, f"{agr}/ram:BuyerReference", ns)
    po_ref = _text(root, f"{agr}/ram:BuyerOrderReferencedDocument/ram:IssuerAssignedID", ns)
    if not buyer_ref and not po_ref:
        rep.error("BR-DE-15",
                  "BuyerReference (BT-10) oder PurchaseOrderReference (BT-13) fehlt",
                  "Nach deutscher Regel BR-DE-15 muss mindestens eine dieser Referenzen "
                  "gesetzt sein. Buchhaltungssysteme wie DATEV verwerfen die Rechnung sonst.")
    elif buyer_ref:
        rep.ok("BR-DE-15", f"BuyerReference: {buyer_ref}")
    else:
        rep.ok("BR-DE-15", f"PurchaseOrderReference: {po_ref}")

    # --- BR-11: Empfänger-Adresse ---
    buyer_addr = _all(root, f"{agr}/ram:BuyerTradeParty/ram:PostalTradeAddress", ns)
    if not buyer_addr:
        rep.error("BR-11", "Empfänger-Adresse (BG-8) fehlt")
    else:
        buyer_country = _text(root, f"{agr}/ram:BuyerTradeParty/ram:PostalTradeAddress/ram:CountryID", ns)
        if not buyer_country:
            rep.error("BR-11", "Empfänger-Länderkennzeichen (BT-55) fehlt")

    # --- BR-13: Netto-Gesamt (BT-109) ---
    sums = f"{settle}/ram:SpecifiedTradeSettlementHeaderMonetarySummation"
    net_total_str = _text(root, f"{sums}/ram:LineTotalAmount", ns)
    tax_basis_str = _text(root, f"{sums}/ram:TaxBasisTotalAmount", ns)
    tax_total_str = _text(root, f"{sums}/ram:TaxTotalAmount", ns)
    gross_total_str = _text(root, f"{sums}/ram:GrandTotalAmount", ns)
    due_amount_str = _text(root, f"{sums}/ram:DuePayableAmount", ns)

    net = _decimal(net_total_str)
    tax_basis = _decimal(tax_basis_str)
    tax = _decimal(tax_total_str)
    gross = _decimal(gross_total_str)
    due = _decimal(due_amount_str)

    if net is None:
        rep.error("BR-13", "Netto-Gesamt (BT-109) fehlt oder ungültig")
    else:
        rep.ok("BR-13", f"Netto: {net} {currency}")
    if tax is None:
        rep.error("BR-14", "USt-Gesamt (BT-110) fehlt oder ungültig")
    else:
        rep.ok("BR-14", f"USt: {tax} {currency}")
    if gross is None:
        rep.error("BR-15", "Brutto-Gesamt (BT-112) fehlt oder ungültig")
    else:
        rep.ok("BR-15", f"Brutto: {gross} {currency}")

    # --- BR-CO-10: Netto + USt = Brutto ---
    if net is not None and tax is not None and gross is not None:
        if abs((net + tax) - gross) > Decimal("0.02"):
            rep.error("BR-CO-10",
                      f"Rechenfehler: Netto ({net}) + USt ({tax}) ≠ Brutto ({gross})",
                      f"Differenz: {abs((net + tax) - gross)} {currency}. "
                      "Nach BR-CO-10 muss die Summe exakt stimmen.")
        else:
            rep.ok("BR-CO-10", "Netto + USt = Brutto ✓")

    # --- BR-CO-11: Zahlbar-Betrag = Brutto (bei normalen Rechnungen) ---
    if gross is not None and due is not None:
        if abs(gross - due) > Decimal("0.02"):
            rep.warn("BR-CO-11",
                     f"Zahlbetrag ({due}) weicht vom Brutto ({gross}) ab",
                     "Könnte Vorauszahlung oder Rundungsdifferenz sein.")
        else:
            rep.ok("BR-CO-11", "Zahlbetrag = Brutto ✓")

    # --- BG-25: Positionszeilen ---
    items = _all(root, f"{tx}/ram:IncludedSupplyChainTradeLineItem", ns)
    if not items:
        rep.error("BR-16", "Keine Positionszeile (BG-25) vorhanden",
                  "Jede Rechnung braucht mindestens eine Positionszeile.")
    else:
        rep.ok("BR-16", f"{len(items)} Positionszeile(n)")

    line_total = Decimal("0")
    for i, item in enumerate(items, start=1):
        line_id = _text(item, "ram:AssociatedDocumentLineDocument/ram:LineID", ns)
        item_name = _text(item, "ram:SpecifiedTradeProduct/ram:Name", ns)
        if not line_id:
            rep.error("BR-21", f"Position {i}: LineID (BT-126) fehlt")
        if not item_name:
            rep.error("BR-25", f"Position {i}: Produktname (BT-153) fehlt")
        line_net_str = _text(item,
            "ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount",
            ns)
        line_net = _decimal(line_net_str)
        if line_net is None:
            rep.error("BR-24", f"Position {i} ({item_name}): Netto-Betrag fehlt")
        else:
            line_total += line_net

    # --- BR-CO-13: Summe der Positionen = Netto-Gesamt ---
    if net is not None and items:
        if abs(line_total - net) > Decimal("0.02"):
            rep.error("BR-CO-13",
                      f"Summe der Positionen ({line_total}) ≠ Netto-Gesamt ({net})",
                      f"Differenz: {abs(line_total - net)} {currency}.")
        else:
            rep.ok("BR-CO-13", "Positionen = Netto-Gesamt ✓")

    # --- BG-23: USt-Aufschlüsselung ---
    tax_breakdowns = _all(root, f"{settle}/ram:ApplicableTradeTax", ns)
    if not tax_breakdowns:
        rep.error("BR-45", "USt-Aufschlüsselung (BG-23) fehlt")
    else:
        rep.ok("BR-45", f"{len(tax_breakdowns)} USt-Satz-Gruppe(n) aufgeschlüsselt")
        for tb in tax_breakdowns:
            cat = _text(tb, "ram:CategoryCode", ns)
            exemption = _text(tb, "ram:ExemptionReason", ns)
            if cat in ("E", "AE", "Z", "K", "G", "O") and not exemption:
                rep.warn("BR-E-10",
                         f"Steuerkategorie {cat} ohne Befreiungsgrund",
                         "Für steuerbefreite Umsätze (Kleinunternehmer, Reverse-Charge, "
                         "Export) muss der Grund angegeben sein.")

    # --- BR-50: Zahlungsmittel ---
    payment_means = _all(root, f"{settle}/ram:SpecifiedTradeSettlementPaymentMeans", ns)
    if not payment_means:
        rep.warn("BR-50", "Keine Zahlungsmittel-Angabe (BG-16)",
                 "Dein Kunde weiß ohne IBAN nicht, wohin er überweisen soll.")
    else:
        iban = _text(root, f"{settle}/ram:SpecifiedTradeSettlementPaymentMeans/ram:PayeePartyCreditorFinancialAccount/ram:IBANID", ns)
        if iban:
            rep.ok("BR-61", f"IBAN: {iban}")
            if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban.replace(" ", "")):
                rep.warn("BR-61", f"IBAN-Format sieht ungewöhnlich aus: {iban}")
        else:
            rep.warn("BR-61", "IBAN (BT-84) fehlt")

    # --- BR-DE-24: Fälligkeitsdatum ---
    due_date = _text(root, f"{settle}/ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString", ns)
    payment_terms = _text(root, f"{settle}/ram:SpecifiedTradePaymentTerms/ram:Description", ns)
    if not due_date and not payment_terms:
        rep.warn("BR-DE-24", "Weder Fälligkeitsdatum noch Zahlungsbedingungen",
                 "Für Rechnungen über 150 € sollten Zahlungsbedingungen angegeben sein.")
    elif due_date and len(due_date) == 8:
        y, m, d = due_date[:4], due_date[4:6], due_date[6:8]
        rep.ok("BR-DE-24", f"Fällig: {d}.{m}.{y}")

    # --- BR-DE-14: Leistungs- / Lieferdatum ---
    delivery_date = _text(root,
        f"{tx}/ram:ApplicableHeaderTradeDelivery/ram:ActualDeliverySupplyChainEvent/ram:OccurrenceDateTime/udt:DateTimeString",
        ns)
    period_start = _text(root,
        f"{settle}/ram:BillingSpecifiedPeriod/ram:StartDateTime/udt:DateTimeString", ns)
    if not delivery_date and not period_start:
        rep.error("BR-DE-14",
                  "Leistungsdatum (BT-72) oder Leistungs-Zeitraum (BG-14) fehlt",
                  "§ 14 Abs. 4 Nr. 6 UStG: Zeitpunkt der Leistungserbringung ist Pflichtangabe. "
                  "DATEV u.a. lehnen die Rechnung sonst ab.")
    elif delivery_date and len(delivery_date) == 8:
        y, m, d = delivery_date[:4], delivery_date[4:6], delivery_date[6:8]
        rep.ok("BR-DE-14", f"Leistungsdatum: {d}.{m}.{y}")


def _validate_ubl(root, rep: ValidationReport) -> None:
    """Minimale UBL-Validierung (XRechnung)."""
    ns = _NS_UBL
    inv_num = _text(root, "cbc:ID", ns)
    if inv_num:
        rep.ok("BR-02", f"Rechnungsnummer: {inv_num}")
    else:
        rep.error("BR-02", "Rechnungsnummer (BT-1) fehlt")

    issue_date = _text(root, "cbc:IssueDate", ns)
    if issue_date:
        rep.ok("BR-03", f"Rechnungsdatum: {issue_date}")
    else:
        rep.error("BR-03", "Rechnungsdatum (BT-2) fehlt")

    type_code = _text(root, "cbc:InvoiceTypeCode", ns)
    if type_code:
        rep.ok("BR-04", f"Typ: {type_code}")
    else:
        rep.error("BR-04", "Rechnungstyp-Code (BT-3) fehlt")

    buyer_ref = _text(root, "cbc:BuyerReference", ns)
    if buyer_ref:
        rep.ok("BR-DE-15", f"BuyerReference: {buyer_ref}")
    else:
        rep.warn("BR-DE-15", "BuyerReference (BT-10) fehlt")

    # Beträge
    for bt, xp in [
        ("BT-109 Netto", "cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount"),
        ("BT-110 USt", "cac:TaxTotal/cbc:TaxAmount"),
        ("BT-112 Brutto", "cac:LegalMonetaryTotal/cbc:PayableAmount"),
    ]:
        v = _text(root, xp, ns)
        if v:
            rep.ok(bt.split()[0], f"{bt}: {v}")
        else:
            rep.error(bt.split()[0], f"{bt} fehlt")


def validate_einvoice_xml(xml_bytes: bytes,
                          source_kind: str = "",
                          pdf_a3: Optional[bool] = None) -> ValidationReport:
    """Validiert eine E-Rechnungs-XML gegen EN 16931 + BR-DE + BR-CO."""
    rep = ValidationReport()

    try:
        root = etree.fromstring(xml_bytes)
    except Exception as e:
        rep.error("XML-Parser", "XML konnte nicht geparst werden", str(e))
        return rep

    tag = etree.QName(root).localname
    if tag == "CrossIndustryInvoice":
        _validate_cii(root, rep, pdf_a3=pdf_a3)
    elif tag == "Invoice":
        _validate_ubl(root, rep)
    else:
        rep.error("Format",
                  f"Unbekanntes XML-Format: <{tag}>",
                  "Nur ZUGFeRD/Factur-X (CrossIndustryInvoice) und "
                  "UBL/XRechnung (Invoice) werden unterstützt.")
        return rep

    # --- PDF/A-3 prüfen ---
    if pdf_a3 is True:
        rep.ok("PDF/A-3", "PDF ist ein PDF/A-3-konformes Hybrid-PDF")
    elif pdf_a3 is False:
        rep.warn("PDF/A-3",
                 "PDF ist NICHT als PDF/A-3 markiert",
                 "ZUGFeRD / Factur-X erwarten PDF/A-3. Manche Archivsysteme "
                 "lehnen die Rechnung ohne PDF/A-3-Konformität ab. Die Daten "
                 "selbst werden aber trotzdem korrekt eingelesen.")

    return rep


def check_pdf_a3(pdf_bytes: bytes) -> Optional[bool]:
    """Prüft (best effort) ob das PDF PDF/A-3-Konformität deklariert."""
    try:
        # Suche nach XMP-Metadaten mit pdfaid:conformance="A" und pdfaid:part="3"
        # Einfach als Byte-Match - sehr defensiv.
        head = pdf_bytes[:200000]  # XMP ist meist im ersten Teil
        has_part3 = b"pdfaid:part=\"3\"" in head or b"<pdfaid:part>3</pdfaid:part>" in head
        has_conformance = (b"pdfaid:conformance=\"A\"" in head
                           or b"pdfaid:conformance=\"B\"" in head
                           or b"pdfaid:conformance=\"U\"" in head
                           or b"<pdfaid:conformance>" in head)
        if has_part3 and has_conformance:
            return True
        if b"PDF/A" in head or b"pdfaid:" in head:
            return False  # markiert aber nicht als Part 3
        return None  # keine XMP-Metadaten gefunden
    except Exception:
        return None
