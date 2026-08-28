"""Pending review workflow for collaborative compatibility editing."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.compatibility_editor import (
    CompatibilityCell,
    CompatibilityChangeRequest,
    CompatibilityComputer,
    CompatibilityDock,
    CompatibilityEditorOperation,
    CompatibilityEditorState,
)
from app.services.audit_service import AuditService
from app.services.compatibility_editor_service import (
    COMPATIBILITY_STATUS_VALUES,
    DATASET_ID,
    CompatibilityEditorConflict,
    CompatibilityEditorError,
    CompatibilityEditorNotInitialized,
    _check_version,
    _clean_key,
    _normalize_cell,
    _require_expected_version,
    _serialize_cell,
    _serialize_computer,
    _serialize_dock,
    _set_cell_values,
    _set_computer_values,
    _set_dock_values,
    get_document,
)


PENDING_STATUS = "pending"
APPROVED_STATUS = "approved"
REJECTED_STATUS = "rejected"


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat() + "Z"


def _pending_change(db: Session, target: str) -> Optional[CompatibilityChangeRequest]:
    return (
        db.query(CompatibilityChangeRequest)
        .filter(
            CompatibilityChangeRequest.status == PENDING_STATUS,
            CompatibilityChangeRequest.target == target,
        )
        .order_by(CompatibilityChangeRequest.submitted_at.desc())
        .first()
    )


def _target_for_mutation(mutation: dict[str, Any]) -> tuple[str, str, Optional[str]]:
    mutation_type = mutation.get("type")
    if mutation_type == "cell.update":
        computer_key = _clean_key(mutation.get("computerKey"), "computerKey")
        dock_key = _clean_key(mutation.get("dockKey"), "dockKey")
        return f"cell:{computer_key}:{dock_key}", computer_key, dock_key
    if mutation_type == "computer.add":
        key = _clean_key(mutation.get("computerKey"), "computerKey")
        return f"computer:{key}", key, None
    if mutation_type == "dock.add":
        key = _clean_key(mutation.get("dockKey"), "dockKey")
        return f"dock:{key}", key, None
    if mutation_type in {"computer.update", "computer.delete"}:
        key = _clean_key(mutation.get("computerKey"), "computerKey")
        return f"computer:{key}", key, None
    if mutation_type in {"dock.update", "dock.delete"}:
        key = _clean_key(mutation.get("dockKey"), "dockKey")
        return f"dock:{key}", key, None
    raise CompatibilityEditorError(f"Unsupported mutation type '{mutation_type}'.")


def _normalize_computer(raw: Any) -> dict[str, Any]:
    row = CompatibilityComputer(sku="proposal", version=1)
    _set_computer_values(row, raw)
    value = _serialize_computer(row)
    value["hidden"] = False
    return value


def _normalize_dock(raw: Any) -> dict[str, Any]:
    row = CompatibilityDock(sku="proposal", version=1)
    _set_dock_values(row, raw)
    value = _serialize_dock(row)
    value["hidden"] = False
    return value


def _current_value(db: Session, change: CompatibilityChangeRequest) -> Optional[dict[str, Any]]:
    parts = change.target.split(":")
    if parts[0] == "cell" and len(parts) == 3:
        row = db.get(CompatibilityCell, (parts[1], parts[2]))
        return _serialize_cell(row) if row is not None else None
    if parts[0] == "computer" and len(parts) == 2:
        row = db.get(CompatibilityComputer, parts[1])
        return _serialize_computer(row) if row is not None else None
    if parts[0] == "dock" and len(parts) == 2:
        row = db.get(CompatibilityDock, parts[1])
        return _serialize_dock(row) if row is not None else None
    return None


def _bundle_parents_for_cell(
    db: Session,
    computer_key: str,
    dock_key: str,
) -> list[CompatibilityChangeRequest]:
    parents = []
    computer_change = _pending_change(db, f"computer:{computer_key}")
    if computer_change is not None and computer_change.mutation_type == "computer.add":
        parents.append(computer_change)
    dock_change = _pending_change(db, f"dock:{dock_key}")
    if dock_change is not None and dock_change.mutation_type == "dock.add":
        parents.append(dock_change)
    return parents


def _bundle_summary(
    db: Session,
    change: CompatibilityChangeRequest,
) -> Optional[dict[str, Any]]:
    parts = change.target.split(":")
    if change.mutation_type == "computer.add" and len(parts) == 2:
        axis = "computer"
        item_key = parts[1]
        required_keys = {row.sku for row in db.query(CompatibilityDock).all()}
        child_index = 2
    elif change.mutation_type == "dock.add" and len(parts) == 2:
        axis = "dock"
        item_key = parts[1]
        required_keys = {row.sku for row in db.query(CompatibilityComputer).all()}
        child_index = 1
    else:
        return None

    completed_keys: set[str] = set()
    child_changes = (
        db.query(CompatibilityChangeRequest)
        .filter(
            CompatibilityChangeRequest.status == PENDING_STATUS,
            CompatibilityChangeRequest.mutation_type == "cell.update",
        )
        .all()
    )
    for child in child_changes:
        child_parts = child.target.split(":")
        if len(child_parts) != 3:
            continue
        if axis == "computer" and child_parts[1] != item_key:
            continue
        if axis == "dock" and child_parts[2] != item_key:
            continue
        if child.proposed_data.get("compatibilityStatus") not in COMPATIBILITY_STATUS_VALUES:
            continue
        completed_keys.add(child_parts[child_index])

    missing_keys = sorted(required_keys - completed_keys)
    return {
        "axis": axis,
        "itemKey": item_key,
        "completedCells": len(required_keys & completed_keys),
        "requiredCells": len(required_keys),
        "missingTargets": missing_keys,
        "ready": bool(change.ready_for_review) and not missing_keys,
    }


def _serialize_change(db: Session, row: CompatibilityChangeRequest) -> dict[str, Any]:
    bundle = _bundle_summary(db, row)
    return {
        "id": row.id,
        "target": row.target,
        "mutationType": row.mutation_type,
        "baseVersion": int(row.base_version),
        "version": int(row.proposal_version),
        "proposedData": copy.deepcopy(row.proposed_data),
        "currentData": _current_value(db, row),
        "status": row.status,
        "readyForReview": bool(row.ready_for_review),
        "bundle": bundle,
        "submittedBy": row.submitted_by,
        "updatedBy": row.updated_by,
        "submittedAt": _iso(row.submitted_at),
        "updatedAt": _iso(row.updated_at),
        "reviewedBy": row.reviewed_by,
        "reviewedAt": _iso(row.reviewed_at),
        "reviewNote": row.review_note,
    }


def _rebuild_derived_fields(data: dict[str, Any]) -> None:
    docks = data.get("docks", {})
    for computer in data.get("computers", {}).values():
        cells = computer.setdefault("compatibilityData", {})
        computer["incompatibleWith"] = []
        computer["partiallyCompatibleWith"] = []
        computer["compatibilityNotes"] = {}
        for dock_key in docks:
            cell = cells.setdefault(dock_key, {})
            status = cell.get("compatibilityStatus", "Compatible")
            if status == "Incompatible":
                computer["incompatibleWith"].append(dock_key)
            elif status == "Partially Compatible":
                computer["partiallyCompatibleWith"].append(dock_key)
            notes = cell.get("notes")
            if isinstance(notes, str) and notes.strip():
                computer["compatibilityNotes"][dock_key] = notes.strip()


def get_workspace_document(db: Session) -> dict[str, Any]:
    """Return approved data with the pending review layer overlaid for editors."""

    document = get_document(db)
    approved_versions = copy.deepcopy(document["versions"])
    data = copy.deepcopy(document["data"])
    versions = copy.deepcopy(approved_versions)
    pending = (
        db.query(CompatibilityChangeRequest)
        .filter(CompatibilityChangeRequest.status == PENDING_STATUS)
        .order_by(CompatibilityChangeRequest.submitted_at.asc())
        .all()
    )

    pending_computers: set[str] = set()
    pending_docks: set[str] = set()
    for change in pending:
        parts = change.target.split(":")
        proposed = copy.deepcopy(change.proposed_data or {})
        if change.mutation_type == "computer.add" and len(parts) == 2:
            key = parts[1]
            proposed["studentEdited"] = True
            proposed["compatibilityData"] = {}
            data["computers"][key] = proposed
            versions["computers"][key] = int(change.proposal_version)
            versions["cells"].setdefault(key, {})
            pending_computers.add(key)
        elif change.mutation_type == "dock.add" and len(parts) == 2:
            key = parts[1]
            proposed["studentEdited"] = True
            data["docks"][key] = proposed
            versions["docks"][key] = int(change.proposal_version)
            pending_docks.add(key)

    for computer_key, computer in data["computers"].items():
        cells = computer.setdefault("compatibilityData", {})
        versions["cells"].setdefault(computer_key, {})
        for dock_key in data["docks"]:
            if dock_key not in cells:
                cell: dict[str, Any] = {}
                if computer_key in pending_computers or dock_key in pending_docks:
                    cell["studentEdited"] = True
                cells[dock_key] = cell
                versions["cells"][computer_key][dock_key] = 0

    for change in pending:
        parts = change.target.split(":")
        if change.mutation_type != "cell.update" or len(parts) != 3:
            continue
        computer_key, dock_key = parts[1], parts[2]
        computer = data["computers"].get(computer_key)
        if computer is None or dock_key not in data["docks"]:
            continue
        proposed = copy.deepcopy(change.proposed_data or {})
        proposed["studentEdited"] = True
        computer.setdefault("compatibilityData", {})[dock_key] = proposed
        versions["cells"].setdefault(computer_key, {})[dock_key] = int(
            change.proposal_version
        )

    _rebuild_derived_fields(data)
    state = db.get(CompatibilityEditorState, DATASET_ID)
    reviewable_changes: list[dict[str, Any]] = []
    draft_bundles: list[dict[str, Any]] = []
    for change in pending:
        serialized = _serialize_change(db, change)
        bundle = serialized.get("bundle")
        if bundle is not None:
            if bundle["ready"]:
                reviewable_changes.append(serialized)
            else:
                draft_bundles.append(serialized)
            continue

        parts = change.target.split(":")
        if (
            change.mutation_type == "cell.update"
            and len(parts) == 3
            and (parts[1] in pending_computers or parts[2] in pending_docks)
        ):
            # Cell changes for new items are reviewed with their parent bundle.
            continue
        reviewable_changes.append(serialized)

    document["data"] = data
    document["versions"] = versions
    document["approvedVersions"] = approved_versions
    document["workspaceRevision"] = int(state.review_revision or 0) if state else 0
    document["approval"] = {
        "pendingCount": len(reviewable_changes),
        "pendingChanges": reviewable_changes,
        "draftCount": len(draft_bundles),
        "draftBundles": draft_bundles,
    }
    return document


def _pending_axis_exists(db: Session, axis: str, key: str) -> bool:
    return _pending_change(db, f"{axis}:{key}") is not None


def submit_change(
    db: Session,
    request_body: Any,
    *,
    actor: str,
) -> tuple[dict[str, Any], bool]:
    """Create or revise a pending proposal without touching approved data."""

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
    if mutation_type not in {"cell.update", "computer.add", "computer.update", "dock.add"}:
        raise CompatibilityEditorError(
            "Contributors may update compatibility cells, edit pending computers, "
            "or propose new computers and docks."
        )

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
    if db.get(CompatibilityEditorOperation, operation_id) is not None:
        db.rollback()
        return get_workspace_document(db), True

    target, primary_key, secondary_key = _target_for_mutation(mutation)
    existing = _pending_change(db, target)
    base_version = 0
    bundle_parents: list[CompatibilityChangeRequest] = []

    if mutation_type == "computer.add":
        if db.get(CompatibilityComputer, primary_key) is not None:
            current = db.get(CompatibilityComputer, primary_key)
            raise CompatibilityEditorConflict(
                f"Computer '{primary_key}' already exists.",
                target=target,
                current_version=int(current.version),
            )
        proposed_data = _normalize_computer(mutation.get("computer"))
    elif mutation_type == "dock.add":
        if db.get(CompatibilityDock, primary_key) is not None:
            current = db.get(CompatibilityDock, primary_key)
            raise CompatibilityEditorConflict(
                f"Dock '{primary_key}' already exists.",
                target=target,
                current_version=int(current.version),
            )
        proposed_data = _normalize_dock(mutation.get("dock"))
    elif mutation_type == "computer.update":
        proposed_data = _normalize_computer(mutation.get("computer"))
    else:
        assert secondary_key is not None
        bundle_parents = _bundle_parents_for_cell(db, primary_key, secondary_key)
        computer_exists = db.get(CompatibilityComputer, primary_key) is not None or _pending_axis_exists(
            db, "computer", primary_key
        )
        dock_exists = db.get(CompatibilityDock, secondary_key) is not None or _pending_axis_exists(
            db, "dock", secondary_key
        )
        if not computer_exists or not dock_exists:
            raise CompatibilityEditorConflict(
                f"'{target}' no longer exists.", target=target, current_version=None
            )
        proposed_data = _normalize_cell(mutation.get("cell"))
        proposed_data.pop("studentEdited", None)
        approved = db.get(CompatibilityCell, (primary_key, secondary_key))
        base_version = int(approved.version) if approved is not None else 0

    expected_version = mutation.get("expectedVersion")
    if mutation_type == "computer.update":
        if existing is None or existing.mutation_type != "computer.add":
            raise CompatibilityEditorError(
                "Contributors may only edit a computer while its new-item proposal is pending."
            )
        _check_version(
            target,
            int(existing.proposal_version),
            _require_expected_version(mutation),
        )
        existing.proposed_data = proposed_data
        existing.proposal_version = int(existing.proposal_version) + 1
        existing.updated_by = actor
        existing.ready_for_review = False
        change = existing
    elif existing is not None:
        if mutation_type != existing.mutation_type:
            raise CompatibilityEditorConflict(
                f"'{target}' already has a pending change.",
                target=target,
                current_version=int(existing.proposal_version),
            )
        if mutation_type != "cell.update":
            raise CompatibilityEditorConflict(
                f"'{target}' already has a pending addition.",
                target=target,
                current_version=int(existing.proposal_version),
            )
        if mutation_type == "cell.update":
            _check_version(
                target,
                int(existing.proposal_version),
                _require_expected_version(mutation),
            )
        existing.proposed_data = proposed_data
        existing.proposal_version = int(existing.proposal_version) + 1
        existing.updated_by = actor
        change = existing
    else:
        if mutation_type == "cell.update":
            if not isinstance(expected_version, int) or expected_version < 0:
                raise CompatibilityEditorError(
                    "'expectedVersion' must be a non-negative integer."
                )
            _check_version(target, base_version, expected_version)
        change = CompatibilityChangeRequest(
            id=str(uuid.uuid4()),
            target=target,
            mutation_type=str(mutation_type),
            base_version=base_version,
            proposal_version=1,
            proposed_data=proposed_data,
            status=PENDING_STATUS,
            ready_for_review=mutation_type not in {"computer.add", "dock.add"},
            submitted_by=actor,
            updated_by=actor,
        )
        db.add(change)

    for parent in bundle_parents:
        parent.ready_for_review = False
        parent.updated_by = actor

    state.review_revision = int(state.review_revision or 0) + 1
    db.add(
        CompatibilityEditorOperation(
            operation_id=operation_id,
            revision=int(state.review_revision),
            mutation_type=f"proposal.{mutation_type}",
            target=target,
            actor=actor,
        )
    )
    AuditService(db).log_action(
        "compatibility_editor",
        change.id,
        "proposal.submitted",
        user_id=actor,
        new_value=copy.deepcopy(proposed_data),
        audit_metadata={"target": target, "proposal_version": change.proposal_version},
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_workspace_document(db), False


def submit_bundle(
    db: Session,
    change_id: str,
    *,
    actor: str,
) -> dict[str, Any]:
    """Mark a complete new-computer or new-dock bundle ready for admin review."""

    state = (
        db.query(CompatibilityEditorState)
        .filter(CompatibilityEditorState.id == DATASET_ID)
        .with_for_update()
        .first()
    )
    change = (
        db.query(CompatibilityChangeRequest)
        .filter(CompatibilityChangeRequest.id == change_id)
        .with_for_update()
        .first()
    )
    if state is None:
        raise CompatibilityEditorNotInitialized(
            "Compatibility editor has not been initialized. Import the seed JSON first."
        )
    if (
        change is None
        or change.status != PENDING_STATUS
        or change.mutation_type not in {"computer.add", "dock.add"}
    ):
        raise CompatibilityEditorConflict(
            "This new-item bundle no longer exists.",
            target=f"change:{change_id}",
            current_version=None,
        )

    summary = _bundle_summary(db, change)
    if summary is None:
        raise CompatibilityEditorError("This change is not a new-item bundle.")
    missing_targets = summary["missingTargets"]
    if missing_targets:
        preview = ", ".join(missing_targets[:5])
        suffix = "" if len(missing_targets) <= 5 else ", …"
        raise CompatibilityEditorError(
            f"Complete all compatibility cells before submitting this item. "
            f"Missing: {preview}{suffix} ({len(missing_targets)} total)."
        )
    if change.ready_for_review:
        return get_workspace_document(db)

    change.ready_for_review = True
    change.updated_by = actor
    state.review_revision = int(state.review_revision or 0) + 1
    AuditService(db).log_action(
        "compatibility_editor",
        change.id,
        "proposal.bundle_submitted",
        user_id=actor,
        new_value={
            "target": change.target,
            "completed_cells": summary["completedCells"],
            "required_cells": summary["requiredCells"],
        },
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_workspace_document(db)


def _resolve_child_cell_changes(
    db: Session,
    *,
    computer_key: Optional[str] = None,
    dock_key: Optional[str] = None,
    actor: str,
    approved: bool,
    allowed_computers: Optional[set[str]] = None,
    allowed_docks: Optional[set[str]] = None,
) -> dict[tuple[str, str], CompatibilityChangeRequest]:
    resolved: dict[tuple[str, str], CompatibilityChangeRequest] = {}
    rows = (
        db.query(CompatibilityChangeRequest)
        .filter(
            CompatibilityChangeRequest.status == PENDING_STATUS,
            CompatibilityChangeRequest.mutation_type == "cell.update",
        )
        .all()
    )
    now = datetime.utcnow()
    for row in rows:
        parts = row.target.split(":")
        if len(parts) != 3:
            continue
        if computer_key is not None and parts[1] != computer_key:
            continue
        if dock_key is not None and parts[2] != dock_key:
            continue
        if allowed_computers is not None and parts[1] not in allowed_computers:
            continue
        if allowed_docks is not None and parts[2] not in allowed_docks:
            continue
        resolved[(parts[1], parts[2])] = row
        row.status = APPROVED_STATUS if approved else REJECTED_STATUS
        row.reviewed_by = actor
        row.reviewed_at = now
        row.review_note = "Resolved with parent item review."
    return resolved


def _return_incomplete_bundles_to_draft(db: Session, *, actor: str) -> None:
    db.flush()
    bundle_changes = (
        db.query(CompatibilityChangeRequest)
        .filter(
            CompatibilityChangeRequest.status == PENDING_STATUS,
            CompatibilityChangeRequest.mutation_type.in_(("computer.add", "dock.add")),
            CompatibilityChangeRequest.ready_for_review.is_(True),
        )
        .all()
    )
    for bundle_change in bundle_changes:
        summary = _bundle_summary(db, bundle_change)
        if summary is not None and summary["missingTargets"]:
            bundle_change.ready_for_review = False
            bundle_change.updated_by = actor


def review_change(
    db: Session,
    change_id: str,
    *,
    action: str,
    actor: str,
    note: Optional[str] = None,
) -> dict[str, Any]:
    if action not in {"approve", "reject"}:
        raise CompatibilityEditorError("Review action must be 'approve' or 'reject'.")
    state = (
        db.query(CompatibilityEditorState)
        .filter(CompatibilityEditorState.id == DATASET_ID)
        .with_for_update()
        .first()
    )
    change = (
        db.query(CompatibilityChangeRequest)
        .filter(CompatibilityChangeRequest.id == change_id)
        .with_for_update()
        .first()
    )
    if state is None:
        raise CompatibilityEditorNotInitialized(
            "Compatibility editor has not been initialized. Import the seed JSON first."
        )
    if change is None or change.status != PENDING_STATUS:
        raise CompatibilityEditorConflict(
            "This pending change was already reviewed or no longer exists.",
            target=f"change:{change_id}",
            current_version=None,
        )

    now = datetime.utcnow()
    old_value = _current_value(db, change)
    applied_value: Optional[dict[str, Any]] = None
    parts = change.target.split(":")
    bundle = _bundle_summary(db, change)

    if action == "approve" and bundle is not None:
        if bundle["missingTargets"]:
            raise CompatibilityEditorError(
                "This new-item bundle is incomplete. The contributor must finish all "
                "required cells and submit it again."
            )
        if not change.ready_for_review:
            raise CompatibilityEditorError(
                "This new-item bundle has not been submitted for review yet."
            )

    try:
        if action == "reject":
            change.status = REJECTED_STATUS
            if change.mutation_type == "computer.add" and len(parts) == 2:
                _resolve_child_cell_changes(
                    db, computer_key=parts[1], actor=actor, approved=False
                )
            elif change.mutation_type == "dock.add" and len(parts) == 2:
                _resolve_child_cell_changes(
                    db, dock_key=parts[1], actor=actor, approved=False
                )
            _return_incomplete_bundles_to_draft(db, actor=actor)
        elif change.mutation_type == "cell.update" and len(parts) == 3:
            row = db.get(CompatibilityCell, (parts[1], parts[2]))
            if row is None:
                raise CompatibilityEditorConflict(
                    "Approve the pending computer or dock before this cell.",
                    target=change.target,
                    current_version=None,
                )
            _check_version(change.target, int(row.version), int(change.base_version))
            _set_cell_values(row, change.proposed_data)
            row.version = int(row.version) + 1
            row.updated_by = actor
            applied_value = _serialize_cell(row)
            change.status = APPROVED_STATUS
        elif change.mutation_type == "computer.add" and len(parts) == 2:
            key = parts[1]
            if db.get(CompatibilityComputer, key) is not None:
                raise CompatibilityEditorConflict(
                    f"Computer '{key}' already exists.",
                    target=change.target,
                    current_version=None,
                )
            row = CompatibilityComputer(sku=key, version=1, updated_by=actor)
            _set_computer_values(row, change.proposed_data)
            row.hidden = False
            db.add(row)
            db.flush()
            approved_docks = db.query(CompatibilityDock).all()
            child_changes = _resolve_child_cell_changes(
                db,
                computer_key=key,
                actor=actor,
                approved=True,
                allowed_docks={dock.sku for dock in approved_docks},
            )
            for dock in approved_docks:
                child = child_changes.get((key, dock.sku))
                cell = CompatibilityCell(
                    computer_sku=key,
                    dock_sku=dock.sku,
                    version=1,
                    updated_by=actor,
                )
                _set_cell_values(
                    cell,
                    child.proposed_data if child is not None else {"compatibilityStatus": "Compatible"},
                )
                db.add(cell)
            applied_value = _serialize_computer(row)
            change.status = APPROVED_STATUS
        elif change.mutation_type == "dock.add" and len(parts) == 2:
            key = parts[1]
            if db.get(CompatibilityDock, key) is not None:
                raise CompatibilityEditorConflict(
                    f"Dock '{key}' already exists.",
                    target=change.target,
                    current_version=None,
                )
            row = CompatibilityDock(sku=key, version=1, updated_by=actor)
            _set_dock_values(row, change.proposed_data)
            row.hidden = False
            db.add(row)
            db.flush()
            approved_computers = db.query(CompatibilityComputer).all()
            child_changes = _resolve_child_cell_changes(
                db,
                dock_key=key,
                actor=actor,
                approved=True,
                allowed_computers={computer.sku for computer in approved_computers},
            )
            for computer in approved_computers:
                child = child_changes.get((computer.sku, key))
                cell = CompatibilityCell(
                    computer_sku=computer.sku,
                    dock_sku=key,
                    version=1,
                    updated_by=actor,
                )
                _set_cell_values(
                    cell,
                    child.proposed_data if child is not None else {"compatibilityStatus": "Compatible"},
                )
                db.add(cell)
            applied_value = _serialize_dock(row)
            change.status = APPROVED_STATUS
        elif action == "approve":
            raise CompatibilityEditorError(
                f"Unsupported pending change type '{change.mutation_type}'."
            )

        change.reviewed_by = actor
        change.reviewed_at = now
        change.review_note = (note or "").strip() or None
        state.review_revision = int(state.review_revision or 0) + 1
        if action == "approve":
            state.revision = int(state.revision) + 1
            if state.pending_since is None:
                state.pending_since = now

        AuditService(db).log_action(
            "compatibility_editor",
            change.id,
            f"proposal.{action}d",
            user_id=actor,
            old_value=old_value,
            new_value=applied_value if action == "approve" else change.proposed_data,
            audit_metadata={
                "target": change.target,
                "approved_revision": int(state.revision),
                "review_note": change.review_note,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_workspace_document(db)


def resolve_pending_after_admin_mutation(
    db: Session, request_body: Any, *, actor: str
) -> dict[str, Any]:
    mutation = request_body.get("mutation") if isinstance(request_body, dict) else None
    if not isinstance(mutation, dict):
        return get_workspace_document(db)
    target, _primary, _secondary = _target_for_mutation(mutation)
    change = _pending_change(db, target)
    if change is None:
        return get_workspace_document(db)
    change.status = APPROVED_STATUS
    change.proposed_data = copy.deepcopy(
        mutation.get("cell") or mutation.get("computer") or mutation.get("dock") or {}
    )
    change.reviewed_by = actor
    change.reviewed_at = datetime.utcnow()
    change.review_note = "Applied through an admin edit."
    db.commit()
    return get_workspace_document(db)
