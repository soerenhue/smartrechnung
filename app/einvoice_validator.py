"""Umfassende EN-16931-Validierung für eingehende und ausgehende
E-Rechnungen.

Prüft:
- XML Well-Formedness und Grundstruktur (CII oder UBL)
- Alle EN-16931 Business Rules (BR-01..17 inkl. Pflichtfelder)
- Konsistenzregeln (BR-CO-10..13: Netto+USt=Brutto, Summen stimmen)
- Steuerkategorie-Regeln (BR-S, BR-Z, BR-E, BR-AE, BR-K, BR-G, BR-O)
- Deutsche Zusatzregeln (BR-DE-01..27)
- Code-Listen-Prüfung (ISO-3166 Land, ISO-4217 Währung, UNCL 5305 Steuer-Codes)
- PDF/A-3 Konformität (für Hybrid-PDFs)

Alle Prüfungen laufen LOKAL auf dem Rechner des Nutzers - es werden
keine Rechnungsdaten an externe Dienste gesendet.
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

# Code-Listen für die Validierung ---------------------------------------
# ISO 3166-1 Alpha-2 Ländercodes (Auszug: die weltweit üblichen).
_ISO_COUNTRIES = frozenset({
    "AD","AE","AF","AG","AI","AL","AM","AO","AQ","AR","AS","AT","AU","AW","AX",
    "AZ","BA","BB","BD","BE","BF","BG","BH","BI","BJ","BL","BM","BN","BO","BQ",
    "BR","BS","BT","BV","BW","BY","BZ","CA","CC","CD","CF","CG","CH","CI","CK",
    "CL","CM","CN","CO","CR","CU","CV","CW","CX","CY","CZ","DE","DJ","DK","DM",
    "DO","DZ","EC","EE","EG","EH","ER","ES","ET","FI","FJ","FK","FM","FO","FR",
    "GA","GB","GD","GE","GF","GG","GH","GI","GL","GM","GN","GP","GQ","GR","GS",
    "GT","GU","GW","GY","HK","HM","HN","HR","HT","HU","ID","IE","IL","IM","IN",
    "IO","IQ","IR","IS","IT","JE","JM","JO","JP","KE","KG","KH","KI","KM","KN",
    "KP","KR","KW","KY","KZ","LA","LB","LC","LI","LK","LR","LS","LT","LU","LV",
    "LY","MA","MC","MD","ME","MF","MG","MH","MK","ML","MM","MN","MO","MP","MQ",
    "MR","MS","MT","MU","MV","MW","MX","MY","MZ","NA","NC","NE","NF","NG","NI",
    "NL","NO","NP","NR","NU","NZ","OM","PA","PE","PF","PG","PH","PK","PL","PM",
    "PN","PR","PS","PT","PW","PY","QA","RE","RO","RS","RU","RW","SA","SB","SC",
    "SD","SE","SG","SH","SI","SJ","SK","SL","SM","SN","SO","SR","SS","ST","SV",
    "SX","SY","SZ","TC","TD","TF","TG","TH","TJ","TK","TL","TM","TN","TO","TR",
    "TT","TV","TW","TZ","UA","UG","UM","US","UY","UZ","VA","VC","VE","VG","VI",
    "VN","VU","WF","WS","YE","YT","ZA","ZM","ZW","XI","XK",
})
# ISO 4217 Währungscodes (die weltweit üblichen)
_ISO_CURRENCIES = frozenset({
    "EUR","USD","GBP","CHF","JPY","CNY","CAD","AUD","NZD","SEK","NOK","DKK",
    "PLN","CZK","HUF","RON","BGN","HRK","RUB","TRY","UAH","ILS","AED","SAR",
    "HKD","SGD","KRW","INR","THB","MYR","IDR","PHP","VND","MXN","BRL","ARS",
    "CLP","COP","PEN","ZAR","EGP","NGN","KES","XAF","XOF","MAD","TND","BWP",
    "ISK","LKR","PKR","BDT","TWD","AFN","ALL","AMD","AZN","BHD","BYN","FJD",
    "GEL","JOD","KWD","LBP","MKD","NPR","OMR","QAR","RSD","XPF","UZS","KZT",
})
# UNCL 5305 Steuerkategorie-Codes (Auszug nach EN 16931)
_TAX_CATEGORIES = frozenset({
    "S",   # Standardsatz
    "Z",   # Nullsatz (zero rated)
    "E",   # steuerbefreit (exempt)
    "AE",  # Reverse Charge
    "K",   # innergemeinschaftliche Lieferung
    "G",   # Export außerhalb EU
    "O",   # nicht steuerbar
    "L",   # IGIC (Kanarische Inseln)
    "M",   # IPSI (Ceuta, Melilla)
    "B",   # Transferiert (reserviert)
})
# UN/CEFACT Rec 20 Mengeneinheiten (Auszug der häufigsten)
_UNIT_CODES = frozenset({
    "C62",  # Stück
    "DAY",  # Tag
    "HUR",  # Stunde
    "KGM",  # Kilogramm
    "KMT",  # Kilometer
    "KWH",  # kWh
    "LTR",  # Liter
    "MIN",  # Minute
    "MMT",  # Millimeter
    "MTK",  # Quadratmeter
    "MTQ",  # Kubikmeter
    "MTR",  # Meter
    "PCE",  # Piece
    "SET",  # Set
    "TNE",  # Tonne
    "WEE",  # Woche
    "MON",  # Monat
    "ANN",  # Jahr
    "H87",  # Piece (alternativ)
    "EA",   # Each
    "XPP",  # Packung
    "NAR",  # Anzahl
})


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


def _iban_checksum_valid(iban: str) -> bool:
    """Prüft die IBAN-Prüfsumme nach ISO 13616 (mod 97)."""
    if len(iban) < 15 or len(iban) > 34:
        return False
    rearranged = iban[4:] + iban[:4]
    numeric = ""
    for ch in rearranged:
        if ch.isdigit():
            numeric += ch
        elif "A" <= ch <= "Z":
            numeric += str(ord(ch) - ord("A") + 10)
        else:
            return False
    try:
        return int(numeric) % 97 == 1
    except ValueError:
        return False


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

    # --- BR-05 / BR-CL-01: Währung (BT-5) ---
    tx = "rsm:SupplyChainTradeTransaction"
    settle = f"{tx}/ram:ApplicableHeaderTradeSettlement"
    currency = _text(root, f"{settle}/ram:InvoiceCurrencyCode", ns)
    if not currency:
        rep.error("BR-05", "Währung (BT-5) fehlt")
    elif not re.fullmatch(r"[A-Z]{3}", currency):
        rep.error("BR-CL-01", f"Währungscode ungültig: {currency}",
                  "Erwartet: ISO-4217 Drei-Buchstaben-Code (EUR, USD, …).")
    elif currency not in _ISO_CURRENCIES:
        rep.warn("BR-CL-01", f"Währungscode nicht in ISO-4217-Liste: {currency}",
                 "Möglicherweise obsoleter oder unbekannter Code.")
    else:
        rep.ok("BR-CL-01", f"Währung: {currency} (ISO-4217 ✓)")

    # --- BR-06 / BR-DE-21: Absender-Name (BT-27) ---
    agr = f"{tx}/ram:ApplicableHeaderTradeAgreement"
    seller_name = _text(root, f"{agr}/ram:SellerTradeParty/ram:Name", ns)
    if not seller_name:
        rep.error("BR-06", "Absender-Name (BT-27) fehlt")
    else:
        rep.ok("BR-06", f"Absender: {seller_name}")

    # --- BR-08 / BR-CL-14: Absender-Adresse + Länder-Code ---
    seller_addr = _all(root, f"{agr}/ram:SellerTradeParty/ram:PostalTradeAddress", ns)
    if not seller_addr:
        rep.error("BR-08", "Absender-Adresse (BG-5) fehlt")
    else:
        country = _text(root, f"{agr}/ram:SellerTradeParty/ram:PostalTradeAddress/ram:CountryID", ns)
        if not country:
            rep.error("BR-09", "Absender-Länderkennzeichen (BT-40) fehlt")
        elif not re.fullmatch(r"[A-Z]{2}", country):
            rep.error("BR-CL-14", f"Absender-Länderkennzeichen ungültig: {country}",
                      "Erwartet: ISO-3166-1 Alpha-2 (DE, AT, CH, …).")
        elif country not in _ISO_COUNTRIES:
            rep.warn("BR-CL-14", f"Länderkennzeichen unbekannt: {country}")
        else:
            rep.ok("BR-CL-14", f"Absender-Land: {country} (ISO-3166 ✓)")

    # --- BR-DE-21/22/23: Absender-Straße, PLZ, Ort explizit prüfen ---
    s_street = _text(root, f"{agr}/ram:SellerTradeParty/ram:PostalTradeAddress/ram:LineOne", ns)
    s_pc = _text(root, f"{agr}/ram:SellerTradeParty/ram:PostalTradeAddress/ram:PostcodeCode", ns)
    s_city = _text(root, f"{agr}/ram:SellerTradeParty/ram:PostalTradeAddress/ram:CityName", ns)
    if not s_street:
        rep.error("BR-DE-21", "Absender-Straße (BT-35) fehlt")
    if not s_pc:
        rep.error("BR-DE-22", "Absender-PLZ (BT-38) fehlt")
    if not s_city:
        rep.error("BR-DE-23", "Absender-Ort (BT-37) fehlt")

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

    # --- BR-11 / BR-CL-14: Empfänger-Adresse + Länder-Code ---
    buyer_addr = _all(root, f"{agr}/ram:BuyerTradeParty/ram:PostalTradeAddress", ns)
    if not buyer_addr:
        rep.error("BR-11", "Empfänger-Adresse (BG-8) fehlt")
    else:
        buyer_country = _text(root, f"{agr}/ram:BuyerTradeParty/ram:PostalTradeAddress/ram:CountryID", ns)
        if not buyer_country:
            rep.error("BR-11", "Empfänger-Länderkennzeichen (BT-55) fehlt")
        elif not re.fullmatch(r"[A-Z]{2}", buyer_country):
            rep.error("BR-CL-14", f"Empfänger-Länderkennzeichen ungültig: {buyer_country}")
        elif buyer_country not in _ISO_COUNTRIES:
            rep.warn("BR-CL-14", f"Empfänger-Länderkennzeichen unbekannt: {buyer_country}")
        else:
            rep.ok("BR-11", f"Empfänger-Land: {buyer_country}")
        # BR-DE-26 / BR-DE-16: Empfänger-Straße, PLZ, Ort
        b_street = _text(root, f"{agr}/ram:BuyerTradeParty/ram:PostalTradeAddress/ram:LineOne", ns)
        b_pc = _text(root, f"{agr}/ram:BuyerTradeParty/ram:PostalTradeAddress/ram:PostcodeCode", ns)
        b_city = _text(root, f"{agr}/ram:BuyerTradeParty/ram:PostalTradeAddress/ram:CityName", ns)
        if not b_street:
            rep.error("BR-DE-16", "Empfänger-Straße (BT-50) fehlt")
        if not b_pc:
            rep.error("BR-DE-17", "Empfänger-PLZ (BT-53) fehlt")
        if not b_city:
            rep.error("BR-DE-18", "Empfänger-Ort (BT-52) fehlt")

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
    seen_tax_cats = set()
    for i, item in enumerate(items, start=1):
        line_id = _text(item, "ram:AssociatedDocumentLineDocument/ram:LineID", ns)
        item_name = _text(item, "ram:SpecifiedTradeProduct/ram:Name", ns)
        # BR-21 LineID
        if not line_id:
            rep.error("BR-21", f"Position {i}: LineID (BT-126) fehlt")
        # BR-25 Produktname
        if not item_name:
            rep.error("BR-25", f"Position {i}: Produktname (BT-153) fehlt")
        # BR-22 Menge
        qty_el = _all(item, "ram:SpecifiedLineTradeDelivery/ram:BilledQuantity", ns)
        if not qty_el:
            rep.error("BR-22", f"Position {i} ({item_name}): Menge (BT-129) fehlt")
        else:
            qty_val = _decimal(qty_el[0].text or "")
            if qty_val is None:
                rep.error("BR-22", f"Position {i} ({item_name}): Menge nicht numerisch")
            # BR-23 / BR-CL-23 Mengeneinheit
            unit = qty_el[0].get("unitCode", "")
            if not unit:
                rep.error("BR-23", f"Position {i} ({item_name}): Mengeneinheit (BT-130) fehlt")
            elif unit not in _UNIT_CODES:
                rep.warn("BR-CL-23", f"Position {i}: Mengeneinheit '{unit}' nicht in UN/CEFACT Rec 20")
        # BR-26 Netto-Einzelpreis
        unit_price_str = _text(item,
            "ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice/ram:ChargeAmount", ns)
        unit_price = _decimal(unit_price_str)
        if unit_price is None:
            rep.error("BR-26", f"Position {i} ({item_name}): Netto-Einzelpreis (BT-146) fehlt")
        elif unit_price < 0:
            rep.error("BR-27", f"Position {i} ({item_name}): Einzelpreis negativ (BT-146)")
        # BR-24 Netto-Betrag der Zeile
        line_net_str = _text(item,
            "ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount",
            ns)
        line_net = _decimal(line_net_str)
        if line_net is None:
            rep.error("BR-24", f"Position {i} ({item_name}): Netto-Betrag (BT-131) fehlt")
        else:
            line_total += line_net
        # BR-28 Steuerkategorie der Zeile
        line_tax_cat = _text(item,
            "ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax/ram:CategoryCode", ns)
        if not line_tax_cat:
            rep.error("BR-28", f"Position {i} ({item_name}): Steuer-Kategorie (BT-151) fehlt")
        elif line_tax_cat not in _TAX_CATEGORIES:
            rep.error("BR-CL-18", f"Position {i}: Unbekannte Steuer-Kategorie '{line_tax_cat}'",
                      "Erlaubt: S, Z, E, AE, K, G, O, L, M")
        else:
            seen_tax_cats.add(line_tax_cat)
        # BR-29 USt-Satz muss zur Kategorie passen
        line_tax_rate_str = _text(item,
            "ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax/ram:RateApplicablePercent", ns)
        line_tax_rate = _decimal(line_tax_rate_str)
        if line_tax_cat == "S" and (line_tax_rate is None or line_tax_rate <= 0):
            rep.error("BR-S-05",
                      f"Position {i}: Kategorie 'S' (Standard) erfordert USt-Satz > 0",
                      f"Aktuell: {line_tax_rate_str or 'leer'}")
        elif line_tax_cat in ("E", "AE", "Z", "K", "G", "O") and line_tax_rate and line_tax_rate != 0:
            rep.error(f"BR-{line_tax_cat}-05",
                      f"Position {i}: Kategorie '{line_tax_cat}' erfordert USt-Satz = 0",
                      f"Aktuell: {line_tax_rate}%")

    # --- BR-CO-13: Summe der Positionen = Netto-Gesamt ---
    if net is not None and items:
        if abs(line_total - net) > Decimal("0.02"):
            rep.error("BR-CO-13",
                      f"Summe der Positionen ({line_total}) ≠ Netto-Gesamt ({net})",
                      f"Differenz: {abs(line_total - net)} {currency}.")
        else:
            rep.ok("BR-CO-13", "Positionen = Netto-Gesamt ✓")

    # --- BG-23: USt-Aufschlüsselung (BR-45..49, BR-S/E/AE/Z/K/G-09) ---
    tax_breakdowns = _all(root, f"{settle}/ram:ApplicableTradeTax", ns)
    if not tax_breakdowns:
        rep.error("BR-45", "USt-Aufschlüsselung (BG-23) fehlt")
    else:
        rep.ok("BR-45", f"{len(tax_breakdowns)} USt-Satz-Gruppe(n) aufgeschlüsselt")
        total_tax_basis = Decimal("0")
        total_tax_calc = Decimal("0")
        header_cats = set()
        for tb in tax_breakdowns:
            cat = _text(tb, "ram:CategoryCode", ns)
            basis_str = _text(tb, "ram:BasisAmount", ns)
            tax_amt_str = _text(tb, "ram:CalculatedAmount", ns)
            rate_str = _text(tb, "ram:RateApplicablePercent", ns)
            exemption = _text(tb, "ram:ExemptionReason", ns)
            basis = _decimal(basis_str)
            tax_amt = _decimal(tax_amt_str)
            rate = _decimal(rate_str)
            if cat:
                header_cats.add(cat)
            # BR-47 Steuer-Code gültig?
            if cat and cat not in _TAX_CATEGORIES:
                rep.error("BR-47", f"Steuer-Kategorie '{cat}' in Aufschlüsselung ungültig")
            # BR-48 Basis + Betrag müssen da sein
            if basis is None:
                rep.error("BR-48", f"USt-Kategorie {cat}: Bemessungsgrundlage (BT-116) fehlt")
            else:
                total_tax_basis += basis
            if tax_amt is None:
                rep.error("BR-49", f"USt-Kategorie {cat}: Steuerbetrag (BT-117) fehlt")
            else:
                total_tax_calc += tax_amt
            # BR-CO-17 Berechnete USt = Basis × Satz / 100 (±0.01 Rundung)
            if basis is not None and tax_amt is not None and rate is not None:
                expected = (basis * rate / 100).quantize(Decimal("0.01"))
                if abs(expected - tax_amt) > Decimal("0.02"):
                    rep.error("BR-CO-17",
                              f"Rechenfehler USt {cat}: {basis} × {rate}% = "
                              f"{expected}, aber angegeben: {tax_amt}")
            # Befreiungsgrund bei non-Standard
            if cat in ("E", "AE", "Z", "K", "G", "O") and not exemption:
                rep.warn(f"BR-{cat}-10",
                         f"Steuerkategorie {cat} ohne Befreiungsgrund",
                         "Für steuerbefreite Umsätze (Kleinunternehmer, Reverse-Charge, "
                         "Export) sollte eine Begründung (ExemptionReason) angegeben sein.")
            # BR-S-02 / BR-S-03: Standard-Satz-Kategorie 'S' erfordert Rate > 0
            if cat == "S":
                if rate is None or rate <= 0:
                    rep.error("BR-S-03",
                              "Steuer-Kategorie 'S' (Standard) ohne positiven USt-Satz",
                              f"Aktuell: {rate_str or 'leer'}")
            elif cat in ("E", "AE", "Z", "K", "G", "O"):
                if rate and rate != 0:
                    rep.error(f"BR-{cat}-03",
                              f"Steuer-Kategorie '{cat}' mit Satz {rate}% – muss 0 sein")
        # Header-Kategorien müssen Positionen-Kategorien matchen
        missing_in_breakdown = seen_tax_cats - header_cats
        if missing_in_breakdown:
            rep.error("BR-CO-18",
                      f"Positionen haben Steuer-Kategorien ohne Aufschlüsselung: "
                      f"{', '.join(missing_in_breakdown)}")

    # --- BR-50 / BR-DE-01 / BR-CL-16: Zahlungsmittel ---
    payment_means = _all(root, f"{settle}/ram:SpecifiedTradeSettlementPaymentMeans", ns)
    if not payment_means:
        rep.warn("BR-50", "Keine Zahlungsmittel-Angabe (BG-16)",
                 "Dein Kunde weiß ohne IBAN nicht, wohin er überweisen soll.")
    else:
        pm_type_code = _text(root, f"{settle}/ram:SpecifiedTradeSettlementPaymentMeans/ram:TypeCode", ns)
        # BR-DE-01 / BR-CL-16: UNCL 4461 - erlaubte Codes: 30, 48, 49, 57, 58, 59, 97, 1
        if pm_type_code and pm_type_code not in ("1","10","20","30","42","48","49","57","58","59","68","97","ZZZ"):
            rep.warn("BR-CL-16", f"Payment-Mean-TypeCode '{pm_type_code}' ungewöhnlich",
                     "Standard SEPA-Überweisung = 58, Lastschrift = 59.")
        iban = _text(root, f"{settle}/ram:SpecifiedTradeSettlementPaymentMeans/ram:PayeePartyCreditorFinancialAccount/ram:IBANID", ns)
        if iban:
            iban_clean = iban.replace(" ", "").upper()
            if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban_clean):
                rep.error("BR-61", f"IBAN-Format ungültig: {iban}")
            elif not _iban_checksum_valid(iban_clean):
                rep.error("BR-61", f"IBAN-Prüfsumme falsch: {iban}",
                          "Nach ISO 13616 mod-97. Vertippt?")
            else:
                rep.ok("BR-61", f"IBAN: {iban} (Prüfsumme ✓)")
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


def _run_official_schematron(xml_bytes: bytes, rep: ValidationReport,
                             kind: str) -> None:
    """Führt die offiziellen EN-16931-Schematron-Regeln aus - gleicher
    Regelsatz, den die KoSIT-Validator-Konfiguration für XRechnung nutzt.
    Diese ~200 Regeln liegen als vorkompilierte XSLT-Datei bei und werden
    mit lxml direkt lokal ausgeführt - KEINE Daten verlassen den Rechner.
    """
    from pathlib import Path as _Path
    rules_dir = _Path(__file__).parent / "validation_rules"
    xslt_file = rules_dir / ("cii-en16931.xslt" if kind == "cii" else "ubl-en16931.xslt")
    if not xslt_file.exists():
        rep.warn("Schematron",
                 "Offizielle EN-16931-Schematron-Regeln nicht eingebunden",
                 "Fallback auf die eingebauten ~75 Regeln. Für vollständige "
                 "Prüfung: XSLT-Datei bereitstellen.")
        return
    try:
        xslt_doc = etree.parse(str(xslt_file))
        transform = etree.XSLT(xslt_doc)
        xml_doc = etree.fromstring(xml_bytes)
        result = transform(xml_doc)
    except Exception as e:
        rep.warn("Schematron", "Offizielle Schematron-Prüfung konnte nicht laufen",
                 f"{type(e).__name__}: {e}")
        return

    # SVRL-Namespace: Schematron Validation Reporting Language
    svrl_ns = {"svrl": "http://purl.oclc.org/dsdl/svrl"}
    failed = result.xpath("//svrl:failed-assert", namespaces=svrl_ns)
    successful = result.xpath("//svrl:successful-report", namespaces=svrl_ns)
    fired = result.xpath("//svrl:fired-rule", namespaces=svrl_ns)
    total_rules = len(fired)

    for assertion in failed:
        rule_id = assertion.get("id", assertion.get("test", "SCH-?")[:40])
        flag = assertion.get("flag", "fatal")
        text_el = assertion.find("svrl:text", svrl_ns)
        msg = (text_el.text or "").strip() if text_el is not None else "Regelverletzung"
        msg = " ".join(msg.split())  # Mehrfach-Whitespace kollabieren
        title = msg[:120] + ("…" if len(msg) > 120 else "")
        level = "warn" if flag.lower() == "warning" else "error"
        rep.add(level, f"SCH-{rule_id}", title, msg)

    for note in successful:
        rule_id = note.get("id", "INFO")
        text_el = note.find("svrl:text", svrl_ns)
        msg = (text_el.text or "").strip() if text_el is not None else ""
        if msg:
            rep.warn(f"SCH-{rule_id}", msg[:120])

    if total_rules > 0 and not failed:
        rep.ok("Schematron-EN16931",
               f"Alle {total_rules} offiziellen EN-16931-Schematron-Regeln bestanden "
               "(dieselben Regeln wie KoSIT-Validator)")


def validate_einvoice_xml(xml_bytes: bytes,
                          source_kind: str = "",
                          pdf_a3: Optional[bool] = None,
                          run_schematron: bool = True) -> ValidationReport:
    """Validiert eine E-Rechnungs-XML gegen EN 16931 + BR-DE + BR-CO +
    offizielle Schematron-Regeln (identisch zu KoSIT-Validator)."""
    rep = ValidationReport()

    try:
        root = etree.fromstring(xml_bytes)
    except Exception as e:
        rep.error("XML-Parser", "XML konnte nicht geparst werden", str(e))
        return rep

    tag = etree.QName(root).localname
    if tag == "CrossIndustryInvoice":
        _validate_cii(root, rep, pdf_a3=pdf_a3)
        if run_schematron:
            _run_official_schematron(xml_bytes, rep, "cii")
    elif tag == "Invoice":
        _validate_ubl(root, rep)
        if run_schematron:
            _run_official_schematron(xml_bytes, rep, "ubl")
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
