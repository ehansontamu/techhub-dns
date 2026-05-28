from app.services.inflow_service import InflowService


class _DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _DummyClient:
    def __init__(self, captured):
        self._captured = captured

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def put(self, url, json, headers):
        self._captured["url"] = url
        self._captured["json"] = json
        self._captured["headers"] = headers
        return _DummyResponse({"items": [json]})


def test_update_proof_of_delivery_url_sync_updates_custom5_without_losing_existing_fields(
    monkeypatch,
):
    service = InflowService()
    captured = {}
    original_order = {
        "id": "sales-order-1",
        "orderNumber": "TH5000",
        "customFields": {
            "custom1": "Existing value",
            "custom4": "Other field",
        },
    }

    monkeypatch.setattr(service, "get_order_by_id_sync", lambda sales_order_id: original_order)
    monkeypatch.setattr(
        "app.services.inflow_service.httpx.Client",
        lambda *args, **kwargs: _DummyClient(captured),
    )
    monkeypatch.setattr(
        InflowService,
        "headers",
        property(lambda self: {"Authorization": "Bearer test-token"}),
    )

    result = service.update_proof_of_delivery_url_sync(
        "sales-order-1",
        "https://sharepoint.example.test/bundles/TH5000_bundle.pdf",
    )

    assert captured["json"]["customFields"]["custom5"] == "https://sharepoint.example.test/bundles/TH5000_bundle.pdf"
    assert captured["json"]["customFields"]["custom1"] == "Existing value"
    assert captured["json"]["customFields"]["custom4"] == "Other field"
    assert result["customFields"]["custom5"] == "https://sharepoint.example.test/bundles/TH5000_bundle.pdf"
