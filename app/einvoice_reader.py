"""Liest und verifiziert eingehende E-Rechnungen.

Unterstützt:
  * ZUGFeRD / Factur-X Hybrid-PDFs (XML als Anhang im PDF)
  * Reine XRechnung / UBL / CII XML-Dateien

Extrahiert daraus die wichtigsten Felder und validiert gegen EN-16931-Minimum.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from lxml import etree


@dataclass
class ReadInvoice:
    source_kind: str = ""          # "pdf-hybrid" | "xml-pure"
    is_valid: bool = False
    issues: list[str] = field(default_factory=list)
    xml_bytes: Optional[bytes] = None
    # Extrahierte Felder
    invoice_number: str = ""
    issue_date: str = ""
    seller_name: str = ""
    buyer_name: str = ""
    currency: str = "EUR"
    net_total: str = ""
    tax_total: str = ""
    gross_total: str = ""
    iban: str = ""
    format_profile: str = ""  # z.B. "urn:cen.eu:en16931:2017" oder "XRechnung 3.0"


# Namespaces für UN/CEFACT Cross Industry Invoice (ZUGFeRD / Factur-X)
_NS_CII = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}

# UBL (XRechnung alternativ)
_NS_UBL = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


def extract_xml_from_pdf(pdf_bytes: bytes) -> Optional[bytes]:
    """Holt die eingebettete XML-Datei (factur-x.xml / zugferd-invoice.xml)
    aus einem PDF/A-3 heraus. Gibt None zurück, wenn keine drin ist."""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for i in range(doc.embfile_count()):
                name = doc.embfile_info(i).get("filename", "").lower()
                if name in {"factur-x.xml", "zugferd-invoice.xml",
                            "xrechnung.xml", "order-x.xml"}:
                    return doc.embfile_get(i)
            # Fallback: erste eingebettete XML-Datei
            for i in range(doc.embfile_count()):
                name = doc.embfile_info(i).get("filename", "").lower()
                if name.endswith(".xml"):
                    return doc.embfile_get(i)
    except Exception:
        pass
    return None


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


def parse_xml(xml_bytes: bytes) -> ReadInvoice:
    """Parst eine EN-16931-XML (CII oder UBL) und extrahiert die Kerndaten."""
    result = ReadInvoice(xml_bytes=xml_bytes)
    try:
        root = etree.fromstring(xml_bytes)
    except Exception as e:
        result.issues.append(f"XML konnte nicht geparst werden: {e}")
        return result

    tag = etree.QName(root).localname

    if tag == "CrossIndustryInvoice":
        # ZUGFeRD / Factur-X
        ns = _NS_CII
        result.format_profile = _text(
            root,
            "rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID",
            ns) or "ZUGFeRD / Factur-X"
        result.invoice_number = _text(root, "rsm:ExchangedDocument/ram:ID", ns)
        result.issue_date = _text(
            root,
            "rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString", ns)
        tx = "rsm:SupplyChainTradeTransaction"
        agr = f"{tx}/ram:ApplicableHeaderTradeAgreement"
        result.seller_name = _text(root, f"{agr}/ram:SellerTradeParty/ram:Name", ns)
        result.buyer_name = _text(root, f"{agr}/ram:BuyerTradeParty/ram:Name", ns)
        settle = f"{tx}/ram:ApplicableHeaderTradeSettlement"
        result.currency = _text(root, f"{settle}/ram:InvoiceCurrencyCode", ns) or "EUR"
        sums = f"{settle}/ram:SpecifiedTradeSettlementHeaderMonetarySummation"
        result.net_total = _text(root, f"{sums}/ram:LineTotalAmount", ns)
        result.tax_total = _text(root, f"{sums}/ram:TaxTotalAmount", ns)
        result.gross_total = _text(root, f"{sums}/ram:GrandTotalAmount", ns)
        result.iban = _text(
            root,
            f"{settle}/ram:SpecifiedTradeSettlementPaymentMeans/ram:PayeePartyCreditorFinancialAccount/ram:IBANID",
            ns)
    elif tag == "Invoice":
        # UBL (XRechnung)
        ns = _NS_UBL
        result.format_profile = _text(root, "cbc:CustomizationID", ns) or "UBL / XRechnung"
        result.invoice_number = _text(root, "cbc:ID", ns)
        result.issue_date = _text(root, "cbc:IssueDate", ns)
        result.seller_name = _text(root, "cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName", ns) \
            or _text(root, "cac:AccountingSupplierParty/cac:Party/cac:PartyName/cbc:Name", ns)
        result.buyer_name = _text(root, "cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName", ns) \
            or _text(root, "cac:AccountingCustomerParty/cac:Party/cac:PartyName/cbc:Name", ns)
        result.currency = _text(root, "cbc:DocumentCurrencyCode", ns) or "EUR"
        result.net_total = _text(root, "cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount", ns)
        result.tax_total = _text(root, "cac:TaxTotal/cbc:TaxAmount", ns)
        result.gross_total = _text(root, "cac:LegalMonetaryTotal/cbc:PayableAmount", ns)
        result.iban = _text(root, "cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID", ns)
    else:
        result.issues.append(
            f"Unbekanntes XML-Wurzelelement: {tag}. "
            "Nur ZUGFeRD/Factur-X (CrossIndustryInvoice) und UBL/XRechnung "
            "(Invoice) werden unterstützt."
        )
        return result

    # Minimale Pflichtfelder-Prüfung
    missing = []
    if not result.invoice_number: missing.append("Rechnungsnummer")
    if not result.issue_date: missing.append("Rechnungsdatum")
    if not result.seller_name: missing.append("Absender")
    if not result.buyer_name: missing.append("Empfänger")
    if not result.gross_total: missing.append("Gesamtbetrag")
    if missing:
        result.issues.append("Pflichtfelder fehlen: " + ", ".join(missing))
    else:
        result.is_valid = True

    return result


def read_einvoice(raw: bytes) -> ReadInvoice:
    """Nimmt Bytes einer Datei und erkennt automatisch: PDF-Hybrid oder reines XML."""
    if raw.startswith(b"%PDF"):
        xml = extract_xml_from_pdf(raw)
        if xml is None:
            result = ReadInvoice(source_kind="pdf-no-xml")
            result.issues.append(
                "Das PDF enthält keine eingebettete XML-Datei. "
                "Es ist damit KEINE E-Rechnung nach EN 16931, sondern nur ein "
                "normales PDF."
            )
            return result
        result = parse_xml(xml)
        result.source_kind = "pdf-hybrid"
        return result

    # Sonst: wahrscheinlich reines XML
    stripped = raw.lstrip()
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<"):
        result = parse_xml(raw)
        result.source_kind = "xml-pure"
        return result

    result = ReadInvoice(source_kind="unknown")
    result.issues.append(
        "Dateiformat nicht erkannt. Erwartet: PDF (Hybrid-E-Rechnung) "
        "oder reine XML-Datei (XRechnung, ZUGFeRD-XML, UBL)."
    )
    return result
