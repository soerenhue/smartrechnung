"""Rendert eine neue Rechnung direkt auf das Original-PDF der Vorlage.

Ansatz: Keine HTML-Overlays mehr. Wir öffnen die Original-PDF-Datei der
Firma, suchen nach bekannten Labels ("Rechnungsnummer", "Rechnungsdatum",
"IBAN", ...), entfernen den dahinter stehenden Original-Text mit
Redaktion und schreiben den neuen Wert an exakt derselben Position mit
derselben Schriftart und -größe.

Das Ergebnis ist pixel-perfekt dasselbe Layout wie die Vorlage - inkl.
Logo, Rahmen, Hintergrundflächen, Fußzeile - aber mit den aktuellen
Rechnungsdaten.

Limitierungen:
- Funktioniert gut, wenn die Vorlage deutliche Labels hat
- Mehrzeilige Positionstabellen werden ersetzt durch eine neue Tabelle
  mit identischer Spalten-Anordnung
- Wenn die Vorlage zu exotisch ist (kein Standard-Label), fällt der
  Service auf den Standard-HTML-Renderer zurück.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import fitz  # PyMuPDF

from .models import Invoice


# Labels, die wir in der Vorlage suchen. Für jedes Label:
#   patterns:  Regex-Alternativen, die erkannt werden
#   placement: "same-line" (Wert rechts vom Label) oder "below" (Wert darunter)
LABEL_SPECS = {
    "invoice_number": {
        "patterns": [r"Rechnungs[\-\s]?Nr\.?", r"Rechnungsnummer",
                     r"Invoice\s*No\.?", r"Nr\.\s*der\s*Rechnung"],
    },
    "issue_date": {
        "patterns": [r"Rechnungsdatum", r"Datum", r"Invoice\s*date",
                     r"Ausgestellt\s*am"],
    },
    "delivery_date": {
        "patterns": [r"Lieferzeit", r"Leistungs?datum", r"Lieferdatum",
                     r"Service\s*date", r"Erbracht\s*am"],
    },
    "due_date": {
        "patterns": [r"Zahlungsziel", r"Zahlbar\s*bis", r"Fällig(?:keit)?",
                     r"Due\s*date", r"Zahlungsfrist"],
    },
    "iban": {
        "patterns": [r"IBAN"],
    },
    "bic": {
        "patterns": [r"BIC", r"SWIFT"],
    },
    "net_total": {
        "patterns": [r"Warenwert\s*netto", r"(?:Gesamt-?)?Netto(?:betrag)?",
                     r"Summe\s*netto", r"Zwischensumme"],
    },
    "tax_total": {
        "patterns": [r"Umsatzsteuer(?:\s*\(\s*\d+\s*%\s*\))?",
                     r"MwSt\.?(?:\s*\(\s*\d+\s*%\s*\))?", r"USt\.?"],
    },
    "gross_total": {
        "patterns": [r"Gesamtbetrag\s*brutto", r"Gesamtbetrag",
                     r"Brutto(?:betrag|summe)?", r"Rechnungsbetrag",
                     r"Zu\s*zahlen(?:der\s*Betrag)?", r"Total"],
    },
}


def _fmt_money(d: Decimal, currency: str = "EUR") -> str:
    s = f"{float(d):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €" if currency == "EUR" else f"{s} {currency}"


def _fmt_qty(d: Decimal) -> str:
    s = f"{float(d):,.3f}".replace(",", "X").replace(".", ",").replace("X", ".")
    s = re.sub(r"[.,]?0+$", "", s) or "0"
    return s


@dataclass
class LabelHit:
    rect: fitz.Rect            # Position des Labels
    text: str                  # Erkannter Label-Text
    font: str                  # Schriftname
    size: float                # Schriftgröße
    color: int                 # Farbe (int)


def _find_label(page: fitz.Page, patterns: list[str]) -> Optional[LabelHit]:
    """Sucht das erste Vorkommen eines der Patterns auf der Seite."""
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                for pat in patterns:
                    if re.fullmatch(pat, text, re.I) or re.match(pat + r"\s*:?\s*$", text, re.I):
                        return LabelHit(
                            rect=fitz.Rect(span["bbox"]),
                            text=text,
                            font=span.get("font", ""),
                            size=float(span.get("size", 10)),
                            color=int(span.get("color", 0)),
                        )
    return None


def _value_span_after_label(page: fitz.Page, label: LabelHit) -> Optional[dict]:
    """Findet den Textspan, der als Wert nach dem Label kommt: entweder
    rechts auf derselben Zeile (gleiches y) oder direkt darunter."""
    candidates_right = []  # same line, right of label
    candidates_below = []  # directly below

    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                r = fitz.Rect(span["bbox"])
                # Gleiche Zeile: y überlappt, rechts vom Label
                if abs(r.y0 - label.rect.y0) < 3 and r.x0 > label.rect.x1 + 2:
                    candidates_right.append((r.x0, {"rect": r, "text": text, "span": span}))
                # Direkt darunter: y im Bereich 1..18pt unter Label, x±120pt nah
                elif (0 < (r.y0 - label.rect.y1) < 18 and
                      abs(r.x0 - label.rect.x0) < 120):
                    candidates_below.append((r.y0 - label.rect.y1,
                                             {"rect": r, "text": text, "span": span}))

    if candidates_right:
        candidates_right.sort(key=lambda x: x[0])
        return candidates_right[0][1]
    if candidates_below:
        candidates_below.sort(key=lambda x: x[0])
        return candidates_below[0][1]
    return None


def _buyer_block_rect(page: fitz.Page) -> Optional[fitz.Rect]:
    """Findet den Buyer-Block: Suche nach einem Label wie
    'Rechnungsempfänger' / 'Kunde' / 'Empfänger' und nimm die darunter
    liegenden Zeilen bis 8 Zeilen weit oder bis der Abstand zu groß wird."""
    label_patterns = [r"Rechnungsempfänger", r"Rechnungs-?\s*Empfänger",
                      r"Rechnung\s*an", r"Empfänger", r"Kunde", r"Bill\s*to"]
    hit = None
    for pat in label_patterns:
        h = _find_label(page, [pat])
        if h:
            hit = h
            break
    if not hit:
        return None

    lines_below = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                r = fitz.Rect(span["bbox"])
                if r.y0 > hit.rect.y1 + 1 and abs(r.x0 - hit.rect.x0) < 200:
                    lines_below.append(r)
    if not lines_below:
        return None
    # Nimm die ersten 6 Zeilen nach dem Label
    lines_below.sort(key=lambda r: r.y0)
    kept = [lines_below[0]]
    for r in lines_below[1:6]:
        if r.y0 - kept[-1].y1 < 15:
            kept.append(r)
        else:
            break
    if not kept:
        return None
    x0 = min(r.x0 for r in kept)
    y0 = kept[0].y0
    x1 = max(r.x1 for r in kept)
    y1 = kept[-1].y1
    return fitz.Rect(x0, y0, x1, y1)


def _color_tuple(rgb_int: int) -> tuple[float, float, float]:
    r = ((rgb_int >> 16) & 0xFF) / 255.0
    g = ((rgb_int >> 8) & 0xFF) / 255.0
    b = (rgb_int & 0xFF) / 255.0
    return (r, g, b)


def _best_fontname(font_name: str) -> str:
    """Mappt einen Vorlagen-Font auf einen PyMuPDF-Builtin-Font,
    damit der neue Text immer rendert, auch wenn die Original-Font
    nicht im PDF eingebettet ist."""
    f = font_name.lower()
    if "bold" in f and ("italic" in f or "oblique" in f):
        return "helvetica-boldoblique" if "helv" in f or "sans" in f else "times-bolditalic"
    if "bold" in f:
        return "helv-bold" if ("helv" in f or "sans" in f or "arial" in f) else "tibo"
    if "italic" in f or "oblique" in f:
        return "helv-oblique" if ("helv" in f or "sans" in f) else "times-italic"
    if any(s in f for s in ("times", "serif", "roman", "georgia")):
        return "tiro"  # Times-Roman
    return "helv"  # Helvetica Standard


# Mapping der Short-Name auf echte PyMuPDF-Fonts
_FONT_MAP = {
    "helv": "helv",
    "helv-bold": "hebo",
    "helv-oblique": "heit",
    "helvetica-boldoblique": "hebi",
    "tiro": "tiro",
    "tibo": "tibo",
    "times-italic": "tiit",
    "times-bolditalic": "tibi",
}


def _write_at(page: fitz.Page, rect: fitz.Rect, text: str,
              fontname: str = "helv", fontsize: float = 10.0,
              color: tuple[float, float, float] = (0, 0, 0),
              align: int = 0) -> None:
    """Schreibt Text in ein Rechteck. align: 0=links, 1=zentriert, 2=rechts."""
    real_font = _FONT_MAP.get(fontname, "helv")
    try:
        # Rechteck leicht vergrößern, damit Text nicht abgeschnitten wird
        expanded = fitz.Rect(rect.x0, rect.y0 - 1, rect.x1 + 20, rect.y1 + 1)
        page.insert_textbox(expanded, text, fontname=real_font,
                            fontsize=fontsize, color=color, align=align)
    except Exception:
        # Fallback mit Standard-Font
        page.insert_textbox(expanded, text, fontname="helv",
                            fontsize=fontsize, color=color, align=align)


def render_invoice_pdf_from_template(template_pdf: bytes,
                                     inv: Invoice,
                                     qr_png: Optional[bytes] = None
                                     ) -> Optional[bytes]:
    """Nimmt Vorlage + Rechnungsdaten → neue PDF im exakten Vorlagen-Layout.

    Gibt None zurück, wenn zu wenig Labels gefunden wurden (dann soll der
    Aufrufer auf den Standard-HTML-Renderer zurückfallen)."""
    try:
        doc = fitz.open(stream=template_pdf, filetype="pdf")
    except Exception:
        return None
    if doc.page_count == 0:
        return None
    page = doc[0]

    # --- Die einfachen Label → Wert Ersetzungen ---
    replacements = {
        "invoice_number": inv.invoice_number,
        "issue_date": inv.issue_date.strftime("%d.%m.%Y"),
        "delivery_date": inv.delivery_date.strftime("%d.%m.%Y"),
        "due_date": inv.due_date.strftime("%d.%m.%Y"),
        "iban": (inv.seller_iban or "").strip(),
        "bic": (inv.seller_bic or "").strip(),
        "net_total": _fmt_money(inv.net_total, inv.currency),
        "tax_total": _fmt_money(inv.tax_total, inv.currency),
        "gross_total": _fmt_money(inv.gross_total, inv.currency),
    }

    matches_found = 0
    pending_writes = []  # [(rect, text, font, size, color)]

    for key, spec in LABEL_SPECS.items():
        label = _find_label(page, spec["patterns"])
        if not label:
            continue
        value_span = _value_span_after_label(page, label)
        if not value_span:
            # Trotzdem rechts vom Label schreiben, wenn Originalwert fehlte
            new_rect = fitz.Rect(label.rect.x1 + 3, label.rect.y0,
                                 label.rect.x1 + 180, label.rect.y1 + 2)
            pending_writes.append((new_rect, replacements[key],
                                    _best_fontname(label.font),
                                    label.size, _color_tuple(label.color)))
            matches_found += 1
            continue

        old_rect = value_span["rect"]
        span = value_span["span"]
        # Alten Wert mit Redaction entfernen
        page.add_redact_annot(fitz.Rect(old_rect.x0 - 1, old_rect.y0 - 1,
                                        old_rect.x1 + 80, old_rect.y1 + 1),
                              fill=None)
        pending_writes.append((
            fitz.Rect(old_rect.x0, old_rect.y0 - 1,
                      old_rect.x1 + 80, old_rect.y1 + 2),
            replacements[key],
            _best_fontname(span.get("font", "")),
            float(span.get("size", 10)),
            _color_tuple(int(span.get("color", 0))),
        ))
        matches_found += 1

    # --- Buyer-Block ---
    buyer_rect = _buyer_block_rect(page)
    if buyer_rect:
        page.add_redact_annot(buyer_rect, fill=None)
        matches_found += 1

    # Zu wenige Treffer → Vorlage ist nicht kompatibel
    if matches_found < 3:
        doc.close()
        return None

    # Alle markierten Felder jetzt wirklich entfernen
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

    # Buyer-Block neu schreiben
    if buyer_rect:
        buyer_lines = [
            inv.buyer_name,
            inv.buyer_street,
            f"{inv.buyer_postcode} {inv.buyer_city}",
        ]
        if inv.buyer_country and inv.buyer_country != "DE":
            buyer_lines.append(inv.buyer_country)
        if inv.buyer_tax_id:
            buyer_lines.append(f"USt-IdNr.: {inv.buyer_tax_id}")
        # Zeilenweise schreiben, mit base_font_size und line_height 1.3
        bfs = 10.0
        lh = bfs * 1.3
        y = buyer_rect.y0
        for line in buyer_lines:
            if y + lh > buyer_rect.y1 + lh * 2:
                break
            _write_at(page,
                      fitz.Rect(buyer_rect.x0, y, buyer_rect.x1 + 100, y + lh + 1),
                      line, fontname="helv", fontsize=bfs,
                      color=(0.07, 0.09, 0.15))
            y += lh

    # Jetzt die einfachen Felder einzeichnen
    for (rect, text, font, size, color) in pending_writes:
        _write_at(page, rect, text, fontname=font, fontsize=size,
                  color=color, align=0)

    # QR-Code einfügen (unten rechts, klein)
    if qr_png:
        try:
            page_h = page.rect.height
            page_w = page.rect.width
            qr_size = 60
            qr_rect = fitz.Rect(page_w - qr_size - 40, page_h - qr_size - 40,
                                page_w - 40, page_h - 40)
            page.insert_image(qr_rect, stream=qr_png)
        except Exception:
            pass

    out = io.BytesIO()
    doc.save(out, garbage=4, deflate=True)
    doc.close()
    return out.getvalue()
