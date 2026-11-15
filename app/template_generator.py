"""Erzeugt eine frische, sauber gerenderte Jinja2-HTML-Rechnungsvorlage aus
einem ExtractedLayout. Keine Hintergrundbilder - alles echter, scharfer Text
und Vektor-Linien, die bei jeder Auflösung gleich gut aussehen.

Übernommen aus dem Original:
  - Primär- und Akzentfarbe
  - Schriftart und -größe
  - Logo (wird oben rechts eingesetzt)
  - Fußzeile mit Bankverbindung
"""

from __future__ import annotations

from .pdf_analyzer import ExtractedLayout


_TEMPLATE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Rechnung {{ inv.invoice_number }}</title>
<style>
  @page {
    size: A4;
    margin: 20mm 18mm 25mm 18mm;
    @bottom-center {
      content: element(pageFooter);
      font-size: 8pt;
      color: %MUTED_COLOR%;
    }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0;
               font-family: %FONT_FALLBACK%;
               font-size: %BASE_SIZE%pt; color: %TEXT_COLOR%; }

  .header { display: flex; justify-content: space-between; align-items: flex-start;
            margin-bottom: 14mm; }
  .header .seller { font-size: 9.5pt; line-height: 1.45; }
  .header .seller .name { color: %PRIMARY_COLOR%; font-weight: 600;
                          font-size: 11pt; margin-bottom: 1mm; }
  .header .logo { max-width: 55mm; max-height: 25mm; }
  .header .logo img { max-width: 100%; max-height: 25mm; object-fit: contain; }

  .buyer-line { font-size: 7.5pt; color: %MUTED_COLOR%;
                border-bottom: 0.2mm solid #d4d4d8;
                padding-bottom: 1.5mm; margin-bottom: 3mm; }
  .buyer { font-size: 10pt; line-height: 1.5; margin-bottom: 14mm; }
  .buyer .name { font-weight: 600; }

  h1 { color: %PRIMARY_COLOR%; font-size: %HEADING_SIZE%pt;
       margin: 0 0 5mm 0; font-weight: 600; }

  .meta { display: flex; justify-content: space-between;
          padding: 3mm 0; margin-bottom: 5mm;
          border-top: 0.3mm solid %PRIMARY_COLOR%;
          border-bottom: 0.3mm solid %PRIMARY_COLOR%; }
  .meta .block .label { color: %MUTED_COLOR%; font-size: 7.5pt;
                        text-transform: uppercase; letter-spacing: 0.4pt;
                        margin-bottom: 1mm; }
  .meta .block .value { font-size: 10pt; }

  table.items { width: 100%; border-collapse: collapse; margin-top: 2mm; }
  table.items thead th { background: %PRIMARY_COLOR%; color: #fff;
                         font-weight: 600; padding: 2.5mm 2.5mm;
                         text-align: left; font-size: 9pt; }
  table.items thead th.num { text-align: right; }
  table.items tbody td { padding: 2.5mm; border-bottom: 0.15mm solid #e5e7eb;
                         vertical-align: top; font-size: 9.5pt; }
  table.items tbody td.num { text-align: right; white-space: nowrap; }

  .totals { margin-top: 6mm; margin-left: auto; width: 68mm;
            font-size: 10pt; }
  .totals .row { display: flex; justify-content: space-between;
                 padding: 1.2mm 0; }
  .totals .row.grand { border-top: 0.6mm solid %PRIMARY_COLOR%;
                       font-weight: 700; font-size: 11.5pt; color: %PRIMARY_COLOR%;
                       margin-top: 2mm; padding-top: 2.5mm; }

  .notes { margin-top: 10mm; font-size: 9.5pt; line-height: 1.5; }

  .payment { margin-top: 8mm; padding: 4mm; border-radius: 1.5mm;
             background: #f6f7f9; border-left: 1.2mm solid %ACCENT_COLOR%;
             font-size: 9.5pt; line-height: 1.5;
             display: flex; gap: 6mm; align-items: center; }
  .payment .text { flex: 1; }
  .payment strong { color: %PRIMARY_COLOR%; }
  .payment .qr { text-align: center; }
  .payment .qr img { width: 26mm; height: 26mm; object-fit: contain; }
  .payment .qr .cap { font-size: 7pt; color: %MUTED_COLOR%; margin-top: 0.5mm; }

  .page-footer { position: running(pageFooter); text-align: center;
                 font-size: 7.5pt; color: %MUTED_COLOR%;
                 border-top: 0.2mm solid #d4d4d8; padding-top: 1.5mm;
                 line-height: 1.4; }
</style>
</head>
<body>

<div class="page-footer">
  {% if branding.footer_line_1 %}<div>{{ branding.footer_line_1 }}</div>{% endif %}
  {% if branding.footer_line_2 %}<div>{{ branding.footer_line_2 }}</div>{% endif %}
  {% if branding.footer_line_3 %}<div>{{ branding.footer_line_3 }}</div>{% endif %}
</div>

<div class="header">
  <div class="seller">
    <div class="name">{{ inv.seller_name }}</div>
    {{ inv.seller_street }}<br>
    {{ inv.seller_postcode }} {{ inv.seller_city }}<br>
    {{ inv.seller_email }}
  </div>
  {% if branding.logo_filename %}
  <div class="logo"><img src="{{ branding.logo_filename }}"></div>
  {% endif %}
</div>

<div class="buyer-line">
  {{ inv.seller_name }} &middot; {{ inv.seller_street }} &middot; {{ inv.seller_postcode }} {{ inv.seller_city }}
</div>

<div class="buyer">
  <div class="name">{{ inv.buyer_name }}</div>
  {{ inv.buyer_street }}<br>
  {{ inv.buyer_postcode }} {{ inv.buyer_city }}
  {% if inv.buyer_country and inv.buyer_country != "DE" %}<br>{{ inv.buyer_country }}{% endif %}
  {% if inv.buyer_tax_id %}<br><span style="color:%MUTED_COLOR%">USt-IdNr.: {{ inv.buyer_tax_id }}</span>{% endif %}
</div>

<h1>Rechnung {{ inv.invoice_number }}</h1>

<div class="meta">
  <div class="block">
    <div class="label">Rechnungsdatum</div>
    <div class="value">{{ inv.issue_date|date }}</div>
  </div>
  <div class="block">
    <div class="label">Leistungsdatum</div>
    <div class="value">{{ inv.delivery_date|date }}</div>
  </div>
  <div class="block">
    <div class="label">Fällig am</div>
    <div class="value">{{ inv.due_date|date }}</div>
  </div>
  {% if inv.buyer_reference %}
  <div class="block">
    <div class="label">Referenz</div>
    <div class="value">{{ inv.buyer_reference }}</div>
  </div>
  {% endif %}
</div>

<table class="items">
  <thead>
    <tr>
      <th style="width:8mm">#</th>
      <th>Beschreibung</th>
      <th class="num" style="width:16mm">Menge</th>
      <th class="num" style="width:22mm">Einzelpreis</th>
      <th class="num" style="width:12mm">USt %</th>
      <th class="num" style="width:26mm">Betrag</th>
    </tr>
  </thead>
  <tbody>
  {% for it in items %}
    <tr>
      <td>{{ it.position }}</td>
      <td>{{ it.description }}</td>
      <td class="num">{{ it.quantity|qty }}</td>
      <td class="num">{{ it.unit_price|money(inv.currency) }}</td>
      <td class="num">{{ '%.0f'|format(it.tax_rate) }} %</td>
      <td class="num">{{ it.net_amount|money(inv.currency) }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>

<div class="totals">
  <div class="row"><span>Netto</span><span>{{ inv.net_total|money(inv.currency) }}</span></div>
  <div class="row"><span>Umsatzsteuer</span><span>{{ inv.tax_total|money(inv.currency) }}</span></div>
  <div class="row grand"><span>Gesamt</span><span>{{ inv.gross_total|money(inv.currency) }}</span></div>
</div>

{% if inv.notes %}
<div class="notes"><strong>Hinweise:</strong><br>{{ inv.notes|replace('\n', '<br>')|safe }}</div>
{% endif %}

<div class="payment">
  <div class="text">
    <strong>Zahlungsbedingungen:</strong> {{ inv.payment_terms }}<br>
    <strong>Bankverbindung:</strong>
    {% if inv.seller_bank %}{{ inv.seller_bank }} &middot; {% endif %}
    IBAN {{ inv.seller_iban }}
    {% if inv.seller_bic %}&middot; BIC {{ inv.seller_bic }}{% endif %}<br>
    <strong>Verwendungszweck:</strong> {{ inv.invoice_number }}
  </div>
  {% if inv.qr_code_filename %}
  <div class="qr">
    <img src="{{ inv.qr_code_filename }}">
    <div class="cap">Mit Banking-App scannen</div>
  </div>
  {% endif %}
</div>

</body>
</html>
"""


def _fallback_chain_for(font_family: str, font_class: str) -> str:
    """Stilistisch passende Fallback-Kette für den Fall, dass die Wunsch-Font
    auf dem Zielrechner nicht installiert ist."""
    if font_class == "serif":
        return (f'"{font_family}", Georgia, "Times New Roman", Times, '
                f'"DejaVu Serif", serif')
    if font_class == "mono":
        return (f'"{font_family}", Menlo, Monaco, Consolas, '
                f'"DejaVu Sans Mono", monospace')
    return (f'"{font_family}", "Helvetica Neue", Helvetica, Arial, '
            f'"DejaVu Sans", sans-serif')


def generate_template(layout) -> str:
    """Nimmt ExtractedLayout und erzeugt eine frische, sauber gerenderte
    Jinja2-HTML-Vorlage. Keine Rastergrafiken - nur Vektortext und -linien."""
    muted = "#6b7280"
    replacements = {
        "%PRIMARY_COLOR%": layout.primary_color,
        "%ACCENT_COLOR%": layout.accent_color,
        "%TEXT_COLOR%": layout.text_color,
        "%MUTED_COLOR%": muted,
        "%FONT_FAMILY%": layout.font_family,
        "%FONT_FALLBACK%": _fallback_chain_for(layout.font_family, layout.font_class),
        "%BASE_SIZE%": f"{max(9, min(12, layout.base_font_size)):.1f}",
        "%HEADING_SIZE%": f"{max(14, min(22, layout.heading_font_size)):.1f}",
    }
    html = _TEMPLATE
    for k, v in replacements.items():
        html = html.replace(k, v)
    return html
