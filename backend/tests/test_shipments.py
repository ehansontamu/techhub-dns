"""Sandbox shipping tests. No network, printer, or production database access."""

import base64
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

import httpx
import pytest
from flask import Flask, g, request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.order import Order
from app.models.print_job import PrintJob
from app.models.shipment import Shipment, ShippingProduct
from app.models.shipping_contact import ShippingContact
from app.schemas.shipment import PackingDraft, ProductWeightUpdate
from app.services.fedex_service import FedExRejected, FedExService
from app.services.print_job_service import PrintJobService
from app.services.shipment_service import ShipmentService, shipping_items
from app.services.shipping_contacts import parse_fedex_csv, import_fedex_contacts, saved_fedex_contacts
from app.utils.exceptions import ConflictError, ValidationError


RECIPIENT = dict(name="Test Recipient", company="", phone="9795550100", address1="123 Test St", address2="",
                 city="Houston", state="TX", postal_code="77001", country="US", residential=False)


def line(product_id, quantity, name=None):
    return {"productId": product_id, "description": name or product_id, "product": {"name": name or product_id, "sku": product_id},
            "quantity": {"standardQuantity": str(quantity)}}


def box(items=None, **kwargs):
    return dict(name="Box 1", use_as_box=False, items=items or [{"product_id": "laptop", "quantity": 2}],
                **kwargs)


def plan(boxes=None, revision=0):
    return PackingDraft(expected_revision=revision, recipient=RECIPIENT, contact_id="contact-1", service_type="FEDEX_GROUND", boxes=boxes or [box()])


def carrier_reply(count):
    return {"output": {"transactionShipments": [{"masterTrackingNumber": "master-test", "pieceResponses": [
        {"trackingNumber": f"tracking-{i}", "packageSequenceNumber": i + 1,
         "packageDocuments": [{"contentType": "LABEL", "encodedLabel": base64.b64encode(b"%PDF-1.4\ntest label").decode()}]}
        for i in range(count)
    ]}]}}


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    order = Order(id="order-1", inflow_order_id="TH-SANDBOX", status="shipping", qa_method="Shipping", qa_completed_at=datetime.utcnow(),
                  inflow_data={"lines": [line("laptop", 2)], "pickLines": [line("laptop", 2)], "packLines": []})
    session.add_all([ShippingContact(id="contact-1", label="Test Recipient", recipient=RECIPIENT, search_text="test recipient"), order, ShippingProduct(product_id="laptop", name="Laptop", sku="laptop", weight_lb=Decimal("3.125"))])
    session.commit()
    yield session
    session.close()
    engine.dispose()


def service(db, is_admin=False):
    carrier = Mock(spec=FedExService)
    carrier.environment = "sandbox"
    carrier.config_status.return_value = {"environment": "sandbox", "configured": True, "missing": []}
    carrier.build_request.return_value = {"test": True}
    carrier.create_shipment.return_value = carrier_reply(1)
    carrier.rates.return_value = [{"service_type": "FEDEX_GROUND", "name": "FedEx Ground", "amount": "12.34", "currency": "USD"}, {"service_type": "FEDEX_2_DAY", "name": "FedEx 2Day", "amount": "28.90", "currency": "USD"}]
    carrier.extract_packages.side_effect = FedExService.extract_packages
    return ShipmentService(db, carrier, is_admin=is_admin)


def test_weights_use_only_product_quantities(db):
    svc = service(db)
    computed = svc.calculate(svc.order("order-1"), plan())
    assert computed[0]["product_weight_lb"] == 6.25
    assert "packaging_weight_lb" not in computed[0]
    assert computed[0]["shipping_weight_lb"] == 6.25


@pytest.mark.parametrize("quantity", [1, 3])
def test_under_and_over_packing_are_rejected(db, quantity):
    with pytest.raises(ValidationError, match="exactly once"):
        service(db).calculate(db.get(Order, "order-1"), plan([box(items=[{"product_id": "laptop", "quantity": quantity}])]))


def test_own_box_is_one_unit_and_can_split_multiple_units(db):
    draft = plan()
    draft.boxes[0].use_as_box = True
    with pytest.raises(ValidationError, match="exactly one"):
        service(db).calculate(db.get(Order, "order-1"), draft)
    one = box(items=[{"product_id": "laptop", "quantity": 1}])
    one.update(use_as_box=True)
    boxes = service(db).calculate(db.get(Order, "order-1"), plan([one, {**one, "name": "Monitor 2"}]))
    assert len(boxes) == 2
    assert boxes[0]["shipping_weight_lb"] == 3.125


def test_missing_weights_and_foreign_items_block_creation(db):
    draft = plan([box(items=[{"product_id": "other-order-item", "quantity": 2}])])
    with pytest.raises(ValidationError, match="not on this order"):
        service(db).calculate(db.get(Order, "order-1"), draft)
    db.get(ShippingProduct, "laptop").weight_lb = None
    db.commit()
    with pytest.raises(ValidationError, match="Save a product weight"):
        service(db).save_draft("order-1", plan(), "staff")


def test_qa_is_required_on_server(db):
    db.get(Order, "order-1").qa_completed_at = None
    with pytest.raises(ValidationError, match="shipping QA"):
        service(db).save_draft("order-1", plan(), "staff")


def test_package_weight_limit_is_enforced(db):
    db.get(ShippingProduct, "laptop").weight_lb = Decimal("80")
    db.commit()
    with pytest.raises(ValidationError, match="at most 150"):
        service(db).calculate(db.get(Order, "order-1"), plan())


def test_full_catalog_import_preserves_saved_weights(db, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "inflow_api_key", "test-only")
    real_client = httpx.Client

    def handler(request):
        assert request.url.path.endswith("/products")
        batch = [{"productId": "laptop", "name": "New Laptop Name", "sku": "NEW"},
                 {"productId": "monitor", "name": "Monitor", "sku": "MON"}] if request.url.params["skip"] == "0" else []
        return httpx.Response(200, json=batch)

    with patch("app.services.shipment_service.httpx.Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)):
        assert service(db).import_catalog("admin") == 2
    assert db.get(ShippingProduct, "laptop").weight_lb == Decimal("3.125")
    assert db.get(ShippingProduct, "laptop").name == "New Laptop Name"
    assert db.get(ShippingProduct, "monitor").weight_lb is None


def test_partial_leg_excludes_services_and_previously_shipped_units(db):
    order = db.get(Order, "order-1")
    order.inflow_data = {"lines": [line("laptop", 9), line("service", 1, "Computer Imaging")],
                         "pickLines": [line("laptop", 2), line("service", 1, "Computer Imaging")],
                         "packLines": [line("laptop", 1)]}
    assert shipping_items(order) == [{"product_id": "laptop", "name": "laptop", "sku": "laptop", "quantity": 1}]


def test_old_draft_revision_and_changed_weights_block_submission(db):
    svc = service(db)
    shipment = svc.save_draft("order-1", plan(), "staff")
    with pytest.raises(ConflictError):
        svc.save_draft("order-1", plan(revision=0), "other-user")
    db.rollback()
    svc.save_weight("laptop", ProductWeightUpdate(name="Laptop", weight_lb=4), "admin")
    with pytest.raises(ConflictError, match="Product weights"):
        svc.create("order-1", shipment.revision, "staff")
    svc.carrier.create_shipment.assert_not_called()


def test_create_is_durable_idempotent_and_queues_labels_without_handoff(db):
    svc = service(db)
    draft = svc.save_draft("order-1", plan(), "staff")
    svc.quote_rates("order-1", draft.revision, "staff")
    revision = draft.revision
    shipment = svc.create("order-1", revision, "staff")
    assert shipment.status == "created"
    assert shipment.environment == "sandbox"
    assert shipment.master_tracking_number == "master-test"
    assert shipment.carrier_response == carrier_reply(1)
    assert svc.label(shipment.id, 0).startswith(b"%PDF-")
    assert svc.create("order-1", revision, "staff").id == shipment.id
    svc.carrier.create_shipment.assert_called_once()
    assert db.query(PrintJob).count() == 1
    job = db.query(PrintJob).one()
    assert job.document_type == "shipping_label"
    assert db.get(Order, "order-1").status == "shipping"
    assert db.get(Order, "order-1").tracking_number is None  # Sandbox must not contaminate real handoff data.
    assert svc.queue_label(shipment, 0, "staff").id == job.id


def test_timeout_locks_shipment_against_automatic_resubmission(db):
    svc = service(db)
    draft = svc.save_draft("order-1", plan(), "staff")
    svc.quote_rates("order-1", draft.revision, "staff")
    svc.carrier.create_shipment.side_effect = httpx.ReadTimeout("lost reply")
    shipment = svc.create("order-1", draft.revision, "staff")
    assert shipment.status == "needs_review"
    svc.create("order-1", shipment.revision, "staff")
    svc.carrier.create_shipment.assert_called_once()
    with pytest.raises(ConflictError):
        svc.save_draft("order-1", plan(revision=shipment.revision), "staff")
    assert db.query(PrintJob).count() == 0


def test_rejection_can_be_corrected_and_resubmitted(db):
    svc = service(db)
    draft = svc.save_draft("order-1", plan(), "staff")
    svc.quote_rates("order-1", draft.revision, "staff")
    svc.carrier.create_shipment.side_effect = FedExRejected("Address rejected")
    rejected = svc.create("order-1", draft.revision, "staff")
    assert rejected.status == "rejected"
    corrected = svc.save_draft("order-1", plan(revision=rejected.revision), "staff")
    svc.carrier.create_shipment.side_effect = None
    svc.quote_rates("order-1", corrected.revision, "staff")
    assert svc.create("order-1", corrected.revision, "staff").status == "created"


def test_missing_label_preserves_response_and_never_recreates(db):
    svc = service(db)
    draft = svc.save_draft("order-1", plan(), "staff")
    svc.quote_rates("order-1", draft.revision, "staff")
    response = carrier_reply(1)
    response["output"]["transactionShipments"][0]["pieceResponses"][0]["packageDocuments"] = []
    svc.carrier.create_shipment.return_value = response
    shipment = svc.create("order-1", draft.revision, "staff")
    assert shipment.status == "needs_review"
    assert shipment.carrier_response == response
    svc.create("order-1", shipment.revision, "staff")
    svc.carrier.create_shipment.assert_called_once()


def test_legacy_agent_never_claims_labels_and_new_agent_can(db):
    svc = service(db)
    draft = svc.save_draft("order-1", plan(), "staff")
    svc.quote_rates("order-1", draft.revision, "staff")
    svc.create("order-1", draft.revision, "staff")
    queue = PrintJobService(db)
    assert queue.claim_next_pending_job(claim_timeout_seconds=60) is None
    label = queue.claim_next_pending_job(claim_timeout_seconds=60, document_types=("picklist", "shipping_label"))
    assert label.document_type == "shipping_label"
    label.claim_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    queue.release_expired_claims(claim_timeout_seconds=60)
    assert label.status == "failed"  # Avoid duplicate labels after a lost printer acknowledgement.


def test_sandbox_payload_has_one_package_per_box_and_pdf_labels(db, monkeypatch):
    from app.config import settings
    import json
    for key, value in dict(fedex_enabled=True, fedex_client_id="test", fedex_client_secret="test", fedex_account_number="test-account", fedex_shipper_json=json.dumps(RECIPIENT)).items():
        monkeypatch.setattr(settings, key, value)
    svc = service(db)
    draft = plan()
    payload = FedExService().build_request(draft.model_dump(), svc.calculate(svc.order("order-1"), draft), "TH-1")
    assert FedExService.base_url == "https://apis-sandbox.fedex.com"
    assert payload["requestedShipment"]["labelSpecification"]["labelStockType"] == "PAPER_4X6"
    assert payload["requestedShipment"]["requestedPackageLineItems"][0]["weight"] == {"units": "LB", "value": 6.25}
    assert payload["processingOptionType"] == "SYNCHRONOUS_ONLY"
    assert "dimensions" not in payload["requestedShipment"]["requestedPackageLineItems"][0]


def test_multibox_labels_follow_package_sequence_not_response_order():
    reply = carrier_reply(2)
    reply["output"]["transactionShipments"][0]["pieceResponses"].reverse()
    _, packages = FedExService.extract_packages(reply, [box(), {**box(), "name": "Box 2"}])
    assert packages[0]["tracking_number"] == "tracking-0"
    assert packages[1]["tracking_number"] == "tracking-1"


def rate_reply():
    return {"output": {"rateReplyDetails": [
        {"serviceType": "FEDEX_2_DAY", "ratedShipmentDetails": [{"rateType": "ACCOUNT", "totalNetCharge": 29.40, "shipmentRateDetail": {"currency": "USD"}}]},
        {"serviceType": "FEDEX_GROUND", "ratedShipmentDetails": [{"rateType": "LIST", "totalNetCharge": 7, "shipmentRateDetail": {"currency": "USD"}},
                                                               {"rateType": "ACCOUNT", "totalNetCharge": 12.34, "shipmentRateDetail": {"currency": "USD"}}]},
        {"serviceType": "GROUND_HOME_DELIVERY", "ratedShipmentDetails": [{"rateType": "ACCOUNT", "totalNetCharge": 10, "shipmentRateDetail": {"currency": "USD"}}]},
        {"serviceType": "FEDEX_FREIGHT_ECONOMY", "ratedShipmentDetails": [{"rateType": "ACCOUNT", "totalNetCharge": 1, "shipmentRateDetail": {"currency": "USD"}}]},
    ]}}


def test_rates_sort_actual_account_totals_and_filter_ineligible_services():
    options = FedExService.extract_rates(rate_reply(), residential=False)
    assert [option["service_type"] for option in options] == ["FEDEX_GROUND", "FEDEX_2_DAY"]
    assert options[0]["amount"] == "12.34"  # Don't use the lower LIST rate.
    assert FedExService.extract_rates(rate_reply(), residential=True)[0]["service_type"] == "GROUND_HOME_DELIVERY"


def test_only_cheapest_rate_is_saved_and_exposed_even_if_carrier_returns_unsorted(db):
    svc = service(db)
    svc.carrier.rates.return_value.reverse()
    shipment = svc.save_draft("order-1", plan(), "staff")
    with pytest.raises(ValidationError, match="Get FedEx prices"):
        svc.create("order-1", shipment.revision, "staff")
    shipment = svc.quote_rates("order-1", shipment.revision, "staff")
    assert shipment.draft["service_type"] == "FEDEX_GROUND"
    assert svc.checked_rate(shipment)["amount"] == "12.34"
    assert len(svc.serialize(shipment)["draft"]["rate_quote"]["options"]) == 1
    assert "28.90" not in str(svc.serialize(shipment))
    svc.carrier.create_shipment.assert_not_called()


def test_legacy_multi_service_quotes_are_hidden_and_require_refresh(db):
    svc = service(db)
    shipment = svc.save_draft("order-1", plan(), "staff")
    svc.quote_rates("order-1", shipment.revision, "staff")
    quote = dict(shipment.draft["rate_quote"])
    quote.pop("policy")
    quote["options"] = svc.carrier.rates.return_value
    shipment.draft = {**shipment.draft, "rate_quote": quote, "service_type": "FEDEX_2_DAY"}
    db.commit()
    assert "rate_quote" not in svc.serialize(shipment)["draft"]
    with pytest.raises(ValidationError, match="cheapest-only"):
        svc.create("order-1", shipment.revision, "staff")
    svc.carrier.create_shipment.assert_not_called()


def test_more_expensive_service_cannot_be_used_after_cheapest_quote(db):
    svc = service(db)
    shipment = svc.save_draft("order-1", plan(), "staff")
    svc.quote_rates("order-1", shipment.revision, "staff")
    shipment.draft = {**shipment.draft, "service_type": "FEDEX_2_DAY"}
    db.commit()
    with pytest.raises(ValidationError, match="only the cheapest"):
        svc.create("order-1", shipment.revision, "staff")
    svc.carrier.create_shipment.assert_not_called()


def test_expired_quotes_and_address_changes_block_old_prices(db):
    svc = service(db)
    shipment = svc.save_draft("order-1", plan(), "staff")
    svc.quote_rates("order-1", shipment.revision, "staff")
    shipment.draft = {**shipment.draft, "rate_quote": {**shipment.draft["rate_quote"], "expires_at": "2000-01-01T00:00:00Z"}}
    db.commit()
    with pytest.raises(ValidationError, match="expired"):
        svc.create("order-1", shipment.revision, "staff")
    edited = plan(revision=shipment.revision)
    edited.recipient.address1 = "456 New Address"
    edited.manual_address_override = True
    svc.is_admin = True
    svc.save_draft("order-1", edited, "staff")
    assert "rate_quote" not in shipment.draft
    svc.carrier.create_shipment.assert_not_called()


def test_rate_response_for_stale_packing_is_discarded(db):
    svc = service(db)
    shipment = svc.save_draft("order-1", plan(), "staff")

    def change_while_loading(*_args):
        svc.save_draft("order-1", plan(revision=shipment.revision), "other-staff")
        return [{"service_type": "FEDEX_GROUND", "name": "FedEx Ground", "amount": "12.34", "currency": "USD"}]

    svc.carrier.rates.side_effect = change_while_loading
    with pytest.raises(ConflictError, match="while prices loaded"):
        svc.quote_rates("order-1", shipment.revision, "staff")


CSV_CONTACTS = b"Contact ID,Contact Name,Company,Phone Number,Address Line 1,Address Line 2,City,State,Postal Code,Country,Residential\n123,Test Recipient,Test Dept,9795550100,123 Test St,Suite 4,Houston,TX,77001,US,Y\n"


def test_fedex_csv_import_and_search_preserve_phone_and_address(db):
    assert import_fedex_contacts(db, CSV_CONTACTS, "admin") == 1
    result = saved_fedex_contacts(db, "test dept")
    assert len(result) == 1
    assert result[0]["recipient"]["phone"] == "9795550100"
    assert result[0]["recipient"]["address2"] == "Suite 4"
    assert result[0]["recipient"]["residential"] is True
    assert saved_fedex_contacts(db, "unrelated") == []


def test_fedex_reimport_updates_existing_contact_without_duplicates(db):
    import_fedex_contacts(db, CSV_CONTACTS, "admin")
    import_fedex_contacts(db, CSV_CONTACTS.replace(b"9795550100", b"9795550199"), "admin")
    result = saved_fedex_contacts(db, "test dept")
    assert len(result) == 1
    assert result[0]["recipient"]["phone"] == "9795550199"


def test_imported_address_book_is_available_in_a_new_session_without_reupload(db):
    import_fedex_contacts(db, CSV_CONTACTS, "admin")
    other = sessionmaker(bind=db.bind)()
    try:
        assert saved_fedex_contacts(other, "test dept")[0]["recipient"]["phone"] == "9795550100"
    finally:
        other.close()


def test_fedex_csv_header_aliases_and_zip_zeroes():
    csv = b"Name,Street,City,Province,ZIP,Country/Territory\nTest,123 Street,Boston,MA,02110,USA\n"
    recipient = parse_fedex_csv(csv)[0]["recipient"]
    assert recipient["postal_code"] == "02110"
    assert recipient["country"] == "US"


@pytest.mark.parametrize("csv", [b"name\nTest\n", CSV_CONTACTS + b"bad,missing,address\n", CSV_CONTACTS.replace(b"Contact ID", b"Contact Name")])
def test_invalid_fedex_csv_is_rejected_before_any_contacts_are_written(db, csv):
    before = saved_fedex_contacts(db)
    with pytest.raises(ValidationError):
        import_fedex_contacts(db, csv, "admin")
    assert saved_fedex_contacts(db) == before


def test_rates_api_sends_weight_only_without_a_preset_service(db, monkeypatch):
    import json
    from app.config import settings
    for key, value in dict(fedex_enabled=True, fedex_client_id="test", fedex_client_secret="test", fedex_account_number="test-account", fedex_shipper_json=json.dumps(RECIPIENT)).items():
        monkeypatch.setattr(settings, key, value)
    bodies = []

    def handler(request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "test-only"})
        assert request.url.path == "/rate/v1/rates/quotes"
        body = json.loads(request.content)
        assert "serviceType" not in body["requestedShipment"]
        assert "dimensions" not in body["requestedShipment"]["requestedPackageLineItems"][0]
        bodies.append(body)
        return httpx.Response(200, json=rate_reply())

    client_type = httpx.Client
    svc = service(db)
    boxes = svc.calculate(svc.order("order-1"), plan())
    with patch("app.services.fedex_service.httpx.Client", side_effect=lambda **kw: client_type(transport=httpx.MockTransport(handler), **kw)):
        assert FedExService().rates(plan().model_dump(), boxes)[0]["amount"] == "12.34"
    assert len(bodies) == 1


def test_another_worker_sees_creation_in_progress_and_does_not_resubmit(db):
    svc = service(db)
    draft = svc.save_draft("order-1", plan(), "staff")
    svc.quote_rates("order-1", draft.revision, "staff")
    revision = draft.revision

    def during_carrier_call(*_args):
        other_db = sessionmaker(bind=db.bind)()
        try:
            other = service(other_db)
            result = other.create("order-1", revision, "other-staff")
            assert result.status == "creating"
            other.carrier.create_shipment.assert_not_called()
        finally:
            other_db.close()
        return carrier_reply(1)

    svc.carrier.create_shipment.side_effect = during_carrier_call
    assert svc.create("order-1", revision, "staff").status == "created"


def test_saved_response_can_finish_after_interruption_without_new_carrier_request(db):
    svc = service(db)
    shipment = svc.save_draft("order-1", plan(), "staff")
    shipment.status = "processing_response"
    shipment.environment = "sandbox"
    shipment.carrier_response = carrier_reply(1)
    db.commit()
    assert svc.create("order-1", shipment.revision, "staff").status == "created"
    svc.carrier.create_shipment.assert_not_called()
    assert db.query(PrintJob).count() == 1


def test_fedex_transport_uses_sandbox_and_does_not_retry_a_lost_reply(monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "sandbox-test"})
        raise httpx.ReadTimeout("Shipment outcome unknown")

    client_type = httpx.Client
    with patch("app.services.fedex_service.httpx.Client", side_effect=lambda **kw: client_type(transport=httpx.MockTransport(handler), **kw)):
        with pytest.raises(httpx.ReadTimeout):
            FedExService().create_shipment({"test": True}, "transaction-1")
    assert calls == ["https://apis-sandbox.fedex.com/oauth/token", "https://apis-sandbox.fedex.com/ship/v1/shipments"]


def test_http_routes_require_auth_and_admin_for_weights(db):
    from contextlib import contextmanager
    from app.api.routes.shipments import bp
    from app.api.middleware import register_error_handlers

    @contextmanager
    def session():
        yield db

    app = Flask(__name__)
    app.register_blueprint(bp)
    register_error_handlers(app)

    @app.before_request
    def identity():
        if request.headers.get("X-Test-User"):
            g.user_id = "test-staff"
            g.user_email = "staff@example.com"

    client = app.test_client()
    assert client.get("/api/shipping/orders/order-1").status_code == 401
    assert client.get("/api/shipping/orders/order-1/contacts").status_code == 401
    with patch("app.api.routes.shipments.get_db", session), patch("app.api.auth_middleware.is_current_user_admin", return_value=False):
        assert client.put("/api/shipping/products/laptop", json={"name": "Laptop", "weight_lb": 5}, headers={"X-Test-User": "1"}).status_code == 403
        assert client.post("/api/shipping/contacts/import", headers={"X-Test-User": "1"}).status_code == 403
        assert client.put("/api/shipping/orders/order-1/packing", json={}, headers={"X-Test-User": "1"}).status_code == 400


@pytest.mark.parametrize("patch_data", [{"packaging_weight_lb": -1}, {"measured_weight_lb": float("nan")}, {"length_in": 1.5}])
def test_invalid_units_rejected_at_schema(patch_data):
    from pydantic import ValidationError as SchemaError
    invalid_box = {**box(), **patch_data}
    with pytest.raises(SchemaError):
        plan([invalid_box])


@pytest.mark.parametrize("amount,confirmation_needed", [("99.99", False), ("100.00", False), ("100.01", True), ("125.00", True)])
def test_high_cost_confirmation_is_required_only_above_100(db, amount, confirmation_needed):
    svc = service(db)
    svc.carrier.rates.return_value = [{"service_type": "FEDEX_GROUND", "name": "FedEx Ground", "amount": amount, "currency": "USD"}]
    shipment = svc.save_draft("order-1", plan(), "staff")
    svc.quote_rates("order-1", shipment.revision, "staff")
    if confirmation_needed:
        with pytest.raises(ValidationError, match="Are you sure"):
            svc.create("order-1", shipment.revision, "staff")
        svc.carrier.create_shipment.assert_not_called()
        assert svc.create("order-1", shipment.revision, "staff", confirm_high_shipping_cost=True).status == "created"
    else:
        assert svc.create("order-1", shipment.revision, "staff").status == "created"


def test_saved_contact_is_required_and_recipient_changes_are_blocked(db):
    svc = service(db)
    draft = plan()
    draft.contact_id = None
    with pytest.raises(ValidationError, match="Select a saved"):
        svc.save_draft("order-1", draft, "staff")
    draft.contact_id = "contact-1"
    draft.recipient.address1 = "Unapproved destination"
    with pytest.raises(ValidationError, match="must match"):
        svc.save_draft("order-1", draft, "staff")


def test_only_admin_can_use_one_off_address_and_it_does_not_change_book(db):
    draft = plan()
    draft.contact_id = None
    draft.manual_address_override = True
    draft.recipient.address1 = "One-off destination"
    with pytest.raises(ValidationError, match="Only an admin"):
        service(db).save_draft("order-1", draft, "staff")
    svc = service(db, is_admin=True)
    shipment = svc.save_draft("order-1", draft, "admin")
    svc.quote_rates("order-1", shipment.revision, "admin")
    assert db.query(ShippingContact).count() == 1
    assert db.get(ShippingContact, "contact-1").recipient["address1"] == RECIPIENT["address1"]
    with pytest.raises(ValidationError, match="Only an admin"):
        service(db).create("order-1", shipment.revision, "staff")


def test_changed_or_removed_contact_requires_reselection_before_shipping(db):
    svc = service(db)
    shipment = svc.save_draft("order-1", plan(), "staff")
    svc.quote_rates("order-1", shipment.revision, "staff")
    contact = db.get(ShippingContact, "contact-1")
    contact.recipient = {**contact.recipient, "address1": "Updated destination"}
    db.commit()
    with pytest.raises(ValidationError, match="must match"):
        svc.create("order-1", shipment.revision, "staff")
    svc.carrier.create_shipment.assert_not_called()

# Combined shipments retain per-order product ownership and one saved destination.
def destination_orders(db, second_unit="3302"):
    contact = db.get(ShippingContact, "contact-1")
    contact.label = "Dallas 3302"
    contact.recipient = {**RECIPIENT, "city": "Dallas", "address2": "3302"}
    first = db.get(Order, "order-1")
    first.inflow_data = {**first.inflow_data, "shippingAddress": {
        "city": "Dallas", "address1": RECIPIENT["address1"], "address2": "3302", "stateProvince": "TX", "postalCode": "77001"}}
    second = Order(id="order-2", inflow_order_id="TH-SECOND", status="shipping", qa_method="Shipping", qa_completed_at=datetime.utcnow(),
        inflow_data={"lines": [line("laptop", 1)], "pickLines": [line("laptop", 1)], "shippingAddress": {
            **first.inflow_data["shippingAddress"], "address2": second_unit}})
    db.add_all([second, ShippingContact(id="dallas-3300", label="Dallas 3300", search_text="dallas 3300",
        recipient={**contact.recipient, "address2": "3300"})])
    db.commit()
    return first, second


def combined_plan(db):
    return PackingDraft(expected_revision=0, order_ids=["order-1", "order-2"],
        recipient=db.get(ShippingContact, "contact-1").recipient, contact_id="contact-1", boxes=[box(items=[
            {"order_id": "order-1", "product_id": "laptop", "quantity": 2},
            {"order_id": "order-2", "product_id": "laptop", "quantity": 1}])])


def test_queue_separates_named_locations_and_excludes_unready_and_handed_off(db):
    first, second = destination_orders(db, "3300")
    svc = service(db)
    locations = svc.shipping_queue()["cities"][0]["locations"]
    assert {row["name"]: [order["id"] for order in row["orders"]] for row in locations} == {
        "Dallas 3300": ["order-2"], "Dallas 3302": ["order-1"]}
    second.qa_completed_at = None
    first.shipping_workflow_status = "shipped"
    db.commit()
    assert svc.shipping_queue() == {"cities": [], "unmatched": []}


def test_ambiguous_city_goes_to_review_instead_of_guessing(db):
    first, _ = destination_orders(db)
    first.inflow_data = {**first.inflow_data, "shippingAddress": {"city": "Dallas", "address1": RECIPIENT["address1"]}}
    db.commit()
    queue = service(db).shipping_queue()
    assert [row["id"] for row in queue["unmatched"]] == ["order-1"]
    assert len(queue["cities"][0]["locations"]) == 1


def test_location_identifier_can_match_contact_name(db):
    from app.services.shipping_destinations import destination_contact
    first, _ = destination_orders(db)
    contact = db.get(ShippingContact, "contact-1")
    contact.recipient = {**contact.recipient, "address2": ""}
    first.inflow_data = {**first.inflow_data, "shippingAddress": {"city": "Dallas", "address1": RECIPIENT["address1"], "address2": "Dallas 3302"}}
    assert destination_contact(first, db.query(ShippingContact).all()).id == "contact-1"


def test_single_location_city_matches_and_prefills_saved_recipient(db):
    first = db.get(Order, "order-1")
    contact = db.get(ShippingContact, "contact-1")
    contact.label = "Galveston"
    contact.recipient = {**RECIPIENT, "city": "Galveston"}
    first.inflow_data = {**first.inflow_data, "shippingAddress": {"city": "Galveston", "address1": RECIPIENT["address1"]}}
    db.commit()
    svc = service(db)
    queue = svc.shipping_queue()
    assert queue["cities"][0]["name"] == "Galveston"
    assert len(queue["cities"][0]["locations"]) == 1
    workspace = svc.workspace(first.id, [first.id], contact.id)
    assert workspace["contact_id"] == contact.id
    assert workspace["recipient"] == contact.recipient


def test_combined_shared_sku_weights_reservations_and_follower_access(db):
    from app.models.shipment_order import ShipmentOrder
    destination_orders(db)
    svc = service(db)
    shipment = svc.save_draft("order-1", combined_plan(db), "staff")
    assert shipment.draft["computed_boxes"][0]["shipping_weight_lb"] == 9.375
    assert db.query(ShipmentOrder).count() == 2
    assert svc.get("order-2").id == shipment.id
    workspace = svc.workspace("order-2")
    assert set(workspace["order_ids"]) == {"order-1", "order-2"}
    assert {(row["order_id"], row["quantity"]) for row in workspace["items"]} == {("order-1", 2), ("order-2", 1)}
    svc.quote_rates("order-2", shipment.revision, "staff")
    created = svc.create("order-2", shipment.revision, "staff")
    assert created.status == "created"
    svc.carrier.build_request.assert_called_once_with(created.draft, created.draft["computed_boxes"], "TH-SANDBOX")
    assert svc.create("order-1", created.revision, "staff").id == created.id
    svc.carrier.create_shipment.assert_called_once()
    assert db.get(Order, "order-1").status == db.get(Order, "order-2").status == "shipping"


@pytest.mark.parametrize("wrong_owner", [False, True])
def test_combined_products_require_exact_per_order_quantities(db, wrong_owner):
    destination_orders(db)
    draft = combined_plan(db)
    if wrong_owner:
        draft.boxes[0].items[1].order_id = "order-1"
    else:
        draft.boxes[0].items[0].quantity = 1
        draft.boxes[0].items[1].quantity = 2
    with pytest.raises(ValidationError, match="exactly once"):
        service(db).save_draft("order-1", draft, "staff")


@pytest.mark.parametrize("admin_override", [False, True])
def test_different_locations_cannot_be_combined_even_by_admin(db, admin_override):
    destination_orders(db, "3300")
    draft = combined_plan(db)
    draft.manual_address_override = admin_override
    with pytest.raises(ValidationError, match="same saved location"):
        service(db, is_admin=admin_override).save_draft("order-1", draft, "staff")
    assert db.query(Shipment).count() == 0


def test_member_already_reserved_rejects_entire_group(db):
    from app.models.shipment_order import ShipmentOrder
    destination_orders(db)
    svc = service(db)
    second = PackingDraft(expected_revision=0, recipient=db.get(ShippingContact, "contact-1").recipient,
        contact_id="contact-1", boxes=[box(items=[{"product_id": "laptop", "quantity": 1}])])
    existing = svc.save_draft("order-2", second, "staff")
    with pytest.raises(ConflictError, match="already assigned"):
        svc.save_draft("order-1", combined_plan(db), "staff")
    assert db.query(Shipment).count() == db.query(ShipmentOrder).count() == 1
    assert svc.get("order-2").id == existing.id
    assert svc.get("order-1") is None


@pytest.mark.parametrize("change", ["qa", "location", "handoff"])
def test_each_member_is_revalidated_before_carrier_submission(db, change):
    _, second = destination_orders(db)
    svc = service(db)
    shipment = svc.save_draft("order-1", combined_plan(db), "staff")
    svc.quote_rates("order-1", shipment.revision, "staff")
    if change == "qa":
        second.qa_completed_at = None
    elif change == "handoff":
        second.shipping_workflow_status = "shipped"
    else:
        second.inflow_data = {**second.inflow_data, "shippingAddress": {**second.inflow_data["shippingAddress"], "address2": "3300"}}
    db.commit()
    with pytest.raises(ValidationError):
        svc.create("order-1", shipment.revision, "staff")
    svc.carrier.create_shipment.assert_not_called()


def test_removing_member_releases_only_that_order_reservation(db):
    from app.models.shipment_order import ShipmentOrder
    destination_orders(db)
    svc = service(db)
    shipment = svc.save_draft("order-1", combined_plan(db), "staff")
    single = PackingDraft(expected_revision=shipment.revision, order_ids=["order-1"],
        recipient=db.get(ShippingContact, "contact-1").recipient, contact_id="contact-1", boxes=[box()])
    svc.save_draft("order-1", single, "staff")
    assert svc.get("order-2") is None
    assert db.query(ShipmentOrder).count() == 1
