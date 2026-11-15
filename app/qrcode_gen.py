"""EPC-QR-Code ("Giro-Code") fuer Rechnungen.

Wird gescannt von allen deutschen Banking-Apps (Sparkasse, Volksbank,
Postbank, ING, DKB, N26, ...). Alle Zahldaten werden automatisch in
die Ueberweisungsmaske uebernommen - der Kunde muss nur noch bestaetigen.

Format: EPC069-12 (European Payments Council Quick Response Code)
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Optional

import segno
from segno.helpers import make_epc_qr

from .models import Invoice


def generate_payment_qr(inv: Invoice, out_dir: Path) -> Optional[str]:
    """Erzeugt einen EPC-QR-Code als PNG. Gibt den Dateinamen zurueck
    (relativ zu out_dir), oder None wenn IBAN/Betrag unplausibel.

    Wird auf die Rechnung gedruckt. Der Kunde oeffnet seine Banking-App,
    scannt den Code mit der Kamera, und alle Felder (Empfaenger, IBAN,
    Betrag, Verwendungszweck) sind automatisch gesetzt - er muss nur noch
    die Ueberweisung bestaetigen.
    """
    iban = (inv.seller_iban or "").replace(" ", "").upper()
    if not iban or len(iban) < 15:
        return None
    try:
        amount = Decimal(inv.gross_total or 0)
    except Exception:
        return None
    if amount <= 0 or amount > Decimal("999999999.99"):
        return None

    name = (inv.seller_name or "")[:70]
    text = f"Rechnung {inv.invoice_number}"[:140]
    bic = (inv.seller_bic or "").strip() or None

    try:
        qr = make_epc_qr(
            name=name, iban=iban, amount=amount, text=text,
            bic=bic, encoding="utf-8",
        )
    except Exception:
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    safe_number = inv.invoice_number.replace("/", "-").replace(" ", "_").replace("\\", "-")
    filename = f"qr-{safe_number}.png"
    try:
        qr.save(str(out_dir / filename), scale=5, border=1,
                dark="#1f2937", light="white")
    except Exception:
        return None
    return filename
