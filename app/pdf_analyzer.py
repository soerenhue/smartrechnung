"""Analysiert eine hochgeladene PDF-Rechnung und extrahiert Layout-Info,
aus der sich eine neue HTML/CSS-Vorlage bauen laesst.

Erkennt:
  - Logo (groesstes Bild im oberen Drittel) -> wird extrahiert und abgelegt
  - Dominante Farbe (Primaerfarbe) + Akzentfarbe aus farbigen Bereichen
  - Haeufigste Schriftart + Groessen
  - Tabellen-artige Strukturen (Rechnungspositionen)
  - Typische Felder (Rechnungsnummer, Datum, Summen) ueber Regex
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
class ExtractedLayout:
    page_width: float = 595.0   # A4 in pt
    page_height: float = 842.0
    primary_color: str = "#1e3a8a"
    accent_color: str = "#3b82f6"
    text_color: str = "#111827"
    font_family: str = "DejaVu Sans"
    logo_bytes: Optional[bytes] = None
    logo_ext: str = "png"
    # Groessen-Hierarchie in pt
    base_font_size: float = 10.0
    heading_font_size: float = 20.0
    # Grobe Positionen in Prozent der Seitenbreite/-hoehe
    logo_x_pct: float = 70.0
    logo_y_pct: float = 3.0
    logo_width_pct: float = 25.0
    buyer_y_pct: float = 25.0
    items_y_pct: float = 45.0
    # Fuer Debug / Weiterverarbeitung
    detected_fonts: list[str] = field(default_factory=list)
    detected_colors: list[str] = field(default_factory=list)
    table_column_xs: list[float] = field(default_factory=list)


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        int(round(r * 255)), int(round(g * 255)), int(round(b * 255))
    )


def _is_near_white(hex_: str) -> bool:
    r = int(hex_[1:3], 16); g = int(hex_[3:5], 16); b = int(hex_[5:7], 16)
    return r > 240 and g > 240 and b > 240


def _is_near_black(hex_: str) -> bool:
    r = int(hex_[1:3], 16); g = int(hex_[3:5], 16); b = int(hex_[5:7], 16)
    return r < 40 and g < 40 and b < 40


def _colorfulness(hex_: str) -> float:
    """Hoeher = bunter. Weiss/Schwarz/Grau nahe 0."""
    r = int(hex_[1:3], 16); g = int(hex_[3:5], 16); b = int(hex_[5:7], 16)
    return max(r, g, b) - min(r, g, b)


def analyze_pdf(pdf_bytes: bytes) -> ExtractedLayout:
    """Analysiert ein PDF und gibt ExtractedLayout zurueck."""
    layout = ExtractedLayout()
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if not doc.page_count:
            return layout
        page = doc[0]
        layout.page_width = page.rect.width
        layout.page_height = page.rect.height

        # --- Bilder (fuer Logo) ---
        images = page.get_images(full=True)
        best_logo = None   # (area, xref, bbox)
        for img in images:
            xref = img[0]
            try:
                info = page.get_image_bbox(img)
                if info is None or info.is_empty:
                    continue
                bbox = info
            except Exception:
                continue
            area = bbox.width * bbox.height
            # Nur im oberen Drittel und nicht winzig
            if bbox.y0 < layout.page_height * 0.35 and area > 400:
                if best_logo is None or area > best_logo[0]:
                    best_logo = (area, xref, bbox)

        if best_logo is not None:
            _, xref, bbox = best_logo
            try:
                extracted = doc.extract_image(xref)
                layout.logo_bytes = extracted["image"]
                layout.logo_ext = extracted.get("ext", "png")
                # Position relativ
                layout.logo_x_pct = (bbox.x0 / layout.page_width) * 100
                layout.logo_y_pct = (bbox.y0 / layout.page_height) * 100
                layout.logo_width_pct = (bbox.width / layout.page_width) * 100
            except Exception:
                pass

        # --- Textbloecke, Farben, Schriften ---
        text_dict = page.get_text("dict")
        font_counter: Counter[str] = Counter()
        color_counter: Counter[str] = Counter()
        size_counter: Counter[float] = Counter()
        text_xs: list[float] = []
        heading_candidates: list[tuple[float, str]] = []  # (size, text)
        buyer_block_y = None
        items_block_y = None

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
                    color = int(span.get("color", 0))  # 0xRRGGBB als int
                    r = ((color >> 16) & 0xFF) / 255.0
                    g = ((color >> 8) & 0xFF) / 255.0
                    b = (color & 0xFF) / 255.0
                    hex_ = _rgb_to_hex(r, g, b)
                    font_counter[font] += 1
                    color_counter[hex_] += 1
                    size_counter[size] += 1
                    text_xs.append(span.get("bbox", [0])[0])

                    # Ueberschriften: deutlich groesser als Durchschnitt
                    if size >= 16:
                        heading_candidates.append((size, text))

                    # Buyer-Block grob erkennen: enthaelt PLZ-Pattern oder "An"
                    if re.search(r"\b\d{5}\b", text) and buyer_block_y is None:
                        y0 = span.get("bbox", [0, 0, 0, 0])[1]
                        if y0 > layout.page_height * 0.15 and y0 < layout.page_height * 0.5:
                            buyer_block_y = y0

                    # Rechnungspositionen: Woerter wie "Menge", "Beschreibung", "Netto"
                    if re.search(r"(Menge|Beschreibung|Pos\b|Netto|Einzelpreis|USt)", text, re.I):
                        y0 = span.get("bbox", [0, 0, 0, 0])[1]
                        if items_block_y is None:
                            items_block_y = y0

        # Dominante Farbe = farbigste, nicht schwarz, nicht weiss
        detected_colors = [
            h for h, _ in color_counter.most_common()
            if not _is_near_white(h) and not _is_near_black(h)
        ]
        colorful = sorted(detected_colors, key=_colorfulness, reverse=True)
        if colorful:
            layout.primary_color = colorful[0]
            if len(colorful) > 1:
                layout.accent_color = colorful[1]
            else:
                layout.accent_color = colorful[0]
        # Schwarz/dunkelste Text-Farbe
        dark = [h for h in color_counter if _is_near_black(h)]
        if dark:
            layout.text_color = dark[0]

        # Schriftart: haeufigste
        if font_counter:
            top_font = font_counter.most_common(1)[0][0]
            # PDF-Font-Namen sehen oft aus wie "ABCDEF+Helvetica-Bold" - cleanen
            clean = re.sub(r"^[A-Z]{6}\+", "", top_font)
            clean = clean.split(",")[0].split("-")[0]
            layout.font_family = clean or "Helvetica"
            layout.detected_fonts = [f for f, _ in font_counter.most_common(5)]

        # Groessen
        if size_counter:
            sizes = sorted(size_counter.keys())
            layout.base_font_size = sizes[len(sizes) // 2]  # Median
            if heading_candidates:
                layout.heading_font_size = max(s for s, _ in heading_candidates)

        if buyer_block_y is not None:
            layout.buyer_y_pct = (buyer_block_y / layout.page_height) * 100
        if items_block_y is not None:
            layout.items_y_pct = (items_block_y / layout.page_height) * 100

        # Tabellen-Spalten: haeufigste X-Startpositionen von Textbloecken
        # (grobe Heuristik - verbessert sich mit mehr Analyse)
        if text_xs:
            rounded = Counter(round(x / 10) * 10 for x in text_xs)
            common_xs = sorted(x for x, _ in rounded.most_common(6))
            layout.table_column_xs = common_xs

        layout.detected_colors = colorful[:5]

    return layout


def save_logo(layout: ExtractedLayout, target_dir: Path, name_stem: str = "logo-imported") -> Optional[str]:
    """Speichert das extrahierte Logo. Gibt den Dateinamen zurueck (oder None)."""
    if not layout.logo_bytes:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{name_stem}.{layout.logo_ext}"
    (target_dir / filename).write_bytes(layout.logo_bytes)
    return filename
