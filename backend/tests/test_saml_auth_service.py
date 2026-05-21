#!/usr/bin/env python3

import os
import sys
import tempfile

import pytest


temp_dir = tempfile.mkdtemp(prefix="techhub-saml-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(temp_dir, 'test.db')}"
sys.path.append(".")

from app.database import Base, SessionLocal, engine
from app.services.saml_auth_service import saml_auth_service


def _new_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def test_get_or_create_user_uses_custom_display_name_claim():
    db = _new_db()
    try:
        user = saml_auth_service.get_or_create_user(
            db,
            {
                "oid": ["oid-123"],
                "email": ["tech1@example.com"],
                "display_name": ["Tech One"],
                "department": "Service",
                "employee_id": "10001",
            },
        )

        assert user.tamu_oid == "oid-123"
        assert user.email == "tech1@example.com"
        assert user.display_name == "Tech One"
        assert user.department == "Service"
        assert user.employee_id == "10001"
    finally:
        db.close()


@pytest.mark.parametrize(
    "saml_attributes, expected_display_name",
    [
        (
            {
                "http://schemas.microsoft.com/identity/claims/objectidentifier": ["oid-456"],
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress": ["tech2@example.com"],
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname": ["Jane"],
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname": ["Tech"],
            },
            "Jane Tech",
        ),
        (
            {
                "objectidentifier": "oid-789",
                "userprincipalname": "tech3@example.com",
                "displayname": "Tech Three",
            },
            "Tech Three",
        ),
        (
            {
                "http://schemas.microsoft.com/identity/claims/objectidentifier": ["oid-999"],
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress": ["tech4@example.com"],
                "http://schemas.microsoft.com/identity/claims/displayname": ["Tech Four"],
            },
            "Tech Four",
        ),
    ],
)
def test_get_or_create_user_supports_standard_and_custom_aliases(saml_attributes, expected_display_name):
    db = _new_db()
    try:
        user = saml_auth_service.get_or_create_user(db, saml_attributes)

        assert user.display_name == expected_display_name
    finally:
        db.close()


def test_get_or_create_user_preserves_existing_display_name_when_absent():
    db = _new_db()
    try:
        user = saml_auth_service.get_or_create_user(
            db,
            {
                "oid": "oid-preserve",
                "email": "old@example.com",
                "display_name": "Existing Name",
            },
        )
        assert user.display_name == "Existing Name"

        user = saml_auth_service.get_or_create_user(
            db,
            {
                "oid": "oid-preserve",
                "email": "new@example.com",
            },
        )
        assert user.display_name == "Existing Name"
        assert user.email == "new@example.com"
    finally:
        db.close()


def test_get_or_create_user_prefers_graph_api_display_name(monkeypatch):
    """When Graph API returns a displayName, it should override SAML attributes."""
    db = _new_db()

    def _mock_get_user_profile(oid):
        return {
            "displayName": "Kyler Cao",
            "givenName": "Kyler",
            "surname": "Cao",
            "department": "CS",
            "employeeId": "12345",
        }

    monkeypatch.setattr(
        "app.services.saml_auth_service.graph_service.get_user_profile",
        _mock_get_user_profile,
    )

    try:
        user = saml_auth_service.get_or_create_user(
            db,
            {
                "http://schemas.microsoft.com/identity/claims/objectidentifier": ["oid-graph"],
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress": ["kcao@tamu.edu"],
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name": ["kcao@tamu.edu"],  # SAML gives email as name
            },
        )
        assert user.display_name == "Kyler Cao"
        assert user.department == "CS"
        assert user.employee_id == "12345"
    finally:
        db.close()
