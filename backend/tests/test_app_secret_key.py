#!/usr/bin/env python3
"""Smoke tests for Flask app bootstrap settings."""

import os
import sys

# Ensure app imports work when running from backend/
sys.path.append(".")

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.main import app  # noqa: E402
from app.config import settings  # noqa: E402


def test_flask_app_secret_key_is_configured():
    assert app.secret_key is not None
    assert app.secret_key == settings.secret_key
