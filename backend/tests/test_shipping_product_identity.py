"""Regression coverage for Inflow HTML descriptions leaking into shipping labels."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.order import Order
from app.models.shipment import ShippingProduct
from app.schemas.shipment import ProductWeightUpdate
from app.services.shipment_service import ShipmentService, shipping_items
from app.services.shipping_product_identity import product_identity

SPEC = '<p><strong>Ports:</strong>2x HDMI<br>Weight: 35.93 lbs</p>'


def product_line(*, name="Dell UltraSharp 49 Monitor", description=SPEC, sku="DMU4924DW"):
    return {"productId": "monitor-id", "description": description,
        "product": {"name": name, "sku": sku}, "quantity": {"standardQuantity": "2"}}


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'products.sqlite'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()
    engine.dispose()


def add_order(db, line, *, identifier="order-1", picks=None):
    order = Order(id=identifier, inflow_order_id=identifier, status="shipping", qa_method="Shipping",
        qa_completed_at=datetime.utcnow(), inflow_data={"lines": [line], "pickLines": picks or [line]})
    db.add(order)
    db.commit()
    return order


def test_order_product_name_beats_html_description_and_survives_partial_pick_metadata(db):
    order = add_order(db, product_line(), picks=[{
        "productId": "monitor-id", "product": {"sku": "DMU4924DW"},
        "description": SPEC, "quantity": {"standardQuantity": "2"}}])
    assert shipping_items(order) == [{"product_id": "monitor-id", "name": "Dell UltraSharp 49 Monitor",
        "sku": "DMU4924DW", "quantity": 2}]
    assert ShipmentService(db).catalog()[0]["name"] == "Dell UltraSharp 49 Monitor"


@pytest.mark.parametrize("description", [SPEC, '&lt;div&gt;Specifications&lt;/div&gt;',
    '&amp;lt;p&amp;gt;Specification text&amp;lt;/p&amp;gt;', '<img src=x onerror=alert(1)>', 'Ports: '+('HDMI ' * 100)])
def test_missing_name_uses_sku_instead_of_specifications(description):
    identity = product_identity("monitor-id", product_line(name="", description=description))
    assert identity.name == identity.sku == "DMU4924DW"


def test_product_name_entities_plain_description_and_id_fallback():
    assert product_identity("id", {"productName": "USB &amp; HDMI Dock", "description": SPEC}).name == "USB & HDMI Dock"
    assert product_identity("id", {"description": "6 Outlet Surge Protector, 6 Feet"}).name == "6 Outlet Surge Protector, 6 Feet"
    assert product_identity("id", {"description": SPEC}).name == "id"


def test_stored_html_name_is_corrected_on_read_without_changing_saved_weight(db):
    add_order(db, product_line())
    saved = ShippingProduct(product_id="monitor-id", name=SPEC, sku="DMU4924DW", weight_lb=Decimal("35.930"))
    db.add(saved);db.commit()
    result = ShipmentService(db).catalog()[0]
    assert result["name"] == "Dell UltraSharp 49 Monitor"
    assert result["weight_lb"] == 35.93
    assert saved.name == SPEC and saved.weight_lb == Decimal("35.930")
    assert not db.is_modified(saved)


def test_stored_html_without_order_metadata_falls_back_to_sku(db):
    db.add(ShippingProduct(product_id="monitor-id", name=SPEC, sku="DMU4924DW", weight_lb=Decimal("35.930")))
    db.commit()
    assert ShipmentService(db).catalog()[0]["name"] == "DMU4924DW"
    assert ShipmentService(db).catalog()[0]["weight_lb"] == 35.93


def test_html_from_later_order_cannot_replace_actual_name(db):
    add_order(db, product_line())
    add_order(db, product_line(name=""), identifier="order-2")
    assert ShipmentService(db).catalog()[0]["name"] == "Dell UltraSharp 49 Monitor"


def test_imported_catalog_name_is_used_in_queue_and_packing(db):
    order = add_order(db, product_line(name=""))
    db.add(ShippingProduct(product_id="monitor-id", name="Dell UltraSharp 49 Monitor", sku="DMU4924DW", weight_lb=Decimal("35.930")))
    db.commit()
    svc = ShipmentService(db)
    assert svc.workspace(order.id)["items"][0]["name"] == "Dell UltraSharp 49 Monitor"
    assert svc.workspace(order.id)["items"][0]["weight_lb"] == 35.93
    assert svc.shipping_queue()["unmatched"][0]["products"][0]["name"] == "Dell UltraSharp 49 Monitor"


def test_catalog_import_replaces_legacy_html_but_keeps_weight(db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "inflow_api_key", "test-only")
    db.add(ShippingProduct(product_id="monitor-id", name=SPEC, sku="DMU4924DW", weight_lb=Decimal("35.930")))
    db.commit()
    client = httpx.Client
    def handler(request):
        records = [{"productId": "monitor-id", "name": "Dell UltraSharp 49 Monitor", "sku": "DMU4924DW", "description": SPEC}] if request.url.params["skip"] == "0" else []
        return httpx.Response(200, json=records)
    with patch("app.services.shipment_service.httpx.Client", side_effect=lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)):
        assert ShipmentService(db).import_catalog("admin") == 1
    saved = db.get(ShippingProduct, "monitor-id")
    assert saved.name == "Dell UltraSharp 49 Monitor"
    assert saved.weight_lb == Decimal("35.930")


def test_weight_save_does_not_persist_html_as_name(db):
    svc = ShipmentService(db)
    svc.save_weight("monitor-id", ProductWeightUpdate(name=SPEC, sku="DMU4924DW", weight_lb=35.93), "admin")
    product = db.get(ShippingProduct, "monitor-id")
    assert product.name == "DMU4924DW"
    assert product.weight_lb == Decimal("35.930")


def test_previous_sku_fallback_is_replaced_by_real_product_name():
    saved = SimpleNamespace(name="DMU4924DW", sku="DMU4924DW")
    assert product_identity("monitor-id", product_line(), saved).name == "Dell UltraSharp 49 Monitor"


def test_same_product_across_orders_shares_weight_but_duplicate_skus_remain_separate(db):
    add_order(db, product_line())
    add_order(db, product_line(), identifier="order-2")
    other = product_line(name="Different monitor sharing a vendor SKU")
    other["productId"] = "other-monitor-id"
    add_order(db, other, identifier="order-3")
    db.add_all([
        ShippingProduct(product_id="monitor-id", name="Dell UltraSharp 49 Monitor", sku="DMU4924DW", weight_lb=Decimal("35.930")),
        ShippingProduct(product_id="other-monitor-id", name="Other monitor", sku="DMU4924DW", weight_lb=Decimal("20.000"))])
    db.commit()
    products = {product["product_id"]: product for product in ShipmentService(db).catalog()}
    assert len(products) == 2
    assert products["monitor-id"]["weight_lb"] == 35.93
    assert products["other-monitor-id"]["weight_lb"] == 20
