"""Erzeugt eine 1:1-Overlay-Vorlage: Original-PDF als scharfer 300-DPI-
Hintergrund, weiße Boxen überdecken die alten Rechnungsdaten, neue Daten
werden als scharfer Vektortext an exakt den Positionen reingezeichnet.

Ergebnis: Sieht genau wie das Original aus (Logo, Rahmen, Farben, Fußzeile),
aber mit den neuen aktuellen Daten als scharfer Text.
"""

from __future__ import annotations

from .pdf_analyzer import ExtractedLayout, FieldBox


def _fallback_chain_for(font_family: str, font_class: str) -> str:
    if font_class == "serif":
        return (f'"{font_family}", Georgia, "Times New Roman", Times, '
                f'"DejaVu Serif", serif')
    if font_class == "mono":
        return (f'"{font_family}", Menlo, Monaco, Consolas, '
                f'"DejaVu Sans Mono", monospace')
    return (f'"{font_family}", "Helvetica Neue", Helvetica, Arial, '
            f'"DejaVu Sans", sans-serif')


def _box_css(box: FieldBox | None, fallback: dict) -> str:
    """CSS-Positions-String für eine Box (in Prozent der A4-Seite)."""
    if box is None:
        b = fallback
    else:
        b = {"top": box.y_pct, "left": box.x_pct,
             "width": box.width_pct, "height": box.height_pct}
    return (f"top: {b['top']:.2f}%; left: {b['left']:.2f}%; "
            f"width: {b['width']:.2f}%; height: {b['height']:.2f}%;")


_TEMPLATE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Rechnung {{ inv.invoice_number }}</title>
<style>
  @page { size: A4; margin: 0; }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0;
               font-family: %FONT_FALLBACK%;
               color: %TEXT_COLOR%; font-size: %BASE_SIZE%pt; }

  /* Seite in A4-Dimensionen - Hintergrund ist das Original-PDF */
  .page {
    position: relative;
    width: 210mm;
    height: 297mm;
    background-image: url("vorlage-hintergrund.png");
    background-size: 210mm 297mm;
    background-repeat: no-repeat;
    background-position: 0 0;
  }

  /* Overlay ist transparent - das Original wurde vor dem Rendern bereits
     von alten Textdaten befreit (Redaction in pdf_analyzer). Dadurch
     bleiben farbige Flächen/Logos/Rahmen aus dem Original sichtbar. */
  .overlay {
    position: absolute;
    padding: 1mm;
    background: transparent;
  }
  /* Textcontent in den Overlays - scharfer Vektortext */
  .overlay .content {
    width: 100%; height: 100%;
    line-height: 1.35;
  }
  .overlay.buyer .content { font-size: 10pt; }
  .overlay.buyer strong { font-weight: 600; }

  .overlay.meta .content { font-size: 9.5pt; text-align: right; }
  .overlay.meta .label { color: %MUTED_COLOR%; font-size: 7.5pt;
                         text-transform: uppercase; letter-spacing: 0.3pt; }

  .overlay.items .content { font-size: 9.5pt; }
  .overlay.items table { width: 100%; border-collapse: collapse; }
  .overlay.items th { background: %PRIMARY_COLOR%; color: white; padding: 2mm;
                      text-align: left; font-size: 9pt; font-weight: 600; }
  .overlay.items th.num { text-align: right; }
  .overlay.items td { padding: 2mm; border-bottom: 0.15mm solid #ddd;
                      vertical-align: top; }
  .overlay.items td.num { text-align: right; white-space: nowrap; }

  .overlay.totals .content { font-size: 10pt; }
  .overlay.totals .row { display: flex; justify-content: space-between;
                         padding: 1mm 0; }
  .overlay.totals .row.grand { border-top: 0.5mm solid %PRIMARY_COLOR%;
                               font-weight: 700; color: %PRIMARY_COLOR%;
                               font-size: 11pt; padding-top: 1.5mm; margin-top: 1mm; }

  .overlay.payment .content { font-size: 9pt; display: flex; gap: 4mm;
                              align-items: center; }
  .overlay.payment .text { flex: 1; line-height: 1.4; }
  .overlay.payment strong { color: %PRIMARY_COLOR%; }
  .overlay.payment .qr { text-align: center; }
  .overlay.payment .qr img { width: 24mm; height: 24mm; object-fit: contain; }
  .overlay.payment .qr .cap { font-size: 6.5pt; color: %MUTED_COLOR%; }
</style>
</head>
<body>

<div class="page">

  <div class="overlay buyer" style="%BUYER_CSS%">
    <div class="content">
      <strong>{{ inv.buyer_name }}</strong><br>
      {{ inv.buyer_street }}<br>
      {{ inv.buyer_postcode }} {{ inv.buyer_city }}
      {% if inv.buyer_country and inv.buyer_country != "DE" %}<br>{{ inv.buyer_country }}{% endif %}
      {% if inv.buyer_tax_id %}<br><span style="color:%MUTED_COLOR%">USt-IdNr.: {{ inv.buyer_tax_id }}</span>{% endif %}
    </div>
  </div>

  <div class="overlay meta" style="%META_CSS%">
    <div class="content">
      <div><span class="label">Rechnungs-Nr.</span> {{ inv.invoice_number }}</div>
      <div><span class="label">Datum</span> {{ inv.issue_date|date }}</div>
      <div><span class="label">Leistung</span> {{ inv.delivery_date|date }}</div>
      <div><span class="label">Fällig</span> {{ inv.due_date|date }}</div>
    </div>
  </div>

  <div class="overlay items" style="%ITEMS_CSS%">
    <div class="content">
      <table>
        <thead><tr>
          <th style="width:8mm">#</th>
          <th>Beschreibung</th>
          <th class="num" style="width:16mm">Menge</th>
          <th class="num" style="width:22mm">Einzelpreis</th>
          <th class="num" style="width:12mm">USt %</th>
          <th class="num" style="width:26mm">Betrag</th>
        </tr></thead>
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
    </div>
  </div>

  <div class="overlay totals" style="%TOTALS_CSS%">
    <div class="content">
      <div class="row"><span>Netto</span><span>{{ inv.net_total|money(inv.currency) }}</span></div>
      <div class="row"><span>Umsatzsteuer</span><span>{{ inv.tax_total|money(inv.currency) }}</span></div>
      <div class="row grand"><span>Gesamt</span><span>{{ inv.gross_total|money(inv.currency) }}</span></div>
    </div>
  </div>

  <div class="overlay payment" style="%PAYMENT_CSS%">
    <div class="content">
      <div class="text">
        <strong>Zahlbar bis {{ inv.due_date|date }}</strong> auf
        {% if inv.seller_bank %}{{ inv.seller_bank }}, {% endif %}
        IBAN {{ inv.seller_iban }}
        {% if inv.seller_bic %} (BIC {{ inv.seller_bic }}){% endif %}.<br>
        Verwendungszweck: <strong>{{ inv.invoice_number }}</strong>.
      </div>
      {% if inv.qr_code_filename %}
      <div class="qr">
        <img src="{{ inv.qr_code_filename }}">
        <div class="cap">Scannen &amp; bezahlen</div>
      </div>
      {% endif %}
    </div>
  </div>

  <div style="position: absolute; bottom: 2mm; left: 0; right: 0;
              text-align: center; font-size: 6pt; color: %MUTED_COLOR%;
              opacity: 0.75">
    Erstellt mit SmartRechnung &middot; Made by Sören Hülsemeyer
  </div>

</div>

</body>
</html>
"""


def generate_template(layout) -> str:
    """Erzeugt das Overlay-Template. Jede Box bekommt erkannte oder Standard-
    Positionen."""
    muted = "#6b7280"

    # Standard-Fallback-Positionen für den Fall, dass nichts erkannt wurde
    defaults = {
        "buyer":   {"top": 22, "left": 12, "width": 40, "height": 16},
        "meta":    {"top": 22, "left": 55, "width": 38, "height": 14},
        "items":   {"top": 45, "left": 5,  "width": 90, "height": 30},
        "totals":  {"top": 77, "left": 55, "width": 40, "height": 12},
        "payment": {"top": 90, "left": 5,  "width": 90, "height": 8},
    }

    replacements = {
        "%PRIMARY_COLOR%": layout.primary_color,
        "%ACCENT_COLOR%": layout.accent_color,
        "%TEXT_COLOR%": layout.text_color,
        "%MUTED_COLOR%": muted,
        "%FONT_FALLBACK%": _fallback_chain_for(layout.font_family, layout.font_class),
        "%BASE_SIZE%": f"{max(9, min(12, layout.base_font_size)):.1f}",
        "%BUYER_CSS%":   _box_css(layout.buyer_box,   defaults["buyer"]),
        "%META_CSS%":    _box_css(layout.meta_box,    defaults["meta"]),
        "%ITEMS_CSS%":   _box_css(layout.items_box,   defaults["items"]),
        "%TOTALS_CSS%":  _box_css(layout.totals_box,  defaults["totals"]),
        "%PAYMENT_CSS%": _box_css(layout.payment_box, defaults["payment"]),
    }
    html = _TEMPLATE
    for k, v in replacements.items():
        html = html.replace(k, v)
    return html
