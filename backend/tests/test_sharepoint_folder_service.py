from app.services.sharepoint_service import SharePointService


class _DummyResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"Unexpected HTTP status {self.status_code}")


class _DummyClient:
    def __init__(self, responses, captured):
        self._responses = iter(responses)
        self._captured = captured

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, headers):
        self._captured.append(("GET", url, None))
        return next(self._responses)

    def post(self, url, headers, json):
        self._captured.append(("POST", url, json))
        return next(self._responses)


def _configured_service(monkeypatch):
    service = SharePointService()
    monkeypatch.setattr(
        SharePointService,
        "is_enabled",
        property(lambda self: True),
    )
    monkeypatch.setattr(service, "_get_drive_id", lambda: "drive-1")
    monkeypatch.setattr(service, "_get_headers", lambda: {"Authorization": "test"})
    monkeypatch.setattr(
        service,
        "_get_folder_path",
        lambda subfolder: f"TechHub/{subfolder}",
    )
    return service


def test_ensure_folder_returns_existing_folder_url(monkeypatch):
    service = _configured_service(monkeypatch)
    captured = []
    expected_url = "https://sharepoint.example.test/TH5000_bundles"
    monkeypatch.setattr(
        "app.services.sharepoint_service.httpx.Client",
        lambda *args, **kwargs: _DummyClient(
            [_DummyResponse(200, {"webUrl": expected_url})], captured
        ),
    )

    result = service.ensure_folder("bundles/TH5000_bundles")

    assert result == expected_url
    assert [call[0] for call in captured] == ["GET"]


def test_ensure_folder_creates_missing_partial_bundle_folder(monkeypatch):
    service = _configured_service(monkeypatch)
    captured = []
    expected_url = "https://sharepoint.example.test/TH5000_bundles"
    monkeypatch.setattr(
        "app.services.sharepoint_service.httpx.Client",
        lambda *args, **kwargs: _DummyClient(
            [
                _DummyResponse(404),
                _DummyResponse(201, {"webUrl": expected_url}),
            ],
            captured,
        ),
    )

    result = service.ensure_folder("bundles/TH5000_bundles")

    assert result == expected_url
    assert [call[0] for call in captured] == ["GET", "POST"]
    assert captured[1][2]["name"] == "TH5000_bundles"
