"""Minimaler End-to-End-Smoke-Test.

Erwartet: `pip install -r requirements.txt` ist gelaufen und die WeasyPrint-
System-Libraries sind installiert (unter Linux: s. Dockerfile).
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    yield tmp_path


def test_create_invoice(tmp_data):
    # Reimport, damit die Settings den geaenderten DATA_DIR sehen
    from importlib import reload
    from app import config, db, branding, models, service
    reload(config); reload(db); reload(branding); reload(models); reload(service)

    db.Base.metadata.create_all(bind=db.engine)
    branding.ensure_initialized()

    from app.schemas import InvoiceIn, InvoiceItemIn
    data = InvoiceIn(
        invoice_number="2026-001",
        issue_date=date.today(),
        delivery_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        buyer_name="Beispiel AG",
        buyer_street="Teststr. 5",
        buyer_postcode="12345",
        buyer_city="Berlin",
        items=[
            InvoiceItemIn(description="Beratung", quantity=Decimal("3"),
                          unit="HUR", unit_price=Decimal("120"), tax_rate=Decimal("19")),
            InvoiceItemIn(description="Materialpauschale", quantity=Decimal("1"),
                          unit="XPP", unit_price=Decimal("50"), tax_rate=Decimal("19")),
        ],
    )

    sess = db.SessionLocal()
    try:
        inv = service.create_invoice(sess, data)
        assert inv.net_total == Decimal("410.00")
        assert inv.tax_total == Decimal("77.90")
        assert inv.gross_total == Decimal("487.90")
        assert inv.content_hash and inv.chain_hash
        pdf = Path(tmp_data) / inv.pdf_path
        xml = Path(tmp_data) / inv.xml_path
        assert pdf.exists() and pdf.stat().st_size > 1000
        assert xml.exists() and b"CrossIndustryInvoice" in xml.read_bytes()
        # Kettenpruefung
        assert service.verify_chain(sess) == []
    finally:
        sess.close()
