#!/usr/bin/env python3
"""Tests for the local development auth bypass."""

import os
import sys

# Ensure app imports work when running from backend/
sys.path.append(".")

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from flask import Flask, jsonify

from app.api.auth_middleware import (
    init_auth_middleware,
    is_dev_auth_bypass_enabled,
    require_admin,
    require_auth,
)
from app.api.routes.auth import bp as auth_bp
from app.config import settings


def _with_dev_bypass(fn):
    prev_flask_env = settings.flask_env
    prev_dev_auth_bypass = settings.dev_auth_bypass
    prev_dev_auth_email = settings.dev_auth_email
    prev_dev_auth_display_name = settings.dev_auth_display_name
    prev_dev_auth_department = settings.dev_auth_department
    prev_admin_emails = settings.admin_emails
    try:
        settings.flask_env = "development"
        settings.dev_auth_bypass = True
        settings.dev_auth_email = "dev.local@example.com"
        settings.dev_auth_display_name = "Local Dev User"
        settings.dev_auth_department = "Development"
        settings.admin_emails = "someone.else@example.com"
        fn()
    finally:
        settings.flask_env = prev_flask_env
        settings.dev_auth_bypass = prev_dev_auth_bypass
        settings.dev_auth_email = prev_dev_auth_email
        settings.dev_auth_display_name = prev_dev_auth_display_name
        settings.dev_auth_department = prev_dev_auth_department
        settings.admin_emails = prev_admin_emails


def _make_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(auth_bp)
    init_auth_middleware(app)

    @app.route("/api/protected")
    @require_auth
    def protected():
        from app.api.auth_middleware import get_current_user_display_name, get_current_user_email

        return jsonify(
            {
                "ok": True,
                "email": get_current_user_email(),
                "display_name": get_current_user_display_name(),
            }
        )

    @app.route("/api/admin-only")
    @require_admin
    def admin_only():
        return jsonify({"ok": True})

    return app


def test_dev_auth_bypass_populates_auth_context():
    app = _make_app()

    def run():
        assert is_dev_auth_bypass_enabled()

        client = app.test_client()
        res = client.get("/api/auth/me")
        assert res.status_code == 200
        body = res.get_json() or {}

        assert body["user"]["email"] == "dev.local@example.com"
        assert body["user"]["display_name"] == "Local Dev User"
        assert body["is_admin"] is True
        assert body["session"]["id"]

        res2 = client.get("/api/protected")
        assert res2.status_code == 200
        body2 = res2.get_json() or {}
        assert body2["email"] == "dev.local@example.com"
        assert body2["display_name"] == "Local Dev User"

        res3 = client.get("/api/admin-only")
        assert res3.status_code == 200

    _with_dev_bypass(run)


def test_dev_auth_login_route_skips_saml():
    app = _make_app()

    def run():
        client = app.test_client()
        res = client.get("/api/auth/login?next=/orders")
        assert res.status_code == 302
        assert res.headers["Location"].endswith("/orders")

    _with_dev_bypass(run)


if __name__ == "__main__":
    test_dev_auth_bypass_populates_auth_context()
    print("[PASS] dev auth bypass populates auth context")
    test_dev_auth_login_route_skips_saml()
    print("[PASS] dev auth login route skips SAML")
    print("[SUCCESS] All dev auth bypass tests passed")
