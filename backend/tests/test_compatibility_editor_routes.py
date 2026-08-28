import os

from flask import Flask, g


os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.api import auth_middleware
from app.api.routes import system as system_routes


class _FakeDb:
    def close(self):
        return None


def _document():
    return {
        "data": {"computers": {}, "docks": {}},
        "revision": 1,
        "workspaceRevision": 1,
        "versions": {"computers": {}, "docks": {}, "cells": {}},
        "approvedVersions": {"computers": {}, "docks": {}, "cells": {}},
        "approval": {
            "pendingCount": 0,
            "pendingChanges": [],
            "draftCount": 0,
            "draftBundles": [],
        },
        "publication": {
            "publishedRevision": 0,
            "pending": True,
            "pendingSince": None,
            "lastPublishedAt": None,
            "lastAttemptAt": None,
            "lastError": None,
            "sha256": None,
        },
    }


def test_non_admin_can_submit_but_cannot_review_or_publish(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(system_routes.bp)

    @app.before_request
    def set_authenticated_user():
        g.user_id = "user-1"
        g.user_email = "non-admin@example.test"

    monkeypatch.setattr(auth_middleware, "is_current_user_admin", lambda: False)
    monkeypatch.setattr(system_routes, "is_current_user_admin", lambda: False)
    monkeypatch.setattr(system_routes, "get_db_session", lambda: _FakeDb())
    monkeypatch.setattr(system_routes, "_emit_compatibility_editor_update", lambda _document: None)
    monkeypatch.setattr(
        system_routes, "get_compatibility_editor_workspace", lambda _db: _document()
    )
    submitted = []

    def fake_submit(_db, payload, *, actor, preserve_ready_for_review=False):
        submitted.append((payload, actor, preserve_ready_for_review))
        return _document(), False

    monkeypatch.setattr(
        system_routes, "submit_compatibility_editor_change", fake_submit
    )
    submitted_bundles = []

    def fake_submit_bundle(_db, change_id, *, actor):
        submitted_bundles.append((change_id, actor))
        return _document()

    monkeypatch.setattr(
        system_routes, "submit_compatibility_editor_bundle", fake_submit_bundle
    )

    with app.test_client() as client:
        assert client.get("/api/system/compatibility-editor").status_code == 200
        assert client.patch(
            "/api/system/compatibility-editor",
            json={"operationId": "op", "mutation": {"type": "cell.update"}},
        ).status_code == 200
        assert submitted[0][1] == "non-admin@example.test"
        assert client.post(
            "/api/system/compatibility-editor/changes/change-1/submit"
        ).status_code == 200
        assert submitted_bundles == [("change-1", "non-admin@example.test")]
        assert client.post(
            "/api/system/compatibility-editor/changes/change-1/review",
            json={"action": "approve"},
        ).status_code == 403
        assert client.post("/api/system/compatibility-editor/publish").status_code == 403
        assert client.put(
            "/api/system/compatibility-editor-staging", json={}
        ).status_code == 403


def test_admin_pending_item_update_remains_a_proposal(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(system_routes.bp)

    @app.before_request
    def set_authenticated_user():
        g.user_id = "admin-1"
        g.user_email = "admin@example.test"

    monkeypatch.setattr(system_routes, "is_current_user_admin", lambda: True)
    monkeypatch.setattr(system_routes, "get_db_session", lambda: _FakeDb())
    monkeypatch.setattr(
        system_routes, "_emit_compatibility_editor_update", lambda _document: None
    )

    def fake_apply(_db, _payload, *, actor):
        assert actor == "admin@example.test"
        raise system_routes.CompatibilityEditorConflict(
            "Computer 'C2' no longer exists.",
            target="computer:C2",
            current_version=None,
        )

    submitted = []

    def fake_submit(_db, payload, *, actor, preserve_ready_for_review=False):
        submitted.append((payload, actor, preserve_ready_for_review))
        return _document(), False

    monkeypatch.setattr(
        system_routes, "apply_compatibility_editor_mutation", fake_apply
    )
    monkeypatch.setattr(
        system_routes, "submit_compatibility_editor_change", fake_submit
    )

    mutation = {
        "operationId": "admin-correct-pending-computer",
        "mutation": {
            "type": "computer.update",
            "computerKey": "C2",
            "expectedVersion": 1,
            "computer": {
                "name": "Corrected Computer",
                "url": "https://example.test/c2",
            },
        },
    }
    with app.test_client() as client:
        response = client.patch("/api/system/compatibility-editor", json=mutation)

    assert response.status_code == 200
    assert submitted == [(mutation, "admin@example.test", True)]
