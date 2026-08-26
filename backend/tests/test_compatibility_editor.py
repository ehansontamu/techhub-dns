import json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.config import settings
from app.database import Base
from app.services import compatibility_publisher_service
from app.services.compatibility_editor_service import (
    DEFAULT_SEED_PATH,
    CompatibilityEditorConflict,
    apply_mutation,
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


def test_publisher_writes_only_fixed_filename_and_records_revision(db, monkeypatch):
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

    result = compatibility_publisher_service.publish_latest(db)

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


def test_seed_file_matches_supported_schema():
    payload = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8-sig"))
    normalized = validate_payload(payload)
    assert len(normalized["computers"]) == 17
    assert len(normalized["docks"]) == 15
    assert sum(
        len(computer["compatibilityData"])
        for computer in normalized["computers"].values()
    ) == 255
