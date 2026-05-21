#!/usr/bin/env python3

from flask import Flask

from app.api.routes.auth import bp as auth_bp
from app.api.auth_middleware import PUBLIC_ROUTES


def test_auth_blueprint_is_available_under_both_api_and_legacy_prefixes():
    app = Flask(__name__)
    app.register_blueprint(auth_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth", name_prefix="legacy_auth")

    rules = sorted(r.rule for r in app.url_map.iter_rules() if "auth" in r.rule)

    assert "/api/auth/login" in rules
    assert "/auth/login" in rules
    assert "/api/auth/oidc/callback" in rules
    assert "/auth/oidc/callback" in rules
    assert "/api/auth/saml/login" in rules
    assert "/auth/saml/login" in rules
    assert "/api/auth/me" in rules
    assert "/auth/me" in rules


def test_auth_middleware_allows_both_auth_prefixes():
    assert "/api/auth/" in PUBLIC_ROUTES
    assert "/auth/" in PUBLIC_ROUTES
