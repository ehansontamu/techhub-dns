"""Reliable one-way publication of compatibility_superapp.json to WebDAV."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
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
from app.models.compatibility_editor import CompatibilityEditorState
from app.services.compatibility_editor_service import DATASET_ID, build_payload


logger = logging.getLogger(__name__)

COMPATIBILITY_SUPERAPP_FILENAME = "compatibility_superapp.json"
_PUBLISH_LOCK_NAME = "compatibility_editor:publish"
_LOCAL_PUBLISH_LOCK = threading.Lock()
_SCHEDULE_LOCK = threading.Lock()
_SCHEDULE_TIMER: Optional[threading.Timer] = None
_SCHEDULE_FIRST_PENDING: Optional[float] = None


@dataclass(frozen=True)
class PublishResult:
    attempted: bool
    success: bool
    revision: int
    pending: bool
    error: Optional[str] = None


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


def publish_latest(db: Optional[Session] = None) -> PublishResult:
    """Publish the latest DB snapshot once, protected across app workers."""

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
            target_revision = int(state.revision)
            if int(state.published_revision) >= target_revision:
                return PublishResult(False, True, target_revision, False)

            payload = build_payload(session, state)
            body = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
            body_hash = hashlib.sha256(body).hexdigest()
            state.last_publish_attempt_at = datetime.utcnow()
            session.commit()

            try:
                _put_and_verify(get_publish_url(), body)
            except Exception as exc:
                session.rollback()
                current = session.get(CompatibilityEditorState, DATASET_ID)
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
                )

            session.expire_all()
            current = (
                session.query(CompatibilityEditorState)
                .filter(CompatibilityEditorState.id == DATASET_ID)
                .with_for_update()
                .first()
            )
            if current is None:
                return PublishResult(True, True, target_revision, False)
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
            return PublishResult(True, True, target_revision, pending)
    finally:
        if owns_session:
            session.close()


def _scheduled_publish() -> None:
    global _SCHEDULE_TIMER, _SCHEDULE_FIRST_PENDING
    with _SCHEDULE_LOCK:
        _SCHEDULE_TIMER = None
        _SCHEDULE_FIRST_PENDING = None
    result = publish_latest()
    if result.pending:
        schedule_publish(delay_seconds=3 if result.success else 30)


def schedule_publish(*, delay_seconds: Optional[int] = None) -> None:
    """Debounce publications without losing the durable DB pending state."""

    if not is_publish_configured():
        return

    global _SCHEDULE_TIMER, _SCHEDULE_FIRST_PENDING
    debounce = max(
        0,
        min(int(settings.compatibility_editor_publish_debounce_seconds or 3), 60),
    )
    max_delay = max(
        debounce,
        min(int(settings.compatibility_editor_publish_max_delay_seconds or 15), 300),
    )
    now = time.monotonic()
    with _SCHEDULE_LOCK:
        if _SCHEDULE_FIRST_PENDING is None:
            _SCHEDULE_FIRST_PENDING = now
        elapsed = now - _SCHEDULE_FIRST_PENDING
        requested = debounce if delay_seconds is None else max(0, int(delay_seconds))
        delay = min(requested, max(0.0, max_delay - elapsed))
        if _SCHEDULE_TIMER is not None:
            _SCHEDULE_TIMER.cancel()
        timer = threading.Timer(delay, _scheduled_publish)
        timer.daemon = True
        _SCHEDULE_TIMER = timer
        timer.start()
