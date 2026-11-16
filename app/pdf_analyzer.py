"""Analysiert eine hochgeladene PDF-Rechnung und extrahiert:

1. Ein hochauflösendes Bild der ersten Seite (als Hintergrund für die neue
   Rechnungsvorlage - bei 300 DPI Druckqualität, nicht pixelig).
2. Die Positionen der Rechnungsfelder im Original (Empfänger-Block,
   Rechnungsnummer, Datum, Positionen-Tabelle, Summen) - damit die neuen
   Daten exakt dort platziert werden können, wo im Original die alten
   stehen (die mit weißen Overlays überdeckt werden).

Resultat: 1:1-Nachbildung des Originals mit scharfem neuen Text.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF


@dataclass
class FieldBox:
    """Ein erkanntes Rechnungsfeld - Position in Prozent der Seitenbreite/-höhe."""
    x_pct: float = 0.0
    y_pct: float = 0.0
    width_pct: float = 0.0
    height_pct: float = 0.0
    sample_text: str = ""


@dataclass
class ExtractedLayout:
    page_width_pt: float = 595.0
    page_height_pt: float = 842.0
    primary_color: str = "#1e3a8a"
    accent_color: str = "#3b82f6"
    text_color: str = "#111827"
    font_family: str = "DejaVu Sans"
    font_class: str = "sans"
    base_font_size: float = 10.0
    heading_font_size: float = 20.0

    logo_bytes: Optional[bytes] = None
    logo_ext: str = "png"

    # Hintergrundbild des Originals (300 DPI, PNG)
    background_png: Optional[bytes] = None

    # Erkannte Positionen der Rechnungsfelder im Original
    buyer_box: Optional[FieldBox] = None
    meta_box: Optional[FieldBox] = None        # Rechnungsnummer + Datum
    items_box: Optional[FieldBox] = None       # Positionstabelle
    totals_box: Optional[FieldBox] = None      # Summenblock
    payment_box: Optional[FieldBox] = None     # Zahlungsbedingungen / IBAN

    detected_fonts: list[str] = field(default_factory=list)


_SERIF_FONTS = {"times", "georgia", "garamond", "cambria", "palatino",
                "baskerville", "didot", "bodoni", "minion", "caslon",
                "charter", "merriweather", "playfair", "lora", "crimson",
                "pt serif", "libre", "source serif"}
_MONO_FONTS = {"courier", "menlo", "monaco", "consolas", "roboto mono",
               "source code", "fira code", "jetbrains mono", "sf mono"}


def _classify_font(name: str) -> str:
    n = name.lower()
    for s in _SERIF_FONTS:
        if s in n:
            return "serif"
    for m in _MONO_FONTS:
        if m in n:
            return "mono"
    return "sans"


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def _is_near_white(h: str) -> bool:
    r = int(h[1:3], 16); g = int(h[3:5], 16); b = int(h[5:7], 16)
    return r > 240 and g > 240 and b > 240


def _is_near_black(h: str) -> bool:
    r = int(h[1:3], 16); g = int(h[3:5], 16); b = int(h[5:7], 16)
    return r < 40 and g < 40 and b < 40


def _colorfulness(h: str) -> float:
    r = int(h[1:3], 16); g = int(h[3:5], 16); b = int(h[5:7], 16)
    return max(r, g, b) - min(r, g, b)


def _union_bbox(blocks: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    """Vereint mehrere Bounding-Boxen zu einer."""
    if not blocks:
        return (0, 0, 0, 0)
    x0 = min(b[0] for b in blocks)
    y0 = min(b[1] for b in blocks)
    x1 = max(b[2] for b in blocks)
    y1 = max(b[3] for b in blocks)
    return (x0, y0, x1, y1)


def _to_pct(bbox, page_w, page_h) -> FieldBox:
    x0, y0, x1, y1 = bbox
    return FieldBox(
        x_pct=(x0 / page_w) * 100,
        y_pct=(y0 / page_h) * 100,
        width_pct=((x1 - x0) / page_w) * 100,
        height_pct=((y1 - y0) / page_h) * 100,
    )


def analyze_pdf(pdf_bytes: bytes) -> ExtractedLayout:
    """Analysiert ein PDF, rendert den Hintergrund und erkennt Feld-Positionen."""
    layout = ExtractedLayout()

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if not doc.page_count:
            return layout

        page = doc[0]
        page_w = layout.page_width_pt = page.rect.width
        page_h = layout.page_height_pt = page.rect.height

        # --- Hintergrundbild: 300 DPI, OHNE die alten Textdaten ---
        # Erst alle Textbereiche mit Redaction-Annotationen markieren,
        # dann apply_redactions() entfernt den Text und ersetzt ihn durch
        # die Hintergrundfarbe an dieser Stelle (sampling). Dadurch bleibt
        # das Design (Logo, Rahmen, Flächen, Fußzeile) exakt erhalten,
        # aber die alten Rechnungsdaten sind weg. Die neuen Daten können
        # dann ohne weiße Overlay-Boxen draufgeschrieben werden.
        try:
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        bbox = span.get("bbox")
                        if bbox and (bbox[2] - bbox[0]) > 0:
                            rect = fitz.Rect(bbox)
                            # Logo-Text bleibt erhalten (ganz oben rechts/links
                            # wo häufig der Firmenname steht und mit zum Logo gehört)
                            if rect.y0 < page_h * 0.12:
                                continue
                            page.add_redact_annot(rect, fill=None)
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

            zoom = 300 / 72
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            layout.background_png = pix.tobytes("png")
        except Exception:
            # Fallback: Ohne Redaction, einfach rendern
            try:
                zoom = 300 / 72
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                layout.background_png = pix.tobytes("png")
            except Exception:
                pass

        # --- Logo: größtes Bild im oberen Drittel ---
        try:
            images = page.get_images(full=True)
            best = None
            for img in images:
                xref = img[0]
                try:
                    bbox = page.get_image_bbox(img)
                    if bbox is None or bbox.is_empty:
                        continue
                except Exception:
                    continue
                area = bbox.width * bbox.height
                if bbox.y0 < page_h * 0.35 and area > 400:
                    if best is None or area > best[0]:
                        best = (area, xref, bbox)
            if best:
                _, xref, _ = best
                extracted = doc.extract_image(xref)
                layout.logo_bytes = extracted["image"]
                layout.logo_ext = extracted.get("ext", "png")
        except Exception:
            pass

        # --- Textblöcke mit Position einsammeln ---
        text_dict = page.get_text("dict")
        font_counter: Counter[str] = Counter()
        color_counter: Counter[str] = Counter()
        size_counter: Counter[float] = Counter()

        # Alle Textspans mit Position
        spans = []  # (bbox, text, size, color_hex, font)

        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if not text:
                        continue
                    size = float(span.get("size", 10))
                    font = span.get("font", "").strip()
                    color = int(span.get("color", 0))
                    r = ((color >> 16) & 0xFF) / 255.0
                    g = ((color >> 8) & 0xFF) / 255.0
                    b = (color & 0xFF) / 255.0
                    hex_ = _rgb_to_hex(r, g, b)
                    font_counter[font] += 1
                    color_counter[hex_] += 1
                    size_counter[size] += 1
                    bbox = tuple(span.get("bbox", [0, 0, 0, 0]))
                    spans.append((bbox, text, size, hex_, font))

        # --- Farben ---
        colorful = sorted(
            [h for h, _ in color_counter.most_common()
             if not _is_near_white(h) and not _is_near_black(h)],
            key=_colorfulness, reverse=True)
        if colorful:
            layout.primary_color = colorful[0]
            layout.accent_color = colorful[1] if len(colorful) > 1 else colorful[0]
        dark = [h for h in color_counter if _is_near_black(h)]
        if dark:
            layout.text_color = dark[0]

        # --- Schriftart ---
        if font_counter:
            top_font = font_counter.most_common(1)[0][0]
            clean = re.sub(r"^[A-Z]{6}\+", "", top_font)
            clean = clean.split(",")[0].split("-")[0]
            layout.font_family = clean or "Helvetica"
            layout.font_class = _classify_font(clean)
            layout.detected_fonts = [f for f, _ in font_counter.most_common(5)]

        # --- Schriftgrößen ---
        if size_counter:
            sizes = sorted(size_counter.keys())
            layout.base_font_size = sizes[len(sizes) // 2]
            heading_candidates = [s for s in size_counter if s >= 14]
            if heading_candidates:
                layout.heading_font_size = max(heading_candidates)

        # --- Feld-Positionen erkennen ---
        # Heuristik: typische Bereiche einer Rechnung

        # 1. Empfängerblock: Y zwischen 20% und 42%, X in linker Hälfte
        buyer_spans = [s for s in spans
                       if page_h * 0.18 < s[0][1] < page_h * 0.45
                       and s[0][0] < page_w * 0.55
                       and (re.search(r"\b\d{5}\b", s[1]) or s[2] >= layout.base_font_size)]
        if buyer_spans:
            # Finde zusammenhängenden Block (nicht nur eine Zeile mit PLZ)
            # Nimm den Block um die PLZ-Zeile herum
            plz_spans = [s for s in buyer_spans if re.search(r"\b\d{5}\b", s[1])]
            if plz_spans:
                plz_y = plz_spans[0][0][1]
                nearby = [s for s in buyer_spans
                          if abs(s[0][1] - plz_y) < 50]
                if nearby:
                    bbox = _union_bbox([s[0] for s in nearby])
                    layout.buyer_box = _to_pct(bbox, page_w, page_h)

        # 2. Meta-Block (Rechnungsnummer, Datum): Y 20-45%, rechte Seite
        meta_spans = [s for s in spans
                      if page_h * 0.15 < s[0][1] < page_h * 0.50
                      and s[0][0] > page_w * 0.55
                      and re.search(r"(Rechnung|Datum|Nummer|Nr\.|\d{2}[\.\/]\d{2}[\.\/]\d{2,4})", s[1], re.I)]
        if meta_spans:
            bbox = _union_bbox([s[0] for s in meta_spans])
            layout.meta_box = _to_pct(bbox, page_w, page_h)

        # 3. Positionstabelle: Y 42-75%, volle Breite
        table_header_spans = [s for s in spans
                              if re.search(r"(Beschreibung|Position|Menge|Einzelpreis|Netto|USt)", s[1], re.I)
                              and page_h * 0.40 < s[0][1] < page_h * 0.80]
        if table_header_spans:
            header_y = min(s[0][1] for s in table_header_spans)
            # Items-Bereich geht vom Header bis ~75% der Seite
            layout.items_box = FieldBox(
                x_pct=5.0,
                y_pct=(header_y / page_h) * 100,
                width_pct=90.0,
                height_pct=max(20.0, 75.0 - (header_y / page_h) * 100),
            )

        # 4. Summen: nach Items, rechts
        summary_spans = [s for s in spans
                         if page_h * 0.65 < s[0][1] < page_h * 0.90
                         and s[0][0] > page_w * 0.55
                         and re.search(r"(Gesamt|Netto|Brutto|Summe|Umsatzsteuer|MwSt|\d+[,.]?\d+\s*(€|EUR))", s[1], re.I)]
        if summary_spans:
            bbox = _union_bbox([s[0] for s in summary_spans])
            layout.totals_box = _to_pct(bbox, page_w, page_h)

        # 5. Payment-Block (IBAN, Zahlungsbedingungen): unten
        payment_spans = [s for s in spans
                         if page_h * 0.75 < s[0][1] < page_h * 0.95
                         and re.search(r"(IBAN|BIC|Zahlungs|Bankverbindung|Konto|\bDE\d{20})", s[1], re.I)]
        if payment_spans:
            bbox = _union_bbox([s[0] for s in payment_spans])
            layout.payment_box = _to_pct(bbox, page_w, page_h)

    return layout


def save_logo(layout: ExtractedLayout, target_dir: Path,
              name_stem: str = "logo-imported") -> Optional[str]:
    if not layout.logo_bytes:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{name_stem}.{layout.logo_ext}"
    (target_dir / filename).write_bytes(layout.logo_bytes)
    return filename


def save_background(layout: ExtractedLayout, target_dir: Path,
                    name_stem: str = "vorlage-hintergrund") -> Optional[str]:
    """Speichert das 300-DPI-Hintergrundbild der Originalvorlage."""
    if not layout.background_png:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{name_stem}.png"
    (target_dir / filename).write_bytes(layout.background_png)
    return filename
