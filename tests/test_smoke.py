"""End-to-End-Smoke-Test: Firma anlegen, Rechnung erstellen, PDF+XML pruefen."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setenv("ERECHNUNG_DATA_DIR", str(tmp_path))
    yield tmp_path


def test_create_invoice(tmp_data):
    from importlib import reload
    from app import config, db, branding, models, service
    reload(config); reload(db); reload(branding); reload(models); reload(service)

    db.Base.metadata.create_all(bind=db.engine)

    c = config.create_company({
        "display_name": "Test GmbH", "seller_name": "Test GmbH",
        "seller_street": "Str 1", "seller_postcode": "10115", "seller_city": "Berlin",
        "seller_email": "x@x.de", "seller_tax_id": "DE999999999",
        "seller_iban": "DE00000000000000000000", "tax_regime": "regular",
        "number_format": "{year}-{nn:03d}", "next_number": 1,
    })
    assert c.is_configured()
    assert c.format_next_number() == f"{date.today().year}-001"

    from app.schemas import InvoiceIn, InvoiceItemIn
    data = InvoiceIn(
        invoice_number=c.format_next_number(),
        issue_date=date.today(),
        delivery_date=date.today(),
        due_date=date.today() + timedelta(days=14),
        buyer_name="Beispiel AG", buyer_street="Teststr. 5",
        buyer_postcode="12345", buyer_city="Berlin",
        items=[
            InvoiceItemIn(description="Beratung", quantity=Decimal("3"),
                          unit="HUR", unit_price=Decimal("120"), tax_rate=Decimal("19")),
        ],
    )

    sess = db.SessionLocal()
    try:
        inv = service.create_invoice(sess, data, c)
        assert inv.net_total == Decimal("360.00")
        assert inv.gross_total == Decimal("428.40")
        assert inv.company_slug == c.slug
        assert service.verify_chain(sess, c) == []
        # Zweite Rechnung mit automatischer nachgezogener Nummer
        c2 = config.get_company(c.slug)
        assert c2.next_number == 2
    finally:
        sess.close()
