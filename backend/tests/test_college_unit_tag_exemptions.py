"""College/Unit policy, admin settings, and order workflow regressions."""

import json
import os
import sys
from datetime import datetime
from unittest.mock import Mock

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
sys.path.append(".")

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import orders as orders_routes
from app.api.routes import system as system_routes
from app.database import Base
from app.models.audit_log import SystemAuditLog
from app.models.order import Order, OrderStatus
from app.models.system_setting import SystemSetting
from app.services import asset_tag_policy_service, system_setting_service
from app.services.asset_tag_policy_service import AssetTagPolicyService
from app.services.order_service import OrderService
from app.services.order_splitting import OrderSplittingService
from app.services.system_setting_service import (
    SETTING_ASSET_TAG_EXEMPT_COLLEGE_UNITS,
    SystemSettingService,
)
from app.utils.exceptions import ValidationError

TTI = "TTI - Texas A&M Transportation Institute"


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'exemptions.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(system_setting_service, "get_db_session", factory)
    monkeypatch.setattr(asset_tag_policy_service, "get_db_session", factory)
    monkeypatch.setattr(system_routes, "get_db_session", factory)
    monkeypatch.setattr(orders_routes, "get_db", factory)
    # Category lookups must never contact inFlow in these tests.
    monkeypatch.setattr("app.services.inflow_service.InflowService.get_category_map_sync", lambda _self: {})
    yield factory
    engine.dispose()


def make_order(db, college_unit=TTI):
    order = Order(
        inflow_order_id="TH5727",
        status=OrderStatus.PICKED.value,
        inflow_data={
            "customFields": {"custom1": college_unit},
            "lines": [{
                "productId": "laptop",
                "unitPrice": 2260,
                "product": {"name": "Laptop", "category": {"name": "Laptops"}},
                "quantity": {"standardQuantity": 2},
            }],
            "pickLines": [{"productId": "laptop", "quantity": {"standardQuantity": 1}}],
        },
    )
    db.add(order)
    db.commit()
    return order


def test_default_exempts_tti_and_keeps_prep_checks(sessions):
    with sessions() as db:
        order = make_order(db, "  tti - Texas A&M   Transportation Institute  ")
        service = OrderService(db)
        assert service._requires_asset_tags(order) is False
        assert service._get_incomplete_steps(order) == ["picklist", "qa"]
        assert service._prep_steps_complete(order) is False
        order.picklist_generated_at = datetime.utcnow()
        order.qa_completed_at = datetime.utcnow()
        assert service._prep_steps_complete(order) is True
        assert order.tagged_at is None


@pytest.mark.parametrize("unit", [None, "", "TTI", f"{TTI} - Other", "Other College"])
def test_missing_or_nonmatching_units_keep_product_rules(sessions, monkeypatch, unit):
    monkeypatch.setattr(SystemSettingService, "is_setting_enabled", lambda _key: True)
    with sessions() as db:
        order = make_order(db, unit)
        assert AssetTagPolicyService.is_college_unit_exempt(order.inflow_data, db) is False
        assert OrderService(db)._requires_asset_tags(order) is True


def test_removing_default_and_adding_unit_is_persistent_and_audited(sessions):
    SystemSettingService.set_setting(SETTING_ASSET_TAG_EXEMPT_COLLEGE_UNITS, "[]", "admin@example.com")
    with sessions() as db:
        assert not AssetTagPolicyService.is_college_unit_exempt({"customFields": {"custom1": TTI}}, db)
        audit = db.query(SystemAuditLog).one()
        assert audit.user_id == "admin@example.com"
        assert json.loads(audit.old_value["value"]) == [TTI]
        assert audit.new_value == {"value": "[]"}
    SystemSettingService.set_setting(SETTING_ASSET_TAG_EXEMPT_COLLEGE_UNITS, '["Other College"]')
    with sessions() as db:
        assert AssetTagPolicyService.is_college_unit_exempt({"customFields": {"custom1": "other college"}}, db)


def test_invalid_saved_exemptions_keep_product_rules(sessions, monkeypatch):
    monkeypatch.setattr(SystemSettingService, "is_setting_enabled", lambda _key: True)
    with sessions() as db:
        db.add(SystemSetting(key=SETTING_ASSET_TAG_EXEMPT_COLLEGE_UNITS, value="{}"))
        db.commit()
        order = make_order(db)
        assert OrderService(db)._requires_asset_tags(order) is True


def test_partial_legs_and_sync_keep_college_unit(sessions):
    with sessions() as db:
        order = make_order(db)
        splitting = OrderSplittingService(db)
        for snapshot in (
            splitting._build_partial_leg_view(order.inflow_data),
            splitting._build_remainder_leg_state(order.inflow_data),
        ):
            assert snapshot["customFields"]["custom1"] == TTI
            assert AssetTagPolicyService.is_college_unit_exempt(snapshot, db)
        refreshed = {**order.inflow_data, "customFields": {"custom1": "Other College"}}
        merged = OrderService(db).merge_inflow_snapshot_preserving_split(order, refreshed)
        assert AssetTagPolicyService.college_unit(merged) == "Other College"
        assert not AssetTagPolicyService.is_college_unit_exempt(merged, db)


def test_qa_exemption_records_not_required_and_ignores_client_overrides(sessions):
    with sessions() as db:
        order = make_order(db)
        responses = {"verifyAssetTagSerialMatch": True}
        service = OrderService(db)
        service._apply_qa_asset_tag_exemption(order, responses)
        assert responses == {
            "verifyAssetTagSerialMatch": False,
            "assetTagVerificationNotRequired": True,
            "assetTagExemptCollegeUnit": TTI,
        }
        order.inflow_data = {"customFields": {"custom1": "Other College"}}
        service._apply_qa_asset_tag_exemption(order, responses)
        assert responses == {"verifyAssetTagSerialMatch": False}


def test_exempt_qa_submission_preserves_policy_and_does_not_mark_tagged(sessions, monkeypatch, tmp_path):
    monkeypatch.setattr(SystemSettingService, "is_setting_enabled", lambda _key: False)
    upload = Mock(is_enabled=True)
    upload.upload_file.return_value = "https://example.com/qa/TH5727.json"
    monkeypatch.setattr("app.services.sharepoint_service.get_sharepoint_service", lambda: upload)
    with sessions() as db:
        order = make_order(db)
        order.picklist_generated_at = datetime.utcnow()
        service = OrderService(db)
        monkeypatch.setattr(service, "_local_doc_path", lambda _kind, name: tmp_path / name)
        monkeypatch.setattr(service, "_derive_qa_method", lambda _order: "Delivery")
        transition = Mock(return_value=order)
        monkeypatch.setattr(service, "transition_status", transition)
        result = service.submit_qa(order.id, {
            "orderNumber": "TH5727",
            "qaSignature": "QA Technician",
            "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved": True,
            "verifyPackagedProperly": True,
            "verifyPackingSlipSerialsMatch": True,
            "verifyBoxesLabeledCorrectly": True,
        }, technician="QA Technician")
        assert result.tagged_at is None
        assert result.qa_data["verifyAssetTagSerialMatch"] is False
        assert result.qa_data["assetTagVerificationNotRequired"] is True
        assert result.qa_data["assetTagExemptCollegeUnit"] == TTI
        uploaded_data = json.loads(upload.upload_file.call_args.args[0])
        assert uploaded_data["responses"]["assetTagVerificationNotRequired"] is True
        assert transition.call_args.kwargs["new_status"] == OrderStatus.PRE_DELIVERY


def test_exempt_qa_still_requires_a_picklist(sessions):
    with sessions() as db:
        order = make_order(db)
        with pytest.raises(ValidationError, match="Picklist must be generated"):
            OrderService(db).submit_qa(order.id, {})
        assert order.qa_completed_at is None


def test_qa_pdf_preserves_exemption_after_unit_changes(sessions):
    from pathlib import Path

    from pypdf import PdfReader

    with sessions() as db:
        order = make_order(db)
        service = OrderService(db)
        responses = {"verifyPackagedProperly": True}
        service._apply_qa_asset_tag_exemption(order, responses)
        order.inflow_data = {"customFields": {"custom1": "Other College"}}
        path = Path(service._generate_qa_pdf(responses, order))
        try:
            pdf_text = PdfReader(str(path)).pages[0].extract_text()
            assert "[N/A] Asset tagging not required (College/Unit exemption)" in pdf_text
            assert "[PASS] System and all materials packaged properly" in pdf_text
            assert "[FAIL] Boxes labeled" in pdf_text
        finally:
            path.unlink()


def test_order_responses_include_unit_and_effective_requirement(sessions):
    with sessions() as db:
        order = make_order(db)
        app = Flask(__name__)
        with app.app_context():
            for serialize in (orders_routes._order_detail_response_json, orders_routes._order_response_json):
                payload = serialize(order, db)
                assert payload["college_unit"] == TTI
                assert payload["asset_tag_exempt"] is True
                assert payload["asset_tag_required"] is False
            payload = orders_routes._serialize_order_list_item(order, db_session=db)
            assert payload["college_unit"] == TTI
            assert payload["asset_tag_required"] is False


def test_exempt_orders_are_absent_from_candidates_and_cannot_be_marked_tagged(sessions):
    with sessions() as db:
        order = make_order(db)
        with pytest.raises(ValidationError, match="Tags are not required"):
            OrderService(db).mark_asset_tagged(order.id, [])
        assert order.tagged_at is None
    app = Flask(__name__)
    with app.test_request_context("/api/orders/tag-request/candidates"):
        response = orders_routes.get_tag_request_candidates.__wrapped__()
        assert response.get_json() == []


def test_bulk_tag_rejects_exempt_order_without_completion_timestamp(sessions, monkeypatch):
    with sessions() as db:
        order_id = make_order(db).id
    monkeypatch.setattr(orders_routes, "get_current_user_email", lambda: "operator@example.com")
    app = Flask(__name__)
    with app.test_request_context(method="POST", json={"order_ids": [order_id]}):
        response = orders_routes.bulk_tag_orders.__wrapped__()
        payload = response.get_json()
        assert payload["updated_orders"] == []
        assert "Tags are not required" in payload["failed_orders"][0]["reason"]
    with sessions() as db:
        assert db.get(Order, order_id).tagged_at is None


@pytest.mark.parametrize("bypass, number", [(False, "TH5727"), (True, "TH5727"), (True, "TH5727-P")])
def test_uploads_reject_exempt_orders_before_external_calls(sessions, monkeypatch, bypass, number):
    with sessions() as db:
        make_order(db)
    uploader = Mock(side_effect=AssertionError("No external request should occur"))
    monkeypatch.setattr(system_routes, "CanopyOrdersUploaderService", uploader)
    app = Flask(__name__)
    with app.test_request_context(method="POST", json={"orders": [number]}):
        handler = system_routes.upload_canopy_orders_bypass if bypass else system_routes.upload_canopy_orders
        response, status = handler.__wrapped__()
        assert status == 400
        assert response.get_json()["ineligible_orders"][0]["order"] == "TH5727"
    uploader.assert_not_called()


def test_admin_setting_validates_deduplicates_and_can_clear_list(sessions, monkeypatch):
    monkeypatch.setattr(system_routes, "get_current_user_email", lambda: "admin@example.com")
    app = Flask(__name__)
    with app.test_request_context(method="PUT", json={"value": json.dumps([TTI, TTI.lower()])}):
        response = system_routes.update_system_setting.__wrapped__(SETTING_ASSET_TAG_EXEMPT_COLLEGE_UNITS)
        assert json.loads(response.get_json()["value"]) == [TTI]
    for invalid in ("not json", "{}", '[""]', "[12]"):
        with app.test_request_context(method="PUT", json={"value": invalid}):
            _, status = system_routes.update_system_setting.__wrapped__(SETTING_ASSET_TAG_EXEMPT_COLLEGE_UNITS)
            assert status == 400
    with app.test_request_context(method="PUT", json={"value": "[]"}):
        response = system_routes.update_system_setting.__wrapped__(SETTING_ASSET_TAG_EXEMPT_COLLEGE_UNITS)
        assert response.get_json()["value"] == "[]"
    with sessions() as db:
        assert db.query(SystemSetting).filter_by(key=SETTING_ASSET_TAG_EXEMPT_COLLEGE_UNITS).one().value == "[]"
