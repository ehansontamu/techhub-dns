"""Database-backed, revisioned compatibility editor operations."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.compatibility_editor import (
    CompatibilityCell,
    CompatibilityChangeRequest,
    CompatibilityComputer,
    CompatibilityDock,
    CompatibilityEditorOperation,
    CompatibilityEditorState,
    CompatibilityPublicationSnapshot,
)
from app.services.audit_service import AuditService


DATASET_ID = "primary"
DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parents[2]
    / "seeds"
    / "compatibility_initial.json"
)

COMPATIBILITY_STATUS_VALUES = (
    "Compatible",
    "Incompatible",
    "Partially Compatible",
)
DETAIL_STATUS_VALUES = (
    "Functional",
    "Partially Functional",
    "Non-functional",
    "N/A",
)
DETAIL_FIELD_TO_COLUMN = {
    "display": "display",
    "charging": "charging",
    "usbDetection": "usb_detection",
    "ethernet": "ethernet",
    "audio": "audio",
    "sdCard": "sd_card",
}

COMPUTER_FIELDS = {"name", "url", "hidden", "studentEdited"}
COMPUTER_DERIVED_FIELDS = {
    "compatibilityData",
    "compatibilityNotes",
    "incompatibleWith",
    "partiallyCompatibleWith",
}
DOCK_FIELDS = {"name", "url", "hidden", "studentEdited"}
CELL_FIELDS = {
    "compatibilityStatus",
    *DETAIL_FIELD_TO_COLUMN.keys(),
    "rebootNeeded",
    "notes",
    "studentEdited",
}


class CompatibilityEditorError(ValueError):
    pass


class CompatibilityEditorNotInitialized(CompatibilityEditorError):
    pass


class CompatibilityEditorConflict(CompatibilityEditorError):
    def __init__(
        self,
        message: str,
        *,
        target: str,
        current_version: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.target = target
        self.current_version = current_version


def _clean_key(value: Any, field_name: str = "key") -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompatibilityEditorError(f"'{field_name}' must be a non-empty string.")
    key = value.strip()
    if len(key) > 100:
        raise CompatibilityEditorError(f"'{field_name}' cannot exceed 100 characters.")
    if ":" in key:
        raise CompatibilityEditorError(f"'{field_name}' cannot contain ':'.")
    return key


def _clean_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CompatibilityEditorError("'name' must be a non-empty string.")
    name = value.strip()
    if len(name) > 500:
        raise CompatibilityEditorError("'name' cannot exceed 500 characters.")
    return name


def _optional_text(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CompatibilityEditorError(f"'{field_name}' must be a string.")
    return value.strip() or None


def _optional_bool(value: Any, field_name: str) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", ""}:
            return False
    raise CompatibilityEditorError(f"'{field_name}' must be a boolean.")


def _normalize_string_list(value: Any, field_name: str) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise CompatibilityEditorError(f"'{field_name}' must be an array of strings.")
    result: list[str] = []
    for item in value:
        key = _clean_key(item, field_name)
        if key not in result:
            result.append(key)
    return result


def _normalize_cell(raw_cell: Any) -> dict[str, Any]:
    if not isinstance(raw_cell, dict):
        raise CompatibilityEditorError("Compatibility cell must be an object.")

    normalized = copy.deepcopy(raw_cell)
    status = raw_cell.get("compatibilityStatus")
    if status is not None and status not in COMPATIBILITY_STATUS_VALUES:
        raise CompatibilityEditorError(
            "'compatibilityStatus' must be Compatible, Incompatible, or Partially Compatible."
        )
    for field in DETAIL_FIELD_TO_COLUMN:
        detail = raw_cell.get(field)
        if detail is not None and detail not in DETAIL_STATUS_VALUES:
            raise CompatibilityEditorError(
                f"'{field}' must be one of: {', '.join(DETAIL_STATUS_VALUES)}."
            )

    if "rebootNeeded" in raw_cell:
        normalized["rebootNeeded"] = _optional_bool(
            raw_cell.get("rebootNeeded"), "rebootNeeded"
        )
    if "studentEdited" in raw_cell:
        normalized["studentEdited"] = _optional_bool(
            raw_cell.get("studentEdited"), "studentEdited"
        )
    if "notes" in raw_cell:
        notes = _optional_text(raw_cell.get("notes"), "notes")
        if notes is None:
            normalized.pop("notes", None)
        else:
            normalized["notes"] = notes
    return normalized


def validate_payload(payload: Any) -> dict[str, Any]:
    """Validate and normalize an imported compatibility document."""

    if not isinstance(payload, dict):
        raise CompatibilityEditorError("Payload must be a JSON object.")
    raw_docks = payload.get("docks")
    raw_computers = payload.get("computers")
    if not isinstance(raw_docks, dict):
        raise CompatibilityEditorError("'docks' must be an object keyed by SKU.")
    if not isinstance(raw_computers, dict):
        raise CompatibilityEditorError("'computers' must be an object keyed by SKU.")

    docks: dict[str, dict[str, Any]] = {}
    for raw_key, raw_dock in raw_docks.items():
        key = _clean_key(raw_key, "dock key")
        if not isinstance(raw_dock, dict):
            raise CompatibilityEditorError(f"Dock '{key}' must be an object.")
        dock = copy.deepcopy(raw_dock)
        dock["name"] = _clean_name(raw_dock.get("name"))
        if "url" in raw_dock:
            dock["url"] = _optional_text(raw_dock.get("url"), "url") or ""
        if "hidden" in raw_dock:
            dock["hidden"] = bool(_optional_bool(raw_dock.get("hidden"), "hidden"))
        if "studentEdited" in raw_dock:
            dock["studentEdited"] = _optional_bool(
                raw_dock.get("studentEdited"), "studentEdited"
            )
        docks[key] = dock

    dock_keys = set(docks)
    computers: dict[str, dict[str, Any]] = {}
    for raw_key, raw_computer in raw_computers.items():
        key = _clean_key(raw_key, "computer key")
        if not isinstance(raw_computer, dict):
            raise CompatibilityEditorError(f"Computer '{key}' must be an object.")
        computer = copy.deepcopy(raw_computer)
        computer["name"] = _clean_name(raw_computer.get("name"))
        if "url" in raw_computer:
            computer["url"] = _optional_text(raw_computer.get("url"), "url") or ""
        if "hidden" in raw_computer:
            computer["hidden"] = bool(
                _optional_bool(raw_computer.get("hidden"), "hidden")
            )
        if "studentEdited" in raw_computer:
            computer["studentEdited"] = _optional_bool(
                raw_computer.get("studentEdited"), "studentEdited"
            )

        incompatible = _normalize_string_list(
            raw_computer.get("incompatibleWith"), "incompatibleWith"
        )
        partial = _normalize_string_list(
            raw_computer.get("partiallyCompatibleWith"),
            "partiallyCompatibleWith",
        )
        unknown = (set(incompatible) | set(partial)) - dock_keys
        if unknown:
            raise CompatibilityEditorError(
                f"Computer '{key}' references unknown docks: {', '.join(sorted(unknown))}."
            )
        overlap = set(incompatible) & set(partial)
        if overlap:
            raise CompatibilityEditorError(
                f"Computer '{key}' lists docks in two statuses: {', '.join(sorted(overlap))}."
            )

        notes = raw_computer.get("compatibilityNotes") or {}
        if not isinstance(notes, dict):
            raise CompatibilityEditorError(
                f"Computer '{key}' compatibilityNotes must be an object."
            )
        normalized_notes: dict[str, str] = {}
        for raw_dock_key, raw_note in notes.items():
            dock_key = _clean_key(raw_dock_key, "compatibilityNotes key")
            if dock_key not in dock_keys:
                raise CompatibilityEditorError(
                    f"Computer '{key}' notes reference unknown dock '{dock_key}'."
                )
            note = _optional_text(raw_note, "compatibilityNotes value")
            if note:
                normalized_notes[dock_key] = note

        raw_cells = raw_computer.get("compatibilityData") or {}
        if not isinstance(raw_cells, dict):
            raise CompatibilityEditorError(
                f"Computer '{key}' compatibilityData must be an object."
            )
        cells: dict[str, dict[str, Any]] = {}
        for raw_dock_key, raw_cell in raw_cells.items():
            dock_key = _clean_key(raw_dock_key, "compatibilityData key")
            if dock_key not in dock_keys:
                raise CompatibilityEditorError(
                    f"Computer '{key}' data references unknown dock '{dock_key}'."
                )
            cell = _normalize_cell(raw_cell)
            if not cell.get("notes") and normalized_notes.get(dock_key):
                cell["notes"] = normalized_notes[dock_key]
            if not cell.get("compatibilityStatus"):
                if dock_key in incompatible:
                    cell["compatibilityStatus"] = "Incompatible"
                elif dock_key in partial:
                    cell["compatibilityStatus"] = "Partially Compatible"
                else:
                    cell["compatibilityStatus"] = "Compatible"
            cells[dock_key] = cell

        computer["incompatibleWith"] = incompatible
        computer["partiallyCompatibleWith"] = partial
        computer["compatibilityNotes"] = normalized_notes
        computer["compatibilityData"] = cells
        computers[key] = computer

    result = {
        key: copy.deepcopy(value)
        for key, value in payload.items()
        if key not in {"docks", "computers"}
    }
    result["docks"] = docks
    result["computers"] = computers
    return result


def payload_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _serialize_computer(row: CompatibilityComputer) -> dict[str, Any]:
    value = copy.deepcopy(row.extra_data or {})
    value["name"] = row.name
    value["url"] = row.url or ""
    value["hidden"] = bool(row.hidden)
    return value


def _serialize_dock(row: CompatibilityDock) -> dict[str, Any]:
    value = copy.deepcopy(row.extra_data or {})
    value["name"] = row.name
    value["url"] = row.url or ""
    value["hidden"] = bool(row.hidden)
    return value


def _serialize_cell(row: CompatibilityCell) -> dict[str, Any]:
    value = copy.deepcopy(row.extra_data or {})
    if row.compatibility_status is not None:
        value["compatibilityStatus"] = row.compatibility_status
    for json_field, column_name in DETAIL_FIELD_TO_COLUMN.items():
        field_value = getattr(row, column_name)
        if field_value is not None:
            value[json_field] = field_value
    if row.reboot_needed is not None:
        value["rebootNeeded"] = bool(row.reboot_needed)
    if row.notes:
        value["notes"] = row.notes
    return value


def build_payload(db: Session, state: Optional[CompatibilityEditorState] = None) -> dict[str, Any]:
    state = state or db.get(CompatibilityEditorState, DATASET_ID)
    if state is None:
        raise CompatibilityEditorNotInitialized(
            "Compatibility editor has not been initialized. Import the seed JSON first."
        )

    computers = db.query(CompatibilityComputer).order_by(CompatibilityComputer.sku).all()
    docks = db.query(CompatibilityDock).order_by(CompatibilityDock.sku).all()
    cells = (
        db.query(CompatibilityCell)
        .order_by(CompatibilityCell.computer_sku, CompatibilityCell.dock_sku)
        .all()
    )

    payload = copy.deepcopy(state.document_extra_data or {})
    payload["docks"] = {row.sku: _serialize_dock(row) for row in docks}
    payload["computers"] = {row.sku: _serialize_computer(row) for row in computers}

    for computer in payload["computers"].values():
        computer["incompatibleWith"] = []
        computer["partiallyCompatibleWith"] = []
        computer["compatibilityNotes"] = {}
        computer["compatibilityData"] = {}

    for row in cells:
        computer = payload["computers"].get(row.computer_sku)
        if computer is None:
            continue
        cell = _serialize_cell(row)
        computer["compatibilityData"][row.dock_sku] = cell
        if row.compatibility_status == "Incompatible":
            computer["incompatibleWith"].append(row.dock_sku)
        elif row.compatibility_status == "Partially Compatible":
            computer["partiallyCompatibleWith"].append(row.dock_sku)
        if row.notes:
            computer["compatibilityNotes"][row.dock_sku] = row.notes

    return payload


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat() + "Z"


def get_document(db: Session) -> dict[str, Any]:
    state = db.get(CompatibilityEditorState, DATASET_ID)
    if state is None or int(state.revision or 0) <= 0:
        raise CompatibilityEditorNotInitialized(
            "Compatibility editor has not been initialized. Import the seed JSON first."
        )
    return {
        "data": build_payload(db, state),
        "revision": int(state.revision),
        "versions": _version_map(db),
        "publication": {
            "publishedRevision": int(state.published_revision),
            "pending": int(state.published_revision) < int(state.revision),
            "pendingSince": _iso(state.pending_since),
            "lastPublishedAt": _iso(state.last_published_at),
            "lastAttemptAt": _iso(state.last_publish_attempt_at),
            "lastError": state.last_publish_error,
            "sha256": state.published_sha256,
        },
    }


def _version_map(db: Session) -> dict[str, dict[str, Any]]:
    computers = db.query(CompatibilityComputer).all()
    docks = db.query(CompatibilityDock).all()
    cells = db.query(CompatibilityCell).all()
    cell_versions: dict[str, dict[str, int]] = {}
    for row in cells:
        cell_versions.setdefault(row.computer_sku, {})[row.dock_sku] = int(row.version)
    return {
        "computers": {row.sku: int(row.version) for row in computers},
        "docks": {row.sku: int(row.version) for row in docks},
        "cells": cell_versions,
    }


def _set_cell_values(row: CompatibilityCell, raw_cell: dict[str, Any]) -> None:
    cell = _normalize_cell(raw_cell)
    row.compatibility_status = cell.get("compatibilityStatus")
    for json_field, column_name in DETAIL_FIELD_TO_COLUMN.items():
        setattr(row, column_name, cell.get(json_field))
    row.reboot_needed = cell.get("rebootNeeded")
    row.notes = cell.get("notes")
    # Approval state lives in CompatibilityChangeRequest, never in exported data.
    row.student_edited = None
    row.extra_data = {
        key: copy.deepcopy(value)
        for key, value in cell.items()
        if key not in CELL_FIELDS
    } or None


def _set_computer_values(row: CompatibilityComputer, raw: dict[str, Any]) -> None:
    if not isinstance(raw, dict):
        raise CompatibilityEditorError("'computer' must be an object.")
    row.name = _clean_name(raw.get("name"))
    row.url = _optional_text(raw.get("url"), "url")
    row.hidden = bool(_optional_bool(raw.get("hidden", False), "hidden"))
    row.student_edited = None
    row.extra_data = {
        key: copy.deepcopy(value)
        for key, value in raw.items()
        if key not in COMPUTER_FIELDS | COMPUTER_DERIVED_FIELDS
    } or None


def _set_dock_values(row: CompatibilityDock, raw: dict[str, Any]) -> None:
    if not isinstance(raw, dict):
        raise CompatibilityEditorError("'dock' must be an object.")
    row.name = _clean_name(raw.get("name"))
    row.url = _optional_text(raw.get("url"), "url")
    row.hidden = bool(_optional_bool(raw.get("hidden", False), "hidden"))
    row.student_edited = None
    row.extra_data = {
        key: copy.deepcopy(value)
        for key, value in raw.items()
        if key not in DOCK_FIELDS
    } or None


def import_payload(
    db: Session,
    payload: Any,
    *,
    actor: str,
    source_sha256: Optional[str] = None,
    replace: bool = False,
) -> dict[str, Any]:
    normalized = validate_payload(payload)
    state = (
        db.query(CompatibilityEditorState)
        .filter(CompatibilityEditorState.id == DATASET_ID)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if state is not None and state.revision > 0 and not replace:
        raise CompatibilityEditorConflict(
            "Compatibility editor is already initialized.",
            target="dataset",
            current_version=int(state.revision),
        )

    try:
        db.query(CompatibilityChangeRequest).filter(
            CompatibilityChangeRequest.status == "pending"
        ).update(
            {
                "status": "rejected",
                "reviewed_by": actor,
                "reviewed_at": datetime.utcnow(),
                "review_note": "Discarded by authoritative dataset replacement.",
            },
            synchronize_session=False,
        )
        db.query(CompatibilityPublicationSnapshot).filter(
            CompatibilityPublicationSnapshot.status.in_(("queued", "failed", "publishing"))
        ).update({"status": "superseded"}, synchronize_session=False)
        db.query(CompatibilityCell).delete(synchronize_session=False)
        db.query(CompatibilityComputer).delete(synchronize_session=False)
        db.query(CompatibilityDock).delete(synchronize_session=False)
        if state is None:
            state = CompatibilityEditorState(id=DATASET_ID)
            db.add(state)
        db.flush()

        for sku, raw_dock in normalized["docks"].items():
            row = CompatibilityDock(sku=sku, version=1, updated_by=actor)
            _set_dock_values(row, raw_dock)
            db.add(row)
        for sku, raw_computer in normalized["computers"].items():
            row = CompatibilityComputer(sku=sku, version=1, updated_by=actor)
            _set_computer_values(row, raw_computer)
            db.add(row)
        db.flush()

        for computer_sku, raw_computer in normalized["computers"].items():
            raw_cells = raw_computer.get("compatibilityData") or {}
            for dock_sku in normalized["docks"]:
                raw_cell = raw_cells.get(dock_sku, {"studentEdited": True})
                row = CompatibilityCell(
                    computer_sku=computer_sku,
                    dock_sku=dock_sku,
                    version=1,
                    updated_by=actor,
                )
                _set_cell_values(row, raw_cell)
                db.add(row)

        state.document_extra_data = {
            key: copy.deepcopy(value)
            for key, value in normalized.items()
            if key not in {"computers", "docks"}
        } or None
        state.revision = int(state.revision or 0) + 1
        state.review_revision = int(state.review_revision or 0) + 1
        state.published_revision = 0
        state.pending_since = datetime.utcnow()
        state.last_published_at = None
        state.last_publish_attempt_at = None
        state.last_publish_error = None
        state.published_sha256 = None
        state.source_sha256 = source_sha256 or payload_sha256(normalized)
        AuditService(db).log_action(
            "compatibility_editor",
            "compatibility-editor",
            "imported",
            user_id=actor,
            description="Imported compatibility editor seed data",
            audit_metadata={
                "revision": state.revision,
                "computers": len(normalized["computers"]),
                "docks": len(normalized["docks"]),
                "source_sha256": state.source_sha256,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_document(db)


def import_bundled_seed_if_empty(db: Session, *, actor: str) -> dict[str, Any]:
    """Atomically initialize an empty migrated database from the bundled seed."""

    state = db.get(CompatibilityEditorState, DATASET_ID)
    if state is not None and int(state.revision or 0) > 0:
        return get_document(db)

    raw = DEFAULT_SEED_PATH.read_bytes()
    payload = json.loads(raw.decode("utf-8-sig"))
    try:
        return import_payload(
            db,
            payload,
            actor=actor,
            source_sha256=hashlib.sha256(raw).hexdigest(),
        )
    except CompatibilityEditorConflict:
        # Another worker completed the one-time import while this worker was
        # reading the seed file.
        return get_document(db)


def _require_expected_version(mutation: dict[str, Any]) -> int:
    value = mutation.get("expectedVersion")
    if not isinstance(value, int) or value < 0:
        raise CompatibilityEditorError("'expectedVersion' must be a non-negative integer.")
    return value


def _check_version(target: str, current: int, expected: int) -> None:
    if current != expected:
        raise CompatibilityEditorConflict(
            f"'{target}' changed after it was loaded.",
            target=target,
            current_version=current,
        )


def _check_document_revision(
    state: CompatibilityEditorState, mutation: dict[str, Any], target: str
) -> None:
    expected = mutation.get("expectedRevision")
    if not isinstance(expected, int) or expected < 0:
        raise CompatibilityEditorError(
            "'expectedRevision' must be a non-negative integer."
        )
    current = int(state.revision)
    if current != expected:
        raise CompatibilityEditorConflict(
            "The compatibility matrix changed before this deletion could be applied.",
            target=target,
            current_version=current,
        )


def _reject_pending_changes_for_deleted_item(
    db: Session,
    *,
    actor: str,
    computer_key: Optional[str] = None,
    dock_key: Optional[str] = None,
) -> None:
    """Reject pending proposals that cannot survive an approved axis deletion."""

    now = datetime.utcnow()
    pending_changes = db.query(CompatibilityChangeRequest).filter(
        CompatibilityChangeRequest.status == "pending"
    )
    for change in pending_changes:
        parts = change.target.split(":")
        affects_computer = computer_key is not None and (
            change.target == f"computer:{computer_key}"
            or (
                change.mutation_type == "cell.update"
                and len(parts) == 3
                and parts[1] == computer_key
            )
        )
        affects_dock = dock_key is not None and (
            change.target == f"dock:{dock_key}"
            or (
                change.mutation_type == "cell.update"
                and len(parts) == 3
                and parts[2] == dock_key
            )
        )
        if not affects_computer and not affects_dock:
            continue

        deleted_label = (
            f"computer '{computer_key}'"
            if affects_computer
            else f"dock '{dock_key}'"
        )
        change.status = "rejected"
        change.reviewed_by = actor
        change.reviewed_at = now
        change.review_note = f"Discarded because an admin deleted {deleted_label}."


def apply_mutation(
    db: Session,
    request_body: Any,
    *,
    actor: str,
    commit: bool = True,
) -> tuple[dict[str, Any], bool]:
    if not isinstance(request_body, dict):
        raise CompatibilityEditorError("Mutation request must be an object.")
    operation_id = request_body.get("operationId")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise CompatibilityEditorError("'operationId' is required.")
    operation_id = operation_id.strip()
    if len(operation_id) > 64:
        raise CompatibilityEditorError("'operationId' cannot exceed 64 characters.")
    mutation = request_body.get("mutation")
    if not isinstance(mutation, dict):
        raise CompatibilityEditorError("'mutation' must be an object.")
    mutation_type = mutation.get("type")
    if not isinstance(mutation_type, str):
        raise CompatibilityEditorError("Mutation 'type' is required.")

    state = (
        db.query(CompatibilityEditorState)
        .filter(CompatibilityEditorState.id == DATASET_ID)
        .with_for_update()
        .first()
    )
    if state is None:
        raise CompatibilityEditorNotInitialized(
            "Compatibility editor has not been initialized. Import the seed JSON first."
        )
    duplicate = db.get(CompatibilityEditorOperation, operation_id)
    if duplicate is not None:
        db.rollback()
        return get_document(db), True

    old_value: Optional[dict[str, Any]] = None
    new_value: Optional[dict[str, Any]] = None
    target = "dataset"

    try:
        if mutation_type == "cell.update":
            computer_key = _clean_key(mutation.get("computerKey"), "computerKey")
            dock_key = _clean_key(mutation.get("dockKey"), "dockKey")
            target = f"cell:{computer_key}:{dock_key}"
            row = db.get(CompatibilityCell, (computer_key, dock_key))
            if row is None:
                raise CompatibilityEditorConflict(
                    f"'{target}' no longer exists.", target=target, current_version=None
                )
            _check_version(target, int(row.version), _require_expected_version(mutation))
            old_value = _serialize_cell(row)
            _set_cell_values(row, mutation.get("cell"))
            row.version = int(row.version) + 1
            row.updated_by = actor
            new_value = _serialize_cell(row)

        elif mutation_type == "computer.add":
            key = _clean_key(mutation.get("computerKey"), "computerKey")
            target = f"computer:{key}"
            if db.get(CompatibilityComputer, key) is not None:
                current = db.get(CompatibilityComputer, key)
                raise CompatibilityEditorConflict(
                    f"Computer '{key}' already exists.",
                    target=target,
                    current_version=int(current.version),
                )
            row = CompatibilityComputer(sku=key, version=1, updated_by=actor)
            _set_computer_values(row, mutation.get("computer"))
            db.add(row)
            db.flush()
            for dock in db.query(CompatibilityDock).all():
                db.add(
                    CompatibilityCell(
                        computer_sku=key,
                        dock_sku=dock.sku,
                        student_edited=None,
                        version=1,
                        updated_by=actor,
                    )
                )
            new_value = _serialize_computer(row)

        elif mutation_type == "computer.update":
            key = _clean_key(mutation.get("computerKey"), "computerKey")
            target = f"computer:{key}"
            row = db.get(CompatibilityComputer, key)
            if row is None:
                raise CompatibilityEditorConflict(
                    f"Computer '{key}' no longer exists.", target=target
                )
            _check_version(target, int(row.version), _require_expected_version(mutation))
            old_value = _serialize_computer(row)
            _set_computer_values(row, mutation.get("computer"))
            row.version = int(row.version) + 1
            row.updated_by = actor
            new_value = _serialize_computer(row)

        elif mutation_type == "computer.delete":
            key = _clean_key(mutation.get("computerKey"), "computerKey")
            target = f"computer:{key}"
            row = db.get(CompatibilityComputer, key)
            if row is None:
                raise CompatibilityEditorConflict(
                    f"Computer '{key}' no longer exists.", target=target
                )
            _check_document_revision(state, mutation, target)
            _check_version(target, int(row.version), _require_expected_version(mutation))
            old_value = _serialize_computer(row)
            db.query(CompatibilityCell).filter(
                CompatibilityCell.computer_sku == key
            ).delete(synchronize_session=False)
            _reject_pending_changes_for_deleted_item(
                db, computer_key=key, actor=actor
            )
            db.delete(row)

        elif mutation_type == "dock.add":
            key = _clean_key(mutation.get("dockKey"), "dockKey")
            target = f"dock:{key}"
            if db.get(CompatibilityDock, key) is not None:
                current = db.get(CompatibilityDock, key)
                raise CompatibilityEditorConflict(
                    f"Dock '{key}' already exists.",
                    target=target,
                    current_version=int(current.version),
                )
            row = CompatibilityDock(sku=key, version=1, updated_by=actor)
            _set_dock_values(row, mutation.get("dock"))
            db.add(row)
            db.flush()
            for computer in db.query(CompatibilityComputer).all():
                db.add(
                    CompatibilityCell(
                        computer_sku=computer.sku,
                        dock_sku=key,
                        student_edited=None,
                        version=1,
                        updated_by=actor,
                    )
                )
            new_value = _serialize_dock(row)

        elif mutation_type == "dock.update":
            key = _clean_key(mutation.get("dockKey"), "dockKey")
            target = f"dock:{key}"
            row = db.get(CompatibilityDock, key)
            if row is None:
                raise CompatibilityEditorConflict(
                    f"Dock '{key}' no longer exists.", target=target
                )
            _check_version(target, int(row.version), _require_expected_version(mutation))
            old_value = _serialize_dock(row)
            _set_dock_values(row, mutation.get("dock"))
            row.version = int(row.version) + 1
            row.updated_by = actor
            new_value = _serialize_dock(row)

        elif mutation_type == "dock.delete":
            key = _clean_key(mutation.get("dockKey"), "dockKey")
            target = f"dock:{key}"
            row = db.get(CompatibilityDock, key)
            if row is None:
                raise CompatibilityEditorConflict(
                    f"Dock '{key}' no longer exists.", target=target
                )
            _check_document_revision(state, mutation, target)
            _check_version(target, int(row.version), _require_expected_version(mutation))
            old_value = _serialize_dock(row)
            db.query(CompatibilityCell).filter(CompatibilityCell.dock_sku == key).delete(
                synchronize_session=False
            )
            _reject_pending_changes_for_deleted_item(db, dock_key=key, actor=actor)
            db.delete(row)

        else:
            raise CompatibilityEditorError(f"Unsupported mutation type '{mutation_type}'.")

        state.revision = int(state.revision) + 1
        state.review_revision = int(state.review_revision or 0) + 1
        if state.published_revision >= state.revision:
            state.published_revision = state.revision - 1
        if state.pending_since is None:
            state.pending_since = datetime.utcnow()
        state.last_publish_error = None
        db.add(
            CompatibilityEditorOperation(
                operation_id=operation_id,
                revision=state.revision,
                mutation_type=mutation_type,
                target=target,
                actor=actor,
            )
        )
        AuditService(db).log_action(
            "compatibility_editor",
            "compatibility-editor",
            mutation_type,
            user_id=actor,
            old_value=old_value,
            new_value=new_value,
            audit_metadata={
                "revision": state.revision,
                "target": target,
                "operation_id": operation_id,
            },
        )
        if commit:
            db.commit()
        else:
            db.flush()
    except Exception:
        db.rollback()
        raise

    return get_document(db), False
