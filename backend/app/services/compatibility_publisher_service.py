"""Reliable one-way publication of compatibility_superapp.json to WebDAV."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional
from urllib.parse import urlparse, urlunparse

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db_session
from app.models.compatibility_editor import (
    CompatibilityEditorState,
    CompatibilityPublicationSnapshot,
)
from app.services.audit_service import AuditService
from app.services.compatibility_editor_service import DATASET_ID, build_payload


logger = logging.getLogger(__name__)

COMPATIBILITY_SUPERAPP_FILENAME = "compatibility_superapp.json"
_PUBLISH_LOCK_NAME = "compatibility_editor:publish"
_LOCAL_PUBLISH_LOCK = threading.Lock()
_RETRYABLE_SNAPSHOT_STATUSES = ("queued", "failed", "publishing")


@dataclass(frozen=True)
class PublishResult:
    attempted: bool
    success: bool
    revision: int
    pending: bool
    error: Optional[str] = None
    snapshot_id: Optional[str] = None


def _webdav_folder_url() -> str:
    configured = (settings.compatibility_editor_webdav_folder_url or "").strip()
    if configured:
        return configured.rstrip("/") + "/"

    # Safe migration fallback: retain only the directory from the old upload
    # URL, then append the fixed new filename below.
    legacy = (settings.compatibility_editor_staging_upload_url or "").strip()
    if not legacy:
        raise RuntimeError("COMPATIBILITY_EDITOR_WEBDAV_FOLDER_URL is not configured.")
    parsed = urlparse(legacy)
    parent_path = parsed.path.rsplit("/", 1)[0].rstrip("/") + "/"
    return urlunparse(
        (parsed.scheme, parsed.netloc, parent_path, "", parsed.query, "")
    )


def get_publish_url() -> str:
    folder = _webdav_folder_url()
    parsed = urlparse(folder)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("Compatibility editor WebDAV folder URL must be HTTP(S).")
    path = parsed.path.rstrip("/") + "/" + COMPATIBILITY_SUPERAPP_FILENAME
    return urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))


def is_publish_configured() -> bool:
    return bool(
        (settings.compatibility_editor_webdav_folder_url or "").strip()
        or (settings.compatibility_editor_staging_upload_url or "").strip()
    )


def _credentials() -> tuple[str, str]:
    username = (settings.webdav_username or "").strip()
    password = settings.webdav_password
    if not username or not password:
        raise RuntimeError("WEBDAV_USERNAME and WEBDAV_PASSWORD are not configured.")
    return username, password


def _dialect_name(db: Session) -> str:
    bind = db.get_bind()
    return str(getattr(getattr(bind, "dialect", None), "name", "") or "")


@contextmanager
def _publication_lock(db: Session) -> Iterator[bool]:
    if _dialect_name(db) != "mysql":
        acquired = _LOCAL_PUBLISH_LOCK.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                _LOCAL_PUBLISH_LOCK.release()
        return

    bind = db.get_bind()
    with bind.connect() as connection:
        acquired = int(
            connection.execute(
                text("SELECT GET_LOCK(:name, 0)"), {"name": _PUBLISH_LOCK_NAME}
            ).scalar()
            or 0
        ) == 1
        try:
            yield acquired
        finally:
            if acquired:
                connection.execute(
                    text("SELECT RELEASE_LOCK(:name)"), {"name": _PUBLISH_LOCK_NAME}
                )


def _put_and_verify(url: str, body: bytes) -> None:
    username, password = _credentials()
    headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    errors: list[str] = []
    for auth_name, auth in (
        ("digest", HTTPDigestAuth(username, password)),
        ("basic", HTTPBasicAuth(username, password)),
    ):
        try:
            response = requests.put(
                url,
                data=body,
                headers=headers,
                auth=auth,
                timeout=(15, 60),
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            errors.append(f"{auth_name}: {type(exc).__name__}: {exc}")
            continue
        if response.status_code not in (200, 201, 204):
            errors.append(f"{auth_name}: HTTP {response.status_code}")
            continue

        if settings.compatibility_editor_publish_verify:
            try:
                verify = requests.get(
                    url,
                    headers={"Accept": "application/json", "Cache-Control": "no-cache"},
                    auth=auth,
                    timeout=(15, 60),
                    allow_redirects=True,
                )
                verify.raise_for_status()
            except requests.RequestException as exc:
                raise RuntimeError(f"WebDAV verification GET failed: {exc}") from exc
            if hashlib.sha256(verify.content).hexdigest() != hashlib.sha256(body).hexdigest():
                raise RuntimeError("WebDAV verification hash did not match the uploaded file.")
        return
    raise RuntimeError("WebDAV PUT failed (" + "; ".join(errors) + ")")


def request_publication(db: Session, *, actor: str) -> CompatibilityPublicationSnapshot:
    """Capture the complete approved document after an explicit admin action."""

    state = (
        db.query(CompatibilityEditorState)
        .filter(CompatibilityEditorState.id == DATASET_ID)
        .with_for_update()
        .first()
    )
    if state is None:
        raise RuntimeError("Compatibility editor has not been initialized.")

    payload = build_payload(db, state)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    db.query(CompatibilityPublicationSnapshot).filter(
        CompatibilityPublicationSnapshot.status.in_(_RETRYABLE_SNAPSHOT_STATUSES)
    ).update({"status": "superseded"}, synchronize_session=False)
    snapshot = CompatibilityPublicationSnapshot(
        id=str(uuid.uuid4()),
        revision=int(state.revision),
        content=content,
        sha256=content_hash,
        status="queued",
        requested_by=actor,
    )
    db.add(snapshot)
    state.last_publish_error = None
    AuditService(db).log_action(
        "compatibility_editor",
        snapshot.id,
        "publication.requested",
        user_id=actor,
        description="Admin authorized compatibility_superapp.json publication",
        audit_metadata={
            "revision": snapshot.revision,
            "sha256": content_hash,
        },
    )
    db.commit()
    return snapshot


def publish_requested(
    db: Optional[Session] = None,
    *,
    snapshot_id: Optional[str] = None,
) -> PublishResult:
    """Publish only a snapshot previously authorized by an admin."""

    owns_session = db is None
    session = db or get_db_session()
    try:
        with _publication_lock(session) as acquired:
            if not acquired:
                state = session.get(CompatibilityEditorState, DATASET_ID)
                revision = int(state.revision) if state else 0
                published = int(state.published_revision) if state else 0
                return PublishResult(False, False, revision, published < revision)

            state = session.get(CompatibilityEditorState, DATASET_ID)
            if state is None:
                return PublishResult(False, True, 0, False)
            query = session.query(CompatibilityPublicationSnapshot)
            if snapshot_id:
                snapshot = query.filter(
                    CompatibilityPublicationSnapshot.id == snapshot_id,
                    CompatibilityPublicationSnapshot.status.in_(
                        _RETRYABLE_SNAPSHOT_STATUSES
                    ),
                ).first()
            else:
                snapshot = (
                    query.filter(
                        CompatibilityPublicationSnapshot.status.in_(
                            _RETRYABLE_SNAPSHOT_STATUSES
                        )
                    )
                    .order_by(CompatibilityPublicationSnapshot.requested_at.desc())
                    .first()
                )
            if snapshot is None:
                revision = int(state.revision)
                return PublishResult(
                    False,
                    True,
                    revision,
                    int(state.published_revision) < revision,
                )

            target_revision = int(snapshot.revision)
            target_snapshot_id = str(snapshot.id)
            body = snapshot.content.encode("utf-8")
            body_hash = snapshot.sha256
            snapshot.status = "publishing"
            snapshot.last_attempt_at = datetime.utcnow()
            state.last_publish_attempt_at = datetime.utcnow()
            session.commit()

            try:
                _put_and_verify(get_publish_url(), body)
            except Exception as exc:
                session.rollback()
                current = session.get(CompatibilityEditorState, DATASET_ID)
                current_snapshot = session.get(
                    CompatibilityPublicationSnapshot, target_snapshot_id
                )
                if current_snapshot is not None:
                    current_snapshot.status = "failed"
                    current_snapshot.last_attempt_at = datetime.utcnow()
                    current_snapshot.last_error = f"{type(exc).__name__}: {exc}"[:4000]
                if current is not None:
                    current.last_publish_attempt_at = datetime.utcnow()
                    current.last_publish_error = f"{type(exc).__name__}: {exc}"[:4000]
                    session.commit()
                    pending = int(current.published_revision) < int(current.revision)
                else:
                    pending = False
                logger.warning("Compatibility WebDAV publish failed: %s", exc)
                return PublishResult(
                    True,
                    False,
                    target_revision,
                    pending,
                    str(exc),
                    target_snapshot_id,
                )

            session.expire_all()
            current = (
                session.query(CompatibilityEditorState)
                .filter(CompatibilityEditorState.id == DATASET_ID)
                .with_for_update()
                .first()
            )
            if current is None:
                return PublishResult(
                    True,
                    True,
                    target_revision,
                    False,
                    snapshot_id=target_snapshot_id,
                )
            current_snapshot = session.get(
                CompatibilityPublicationSnapshot, target_snapshot_id
            )
            if current_snapshot is not None:
                current_snapshot.status = "published"
                current_snapshot.published_at = datetime.utcnow()
                current_snapshot.last_attempt_at = datetime.utcnow()
                current_snapshot.last_error = None
            current.published_revision = max(
                int(current.published_revision), target_revision
            )
            current.published_sha256 = body_hash
            current.last_published_at = datetime.utcnow()
            current.last_publish_attempt_at = datetime.utcnow()
            current.last_publish_error = None
            pending = int(current.published_revision) < int(current.revision)
            if not pending:
                current.pending_since = None
            session.commit()
            return PublishResult(
                True,
                True,
                target_revision,
                pending,
                snapshot_id=target_snapshot_id,
            )
    finally:
        if owns_session:
            session.close()
