"""Route security uses the real app decorators, with no network or production DB."""

import os
from unittest.mock import Mock

import pytest
from flask import Flask, g

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.api import auth_middleware
from app.api.middleware import register_error_handlers
from app.api.routes import product_checker as routes
from app.utils.exceptions import DNSApiError


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    register_error_handlers(app)
    identity = {"authenticated": True, "admin": True}

    @app.before_request
    def authenticate():
        if identity["authenticated"]:
            g.user_id = "admin-1"
            g.user_email = "admin@example.test"

    monkeypatch.setattr(auth_middleware, "is_current_user_admin", lambda: identity["admin"])
    service = Mock()
    service.status.return_value = {"config": {"configured": True, "missing": []}, "job": None, "report": None}
    service.start.return_value = ({"job": {"status": "running"}}, True)
    monkeypatch.setattr(routes, "product_checker_service", service)
    return app.test_client(), identity, service


@pytest.mark.parametrize("method,path", [("get", ""), ("get", "/status"), ("post", "/refresh")])
@pytest.mark.parametrize("authenticated,admin,expected", [(False, False, 401), (True, False, 403), (True, True, 200)])
def test_all_endpoints_require_admin(client, method, path, authenticated, admin, expected):
    http, identity, service = client
    identity.update(authenticated=authenticated, admin=admin)
    response = getattr(http, method)("/api/system/product-checker" + path)
    assert response.status_code == (202 if expected == 200 and method == "post" else expected)
    if expected != 200:
        service.status.assert_not_called()
        service.start.assert_not_called()
    elif method == "post":
        service.start.assert_called_once_with("admin@example.test")


def test_missing_configuration_uses_central_error_contract(client):
    http, _, service = client
    service.start.side_effect = DNSApiError("PRODUCT_CHECKER_NOT_CONFIGURED", "Connections missing", 503)
    response = http.post("/api/system/product-checker/refresh")
    assert response.status_code == 503
    assert response.json["error"]["code"] == "PRODUCT_CHECKER_NOT_CONFIGURED"
