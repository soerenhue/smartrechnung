"""E-Mail-Versand von Rechnungen per SMTP.

Jede Firma hat ihre eigenen SMTP-Zugangsdaten. Beim Versand kann der
Nutzer wählen: nur PDF, nur XML oder PDF + XML separat zusätzlich.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from .config import Company
from .models import Invoice


class SmtpNotConfiguredError(RuntimeError):
    pass


def _render(tpl: str, inv: Invoice, company: Company) -> str:
    """Template-Platzhalter ersetzen."""
    amount_str = f"{float(inv.gross_total):.2f} {inv.currency}".replace(".", ",")
    return (tpl
            .replace("{number}", inv.invoice_number)
            .replace("{date}", inv.issue_date.strftime("%d.%m.%Y"))
            .replace("{amount}", amount_str)
            .replace("{sender}", company.seller_name)
            .replace("{buyer}", inv.buyer_name))


def send_invoice(inv: Invoice, company: Company, data_dir: Path,
                 to_email: str, attach_pdf: bool = True,
                 attach_xml_separately: bool = False,
                 custom_subject: Optional[str] = None,
                 custom_body: Optional[str] = None) -> None:
    """Versendet die Rechnung per E-Mail.

    attach_pdf:                 Das Hybrid-PDF (mit eingebettetem XML) anhängen
    attach_xml_separately:      Zusätzlich die .xml als separate Datei

    Falls beides False → Fehler (es muss etwas angehängt werden).
    """
    smtp_host = getattr(company, "smtp_host", "")
    smtp_user = getattr(company, "smtp_user", "")
    smtp_pass = getattr(company, "smtp_pass", "")
    if not smtp_host or not smtp_user:
        raise SmtpNotConfiguredError(
            "SMTP-Zugangsdaten für diese Firma fehlen. "
            "Bitte in den Firmendaten eintragen."
        )
    if not (attach_pdf or attach_xml_separately):
        raise ValueError("Mindestens eine Datei muss angehängt werden.")
    if not to_email or "@" not in to_email:
        raise ValueError("Empfänger-E-Mail fehlt oder ist ungültig.")

    subject = custom_subject or _render(company.email_subject_tpl, inv, company)
    body = custom_body or _render(company.email_body_tpl, inv, company)
    from_name = company.smtp_from_name or company.seller_name

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{smtp_user}>"
    msg["To"] = to_email
    msg["Reply-To"] = company.seller_email or smtp_user
    msg.set_content(body)

    # PDF (Hybrid mit XML drin) anhängen
    if attach_pdf and inv.pdf_path:
        pdf_path = data_dir / inv.pdf_path
        if pdf_path.exists():
            with open(pdf_path, "rb") as f:
                msg.add_attachment(
                    f.read(), maintype="application", subtype="pdf",
                    filename=f"Rechnung_{inv.invoice_number.replace('/', '-')}.pdf",
                )

    # XML separat (falls gewünscht)
    if attach_xml_separately and inv.xml_path:
        xml_path = data_dir / inv.xml_path
        if xml_path.exists():
            with open(xml_path, "rb") as f:
                msg.add_attachment(
                    f.read(), maintype="application", subtype="xml",
                    filename=f"Rechnung_{inv.invoice_number.replace('/', '-')}.xml",
                )

    port = int(getattr(company, "smtp_port", 587) or 587)
    use_tls = bool(getattr(company, "smtp_use_tls", True))

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(smtp_host, port, context=context) as server:
            if smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp_host, port) as server:
            if use_tls:
                server.starttls(context=context)
            if smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)
