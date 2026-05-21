#!/usr/bin/env python3
"""Tests for the Microsoft Entra OIDC login flow."""

import contextlib
import os
import sys

# Ensure app imports work when running from backend/
sys.path.append(".")

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from flask import Flask  # noqa: E402

import app.api.routes.auth as auth_routes  # noqa: E402
from app.api.routes.auth import bp as auth_bp  # noqa: E402
from app.config import settings  # noqa: E402


class _DummyMsalClient:
    def __init__(self):
        self.authorization_request_kwargs = None
        self.authorization_code_kwargs = None

    def initiate_auth_code_flow(self, **kwargs):
        self.authorization_request_kwargs = kwargs
        return {
            "auth_uri": (
                "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?"
                f"prompt={kwargs.get('prompt')}&state={kwargs.get('state')}"
            ),
            "state": kwargs.get("state"),
            "redirect_uri": kwargs.get("redirect_uri"),
        }

    def acquire_token_by_auth_code_flow(self, flow, auth_response, scopes=None, **kwargs):
        self.authorization_code_kwargs = {
            "flow": flow,
            "auth_response": auth_response,
            "scopes": scopes,
            "kwargs": kwargs,
        }
        return {
            "id_token_claims": {
                "oid": "oid-123",
                "preferred_username": "user@tamu.edu",
                "name": "Test User",
            }
        }


class _DummyMsalFactory:
    def __init__(self):
        self.instances: list[_DummyMsalClient] = []

    def __call__(self, *args, **kwargs):
        client = _DummyMsalClient()
        client.factory_args = args
        client.factory_kwargs = kwargs
        self.instances.append(client)
        return client


def _make_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "test-secret-key"
    app.register_blueprint(auth_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth", name_prefix="legacy_auth")
    return app


def _configure_oidc_settings() -> tuple[str | None, str | None, str | None, str | None, str | None]:
    previous = (
        settings.oidc_tenant_id,
        settings.oidc_client_id,
        settings.oidc_client_secret,
        settings.frontend_url,
        settings.flask_env,
    )
    settings.oidc_tenant_id = "tenant-id"
    settings.oidc_client_id = "client-id"
    settings.oidc_client_secret = "client-secret"
    settings.frontend_url = "https://dev-techhub.pythonanywhere.com"
    settings.flask_env = "development"
    return previous


def test_login_prefers_oidc_and_requests_account_selection(monkeypatch):
    previous = _configure_oidc_settings()
    factory = _DummyMsalFactory()
    monkeypatch.setattr(auth_routes.msal, "ConfidentialClientApplication", factory)

    try:
        app = _make_app()
        client = app.test_client()

        response = client.get("/api/auth/login?next=/orders")

        assert response.status_code == 302
        assert "prompt=select_account" in response.headers["Location"]

        assert factory.instances, "expected the MSAL client factory to be called"
        client_instance = factory.instances[0]
        assert client_instance.authorization_request_kwargs is not None
        assert client_instance.authorization_request_kwargs["prompt"] == "select_account"
        assert client_instance.authorization_request_kwargs["scopes"] == []
        assert client_instance.authorization_request_kwargs["redirect_uri"] == "https://dev-techhub.pythonanywhere.com/api/auth/oidc/callback"

        with client.session_transaction() as sess:
            assert sess["oidc_login_state"] == client_instance.authorization_request_kwargs["state"]
            assert sess["oidc_login_next"] == "/orders"
            assert sess["oidc_login_flow"]["state"] == client_instance.authorization_request_kwargs["state"]
    finally:
        (
            settings.oidc_tenant_id,
            settings.oidc_client_id,
            settings.oidc_client_secret,
            settings.frontend_url,
            settings.flask_env,
        ) = previous


def test_oidc_callback_creates_session_cookie_and_redirects(monkeypatch):
    previous = _configure_oidc_settings()
    factory = _DummyMsalFactory()
    monkeypatch.setattr(auth_routes.msal, "ConfidentialClientApplication", factory)

    captured = {}

    class _DummyUser:
        email = "user@tamu.edu"

    class _DummySession:
        id = "session-123"

    def _fake_get_db():
        return contextlib.nullcontext(object())

    def _fake_get_or_create_user_from_oidc_claims(_db, claims):
        captured["claims"] = claims
        return _DummyUser()

    def _fake_create_session(_db, user, user_agent=None, ip_address=None):
        captured["user_agent"] = user_agent
        captured["ip_address"] = ip_address
        return _DummySession()

    monkeypatch.setattr(auth_routes, "get_db", _fake_get_db)
    monkeypatch.setattr(auth_routes.saml_auth_service, "get_or_create_user_from_oidc_claims", _fake_get_or_create_user_from_oidc_claims)
    monkeypatch.setattr(auth_routes.saml_auth_service, "create_session", _fake_create_session)

    try:
        app = _make_app()
        client = app.test_client()

        with client.session_transaction() as sess:
            sess["oidc_login_state"] = "state-123"
            sess["oidc_login_nonce"] = "nonce-123"
            sess["oidc_login_next"] = "/orders"

        response = client.get("/api/auth/oidc/callback?code=auth-code&state=state-123", headers={"User-Agent": "pytest"})

        assert response.status_code == 302
        assert response.headers["Location"] == "/orders"
        assert "techhub_session=session-123" in response.headers.get("Set-Cookie", "")

        assert captured["claims"]["oid"] == "oid-123"
        assert captured["claims"]["preferred_username"] == "user@tamu.edu"
        assert captured["user_agent"] == "pytest"
    finally:
        (
            settings.oidc_tenant_id,
            settings.oidc_client_id,
            settings.oidc_client_secret,
            settings.frontend_url,
            settings.flask_env,
        ) = previous


def test_login_surfaces_oidc_initiation_failure_details(monkeypatch):
    previous = _configure_oidc_settings()

    def _raise(_relay_state):
        raise RuntimeError("boom")

    monkeypatch.setattr(auth_routes, "_start_oidc_login", _raise)

    try:
        app = _make_app()
        client = app.test_client()

        response = client.get("/api/auth/login?next=/orders")
        body = response.get_json() or {}

        assert response.status_code == 500
        assert body["error"] == "Failed to initiate Microsoft Entra login"
        assert body["details"]["type"] == "RuntimeError"
        assert body["details"]["message"] == "boom"
        assert body["details"]["context"]["tenant_configured"] is True
        assert body["details"]["context"]["client_configured"] is True
    finally:
        (
            settings.oidc_tenant_id,
            settings.oidc_client_id,
            settings.oidc_client_secret,
            settings.frontend_url,
            settings.flask_env,
        ) = previous
