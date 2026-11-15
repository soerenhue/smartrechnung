"""Erzeugt EN-16931-konformes ZUGFeRD-XML (Profil EN 16931 / "Comfort") und
bettet es per factur-x in das von WeasyPrint erzeugte PDF ein.

Das Ergebnis ist eine Hybrid-Datei: fuer Menschen ein normales PDF, fuer
Maschinen eine strukturierte E-Rechnung im Sinne des deutschen Umsatzsteuer-
gesetzes (§ 14 UStG) und der EU-Norm EN 16931.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from facturx import generate_from_binary

from .models import Invoice


def _fmt(d: Decimal) -> str:
    return f"{Decimal(d):.2f}"


def _fmt4(d: Decimal) -> str:
    return f"{Decimal(d):.4f}"


def _date(d) -> str:
    return d.strftime("%Y%m%d")


def build_xml(inv: Invoice) -> bytes:
    """Baut eine CII-XML (Cross Industry Invoice, UN/CEFACT) im ZUGFeRD-Profil
    EN 16931. factur-x akzeptiert dieses XML und bettet es als 'factur-x.xml' ein."""

    items_xml = []
    tax_groups: dict[str, dict] = {}
    for i, it in enumerate(inv.items, start=1):
        rate_key = _fmt(it.tax_rate)
        grp = tax_groups.setdefault(rate_key, {"rate": it.tax_rate, "basis": Decimal("0"), "tax": Decimal("0")})
        grp["basis"] += it.net_amount
        grp["tax"] += it.tax_amount

        items_xml.append(f"""
  <ram:IncludedSupplyChainTradeLineItem>
    <ram:AssociatedDocumentLineDocument>
      <ram:LineID>{i}</ram:LineID>
    </ram:AssociatedDocumentLineDocument>
    <ram:SpecifiedTradeProduct>
      <ram:Name>{escape(it.description)}</ram:Name>
    </ram:SpecifiedTradeProduct>
    <ram:SpecifiedLineTradeAgreement>
      <ram:NetPriceProductTradePrice>
        <ram:ChargeAmount>{_fmt4(it.unit_price)}</ram:ChargeAmount>
      </ram:NetPriceProductTradePrice>
    </ram:SpecifiedLineTradeAgreement>
    <ram:SpecifiedLineTradeDelivery>
      <ram:BilledQuantity unitCode="{escape(it.unit)}">{_fmt4(it.quantity)}</ram:BilledQuantity>
    </ram:SpecifiedLineTradeDelivery>
    <ram:SpecifiedLineTradeSettlement>
      <ram:ApplicableTradeTax>
        <ram:TypeCode>VAT</ram:TypeCode>
        <ram:CategoryCode>S</ram:CategoryCode>
        <ram:RateApplicablePercent>{_fmt(it.tax_rate)}</ram:RateApplicablePercent>
      </ram:ApplicableTradeTax>
      <ram:SpecifiedTradeSettlementLineMonetarySummation>
        <ram:LineTotalAmount>{_fmt(it.net_amount)}</ram:LineTotalAmount>
      </ram:SpecifiedTradeSettlementLineMonetarySummation>
    </ram:SpecifiedLineTradeSettlement>
  </ram:IncludedSupplyChainTradeLineItem>""")

    tax_xml = []
    for rate_key, grp in sorted(tax_groups.items()):
        tax_xml.append(f"""
      <ram:ApplicableTradeTax>
        <ram:CalculatedAmount>{_fmt(grp['tax'])}</ram:CalculatedAmount>
        <ram:TypeCode>VAT</ram:TypeCode>
        <ram:BasisAmount>{_fmt(grp['basis'])}</ram:BasisAmount>
        <ram:CategoryCode>S</ram:CategoryCode>
        <ram:RateApplicablePercent>{_fmt(grp['rate'])}</ram:RateApplicablePercent>
      </ram:ApplicableTradeTax>""")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
  xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
  xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
  xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocumentContext>
    <ram:GuidelineSpecifiedDocumentContextParameter>
      <ram:ID>urn:cen.eu:en16931:2017</ram:ID>
    </ram:GuidelineSpecifiedDocumentContextParameter>
  </rsm:ExchangedDocumentContext>
  <rsm:ExchangedDocument>
    <ram:ID>{escape(inv.invoice_number)}</ram:ID>
    <ram:TypeCode>380</ram:TypeCode>
    <ram:IssueDateTime>
      <udt:DateTimeString format="102">{_date(inv.issue_date)}</udt:DateTimeString>
    </ram:IssueDateTime>
    {f'<ram:IncludedNote><ram:Content>{escape(inv.notes)}</ram:Content></ram:IncludedNote>' if inv.notes else ''}
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    {''.join(items_xml)}
    <ram:ApplicableHeaderTradeAgreement>
      {f'<ram:BuyerReference>{escape(inv.buyer_reference)}</ram:BuyerReference>' if inv.buyer_reference else ''}
      <ram:SellerTradeParty>
        <ram:Name>{escape(inv.seller_name)}</ram:Name>
        <ram:PostalTradeAddress>
          <ram:PostcodeCode>{escape(inv.seller_postcode)}</ram:PostcodeCode>
          <ram:LineOne>{escape(inv.seller_street)}</ram:LineOne>
          <ram:CityName>{escape(inv.seller_city)}</ram:CityName>
          <ram:CountryID>{escape(inv.seller_country)}</ram:CountryID>
        </ram:PostalTradeAddress>
        <ram:URIUniversalCommunication>
          <ram:URIID schemeID="EM">{escape(inv.seller_email)}</ram:URIID>
        </ram:URIUniversalCommunication>
        <ram:SpecifiedTaxRegistration>
          <ram:ID schemeID="VA">{escape(inv.seller_tax_id)}</ram:ID>
        </ram:SpecifiedTaxRegistration>
        {f'<ram:SpecifiedTaxRegistration><ram:ID schemeID="FC">{escape(inv.seller_tax_number)}</ram:ID></ram:SpecifiedTaxRegistration>' if inv.seller_tax_number else ''}
      </ram:SellerTradeParty>
      <ram:BuyerTradeParty>
        <ram:Name>{escape(inv.buyer_name)}</ram:Name>
        <ram:PostalTradeAddress>
          <ram:PostcodeCode>{escape(inv.buyer_postcode)}</ram:PostcodeCode>
          <ram:LineOne>{escape(inv.buyer_street)}</ram:LineOne>
          <ram:CityName>{escape(inv.buyer_city)}</ram:CityName>
          <ram:CountryID>{escape(inv.buyer_country)}</ram:CountryID>
        </ram:PostalTradeAddress>
        {f'<ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">{escape(inv.buyer_tax_id)}</ram:ID></ram:SpecifiedTaxRegistration>' if inv.buyer_tax_id else ''}
      </ram:BuyerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeDelivery>
      <ram:ActualDeliverySupplyChainEvent>
        <ram:OccurrenceDateTime>
          <udt:DateTimeString format="102">{_date(inv.delivery_date)}</udt:DateTimeString>
        </ram:OccurrenceDateTime>
      </ram:ActualDeliverySupplyChainEvent>
    </ram:ApplicableHeaderTradeDelivery>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode>{escape(inv.currency)}</ram:InvoiceCurrencyCode>
      <ram:SpecifiedTradeSettlementPaymentMeans>
        <ram:TypeCode>58</ram:TypeCode>
        <ram:PayeePartyCreditorFinancialAccount>
          <ram:IBANID>{escape(inv.seller_iban.replace(' ', ''))}</ram:IBANID>
        </ram:PayeePartyCreditorFinancialAccount>
        {f'<ram:PayeeSpecifiedCreditorFinancialInstitution><ram:BICID>{escape(inv.seller_bic)}</ram:BICID></ram:PayeeSpecifiedCreditorFinancialInstitution>' if inv.seller_bic else ''}
      </ram:SpecifiedTradeSettlementPaymentMeans>
      {''.join(tax_xml)}
      <ram:SpecifiedTradePaymentTerms>
        <ram:Description>{escape(inv.payment_terms)}</ram:Description>
        <ram:DueDateDateTime>
          <udt:DateTimeString format="102">{_date(inv.due_date)}</udt:DateTimeString>
        </ram:DueDateDateTime>
      </ram:SpecifiedTradePaymentTerms>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:LineTotalAmount>{_fmt(inv.net_total)}</ram:LineTotalAmount>
        <ram:TaxBasisTotalAmount>{_fmt(inv.net_total)}</ram:TaxBasisTotalAmount>
        <ram:TaxTotalAmount currencyID="{escape(inv.currency)}">{_fmt(inv.tax_total)}</ram:TaxTotalAmount>
        <ram:GrandTotalAmount>{_fmt(inv.gross_total)}</ram:GrandTotalAmount>
        <ram:DuePayableAmount>{_fmt(inv.gross_total)}</ram:DuePayableAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
"""
    return xml.encode("utf-8")


def embed_xml_in_pdf(pdf_bytes: bytes, xml_bytes: bytes, out_path: Path) -> None:
    """Bettet das ZUGFeRD-XML als 'factur-x.xml' in das PDF ein und schreibt
    das Ergebnis als PDF/A-3 nach out_path."""
    out = BytesIO()
    generate_from_binary(
        pdf_bytes,
        xml_bytes,
        output_pdf=out,
        afrelationship="Data",
        attachment_filename="factur-x.xml",
        flavor="factur-x",
        level="en16931",
    )
    out_path.write_bytes(out.getvalue())
