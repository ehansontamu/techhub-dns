import json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.config import settings
from app.database import Base
from app.services import compatibility_publisher_service
from app.services.compatibility_approval_service import (
    review_change,
    submit_bundle,
    submit_change,
)
from app.services.compatibility_editor_service import (
    DEFAULT_SEED_PATH,
    CompatibilityEditorConflict,
    CompatibilityEditorError,
    apply_mutation,
    build_payload,
    import_payload,
    validate_payload,
)


def _payload():
    return {
        "docks": {
            "D1": {
                "name": "Dock One",
                "url": "https://example.test/d1",
                "hidden": False,
                "studentEdited": False,
                "customDockField": "preserved",
            },
            "D2": {"name": "Dock Two", "hidden": False},
        },
        "computers": {
            "C1": {
                "name": "Computer One",
                "url": "https://example.test/c1",
                "hidden": False,
                "studentEdited": False,
                "incompatibleWith": ["D2"],
                "partiallyCompatibleWith": [],
                "compatibilityNotes": {"D2": "Does not work"},
                "compatibilityData": {
                    "D1": {
                        "compatibilityStatus": "Compatible",
                        "display": "Functional",
                        "charging": "N/A",
                        "usbDetection": "Functional",
                        "ethernet": "Functional",
                        "audio": "N/A",
                        "sdCard": "N/A",
                        "rebootNeeded": False,
                        "studentEdited": False,
                    },
                    "D2": {
                        "compatibilityStatus": "Incompatible",
                        "display": "Non-functional",
                        "charging": "N/A",
                        "usbDetection": "N/A",
                        "ethernet": "N/A",
                        "audio": "N/A",
                        "sdCard": "N/A",
                        "rebootNeeded": False,
                        "notes": "Does not work",
                        "studentEdited": False,
                    },
                },
            }
        },
    }


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_import_preserves_payload_and_creates_versions(db):
    document = import_payload(db, _payload(), actor="admin@example.test")

    assert document["revision"] == 1
    assert document["publication"]["pending"] is True
    assert document["versions"]["computers"] == {"C1": 1}
    assert document["versions"]["cells"]["C1"] == {"D1": 1, "D2": 1}
    assert document["data"]["docks"]["D1"]["customDockField"] == "preserved"
    assert document["data"]["computers"]["C1"]["incompatibleWith"] == ["D2"]
    assert "studentEdited" not in document["data"]["computers"]["C1"]


def test_different_cells_merge_and_same_cell_conflicts(db):
    initial = import_payload(db, _payload(), actor="seed")

    first, _ = apply_mutation(
        db,
        {
            "operationId": "op-1",
            "mutation": {
                "type": "cell.update",
                "computerKey": "C1",
                "dockKey": "D1",
                "expectedVersion": initial["versions"]["cells"]["C1"]["D1"],
                "cell": {
                    **initial["data"]["computers"]["C1"]["compatibilityData"]["D1"],
                    "notes": "First editor",
                },
            },
        },
        actor="first@example.test",
    )
    second, _ = apply_mutation(
        db,
        {
            "operationId": "op-2",
            "mutation": {
                "type": "cell.update",
                "computerKey": "C1",
                "dockKey": "D2",
                "expectedVersion": initial["versions"]["cells"]["C1"]["D2"],
                "cell": {
                    **initial["data"]["computers"]["C1"]["compatibilityData"]["D2"],
                    "notes": "Second editor",
                },
            },
        },
        actor="second@example.test",
    )

    assert first["revision"] == 2
    assert second["revision"] == 3
    assert second["data"]["computers"]["C1"]["compatibilityNotes"] == {
        "D1": "First editor",
        "D2": "Second editor",
    }

    with pytest.raises(CompatibilityEditorConflict):
        apply_mutation(
            db,
            {
                "operationId": "op-3",
                "mutation": {
                    "type": "cell.update",
                    "computerKey": "C1",
                    "dockKey": "D1",
                    "expectedVersion": 1,
                    "cell": {"compatibilityStatus": "Incompatible"},
                },
            },
            actor="stale@example.test",
        )


def test_operation_id_is_idempotent(db):
    initial = import_payload(db, _payload(), actor="seed")
    request = {
        "operationId": "same-operation",
        "mutation": {
            "type": "cell.update",
            "computerKey": "C1",
            "dockKey": "D1",
            "expectedVersion": initial["versions"]["cells"]["C1"]["D1"],
            "cell": {"compatibilityStatus": "Compatible", "notes": "saved"},
        },
    }

    first, first_duplicate = apply_mutation(db, request, actor="admin@example.test")
    second, second_duplicate = apply_mutation(db, request, actor="admin@example.test")

    assert first_duplicate is False
    assert second_duplicate is True
    assert first["revision"] == second["revision"] == 2


def test_contributor_change_stays_pending_until_admin_approval(db):
    initial = import_payload(db, _payload(), actor="seed")
    workspace, duplicate = submit_change(
        db,
        {
            "operationId": "proposal-1",
            "mutation": {
                "type": "cell.update",
                "computerKey": "C1",
                "dockKey": "D1",
                "expectedVersion": 1,
                "cell": {"compatibilityStatus": "Incompatible", "notes": "Review me"},
            },
        },
        actor="contributor@example.test",
    )

    assert duplicate is False
    assert workspace["revision"] == initial["revision"]
    assert workspace["approval"]["pendingCount"] == 1
    assert workspace["data"]["computers"]["C1"]["compatibilityData"]["D1"][
        "studentEdited"
    ] is True
    assert build_payload(db)["computers"]["C1"]["compatibilityData"]["D1"][
        "compatibilityStatus"
    ] == "Compatible"

    approved = review_change(
        db,
        workspace["approval"]["pendingChanges"][0]["id"],
        action="approve",
        actor="admin@example.test",
    )

    assert approved["revision"] == initial["revision"] + 1
    assert approved["approval"]["pendingCount"] == 0
    assert build_payload(db)["computers"]["C1"]["compatibilityData"]["D1"] == {
        "compatibilityStatus": "Incompatible",
        "notes": "Review me",
    }


def test_new_item_is_submitted_and_reviewed_as_a_complete_bundle(db):
    import_payload(db, _payload(), actor="seed")
    workspace, _ = submit_change(
        db,
        {
            "operationId": "proposal-computer",
            "mutation": {
                "type": "computer.add",
                "computerKey": "C2",
                "computer": {"name": "Computer Two", "hidden": True},
            },
        },
        actor="contributor@example.test",
    )

    assert "C2" not in build_payload(db)["computers"]
    assert workspace["data"]["computers"]["C2"]["studentEdited"] is True
    assert workspace["approval"]["pendingCount"] == 0
    assert workspace["approval"]["draftCount"] == 1
    bundle_change = workspace["approval"]["draftBundles"][0]
    assert bundle_change["bundle"]["completedCells"] == 0
    assert bundle_change["bundle"]["requiredCells"] == 2

    with pytest.raises(CompatibilityEditorError):
        submit_bundle(
            db,
            bundle_change["id"],
            actor="contributor@example.test",
        )

    for index, dock_key in enumerate(("D1", "D2"), start=1):
        workspace, _ = submit_change(
            db,
            {
                "operationId": f"proposal-computer-cell-{index}",
                "mutation": {
                    "type": "cell.update",
                    "computerKey": "C2",
                    "dockKey": dock_key,
                    "expectedVersion": 0,
                    "cell": {
                        "compatibilityStatus": (
                            "Compatible" if dock_key == "D1" else "Incompatible"
                        ),
                        "notes": f"Tested with {dock_key}",
                    },
                },
            },
            actor="contributor@example.test",
        )

    assert workspace["approval"]["pendingCount"] == 0
    bundle_change = workspace["approval"]["draftBundles"][0]
    assert bundle_change["bundle"]["completedCells"] == 2

    submitted = submit_bundle(
        db,
        bundle_change["id"],
        actor="contributor@example.test",
    )
    assert submitted["approval"]["draftCount"] == 0
    assert submitted["approval"]["pendingCount"] == 1
    ready_change = submitted["approval"]["pendingChanges"][0]
    assert ready_change["bundle"]["ready"] is True

    corrected, _ = submit_change(
        db,
        {
            "operationId": "correct-pending-computer-metadata",
            "mutation": {
                "type": "computer.update",
                "computerKey": "C2",
                "expectedVersion": ready_change["version"],
                "computer": {
                    "name": "Corrected Computer Two",
                    "url": "https://example.test/c2",
                    "hidden": True,
                },
            },
        },
        actor="contributor@example.test",
    )
    assert corrected["approval"]["pendingCount"] == 0
    assert corrected["approval"]["draftCount"] == 1
    corrected_change = corrected["approval"]["draftBundles"][0]
    assert corrected_change["proposedData"] == {
        "name": "Corrected Computer Two",
        "url": "https://example.test/c2",
        "hidden": False,
    }
    assert corrected_change["bundle"]["ready"] is False

    resubmitted = submit_bundle(
        db,
        corrected_change["id"],
        actor="contributor@example.test",
    )
    ready_change = resubmitted["approval"]["pendingChanges"][0]

    admin_corrected, _ = submit_change(
        db,
        {
            "operationId": "admin-correct-pending-computer-metadata",
            "mutation": {
                "type": "computer.update",
                "computerKey": "C2",
                "expectedVersion": ready_change["version"],
                "computer": {
                    "name": "Admin Corrected Computer Two",
                    "url": "https://example.test/c2",
                    "hidden": False,
                },
            },
        },
        actor="admin@example.test",
        preserve_ready_for_review=True,
    )
    assert admin_corrected["approval"]["draftCount"] == 0
    assert admin_corrected["approval"]["pendingCount"] == 1
    ready_change = admin_corrected["approval"]["pendingChanges"][0]
    assert ready_change["bundle"]["ready"] is True

    approved = review_change(
        db,
        ready_change["id"],
        action="approve",
        actor="admin@example.test",
    )
    published_data = build_payload(db)
    assert approved["approval"]["pendingCount"] == 0
    assert published_data["computers"]["C2"]["hidden"] is False
    assert published_data["computers"]["C2"]["name"] == "Admin Corrected Computer Two"
    assert published_data["computers"]["C2"]["url"] == "https://example.test/c2"
    assert "studentEdited" not in published_data["computers"]["C2"]
    assert published_data["computers"]["C2"]["compatibilityData"]["D2"] == {
        "compatibilityStatus": "Incompatible",
        "notes": "Tested with D2",
    }


def test_contributor_can_correct_pending_dock_metadata(db):
    import_payload(db, _payload(), actor="seed")
    workspace, _ = submit_change(
        db,
        {
            "operationId": "proposal-dock",
            "mutation": {
                "type": "dock.add",
                "dockKey": "D3",
                "dock": {
                    "name": "Dock Three",
                    "url": "https://example.test/original-d3",
                    "hidden": True,
                },
            },
        },
        actor="contributor@example.test",
    )
    bundle_change = workspace["approval"]["draftBundles"][0]

    workspace, _ = submit_change(
        db,
        {
            "operationId": "proposal-dock-cell",
            "mutation": {
                "type": "cell.update",
                "computerKey": "C1",
                "dockKey": "D3",
                "expectedVersion": 0,
                "cell": {"compatibilityStatus": "Compatible"},
            },
        },
        actor="contributor@example.test",
    )
    bundle_change = workspace["approval"]["draftBundles"][0]
    submitted = submit_bundle(
        db,
        bundle_change["id"],
        actor="contributor@example.test",
    )
    ready_change = submitted["approval"]["pendingChanges"][0]

    corrected, _ = submit_change(
        db,
        {
            "operationId": "correct-pending-dock-metadata",
            "mutation": {
                "type": "dock.update",
                "dockKey": "D3",
                "expectedVersion": ready_change["version"],
                "dock": {
                    "name": "Corrected Dock Three",
                    "url": "https://example.test/corrected-d3",
                    "hidden": True,
                },
            },
        },
        actor="contributor@example.test",
    )

    assert corrected["approval"]["pendingCount"] == 0
    assert corrected["approval"]["draftCount"] == 1
    corrected_change = corrected["approval"]["draftBundles"][0]
    assert corrected_change["mutationType"] == "dock.add"
    assert corrected_change["proposedData"] == {
        "name": "Corrected Dock Three",
        "url": "https://example.test/corrected-d3",
        "hidden": False,
    }
    assert corrected_change["bundle"]["ready"] is False
    assert build_payload(db)["docks"].get("D3") is None


def test_publisher_writes_only_explicit_snapshot_and_records_revision(db, monkeypatch):
    imported = import_payload(db, _payload(), actor="seed")
    monkeypatch.setattr(
        settings,
        "compatibility_editor_webdav_folder_url",
        "https://dav.example.test/folder/",
    )
    captured = {}

    def fake_put(url, body):
        captured["url"] = url
        captured["body"] = body

    monkeypatch.setattr(compatibility_publisher_service, "_put_and_verify", fake_put)

    snapshot = compatibility_publisher_service.request_publication(
        db, actor="admin@example.test"
    )
    result = compatibility_publisher_service.publish_requested(
        db, snapshot_id=snapshot.id
    )

    assert result.success is True
    assert result.revision == imported["revision"]
    assert captured["url"] == (
        "https://dav.example.test/folder/compatibility_superapp.json"
    )
    assert set(json.loads(captured["body"])["computers"]) == {"C1"}
    db.expire_all()
    from app.services.compatibility_editor_service import get_document

    refreshed = get_document(db)
    assert refreshed["publication"]["pending"] is False
    assert refreshed["publication"]["publishedRevision"] == imported["revision"]


def test_publisher_does_nothing_without_an_admin_snapshot(db, monkeypatch):
    imported = import_payload(db, _payload(), actor="seed")
    called = []
    monkeypatch.setattr(
        compatibility_publisher_service,
        "_put_and_verify",
        lambda _url, _body: called.append(True),
    )

    result = compatibility_publisher_service.publish_requested(db)

    assert result.attempted is False
    assert result.pending is True
    assert result.revision == imported["revision"]
    assert called == []


def test_publication_retry_uses_immutable_admin_snapshot(db, monkeypatch):
    initial = import_payload(db, _payload(), actor="seed")
    snapshot = compatibility_publisher_service.request_publication(
        db, actor="admin@example.test"
    )
    apply_mutation(
        db,
        {
            "operationId": "later-admin-edit",
            "mutation": {
                "type": "cell.update",
                "computerKey": "C1",
                "dockKey": "D1",
                "expectedVersion": 1,
                "cell": {"compatibilityStatus": "Incompatible", "notes": "Later"},
            },
        },
        actor="admin@example.test",
    )
    captured = {}
    monkeypatch.setattr(settings, "compatibility_editor_webdav_folder_url", "https://dav.example.test/folder/")
    monkeypatch.setattr(
        compatibility_publisher_service,
        "_put_and_verify",
        lambda _url, body: captured.update(body=body),
    )

    result = compatibility_publisher_service.publish_requested(
        db, snapshot_id=snapshot.id
    )

    body = json.loads(captured["body"])
    assert result.revision == initial["revision"]
    assert result.pending is True
    assert body["computers"]["C1"]["compatibilityData"]["D1"].get("notes") is None


def test_seed_file_matches_supported_schema():
    payload = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8-sig"))
    normalized = validate_payload(payload)
    assert len(normalized["computers"]) == 17
    assert len(normalized["docks"]) == 15
    assert sum(
        len(computer["compatibilityData"])
        for computer in normalized["computers"].values()
    ) == 255
