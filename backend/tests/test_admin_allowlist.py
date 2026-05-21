#!/usr/bin/env python3
"""Tests for admin allowlist management.

These tests are written to run under pytest OR as a standalone script.
"""

import os
import sys

# Ensure app imports work when running from backend/
sys.path.append(".")

from typing import Callable, Optional

from flask import Flask, g
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _setup_in_memory_db() -> None:
    """Patch app.database to use an in-memory SQLite DB."""
    # Ensure the app can import config even when DATABASE_URL isn't set.
    os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

    import app.database as database
    from app.database import Base

    # Import models so they are registered on Base.metadata.
    from app.models import system_setting  # noqa: F401
    from app.models import audit_log  # noqa: F401

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    database.engine = engine
    database.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)


def _make_test_app(user_email: str) -> Flask:
    from app.api.routes.system import bp as system_bp

    app = Flask(__name__)
    app.register_blueprint(system_bp)

    @app.before_request
    def _attach_fake_user():
        g.user_id = "test-user"
        g.user_email = user_email
        g.user = type("User", (), {"email": user_email})()
        g._auth_session = None

    return app


def _set_db_admin_emails(value: str) -> None:
    from app.database import get_db_session
    from app.models.system_setting import SystemSetting
    from app.services.system_setting_service import SETTING_ADMIN_EMAILS, DEFAULT_SETTINGS

    db = get_db_session()
    try:
        row = db.query(SystemSetting).filter(SystemSetting.key == SETTING_ADMIN_EMAILS).first()
        if not row:
            row = SystemSetting(
                key=SETTING_ADMIN_EMAILS,
                value=value,
                description=DEFAULT_SETTINGS.get(SETTING_ADMIN_EMAILS, {}).get("description"),
                updated_by="test",
            )
            db.add(row)
        else:
            row.value = value
            row.updated_by = "test"
        db.commit()
    finally:
        db.close()


def _with_temp_settings(admin_emails: Optional[str], flask_env: str, fn: Callable[[], None]) -> None:
    from app.config import settings

    prev_admin_emails = settings.admin_emails
    prev_flask_env = settings.flask_env
    try:
        settings.admin_emails = admin_emails
        settings.flask_env = flask_env
        fn()
    finally:
        settings.admin_emails = prev_admin_emails
        settings.flask_env = prev_flask_env


def test_env_override_precedence_and_put_allowed():
    _setup_in_memory_db()
    caller = "env.admin@example.com"
    app = _make_test_app(caller)

    def run():
        from app.config import settings

        settings.admin_emails = caller
        _set_db_admin_emails('["db.admin@example.com"]')

        client = app.test_client()
        res = client.get("/api/system/admins")
        assert res.status_code == 200
        body = res.get_json() or {}
        # Source is "mixed" when both env + DB are present.
        assert body.get("source") == "mixed"
        assert caller in (body.get("admins") or [])
        assert "db.admin@example.com" in (body.get("admins") or [])

        # PUT now succeeds — env entries are pinned, DB entries are updated.
        res2 = client.put("/api/system/admins", json={"admins": ["db.admin@example.com", "new.db@example.com"]})
        assert res2.status_code == 200
        body2 = res2.get_json() or {}
        assert caller in (body2.get("admins") or []), "env admin should still appear after PUT"

    _with_temp_settings(admin_emails=caller, flask_env="production", fn=run)


def test_db_allowlist_grants_admin_when_env_empty():
    _setup_in_memory_db()
    caller = "db.admin@example.com"
    app = _make_test_app(caller)

    def run():
        _set_db_admin_emails('["db.admin@example.com"]')

        client = app.test_client()
        res = client.get("/api/system/admins")
        assert res.status_code == 200
        body = res.get_json() or {}
        assert body.get("source") == "db"
        assert caller in (body.get("admins") or [])

    _with_temp_settings(admin_emails=None, flask_env="production", fn=run)


def test_put_admins_forbidden_for_non_admin():
    _setup_in_memory_db()
    caller = "not.admin@example.com"
    app = _make_test_app(caller)

    def run():
        from app.config import settings

        settings.admin_emails = "someone.else@example.com"
        client = app.test_client()
        res = client.put("/api/system/admins", json={"admins": [caller]})
        assert res.status_code == 403

    _with_temp_settings(admin_emails="someone.else@example.com", flask_env="production", fn=run)


def test_lockout_guard_requires_caller_in_non_dev():
    _setup_in_memory_db()
    caller = "keep.me@example.com"
    app = _make_test_app(caller)

    def run():
        _set_db_admin_emails('["keep.me@example.com"]')
        client = app.test_client()

        res = client.put("/api/system/admins", json={"admins": ["other@example.com"]})
        assert res.status_code == 400
        err = (res.get_json() or {}).get("error") or ""
        assert "include your email" in err.lower() or "remove your own" in err.lower()

        res2 = client.put("/api/system/admins", json={"admins": []})
        assert res2.status_code == 400
        err2 = (res2.get_json() or {}).get("error") or ""
        assert "empty" in err2.lower() and "non-development" in err2.lower()

    _with_temp_settings(admin_emails=None, flask_env="production", fn=run)


if __name__ == "__main__":
    # Allow running as a script.
    test_env_override_precedence_and_put_allowed()
    print("[PASS] env+db merge + PUT allowed")
    test_db_allowlist_grants_admin_when_env_empty()
    print("[PASS] db allowlist grants admin")
    test_put_admins_forbidden_for_non_admin()
    print("[PASS] PUT forbidden for non-admin")
    test_lockout_guard_requires_caller_in_non_dev()
    print("[PASS] lockout guard")
    print("[SUCCESS] All admin allowlist tests passed")
