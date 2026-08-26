import os

from flask import Flask, g


os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.api import auth_middleware
from app.api.routes.system import bp


def test_non_admin_cannot_access_compatibility_editor(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(bp)

    @app.before_request
    def set_authenticated_user():
        g.user_id = "user-1"
        g.user_email = "non-admin@example.test"

    monkeypatch.setattr(auth_middleware, "is_current_user_admin", lambda: False)

    with app.test_client() as client:
        assert client.get("/api/system/compatibility-editor").status_code == 403
        assert client.patch(
            "/api/system/compatibility-editor", json={}
        ).status_code == 403
        assert client.put(
            "/api/system/compatibility-editor-staging", json={}
        ).status_code == 403
