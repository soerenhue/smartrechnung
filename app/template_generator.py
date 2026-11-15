"""Erzeugt eine Jinja2-HTML-Rechnungsvorlage aus einem ExtractedLayout."""

from __future__ import annotations

from .pdf_analyzer import ExtractedLayout


_TEMPLATE_HEAD = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Rechnung {{ inv.invoice_number }}</title>
<style>
  @page {
    size: A4;
    margin: 18mm 15mm 22mm 15mm;
    @bottom-center {
      content: element(pageFooter);
      font-size: 7.5pt;
      color: %MUTED_COLOR%;
    }
    @bottom-right { content: "Seite " counter(page) " / " counter(pages);
                    font-size: 7.5pt; color: %MUTED_COLOR%; }
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0;
               font-family: "%FONT_FAMILY%", DejaVu Sans, sans-serif;
               font-size: %BASE_SIZE%pt; color: %TEXT_COLOR%; }
  h1 { color: %PRIMARY_COLOR%; font-size: %HEADING_SIZE%pt;
       margin: 0 0 4mm 0; font-weight: 600; }

  .seller-address { font-size: 9pt; line-height: 1.4; }
  .seller-address strong { color: %PRIMARY_COLOR%; font-size: 10.5pt; }

  .logo-block { position: absolute; top: %LOGO_Y%%; left: %LOGO_X%%;
                width: %LOGO_W%%; max-height: 32mm; }
  .logo-block img { max-width: 100%; max-height: 32mm; object-fit: contain; }

  .buyer { margin-top: %BUYER_MT%mm; font-size: 10pt; line-height: 1.45; }
  .buyer-sender-line { color: %MUTED_COLOR%; font-size: 7.5pt;
                       border-bottom: 0.2mm solid #d4d4d8; padding-bottom: 1mm; margin-bottom: 2mm; }

  .meta { display: flex; justify-content: space-between; margin: 4mm 0 2mm 0;
          padding: 3mm 0; border-top: 0.3mm solid %PRIMARY_COLOR%;
          border-bottom: 0.3mm solid %PRIMARY_COLOR%; }
  .meta .block { }
  .meta .label { color: %MUTED_COLOR%; font-size: 7.5pt;
                 text-transform: uppercase; letter-spacing: 0.5pt; }

  table.items { width: 100%; border-collapse: collapse; margin-top: 4mm; }
  table.items thead th { background: %PRIMARY_COLOR%; color: #fff; font-weight: 600;
                         padding: 2.2mm; text-align: left; font-size: 9pt; }
  table.items thead th.num { text-align: right; }
  table.items tbody td { padding: 2.2mm; border-bottom: 0.2mm solid #e5e7eb;
                         vertical-align: top; font-size: 9.5pt; }
  table.items tbody td.num { text-align: right; white-space: nowrap; }

  .totals { margin-top: 5mm; margin-left: auto; width: 65mm; }
  .totals .row { display: flex; justify-content: space-between; padding: 1.2mm 0; }
  .totals .row.grand { border-top: 0.5mm solid %PRIMARY_COLOR%;
                       font-weight: 700; font-size: 11pt;
                       margin-top: 2mm; padding-top: 2mm; color: %PRIMARY_COLOR%; }

  .notes { margin-top: 8mm; font-size: 9.5pt; }
  .payment { margin-top: 6mm; background: #f6f6f6; padding: 3.5mm;
             border-left: 1mm solid %ACCENT_COLOR%; font-size: 9pt; }
  .payment strong { color: %PRIMARY_COLOR%; }

  .page-footer { position: running(pageFooter); text-align: center; font-size: 7.5pt;
                 color: %MUTED_COLOR%; border-top: 0.2mm solid #d4d4d8;
                 padding-top: 1.5mm; line-height: 1.3; }
</style>
</head>
<body>

<div class="page-footer">
  {% if branding.footer_line_1 %}<div>{{ branding.footer_line_1 }}</div>{% endif %}
  {% if branding.footer_line_2 %}<div>{{ branding.footer_line_2 }}</div>{% endif %}
  {% if branding.footer_line_3 %}<div>{{ branding.footer_line_3 }}</div>{% endif %}
</div>

<div class="seller-address">
  <strong>{{ inv.seller_name }}</strong><br>
  {{ inv.seller_street }}<br>
  {{ inv.seller_postcode }} {{ inv.seller_city }}<br>
  {{ inv.seller_email }}
</div>

{% if branding.logo_filename %}
<div class="logo-block">
  <img src="{{ branding.logo_filename }}">
</div>
{% endif %}

<div class="buyer">
  <div class="buyer-sender-line">
    {{ inv.seller_name }} &middot; {{ inv.seller_street }} &middot; {{ inv.seller_postcode }} {{ inv.seller_city }}
  </div>
  <strong>{{ inv.buyer_name }}</strong><br>
  {{ inv.buyer_street }}<br>
  {{ inv.buyer_postcode }} {{ inv.buyer_city }}<br>
  {% if inv.buyer_country and inv.buyer_country != "DE" %}{{ inv.buyer_country }}<br>{% endif %}
  {% if inv.buyer_tax_id %}<span style="color:%MUTED_COLOR%">USt-IdNr.: {{ inv.buyer_tax_id }}</span>{% endif %}
</div>

<h1 style="margin-top:12mm">Rechnung {{ inv.invoice_number }}</h1>

<div class="meta">
  <div class="block"><div class="label">Rechnungsdatum</div><div>{{ inv.issue_date|date }}</div></div>
  <div class="block"><div class="label">Leistungsdatum</div><div>{{ inv.delivery_date|date }}</div></div>
  <div class="block"><div class="label">Faellig am</div><div>{{ inv.due_date|date }}</div></div>
  {% if inv.buyer_reference %}
  <div class="block"><div class="label">Referenz</div><div>{{ inv.buyer_reference }}</div></div>
  {% endif %}
</div>

<table class="items">
  <thead>
    <tr>
      <th style="width:7mm">#</th>
      <th>Beschreibung</th>
      <th class="num" style="width:16mm">Menge</th>
      <th class="num" style="width:22mm">Einzelpreis</th>
      <th class="num" style="width:12mm">USt%</th>
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
  <strong>Zahlungsbedingungen:</strong> {{ inv.payment_terms }}<br>
  <strong>Bankverbindung:</strong>
  {% if inv.seller_bank %}{{ inv.seller_bank }} &middot; {% endif %}
  IBAN {{ inv.seller_iban }}
  {% if inv.seller_bic %}&middot; BIC {{ inv.seller_bic }}{% endif %}<br>
  <strong>Verwendungszweck:</strong> {{ inv.invoice_number }}
</div>

</body>
</html>
"""


def generate_template(layout) -> str:
    """Nimmt ExtractedLayout und erzeugt eine fertige Jinja2-HTML-Vorlage als String."""
    muted = "#6b7280"
    replacements = {
        "%PRIMARY_COLOR%": layout.primary_color,
        "%ACCENT_COLOR%": layout.accent_color,
        "%TEXT_COLOR%": layout.text_color,
        "%MUTED_COLOR%": muted,
        "%FONT_FAMILY%": layout.font_family,
        "%BASE_SIZE%": f"{max(9, min(12, layout.base_font_size)):.1f}",
        "%HEADING_SIZE%": f"{max(14, min(24, layout.heading_font_size)):.1f}",
        "%LOGO_X%": f"{max(0, min(85, layout.logo_x_pct)):.1f}",
        "%LOGO_Y%": f"{max(0, min(20, layout.logo_y_pct)):.1f}",
        "%LOGO_W%": f"{max(10, min(40, layout.logo_width_pct)):.1f}",
        "%BUYER_MT%": f"{max(4, min(25, (layout.buyer_y_pct - 10) * 0.5)):.1f}",
    }
    html = _TEMPLATE_HEAD
    for k, v in replacements.items():
        html = html.replace(k, v)
    return html
