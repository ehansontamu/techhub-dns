"""
System status API routes.

Provides endpoints for checking backend feature statuses.
"""

import hmac
import json
import os
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, abort, jsonify, request, send_file
from typing import Dict, Any, cast, Optional, Sequence
from datetime import datetime, timezone
import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from app.config import settings
from app.services.saml_auth_service import saml_auth_service
from app.services.canopy_orders_uploader_service import CanopyOrdersUploaderService
from app.services.graph_service import graph_service
from app.services.inflow_service import InflowService
from app.services.inventory_reorder_service import InventoryReorderService
from app.services.order_service import OrderService
from app.database import get_db_session
from app.models.system_setting import SystemSetting
from app.models.order import Order
from app.models.inflow_webhook import InflowWebhook, WebhookStatus
from app.api.auth_middleware import (
    get_current_user_email,
    is_current_user_admin,
    require_admin,
    require_auth,
)
from app.utils.timezone import to_utc_iso_z
from app.services.audit_service import AuditService
from app.services.print_job_service import (
    PrintJobService,
    emit_orders_update,
    emit_print_job_available,
)
from app.services.compatibility_editor_service import (
    CompatibilityEditorConflict,
    CompatibilityEditorError,
    CompatibilityEditorNotInitialized,
    apply_mutation as apply_compatibility_editor_mutation,
    import_bundled_seed_if_empty,
)
from app.services.compatibility_approval_service import (
    get_workspace_document as get_compatibility_editor_workspace,
    resolve_pending_after_admin_mutation,
    review_change as review_compatibility_change_request,
    submit_bundle as submit_compatibility_editor_bundle,
    submit_change as submit_compatibility_editor_change,
)
from app.services.compatibility_publisher_service import (
    COMPATIBILITY_SUPERAPP_FILENAME,
    is_publish_configured as is_compatibility_publish_configured,
    publish_requested as publish_requested_compatibility_document,
    request_publication as request_compatibility_publication,
)
import logging

bp = Blueprint("system", __name__, url_prefix="/api/system")

logger = logging.getLogger(__name__)
inventory_reorder_service = InventoryReorderService(settings)

from app.services.system_setting_service import (
    SystemSettingService,
    DEFAULT_SETTINGS,
    SETTING_EMAIL_ENABLED,
    SETTING_TEAMS_RECIPIENT_ENABLED,
    SETTING_ADMIN_EMAILS,
    SETTING_ALLOWED_USER_EMAILS,
    SETTING_INVENTORY_REORDER_TEAMS_RECIPIENT_EMAILS,
    SETTING_REQUIRE_DIFFERENT_USER_FOR_PICK_AND_QA,
    SETTING_PICKLIST_PRINT_CLAIM_TIMEOUT_SECONDS,
)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", re.IGNORECASE)
CANOPY_ORDERS_ORDER_RE = re.compile(
    r"^TH(?P<digits>\d+)(?:-(?:P(?:\d+)?|R))?$", re.IGNORECASE
)

WORKFLOW_READABLE_SETTINGS = (
    SETTING_REQUIRE_DIFFERENT_USER_FOR_PICK_AND_QA,
)

VETTING_EDITOR_ALLOWED_SECTIONS = (
    "UnderConsideration",
    "Vetting",
    "AwaitingApproval",
    "ComingSoon",
)
VETTING_EDITOR_VETTING_URL_SECTIONS = ("Vetting", "AwaitingApproval")
VETTING_EDITOR_ALLOWED_CATEGORIES = (
    "ACCESSORIES",
    "MONITORS + DOCKS",
    "LAPTOPS + TABLETS",
    "DESKTOPS",
)

_VETTING_EDITOR_TIMEOUT = (15, 60)
_VETTING_EDITOR_DOWNLOAD_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

PRINT_AGENT_CLAIM_TIMEOUT_SECONDS = 300


def _get_print_agent_claim_timeout_seconds() -> int:
    db = get_db_session()
    try:
        raw_value = SystemSettingService.get_setting(
            db, SETTING_PICKLIST_PRINT_CLAIM_TIMEOUT_SECONDS
        )
    finally:
        db.close()

    try:
        timeout = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return PRINT_AGENT_CLAIM_TIMEOUT_SECONDS

    return timeout if timeout > 0 else PRINT_AGENT_CLAIM_TIMEOUT_SECONDS


def _find_order_for_picklist_reprint(db, order_identifier: str) -> Optional[Order]:
    normalized_identifier = str(order_identifier or "").strip()
    if not normalized_identifier:
        return None

    order = (
        db.query(Order)
        .filter(Order.inflow_order_id_lower == normalized_identifier.lower())
        .first()
    )
    if order:
        return order

    return db.query(Order).filter(Order.id == normalized_identifier).first()


def _require_print_agent() -> None:
    configured_token = (settings.picklist_print_agent_token or "").strip()
    if not configured_token:
        raise RuntimeError("Picklist print agent token is not configured")

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise PermissionError("Missing bearer token")

    provided_token = auth_header.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(provided_token, configured_token):
        raise PermissionError("Invalid bearer token")


def _normalize_canopyorders_order_value(raw_value: str) -> Optional[str]:
    trimmed = raw_value.strip()
    compact = "".join(trimmed.upper().split())
    if not compact:
        return None
    if compact.isdigit():
        return f"TH{compact}"
    if CANOPY_ORDERS_ORDER_RE.fullmatch(compact):
        return compact
    return None


def _base_canopyorders_order(value: str) -> str:
    normalized = _normalize_canopyorders_order_value(value)
    if not normalized:
        raise ValueError(f"Invalid Canopy order value: {value!r}")

    match = CANOPY_ORDERS_ORDER_RE.fullmatch(normalized)
    if not match:
        raise ValueError(f"Invalid Canopy order value: {value!r}")

    return f"TH{match.group('digits')}"


def _send_picklist_pdf(file_path: str, download_name: str):
    if file_path.startswith("http"):
        from app.services.sharepoint_service import get_sharepoint_service

        pdf_bytes = get_sharepoint_service().download_file("picklists", download_name)
        if not pdf_bytes:
            return jsonify({"error": "Picklist file not found"}), 404

        pdf_stream = BytesIO(pdf_bytes)
        pdf_stream.seek(0)
        return send_file(
            pdf_stream,
            mimetype="application/pdf",
            as_attachment=False,
            download_name=download_name,
        )

    storage_root = Path(settings.storage_root).resolve()
    resolved_path = Path(file_path).resolve()
    if not resolved_path.is_relative_to(storage_root):
        abort(403, description="Access denied")

    path = Path(file_path)
    if path.exists():
        return send_file(
            path.resolve(),
            mimetype="application/pdf",
            as_attachment=False,
            download_name=path.name,
        )

    from app.services.sharepoint_service import get_sharepoint_service

    pdf_bytes = get_sharepoint_service().download_file("picklists", download_name)
    if not pdf_bytes:
        return jsonify({"error": "Picklist file not found"}), 404

    pdf_stream = BytesIO(pdf_bytes)
    pdf_stream.seek(0)
    return send_file(
        pdf_stream,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=download_name,
    )


COMPATIBILITY_EDITOR_STATUS_VALUES = (
    "Compatible",
    "Incompatible",
    "Partially Compatible",
)
COMPATIBILITY_EDITOR_DETAIL_STATUS_VALUES = (
    "Functional",
    "Partially Functional",
    "Non-functional",
    "N/A",
)
COMPATIBILITY_EDITOR_DETAIL_FIELDS = (
    "display",
    "charging",
    "usbDetection",
    "ethernet",
    "audio",
    "sdCard",
)

_COMPATIBILITY_EDITOR_TIMEOUT = _VETTING_EDITOR_TIMEOUT
_COMPATIBILITY_EDITOR_DOWNLOAD_HEADERS = dict(_VETTING_EDITOR_DOWNLOAD_HEADERS)


def _normalize_vetting_editor_section_name(section_name: str) -> str:
    return section_name.strip().lower()


_VETTING_EDITOR_SECTION_BY_NORMALIZED_NAME = {
    _normalize_vetting_editor_section_name(section): section
    for section in VETTING_EDITOR_ALLOWED_SECTIONS
}
_VETTING_EDITOR_SECTION_BY_NORMALIZED_NAME.update(
    {
        "underconsideration": "UnderConsideration",
    }
)


def _get_vetting_editor_auth() -> tuple[str, str]:
    username = (
        settings.webdav_username or settings.vetting_editor_webdav_username or ""
    ).strip()
    password = settings.webdav_password or settings.vetting_editor_webdav_password
    if not username or not password:
        raise RuntimeError(
            "Vetting editor credentials are not configured (WEBDAV_USERNAME and WEBDAV_PASSWORD)."
        )
    return username, password


def _validate_vetting_editor_payload(payload: Any) -> dict[str, list[dict[str, str]]]:
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object keyed by section.")

    allowed_categories = set(VETTING_EDITOR_ALLOWED_CATEGORIES)
    unknown_sections: list[str] = []
    section_rows_by_canonical_name: dict[str, Any] = {}

    for raw_section_name, section_rows in payload.items():
        if not isinstance(raw_section_name, str):
            unknown_sections.append(str(raw_section_name))
            continue

        canonical_section_name = _VETTING_EDITOR_SECTION_BY_NORMALIZED_NAME.get(
            _normalize_vetting_editor_section_name(raw_section_name)
        )
        if canonical_section_name is None:
            unknown_sections.append(raw_section_name)
            continue

        if canonical_section_name in section_rows_by_canonical_name:
            raise ValueError(
                f"Duplicate section alias for '{canonical_section_name}': '{raw_section_name}'."
            )

        section_rows_by_canonical_name[canonical_section_name] = section_rows

    if unknown_sections:
        unknown_sections_sorted = sorted(
            unknown_sections, key=lambda section: section.lower()
        )
        raise ValueError(f"Unsupported sections: {', '.join(unknown_sections_sorted)}")

    vetting_url_sections = set(VETTING_EDITOR_VETTING_URL_SECTIONS)
    normalized: dict[str, list[dict[str, str]]] = {}

    for section in VETTING_EDITOR_ALLOWED_SECTIONS:
        if section not in section_rows_by_canonical_name:
            continue

        section_rows = section_rows_by_canonical_name[section]
        if not isinstance(section_rows, list):
            raise ValueError(f"Section '{section}' must be an array of rows.")

        normalized_section_rows: list[dict[str, str]] = []

        for index, row in enumerate(section_rows):
            if not isinstance(row, dict):
                raise ValueError(
                    f"Section '{section}' row {index + 1} must be an object."
                )

            allowed_fields = {"name", "category", "url", "vettingUrl"}
            unknown_fields = sorted(
                key for key in row.keys() if key not in allowed_fields
            )
            if unknown_fields:
                raise ValueError(
                    f"Section '{section}' row {index + 1} has unsupported fields: {', '.join(unknown_fields)}"
                )

            name = row.get("name")
            category = row.get("category")
            product_url = row.get("url")

            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    f"Section '{section}' row {index + 1} has invalid 'name'."
                )
            if not isinstance(category, str) or category not in allowed_categories:
                raise ValueError(
                    f"Section '{section}' row {index + 1} has invalid 'category'."
                )
            if not isinstance(product_url, str) or not product_url.strip():
                raise ValueError(
                    f"Section '{section}' row {index + 1} has invalid 'url'."
                )

            normalized_row: dict[str, str] = {
                "name": name.strip(),
                "category": category,
                "url": product_url.strip(),
            }

            vetting_url = row.get("vettingUrl")
            if section in vetting_url_sections:
                if vetting_url is not None:
                    if not isinstance(vetting_url, str):
                        raise ValueError(
                            f"Section '{section}' row {index + 1} has invalid 'vettingUrl'."
                        )
                    trimmed_vetting_url = vetting_url.strip()
                    if trimmed_vetting_url:
                        normalized_row["vettingUrl"] = trimmed_vetting_url
            elif vetting_url not in (None, ""):
                raise ValueError(
                    f"'vettingUrl' is only allowed for sections: {', '.join(VETTING_EDITOR_VETTING_URL_SECTIONS)}."
                )

            normalized_section_rows.append(normalized_row)

        normalized[section] = normalized_section_rows

    return normalized


def _try_download_vetting_editor_json(
    url: str, username: str, password: str
) -> Optional[dict[str, Any]]:
    headers = dict(_VETTING_EDITOR_DOWNLOAD_HEADERS)
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        base = f"{parsed.scheme}://{parsed.netloc}"
        headers["Origin"] = base
        headers["Referer"] = f"{base}/"

    attempts = (
        ("none", None),
        ("basic", HTTPBasicAuth(username, password)),
        ("digest", HTTPDigestAuth(username, password)),
    )

    for auth_name, auth in attempts:
        try:
            response = requests.get(
                url,
                headers=headers,
                auth=auth,
                timeout=_VETTING_EDITOR_TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            logger.warning("Vetting editor GET failed (%s): %s", auth_name, exc)
            continue

        if response.status_code != 200:
            logger.warning(
                "Vetting editor GET returned %s (%s)", response.status_code, auth_name
            )
            continue

        try:
            payload = response.json()
        except ValueError:
            logger.warning(
                "Vetting editor GET returned non-JSON payload (%s)", auth_name
            )
            continue

        if isinstance(payload, dict):
            return payload

        logger.warning("Vetting editor GET returned non-object JSON (%s)", auth_name)

    return None


def _upload_vetting_editor_json(
    url: str, payload: dict[str, list[dict[str, str]]], username: str, password: str
) -> bool:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    attempts = (
        ("digest", HTTPDigestAuth(username, password)),
        ("basic", HTTPBasicAuth(username, password)),
    )

    for auth_name, auth in attempts:
        try:
            requests.request(
                "PROPFIND",
                url,
                headers=headers,
                auth=auth,
                timeout=_VETTING_EDITOR_TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException:
            logger.debug("Vetting editor PROPFIND warmup failed (%s)", auth_name)

        try:
            response = requests.put(
                url,
                data=body,
                headers=headers,
                auth=auth,
                timeout=_VETTING_EDITOR_TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            logger.warning("Vetting editor PUT failed (%s): %s", auth_name, exc)
            continue

        if response.status_code in (200, 201, 204):
            return True

        logger.warning(
            "Vetting editor PUT returned %s (%s)", response.status_code, auth_name
        )

    return False


def _get_compatibility_editor_staging_auth() -> tuple[str, str]:
    username = (settings.webdav_username or "").strip()
    password = settings.webdav_password
    if not username or not password:
        raise RuntimeError(
            "Compatibility editor staging credentials are not configured "
            "(WEBDAV_USERNAME and WEBDAV_PASSWORD)."
        )
    return username, password


def _normalize_string_array(
    value: Any, field_name: str, *, allow_empty: bool = False
) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError(f"'{field_name}' must be an array of strings.")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"'{field_name}' entries must be strings.")
        trimmed = item.strip()
        if not trimmed and not allow_empty:
            continue
        if trimmed and trimmed not in seen:
            seen.add(trimmed)
            normalized.append(trimmed)
    return normalized


def _coerce_optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", ""}:
            return False
    return None


def _validate_compatibility_editor_staging_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object.")

    docks_raw = payload.get("docks")
    computers_raw = payload.get("computers")
    if not isinstance(docks_raw, dict):
        raise ValueError("'docks' must be an object keyed by dock SKU.")
    if not isinstance(computers_raw, dict):
        raise ValueError("'computers' must be an object keyed by computer SKU.")

    normalized_docks: dict[str, dict[str, Any]] = {}
    for raw_dock_key, raw_dock in docks_raw.items():
        if not isinstance(raw_dock_key, str):
            raise ValueError("Dock keys must be strings.")
        dock_key = raw_dock_key.strip()
        if not dock_key:
            raise ValueError("Dock keys cannot be blank.")
        if not isinstance(raw_dock, dict):
            raise ValueError(f"Dock '{dock_key}' must be an object.")

        name = raw_dock.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Dock '{dock_key}' has invalid 'name'.")

        normalized_dock: dict[str, Any] = dict(raw_dock)
        normalized_dock["name"] = name.strip()

        dock_url = raw_dock.get("url")
        if dock_url is not None:
            if not isinstance(dock_url, str):
                raise ValueError(f"Dock '{dock_key}' has invalid 'url'.")
            normalized_dock["url"] = dock_url.strip()

        hidden = raw_dock.get("hidden")
        if hidden is not None and not isinstance(hidden, bool):
            raise ValueError(f"Dock '{dock_key}' has invalid 'hidden' flag.")

        normalized_docks[dock_key] = normalized_dock

    dock_keys = set(normalized_docks.keys())
    normalized_computers: dict[str, dict[str, Any]] = {}
    for raw_computer_key, raw_computer in computers_raw.items():
        if not isinstance(raw_computer_key, str):
            raise ValueError("Computer keys must be strings.")
        computer_key = raw_computer_key.strip()
        if not computer_key:
            raise ValueError("Computer keys cannot be blank.")
        if not isinstance(raw_computer, dict):
            raise ValueError(f"Computer '{computer_key}' must be an object.")

        name = raw_computer.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Computer '{computer_key}' has invalid 'name'.")

        normalized_computer: dict[str, Any] = dict(raw_computer)
        normalized_computer["name"] = name.strip()

        computer_url = raw_computer.get("url")
        if computer_url is not None:
            if not isinstance(computer_url, str):
                raise ValueError(f"Computer '{computer_key}' has invalid 'url'.")
            normalized_computer["url"] = computer_url.strip()

        hidden = raw_computer.get("hidden")
        if hidden is not None and not isinstance(hidden, bool):
            raise ValueError(f"Computer '{computer_key}' has invalid 'hidden' flag.")

        incompatible_with = _normalize_string_array(
            raw_computer.get("incompatibleWith"), "incompatibleWith"
        )
        partially_compatible_with = _normalize_string_array(
            raw_computer.get("partiallyCompatibleWith"), "partiallyCompatibleWith"
        )

        overlap = set(incompatible_with).intersection(partially_compatible_with)
        if overlap:
            overlap_sorted = ", ".join(sorted(overlap))
            raise ValueError(
                f"Computer '{computer_key}' cannot list the same dock as incompatible and partially compatible: {overlap_sorted}."
            )

        compatibility_notes_raw = raw_computer.get("compatibilityNotes")
        compatibility_notes: dict[str, str] = {}
        if compatibility_notes_raw is not None:
            if not isinstance(compatibility_notes_raw, dict):
                raise ValueError(
                    f"Computer '{computer_key}' has invalid 'compatibilityNotes'."
                )
            for raw_note_dock_key, raw_note in compatibility_notes_raw.items():
                if not isinstance(raw_note_dock_key, str):
                    raise ValueError(
                        f"Computer '{computer_key}' compatibilityNotes keys must be strings."
                    )
                note_dock_key = raw_note_dock_key.strip()
                if note_dock_key not in dock_keys:
                    raise ValueError(
                        f"Computer '{computer_key}' compatibilityNotes references unknown dock '{raw_note_dock_key}'."
                    )
                if not isinstance(raw_note, str):
                    raise ValueError(
                        f"Computer '{computer_key}' compatibilityNotes for dock '{note_dock_key}' must be a string."
                    )
                trimmed_note = raw_note.strip()
                if trimmed_note:
                    compatibility_notes[note_dock_key] = trimmed_note

        compatibility_data_raw = raw_computer.get("compatibilityData")
        compatibility_data: dict[str, dict[str, Any]] = {}
        if compatibility_data_raw is not None:
            if not isinstance(compatibility_data_raw, dict):
                raise ValueError(
                    f"Computer '{computer_key}' has invalid 'compatibilityData'."
                )
            for raw_data_dock_key, raw_data in compatibility_data_raw.items():
                if not isinstance(raw_data_dock_key, str):
                    raise ValueError(
                        f"Computer '{computer_key}' compatibilityData keys must be strings."
                    )
                data_dock_key = raw_data_dock_key.strip()
                if data_dock_key not in dock_keys:
                    raise ValueError(
                        f"Computer '{computer_key}' compatibilityData references unknown dock '{raw_data_dock_key}'."
                    )
                if not isinstance(raw_data, dict):
                    raise ValueError(
                        f"Computer '{computer_key}' compatibilityData for dock '{data_dock_key}' must be an object."
                    )

                normalized_entry: dict[str, Any] = dict(raw_data)
                status = raw_data.get("compatibilityStatus")
                if status is not None:
                    if (
                        not isinstance(status, str)
                        or status not in COMPATIBILITY_EDITOR_STATUS_VALUES
                    ):
                        allowed = ", ".join(COMPATIBILITY_EDITOR_STATUS_VALUES)
                        raise ValueError(
                            f"Computer '{computer_key}' compatibilityData for dock '{data_dock_key}' has invalid "
                            f"compatibilityStatus. Allowed: {allowed}."
                        )

                notes_value = raw_data.get("notes")
                if notes_value is not None:
                    if not isinstance(notes_value, str):
                        raise ValueError(
                            f"Computer '{computer_key}' compatibilityData for dock '{data_dock_key}' has invalid notes."
                        )
                    trimmed_entry_notes = notes_value.strip()
                    if trimmed_entry_notes:
                        normalized_entry["notes"] = trimmed_entry_notes
                    else:
                        normalized_entry.pop("notes", None)

                reboot_needed = raw_data.get("rebootNeeded")
                if reboot_needed is not None:
                    coerced_reboot_needed = _coerce_optional_bool(reboot_needed)
                    if coerced_reboot_needed is None:
                        logger.warning(
                            "Dropping invalid rebootNeeded value for computer '%s', dock '%s': %r",
                            computer_key,
                            data_dock_key,
                            reboot_needed,
                        )
                        normalized_entry.pop("rebootNeeded", None)
                    else:
                        normalized_entry["rebootNeeded"] = coerced_reboot_needed

                student_edited = raw_data.get("studentEdited")
                if student_edited is not None and not isinstance(student_edited, bool):
                    raise ValueError(
                        f"Computer '{computer_key}' compatibilityData for dock '{data_dock_key}' has invalid studentEdited."
                    )

                for detail_field in COMPATIBILITY_EDITOR_DETAIL_FIELDS:
                    detail_value = raw_data.get(detail_field)
                    if detail_value is None:
                        continue
                    if (
                        not isinstance(detail_value, str)
                        or detail_value not in COMPATIBILITY_EDITOR_DETAIL_STATUS_VALUES
                    ):
                        allowed = ", ".join(COMPATIBILITY_EDITOR_DETAIL_STATUS_VALUES)
                        raise ValueError(
                            f"Computer '{computer_key}' compatibilityData for dock '{data_dock_key}' has invalid "
                            f"'{detail_field}'. Allowed: {allowed}."
                        )

                compatibility_data[data_dock_key] = normalized_entry

        def _ensure_known_docks(values: Sequence[str], field_name: str) -> None:
            unknown = sorted({dock for dock in values if dock not in dock_keys})
            if unknown:
                raise ValueError(
                    f"Computer '{computer_key}' {field_name} references unknown docks: {', '.join(unknown)}."
                )

        _ensure_known_docks(incompatible_with, "incompatibleWith")
        _ensure_known_docks(partially_compatible_with, "partiallyCompatibleWith")

        normalized_computer["incompatibleWith"] = incompatible_with
        normalized_computer["partiallyCompatibleWith"] = partially_compatible_with
        normalized_computer["compatibilityNotes"] = compatibility_notes
        normalized_computer["compatibilityData"] = compatibility_data

        normalized_computers[computer_key] = normalized_computer

    normalized_payload: dict[str, Any] = {
        key: value
        for key, value in payload.items()
        if key not in {"docks", "computers"}
    }
    normalized_payload["docks"] = normalized_docks
    normalized_payload["computers"] = normalized_computers
    return normalized_payload


def _try_download_compatibility_editor_staging_json(
    url: str,
    username: str,
    password: str,
) -> Optional[dict[str, Any]]:
    headers = dict(_COMPATIBILITY_EDITOR_DOWNLOAD_HEADERS)
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        base = f"{parsed.scheme}://{parsed.netloc}"
        headers["Origin"] = base
        headers["Referer"] = f"{base}/"

    attempts = (
        ("none", None),
        ("basic", HTTPBasicAuth(username, password)),
        ("digest", HTTPDigestAuth(username, password)),
    )

    for auth_name, auth in attempts:
        try:
            response = requests.get(
                url,
                headers=headers,
                auth=auth,
                timeout=_COMPATIBILITY_EDITOR_TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            logger.warning(
                "Compatibility editor staging GET failed (%s): %s", auth_name, exc
            )
            continue

        if response.status_code != 200:
            logger.warning(
                "Compatibility editor staging GET returned %s (%s)",
                response.status_code,
                auth_name,
            )
            continue

        try:
            payload = response.json()
        except ValueError:
            logger.warning(
                "Compatibility editor staging GET returned non-JSON payload (%s)",
                auth_name,
            )
            continue

        if isinstance(payload, dict):
            return payload

        logger.warning(
            "Compatibility editor staging GET returned non-object JSON (%s)", auth_name
        )

    return None


def _upload_compatibility_editor_staging_json(
    url: str,
    payload: dict[str, Any],
    username: str,
    password: str,
) -> bool:
    body = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    attempts = (
        ("digest", HTTPDigestAuth(username, password)),
        ("basic", HTTPBasicAuth(username, password)),
    )

    for auth_name, auth in attempts:
        try:
            requests.request(
                "PROPFIND",
                url,
                headers=headers,
                auth=auth,
                timeout=_COMPATIBILITY_EDITOR_TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException:
            logger.debug(
                "Compatibility editor staging PROPFIND warmup failed (%s)", auth_name
            )

        try:
            response = requests.put(
                url,
                data=body,
                headers=headers,
                auth=auth,
                timeout=_COMPATIBILITY_EDITOR_TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            logger.warning(
                "Compatibility editor staging PUT failed (%s): %s", auth_name, exc
            )
            continue

        if response.status_code in (200, 201, 204):
            return True

        logger.warning(
            "Compatibility editor staging PUT returned %s (%s)",
            response.status_code,
            auth_name,
        )

    return False


def _to_utc_iso_z(value: Optional[datetime]) -> Optional[str]:
    return to_utc_iso_z(value)


def _normalize_admin_emails(raw_emails: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in raw_emails:
        email = (item or "").strip().lower()
        if not email:
            continue
        normalized.append(email)
    # Deterministic order for diffs and UX.
    return sorted(set(normalized))


def _parse_allowlist_string(raw_value: Optional[str]) -> list[str]:
    # Reuse env parsing logic (accept JSON list string and CSV).
    parsed = settings._parse_admin_emails(raw_value)
    return _normalize_admin_emails(parsed)


def _get_request_user_email_normalized() -> str:
    # Prefer middleware-populated email to avoid extra DB query.
    from flask import g

    email = (
        (getattr(g, "user_email", None) or get_current_user_email() or "")
        .strip()
        .lower()
    )
    return email


def _get_db_admin_allowlist() -> list[str]:
    db = get_db_session()
    try:
        raw = SystemSettingService.get_setting(db, SETTING_ADMIN_EMAILS)
        return _parse_allowlist_string(raw)
    finally:
        db.close()


def _get_db_allowed_user_allowlist() -> list[str]:
    db = get_db_session()
    try:
        raw = SystemSettingService.get_setting(db, SETTING_ALLOWED_USER_EMAILS)
        return _parse_allowlist_string(raw)
    finally:
        db.close()


def _get_db_inventory_reorder_recipients() -> list[str]:
    db = get_db_session()
    try:
        raw = SystemSettingService.get_setting(
            db, SETTING_INVENTORY_REORDER_TEAMS_RECIPIENT_EMAILS
        )
        return _parse_allowlist_string(raw)
    finally:
        db.close()


def _is_env_admin_override_active() -> bool:
    return bool(settings.get_admin_emails())


def _is_env_allowed_user_override_active() -> bool:
    return bool(settings.get_allowed_user_emails())


# ============ Settings Endpoints ============


@bp.route("/settings", methods=["GET"])
@require_admin
def get_system_settings():
    """Get all system settings."""
    # SystemSettingService handles its own DB session if not provided
    result = SystemSettingService.get_all_settings()
    return jsonify(result)


@bp.route("/settings/<key>", methods=["GET"])
@require_admin
def get_system_setting(key: str):
    """Get a single system setting."""
    if key not in DEFAULT_SETTINGS:
        return jsonify({"error": f"Unknown setting: {key}"}), 400

    db = get_db_session()
    try:
        setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        defaults = DEFAULT_SETTINGS[key]
        return jsonify(
            {
                "key": key,
                "value": setting.value if setting else defaults["value"],
                "type": defaults.get("type"),
                "description": defaults.get("description"),
                "updated_at": _to_utc_iso_z(setting.updated_at) if setting else None,
                "updated_by": setting.updated_by if setting else None,
            }
        )
    finally:
        db.close()


@bp.route("/workflow-settings", methods=["GET"])
@require_auth
def get_workflow_settings():
    """Get workflow settings that normal authenticated operators may read."""
    db = get_db_session()
    try:
        result: dict[str, dict[str, Optional[str]]] = {}
        for key in WORKFLOW_READABLE_SETTINGS:
            setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
            defaults = DEFAULT_SETTINGS[key]
            result[key] = {
                "value": setting.value if setting else defaults["value"],
                "description": cast(Optional[str], defaults.get("description")),
                "updated_at": _to_utc_iso_z(setting.updated_at) if setting and setting.updated_at else None,
                "updated_by": setting.updated_by if setting else None,
            }

        return jsonify(result)
    finally:
        db.close()


@bp.route("/settings/<key>", methods=["PUT"])
@require_admin
def update_system_setting(key: str):
    """Update a system setting."""
    if key not in DEFAULT_SETTINGS:
        return jsonify({"error": f"Unknown setting: {key}"}), 400

    if key == SETTING_ADMIN_EMAILS:
        return jsonify(
            {"error": "Admin allowlist must be updated via /api/system/admins"}
        ), 400
    if key == SETTING_ALLOWED_USER_EMAILS:
        return jsonify(
            {"error": "Allowed-user list must be updated via /api/system/allowed-users"}
        ), 400
    if key == SETTING_INVENTORY_REORDER_TEAMS_RECIPIENT_EMAILS:
        return jsonify(
            {
                "error": (
                    "Inventory reorder recipients must be updated via "
                    "/api/system/inventory-reorder-recipients"
                )
            }
        ), 400

    data = request.get_json()
    if not data or "value" not in data:
        return jsonify({"error": "Missing 'value' in request body"}), 400

    updated_by = get_current_user_email()

    # SystemSettingService handles its own DB session
    setting = SystemSettingService.set_setting(key, str(data["value"]), updated_by)
    defaults = DEFAULT_SETTINGS[key]

    return jsonify(
        {
            "key": setting.key,
            "value": setting.value,
            "type": defaults.get("type"),
            "description": defaults.get("description"),
            "updated_at": _to_utc_iso_z(setting.updated_at),
            "updated_by": setting.updated_by,
        }
    )


# ============ Admin Allowlist Endpoints ============


@bp.route("/admins", methods=["GET"])
@require_admin
def get_admins():
    """Get the effective admin allowlist + its source."""
    env_admins = _normalize_admin_emails(settings.get_admin_emails())
    db_admins = _get_db_admin_allowlist()

    # Merge: env entries are pinned, DB entries are app-managed.
    merged = sorted(set(env_admins) | set(db_admins))

    if env_admins and db_admins:
        source = "mixed"
    elif env_admins:
        source = "env"
    elif db_admins:
        source = "db"
    else:
        source = "default"

    response: dict[str, Any] = {
        "admins": merged,
        "source": source,
        "env_admins": env_admins,
        "db_admins": db_admins,
    }
    return jsonify(response)


@bp.route("/admins", methods=["PUT"])
@require_admin
def update_admins():
    """Update the DB-backed admin allowlist. Env entries are pinned and auto-merged on reads."""
    data = request.get_json(silent=True) or {}
    admins_payload = data.get("admins")
    if not isinstance(admins_payload, list):
        return jsonify({"error": "Missing 'admins' list in request body"}), 400

    raw_emails: list[str] = []
    for item in admins_payload:
        if not isinstance(item, str):
            return jsonify({"error": "Each admin email must be a string"}), 400
        raw_emails.append(item)

    normalized = _normalize_admin_emails(raw_emails)

    invalid = [email for email in normalized if not EMAIL_RE.match(email)]
    if invalid:
        return (
            jsonify(
                {
                    "error": "One or more admin emails are invalid.",
                    "invalid": invalid,
                }
            ),
            400,
        )

    caller_email = _get_request_user_email_normalized()
    caller_looks_like_email = bool(caller_email and EMAIL_RE.match(caller_email))
    caller_in_env = caller_looks_like_email and caller_email in _normalize_admin_emails(settings.get_admin_emails())

    if not settings.is_dev():
        if not normalized:
            return (
                jsonify(
                    {
                        "error": "Refusing to set an empty admin allowlist in non-development environments (would lock out all admins).",
                    }
                ),
                400,
            )
        if caller_looks_like_email and caller_email not in normalized and not caller_in_env:
            return (
                jsonify(
                    {
                        "error": f"Refusing to remove your own admin access. The allowlist must include your email ({caller_email}) to prevent accidental lockout.",
                    }
                ),
                400,
            )

    db = get_db_session()
    try:
        setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == SETTING_ADMIN_EMAILS)
            .first()
        )
        old_raw = setting.value if setting else None
        old_list = _parse_allowlist_string(old_raw)

        new_raw = json.dumps(normalized)
        updated_by = caller_email or get_current_user_email()

        if not setting:
            setting = SystemSetting(
                key=SETTING_ADMIN_EMAILS,
                value=new_raw,
                description=DEFAULT_SETTINGS.get(SETTING_ADMIN_EMAILS, {}).get(
                    "description"
                ),
                updated_by=updated_by,
            )
            db.add(setting)
        else:
            setting.value = new_raw
            setting.updated_by = updated_by

        audit = AuditService(db)
        audit.log_system_action(
            action="admins.update",
            entity_id="admin_allowlist",
            user_id=updated_by,
            old_value={"admins": old_list},
            new_value={"admins": normalized},
            description=f"Updated admin allowlist ({len(old_list)} -> {len(normalized)})",
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )

        db.commit()

        # Merge env admins into the response so the caller sees the full picture.
        env_admins = _normalize_admin_emails(settings.get_admin_emails())
        merged = sorted(set(env_admins) | set(normalized))
        source = "mixed" if env_admins else "db"

        return jsonify(
            {
                "admins": merged,
                "db_admins": normalized,
                "env_admins": env_admins,
                "source": source,
                "updated_by": updated_by,
            }
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@bp.route("/allowed-users", methods=["GET"])
@require_admin
def get_allowed_users():
    """Get the effective app-access allowlist + its source."""
    env_allowed_users = _normalize_admin_emails(settings.get_allowed_user_emails())
    db_allowed_users = _get_db_allowed_user_allowlist()

    merged = sorted(set(env_allowed_users) | set(db_allowed_users))

    if env_allowed_users and db_allowed_users:
        source = "mixed"
    elif env_allowed_users:
        source = "env"
    elif db_allowed_users:
        source = "db"
    else:
        source = "default"

    response: dict[str, Any] = {
        "allowed_users": merged,
        "source": source,
        "env_allowed_users": env_allowed_users,
        "db_allowed_users": db_allowed_users,
        "restriction_enabled": bool(merged),
        "admins_are_always_allowed": True,
    }
    return jsonify(response)


@bp.route("/allowed-users", methods=["PUT"])
@require_admin
def update_allowed_users():
    """Update the DB-backed app-access allowlist. Env entries are pinned and auto-merged on reads."""
    data = request.get_json(silent=True) or {}
    allowed_users_payload = data.get("allowed_users")
    if not isinstance(allowed_users_payload, list):
        return jsonify({"error": "Missing 'allowed_users' list in request body"}), 400

    raw_emails: list[str] = []
    for item in allowed_users_payload:
        if not isinstance(item, str):
            return jsonify({"error": "Each allowed-user email must be a string"}), 400
        raw_emails.append(item)

    normalized = _normalize_admin_emails(raw_emails)

    invalid = [email for email in normalized if not EMAIL_RE.match(email)]
    if invalid:
        return (
            jsonify(
                {
                    "error": "One or more allowed-user emails are invalid.",
                    "invalid": invalid,
                }
            ),
            400,
        )

    if not settings.is_dev() and not normalized and not _is_env_allowed_user_override_active():
        return (
            jsonify(
                {
                    "error": "Refusing to set an empty allowed-user list in non-development environments.",
                }
            ),
            400,
        )

    caller_email = _get_request_user_email_normalized()
    updated_by = caller_email or get_current_user_email()

    db = get_db_session()
    try:
        setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == SETTING_ALLOWED_USER_EMAILS)
            .first()
        )
        old_raw = setting.value if setting else None
        old_list = _parse_allowlist_string(old_raw)
        new_raw = json.dumps(normalized)

        if not setting:
            setting = SystemSetting(
                key=SETTING_ALLOWED_USER_EMAILS,
                value=new_raw,
                description=DEFAULT_SETTINGS.get(SETTING_ALLOWED_USER_EMAILS, {}).get(
                    "description"
                ),
                updated_by=updated_by,
            )
            db.add(setting)
        else:
            setting.value = new_raw
            setting.updated_by = updated_by

        audit = AuditService(db)
        audit.log_system_action(
            action="allowed_users.update",
            entity_id="allowed_user_allowlist",
            user_id=updated_by,
            old_value={"allowed_users": old_list},
            new_value={"allowed_users": normalized},
            description=f"Updated app-access allowlist ({len(old_list)} -> {len(normalized)})",
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )

        db.commit()

        env_allowed_users = _normalize_admin_emails(settings.get_allowed_user_emails())
        merged = sorted(set(env_allowed_users) | set(normalized))
        source = "mixed" if env_allowed_users else "db"

        return jsonify(
            {
                "allowed_users": merged,
                "db_allowed_users": normalized,
                "env_allowed_users": env_allowed_users,
                "source": source,
                "restriction_enabled": bool(merged),
                "admins_are_always_allowed": True,
                "updated_by": updated_by,
            }
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ============ Inventory Reorder Recipient Endpoints ============


@bp.route("/inventory-reorder-recipients", methods=["GET"])
@require_admin
def get_inventory_reorder_recipients():
    """Get effective inventory reorder recipients and their configuration sources."""
    env_recipients = _parse_allowlist_string(
        settings.inventory_reorder_teams_recipient_email
    )
    db_recipients = [
        email
        for email in _get_db_inventory_reorder_recipients()
        if email not in env_recipients
    ]
    merged = sorted(set(env_recipients) | set(db_recipients))

    if env_recipients and db_recipients:
        source = "mixed"
    elif env_recipients:
        source = "env"
    elif db_recipients:
        source = "db"
    else:
        source = "default"

    return jsonify(
        {
            "recipients": merged,
            "source": source,
            "env_recipients": env_recipients,
            "db_recipients": db_recipients,
        }
    )


@bp.route("/inventory-reorder-recipients", methods=["PUT"])
@require_admin
def update_inventory_reorder_recipients():
    """Update DB recipients while preserving recipients configured in the environment."""
    data = request.get_json(silent=True) or {}
    recipients_payload = data.get("recipients")
    if not isinstance(recipients_payload, list):
        return jsonify({"error": "Missing 'recipients' list in request body"}), 400

    raw_emails: list[str] = []
    for item in recipients_payload:
        if not isinstance(item, str):
            return jsonify({"error": "Each recipient email must be a string"}), 400
        raw_emails.append(item)

    normalized = _normalize_admin_emails(raw_emails)
    invalid = [email for email in normalized if not EMAIL_RE.match(email)]
    if invalid:
        return (
            jsonify(
                {
                    "error": "One or more recipient emails are invalid.",
                    "invalid": invalid,
                }
            ),
            400,
        )

    env_recipients = _parse_allowlist_string(
        settings.inventory_reorder_teams_recipient_email
    )
    normalized = [email for email in normalized if email not in env_recipients]

    caller_email = _get_request_user_email_normalized()
    updated_by = caller_email or get_current_user_email()

    db = get_db_session()
    try:
        setting = (
            db.query(SystemSetting)
            .filter(
                SystemSetting.key
                == SETTING_INVENTORY_REORDER_TEAMS_RECIPIENT_EMAILS
            )
            .first()
        )
        old_raw = setting.value if setting else None
        old_list = _parse_allowlist_string(old_raw)
        new_raw = json.dumps(normalized)

        if not setting:
            setting = SystemSetting(
                key=SETTING_INVENTORY_REORDER_TEAMS_RECIPIENT_EMAILS,
                value=new_raw,
                description=DEFAULT_SETTINGS[
                    SETTING_INVENTORY_REORDER_TEAMS_RECIPIENT_EMAILS
                ]["description"],
                updated_by=updated_by,
            )
            db.add(setting)
        else:
            setting.value = new_raw
            setting.updated_by = updated_by

        AuditService(db).log_system_action(
            action="inventory_reorder_recipients.update",
            entity_id="inventory_reorder_recipients",
            user_id=updated_by,
            old_value={"recipients": old_list},
            new_value={"recipients": normalized},
            description=(
                "Updated inventory reorder Teams recipients "
                f"({len(old_list)} -> {len(normalized)})"
            ),
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )

        db.commit()

        merged = sorted(set(env_recipients) | set(normalized))
        if env_recipients and normalized:
            source = "mixed"
        elif env_recipients:
            source = "env"
        elif normalized:
            source = "db"
        else:
            source = "default"

        return jsonify(
            {
                "recipients": merged,
                "db_recipients": normalized,
                "env_recipients": env_recipients,
                "source": source,
                "updated_by": updated_by,
            }
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@bp.route("/print-jobs", methods=["GET"])
@require_admin
def list_print_jobs():
    status = (request.args.get("status") or "").strip() or None
    limit_raw = (request.args.get("limit") or "25").strip()
    try:
        limit = max(1, min(int(limit_raw), 100))
    except ValueError:
        limit = 25

    db = get_db_session()
    try:
        jobs = PrintJobService(db).list_jobs(status=status, limit=limit)
        return jsonify({"jobs": [PrintJobService.serialize_job(job) for job in jobs]})
    finally:
        db.close()


@bp.route("/orders/<order_id>/print-jobs", methods=["GET"])
@require_admin
def list_order_print_jobs(order_id: str):
    db = get_db_session()
    try:
        jobs = PrintJobService(db).get_order_jobs(order_id)
        return jsonify({"jobs": [PrintJobService.serialize_job(job) for job in jobs]})
    finally:
        db.close()


@bp.route("/orders/<order_id>/reprint-picklist", methods=["POST"])
@require_admin
def reprint_picklist(order_id: str):
    db = get_db_session()
    try:
        order = _find_order_for_picklist_reprint(db, order_id)
        if not order:
            return jsonify({"error": "Order not found"}), 404
        if not order.picklist_path:
            return jsonify(
                {"error": "Picklist has not been generated for this order"}
            ), 409

        requested_by = get_current_user_email()
        job = PrintJobService(db).enqueue_picklist_print(
            order,
            trigger_source="manual",
            requested_by=requested_by,
        )
        db.commit()

        emit_print_job_available(job)
        emit_orders_update("Picklist print job queued")

        return jsonify({"success": True, "job": PrintJobService.serialize_job(job)})
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@bp.route("/print-agent/claim-next", methods=["POST"])
def claim_next_print_job():
    try:
        _require_print_agent()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    db = get_db_session()
    try:
        service = PrintJobService(db)
        job = service.claim_next_pending_job(
            claim_timeout_seconds=_get_print_agent_claim_timeout_seconds()
        )
        if not job:
            db.commit()
            return jsonify({"job": None})

        db.commit()
        payload = PrintJobService.serialize_job(job)
        payload["download_url"] = f"/api/system/print-agent/jobs/{job.id}/file"
        return jsonify({"job": payload})
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@bp.route("/print-agent/jobs/<job_id>/file", methods=["GET"])
def get_print_job_file(job_id: str):
    try:
        _require_print_agent()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    db = get_db_session()
    try:
        job = PrintJobService(db).get_job(job_id)
        file_path = job.file_path or ""
        order = job.order
        download_name = f"{(order.inflow_order_id if order else None) or job.id}.pdf"
        return _send_picklist_pdf(file_path, download_name)
    finally:
        db.close()


@bp.route("/print-agent/jobs/<job_id>/complete", methods=["POST"])
def complete_print_job(job_id: str):
    try:
        _require_print_agent()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    db = get_db_session()
    try:
        job = PrintJobService(db).mark_completed(job_id)
        db.commit()
        emit_orders_update("Picklist print job completed")
        return jsonify({"success": True, "job": PrintJobService.serialize_job(job)})
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@bp.route("/print-agent/jobs/<job_id>/fail", methods=["POST"])
def fail_print_job(job_id: str):
    try:
        _require_print_agent()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    payload = request.get_json(silent=True) or {}
    error_message = str(payload.get("error") or "Unknown print failure")

    db = get_db_session()
    try:
        job = PrintJobService(db).mark_failed(job_id, error_message=error_message)
        db.commit()
        emit_orders_update("Picklist print job failed")
        return jsonify({"success": True, "job": PrintJobService.serialize_job(job)})
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ============ Vetting Editor Endpoints ============


@bp.route("/vetting-editor", methods=["GET"])
@require_admin
def get_vetting_editor_data():
    download_url = (settings.vetting_editor_download_url or "").strip()
    upload_url = (settings.vetting_editor_upload_url or "").strip()
    if not download_url and not upload_url:
        return jsonify(
            {
                "error": "Vetting editor is not configured (missing VETTING_EDITOR_DOWNLOAD_URL)."
            }
        ), 500

    try:
        username, password = _get_vetting_editor_auth()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    candidate_urls: list[str] = []
    for candidate in (download_url, upload_url):
        if candidate and candidate not in candidate_urls:
            candidate_urls.append(candidate)

    payload: Optional[dict[str, Any]] = None
    for url in candidate_urls:
        payload = _try_download_vetting_editor_json(url, username, password)
        if payload is not None:
            break

    if payload is None:
        return jsonify(
            {"error": "Failed to fetch vetting editor JSON from WebDAV."}
        ), 502

    try:
        normalized = _validate_vetting_editor_payload(payload)
    except ValueError as exc:
        return jsonify({"error": f"Remote vetting editor JSON is invalid: {exc}"}), 502

    return jsonify(normalized)


@bp.route("/vetting-editor", methods=["PUT"])
@require_admin
def save_vetting_editor_data():
    upload_url = (settings.vetting_editor_upload_url or "").strip()
    if not upload_url:
        return jsonify(
            {
                "error": "Vetting editor is not configured (missing VETTING_EDITOR_UPLOAD_URL)."
            }
        ), 500

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Missing JSON request body."}), 400

    try:
        normalized = _validate_vetting_editor_payload(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        username, password = _get_vetting_editor_auth()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    if not _upload_vetting_editor_json(upload_url, normalized, username, password):
        return jsonify(
            {"error": "Failed to upload vetting editor JSON to WebDAV."}
        ), 502

    return jsonify({"success": True})


def _emit_compatibility_editor_update(document: dict[str, Any]) -> None:
    try:
        from app.main import socketio

        socketio.emit(
            "compatibility_editor_updated",
            {
                "revision": document["revision"],
                "workspaceRevision": document.get("workspaceRevision", 0),
            },
            room="compatibility-editor",
        )
    except Exception:
        logger.exception("Failed to broadcast compatibility editor update")


@bp.route("/compatibility-editor", methods=["GET"])
@require_auth
def get_compatibility_editor_data():
    db = get_db_session()
    try:
        document = get_compatibility_editor_workspace(db)
    except CompatibilityEditorNotInitialized as exc:
        try:
            import_bundled_seed_if_empty(db, actor=get_current_user_email())
            document = get_compatibility_editor_workspace(db)
        except (OSError, ValueError) as seed_exc:
            logger.exception("Failed to initialize compatibility editor seed")
            return jsonify(
                {"error": f"Failed to initialize compatibility editor: {seed_exc}"}
            ), 503
    finally:
        db.close()

    document["publication"]["filename"] = COMPATIBILITY_SUPERAPP_FILENAME
    document["publication"]["configured"] = is_compatibility_publish_configured()
    return jsonify(document)


@bp.route("/compatibility-editor", methods=["PATCH"])
@require_auth
def mutate_compatibility_editor_data():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Missing JSON request body."}), 400

    db = get_db_session()
    actor = get_current_user_email()
    is_admin = is_current_user_admin()
    mutation = payload.get("mutation") if isinstance(payload, dict) else None
    mutation_type = mutation.get("type") if isinstance(mutation, dict) else None
    try:
        if is_admin and mutation_type in {"computer.add", "dock.add"}:
            document, duplicate = submit_compatibility_editor_change(
                db,
                payload,
                actor=actor,
            )
        elif is_admin:
            try:
                _approved_document, duplicate = apply_compatibility_editor_mutation(
                    db,
                    payload,
                    actor=actor,
                )
            except CompatibilityEditorConflict as exc:
                if (
                    isinstance(mutation, dict)
                    and mutation.get("type")
                    in {"cell.update", "computer.update", "dock.update"}
                    and exc.current_version is None
                ):
                    document, duplicate = submit_compatibility_editor_change(
                        db,
                        payload,
                        actor=actor,
                        preserve_ready_for_review=True,
                    )
                else:
                    raise
            else:
                document = resolve_pending_after_admin_mutation(
                    db, payload, actor=actor
                )
        else:
            document, duplicate = submit_compatibility_editor_change(
                db,
                payload,
                actor=actor,
            )
    except CompatibilityEditorConflict as exc:
        try:
            current_document = get_compatibility_editor_workspace(db)
            current_document["publication"]["filename"] = (
                COMPATIBILITY_SUPERAPP_FILENAME
            )
            current_document["publication"]["configured"] = (
                is_compatibility_publish_configured()
            )
        except CompatibilityEditorNotInitialized:
            current_document = None
        return jsonify(
            {
                "error": str(exc),
                "conflict": {
                    "target": exc.target,
                    "currentVersion": exc.current_version,
                },
                "document": current_document,
            }
        ), 409
    except CompatibilityEditorNotInitialized as exc:
        return jsonify({"error": str(exc)}), 503
    except CompatibilityEditorError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        db.close()

    if not duplicate:
        _emit_compatibility_editor_update(document)
    document["duplicate"] = duplicate
    document["publication"]["filename"] = COMPATIBILITY_SUPERAPP_FILENAME
    document["publication"]["configured"] = is_compatibility_publish_configured()
    return jsonify(document)


@bp.route("/compatibility-editor/changes/<change_id>/review", methods=["POST"])
@require_admin
def review_compatibility_editor_change(change_id: str):
    payload = request.get_json(silent=True) or {}
    action = payload.get("action")
    note = payload.get("note")
    if note is not None and not isinstance(note, str):
        return jsonify({"error": "'note' must be a string."}), 400

    db = get_db_session()
    try:
        document = review_compatibility_change_request(
            db,
            change_id,
            action=action,
            actor=get_current_user_email(),
            note=note,
        )
    except CompatibilityEditorConflict as exc:
        return jsonify(
            {
                "error": str(exc),
                "conflict": {
                    "target": exc.target,
                    "currentVersion": exc.current_version,
                },
            }
        ), 409
    except CompatibilityEditorNotInitialized as exc:
        return jsonify({"error": str(exc)}), 503
    except CompatibilityEditorError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        db.close()

    _emit_compatibility_editor_update(document)
    document["publication"]["filename"] = COMPATIBILITY_SUPERAPP_FILENAME
    document["publication"]["configured"] = is_compatibility_publish_configured()
    return jsonify(document)


@bp.route("/compatibility-editor/changes/<change_id>/submit", methods=["POST"])
@require_auth
def submit_compatibility_editor_bundle_for_review(change_id: str):
    db = get_db_session()
    try:
        document = submit_compatibility_editor_bundle(
            db,
            change_id,
            actor=get_current_user_email(),
        )
    except CompatibilityEditorConflict as exc:
        return jsonify(
            {
                "error": str(exc),
                "conflict": {
                    "target": exc.target,
                    "currentVersion": exc.current_version,
                },
            }
        ), 409
    except CompatibilityEditorNotInitialized as exc:
        return jsonify({"error": str(exc)}), 503
    except CompatibilityEditorError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        db.close()

    _emit_compatibility_editor_update(document)
    document["publication"]["filename"] = COMPATIBILITY_SUPERAPP_FILENAME
    document["publication"]["configured"] = (
        is_compatibility_publish_configured()
    )
    return jsonify(document)


@bp.route("/compatibility-editor/publish", methods=["POST"])
@require_admin
def publish_compatibility_editor_data():
    db = get_db_session()
    try:
        snapshot = request_compatibility_publication(
            db, actor=get_current_user_email()
        )
        snapshot_id = snapshot.id
    except Exception as exc:
        logger.exception("Failed to prepare compatibility publication snapshot")
        return jsonify({"error": str(exc)}), 500
    finally:
        db.close()

    result = publish_requested_compatibility_document(snapshot_id=snapshot_id)
    status = 200 if result.success else 502
    return jsonify(
        {
            "attempted": result.attempted,
            "success": result.success,
            "revision": result.revision,
            "pending": result.pending,
            "error": result.error,
            "filename": COMPATIBILITY_SUPERAPP_FILENAME,
            "snapshotId": result.snapshot_id,
        }
    ), status


@bp.route("/compatibility-editor-staging", methods=["GET", "PUT"])
@require_admin
def compatibility_editor_staging_retired():
    return jsonify(
        {
            "error": (
                "The legacy compatibility staging endpoint is retired. "
                "Use /api/system/compatibility-editor."
            )
        }
    ), 410


# ============ Inventory Reorder Tool Endpoints ============


@bp.route("/inventory-reorder", methods=["GET"])
@require_admin
def get_inventory_reorder_data():
    show_all = request.args.get("all") == "1"

    try:
        payload = inventory_reorder_service.get_latest_summary(show_all=show_all)
    except (OSError, ValueError) as exc:
        logger.warning("Failed to read inventory reorder summary: %s", exc)
        return jsonify({"error": "Failed to read inventory reorder summary."}), 500

    return jsonify(payload)


@bp.route("/inventory-reorder/refresh", methods=["POST"])
@require_admin
def refresh_inventory_reorder_data():
    cooldown = inventory_reorder_service.get_refresh_cooldown()
    if cooldown["active"]:
        return jsonify(
            {
                "error": "Inventory refresh is cooling down. Please wait before starting another refresh.",
                "cooldown": cooldown,
            }
        ), 409

    job, created = inventory_reorder_service.start_refresh()
    status_code = 202 if created else 200
    return jsonify(
        {
            "job": job,
            "created": created,
            "cooldown": inventory_reorder_service.get_refresh_cooldown(),
        }
    ), status_code


@bp.route("/inventory-reorder/jobs/<job_id>", methods=["GET"])
@require_admin
def get_inventory_reorder_job(job_id: str):
    job = inventory_reorder_service.get_job(job_id)
    if not job:
        return jsonify({"error": "Inventory reorder refresh job not found."}), 404
    return jsonify({"job": job})


@bp.route("/inventory-reorder/download", methods=["GET"])
@require_admin
def download_inventory_reorder_summary():
    summary_path = inventory_reorder_service.latest_summary_path()
    if not summary_path:
        return jsonify({"error": "No inventory reorder summary is available."}), 404

    return send_file(
        summary_path,
        as_attachment=True,
        download_name=summary_path.name,
        mimetype="application/json",
    )


# ============ Testing Endpoints ============


@bp.route("/test/email", methods=["POST"])
@require_admin
def test_email_notification():
    """Send a test email to verify email configuration."""
    from app.services.email_service import email_service

    data = request.get_json() or {}
    to_address = data.get("to_address")

    if not to_address:
        return jsonify({"error": "Missing 'to_address' in request body"}), 400

    if not email_service.is_configured():
        missing = []
        if not settings.azure_tenant_id:
            missing.append("AZURE_TENANT_ID")
        if not settings.azure_client_id:
            missing.append("AZURE_CLIENT_ID")
        if not settings.azure_client_secret:
            missing.append("AZURE_CLIENT_SECRET")
        if not settings.smtp_from_address:
            missing.append("SMTP_FROM_ADDRESS")

        return jsonify(
            {
                "success": False,
                "error": f"Email not configured. Missing environment variables: {', '.join(missing)}",
            }
        ), 400

    # Send test email (force=True to bypass enabled check)
    subject = "TechHub DNS - Test Email"
    body_html = """
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h2 style="color: #500000;">Test Email from TechHub</h2>
        <p>This is a test email to verify your email configuration is working correctly.</p>
        <p>If you received this, your SMTP settings are properly configured!</p>
        <hr>
        <p style="font-size: 12px; color: #666;">TechHub Delivery Notification System</p>
    </body>
    </html>
    """
    body_text = "Test Email from TechHub\n\nThis is a test email to verify your email configuration is working correctly."

    success = email_service.send_email(
        to_address=to_address,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        force=True,
    )

    if success:
        return jsonify({"success": True, "message": f"Test email sent to {to_address}"})
    else:
        return jsonify(
            {"success": False, "error": "Failed to send email. Check server logs."}
        ), 500


@bp.route("/test/teams-recipient", methods=["POST"])
@require_admin
def test_teams_recipient():
    """Queue a test Teams notification to a recipient via Graph API."""
    from app.services.teams_recipient_service import teams_recipient_service

    data = request.get_json() or {}
    recipient_email = data.get("recipient_email")
    recipient_name = data.get("recipient_name", "Test User")

    if not recipient_email:
        return jsonify({"error": "Missing 'recipient_email' in request body"}), 400

    if not teams_recipient_service.is_configured():
        # Even if not configured, we might want to try forced send if enabled in settings?
        # Actually is_configured checks settings. Let's send a warning if disabled.
        pass

    try:
        # Send test notification
        success = teams_recipient_service.send_delivery_notification(
            recipient_email=recipient_email,
            recipient_name=recipient_name,
            order_number="TEST-123",
            delivery_runner="System Administrator",
            order_items=["Test Item 1", "Test Item 2"],
            force=True,  # Force send even if disabled in settings
        )

        if success:
            return jsonify(
                {
                    "success": True,
                    "message": f"Notification queued for {recipient_email}",
                }
            )
        else:
            return jsonify(
                {"success": False, "error": "Failed to send Teams message. Check logs."}
            ), 500

    except Exception as e:
        logger.error(f"Teams recipient test failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/test/teams-inventory-reorder", methods=["POST"])
@require_admin
def test_inventory_reorder_teams_notification():
    """Queue the same Teams alert sent for a new 10+ BC order."""
    from app.services.teams_recipient_service import teams_recipient_service

    data = request.get_json() or {}
    recipient_email = data.get("recipient_email")
    recipient_name = data.get("recipient_name", "Test User")

    if not recipient_email:
        return jsonify({"error": "Missing 'recipient_email' in request body"}), 400

    try:
        success = teams_recipient_service.send_inventory_reorder_notification(
            recipient_email=recipient_email,
            recipient_name=recipient_name,
            bigcommerce_order_id="TEST-10PLUS",
            order_items=["6 x Test Product A", "4 x Test Product B"],
            total_quantity=10,
        )
        if success:
            return jsonify(
                {
                    "success": True,
                    "message": f"10+ BC order test alert queued for {recipient_email}",
                }
            )
        return jsonify(
            {"success": False, "error": "Failed to queue 10+ BC order test alert. Check server logs."}
        ), 500
    except Exception as e:
        logger.error("Inventory reorder Teams test failed: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/test/inflow", methods=["POST"])
@require_admin
def test_inflow_connection():
    """Test connection to Inflow API."""
    service = InflowService()

    try:
        # Try to fetch a small number of orders to verify connection
        orders = service.sync_recent_started_orders_sync(max_pages=1, target_matches=1)
        return jsonify(
            {
                "success": True,
                "message": f"Inflow API connected. Found {len(orders)} order(s) in sample query.",
            }
        )
    except Exception as e:
        logger.error(f"Inflow connection test failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/test/sharepoint", methods=["POST"])
@require_admin
def test_sharepoint_connection():
    """Test connection to SharePoint."""
    from app.services.sharepoint_service import get_sharepoint_service

    try:
        sp_service = get_sharepoint_service()

        if not sp_service.is_enabled:
            return jsonify(
                {
                    "success": False,
                    "error": "SharePoint not enabled. Check SHAREPOINT_ENABLED and Azure configuration.",
                }
            ), 400

        # Test authentication and site access
        sp_service._get_access_token()

        return jsonify(
            {
                "success": True,
                "message": f"SharePoint connected. Site: {settings.sharepoint_site_url}",
            }
        )
    except Exception as e:
        logger.error(f"SharePoint connection test failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route("/status", methods=["GET"])
@require_admin
def get_system_status():
    """
    Get status of all backend features.

    Returns configuration and health status for each feature.
    """
    status = {
        "saml_auth": _get_saml_status(),
        "graph_api": _get_graph_status(),
        "sharepoint": _get_sharepoint_status(),
        "inflow_sync": _get_inflow_sync_status(),
    }

    return jsonify(status)


@bp.route("/sync-health", methods=["GET"])
def get_sync_health():
    """Get webhook health signals safe for non-admin users."""

    now = datetime.now(timezone.utc)
    stale_threshold_minutes = 120
    inflow = {
        "webhook_enabled": bool(settings.inflow_webhook_enabled),
        "webhook_failed": False,
        "last_webhook_received_at": None,
        "webhook_last_received_age_minutes": None,
        "webhook_registered": False,
        "webhook_matches_config": False,
        "webhook_secret_matches_config": False,
        "webhook_stale": False,
        "webhook_stale_reason": None,
    }

    if settings.inflow_webhook_enabled:
        db = get_db_session()
        try:
            webhook = (
                db.query(InflowWebhook)
                .filter(
                    InflowWebhook.status.in_(
                        [WebhookStatus.active, WebhookStatus.failed]
                    )
                )
                .order_by(InflowWebhook.updated_at.desc())
                .first()
            )

            inflow["webhook_failed"] = bool(
                webhook and webhook.status == WebhookStatus.failed
            )
            inflow["webhook_registered"] = bool(webhook)
            if webhook:
                inflow["last_webhook_received_at"] = _to_utc_iso_z(
                    getattr(webhook, "last_received_at", None)
                )
                if webhook.last_received_at:
                    age_minutes = int(
                        (now - webhook.last_received_at.replace(tzinfo=timezone.utc))
                        .total_seconds()
                        // 60
                    )
                    inflow["webhook_last_received_age_minutes"] = age_minutes
                    if age_minutes >= stale_threshold_minutes:
                        inflow["webhook_stale"] = True
                        inflow["webhook_stale_reason"] = "stale_receipts"
                else:
                    inflow["webhook_stale"] = True
                    inflow["webhook_stale_reason"] = "never_received"

                if settings.inflow_webhook_url:
                    inflow["webhook_matches_config"] = (
                        webhook.url.strip().rstrip("/")
                        == settings.inflow_webhook_url.strip().rstrip("/")
                    )
                    if not inflow["webhook_matches_config"]:
                        inflow["webhook_stale"] = True
                        inflow["webhook_stale_reason"] = "url_mismatch"

                if settings.inflow_webhook_secret and webhook.secret:
                    inflow["webhook_secret_matches_config"] = (
                        webhook.secret == settings.inflow_webhook_secret
                    )
                    if not inflow["webhook_secret_matches_config"]:
                        inflow["webhook_stale"] = True
                        inflow["webhook_stale_reason"] = "secret_mismatch"
                elif settings.inflow_webhook_secret and not webhook.secret:
                    inflow["webhook_stale"] = True
                    inflow["webhook_stale_reason"] = "missing_secret"
        finally:
            db.close()

    return jsonify(
        {
            "server_time": now.isoformat().replace("+00:00", "Z"),
            "inflow": inflow,
        }
    )


@bp.route("/sync", methods=["POST"])
@require_admin
def sync_orders():
    """
    Manually trigger order sync from Inflow.
    """
    service = InflowService()

    # Sync recent started orders
    # We use sync version because this is a blocking HTTP request
    from app.database import get_db_session

    db = get_db_session()

    try:
        # First fetch orders from Inflow
        orders = service.sync_recent_started_orders_sync(max_pages=3, target_matches=50)

        # Then create/update them in local DB
        from app.services.order_service import OrderService

        order_service = OrderService(db)

        synced_count = 0
        for order_data in orders:
            try:
                order_service.create_order_from_inflow(order_data)
                synced_count += 1
            except Exception as e:
                # Log but continue
                import logging

                logging.getLogger(__name__).error(
                    f"Failed to sync order {order_data.get('orderNumber')}: {e}"
                )

        return jsonify(
            {
                "success": True,
                "message": f"Synced {synced_count} orders from Inflow",
                "count": synced_count,
            }
        )
    finally:
        db.close()


@bp.route("/canopyorders/upload", methods=["POST"])
@require_auth
def upload_canopy_orders():
    data = request.get_json(silent=True) or {}
    orders_payload = data.get("orders")

    if not isinstance(orders_payload, list):
        return jsonify({"error": "Missing 'orders' list in request body"}), 400

    normalized_orders: list[str] = []
    seen_orders: set[str] = set()

    for raw_order in orders_payload:
        if not isinstance(raw_order, str):
            return jsonify({"error": "Each order must be a string"}), 400

        compact = "".join(raw_order.strip().upper().split())
        if not compact:
            return jsonify({"error": "Order number cannot be empty"}), 400

        normalized = _normalize_canopyorders_order_value(raw_order)
        if normalized is None:
            return jsonify(
                {
                    "error": "Order number must be TH + digits and may include partial suffixes like -P, -P2, or -R"
                }
            ), 400
        if normalized in seen_orders:
            continue

        seen_orders.add(normalized)
        normalized_orders.append(normalized)

    if not normalized_orders:
        return jsonify({"error": "No orders provided"}), 400

    db = get_db_session()
    try:
        order_service = OrderService(db)
        db_orders = (
            db.query(Order).filter(Order.inflow_order_id.in_(normalized_orders)).all()
        )
        orders_by_inflow_id: dict[str, Order] = {
            cast(str, order.inflow_order_id): order for order in db_orders
        }

        eligible_orders: list[str] = []
        ineligible_orders: list[dict[str, str]] = []
        missing_orders: list[str] = []

        for th in normalized_orders:
            order = orders_by_inflow_id.get(th)
            if not order:
                missing_orders.append(th)
                continue

            status_value = (getattr(order, "status", None) or "").strip()
            if status_value != "picked":
                ineligible_orders.append(
                    {"order": th, "reason": f"status={status_value or 'unknown'}"}
                )
                continue

            if getattr(order, "tagged_at", None) is not None:
                ineligible_orders.append({"order": th, "reason": "already tagged"})
                continue

            inflow_data = getattr(order, "inflow_data", None)
            if not inflow_data or not order_service._requires_asset_tags(order):
                ineligible_orders.append(
                    {"order": th, "reason": "not asset-tag required"}
                )
                continue

            eligible_orders.append(th)

        if missing_orders or ineligible_orders:
            return (
                jsonify(
                    {
                        "error": "One or more orders are not eligible for upload.",
                        "eligible_orders": eligible_orders,
                        "ineligible_orders": ineligible_orders,
                        "missing_orders": missing_orders,
                    }
                ),
                400,
            )
    finally:
        db.close()

    canopy_orders = list(
        dict.fromkeys(
            _base_canopyorders_order(order_id) for order_id in eligible_orders
        )
    )

    uploader = CanopyOrdersUploaderService()
    result = uploader.upload_orders(canopy_orders)

    if not result.get("success"):
        response_body = {
            "success": False,
            "filename": result.get("filename"),
            "uploaded_url": result.get("uploaded_url"),
            "count": len(eligible_orders),
            "teams_notified": False,
            "error": result.get("error"),
            "error_type": result.get("error_type"),
            "status_code": result.get("status_code"),
        }
        return jsonify(response_body), 502

    uploaded_url = result.get("uploaded_url")
    teams_notified = False
    if uploaded_url:
        teams_notified = uploader.send_teams_notification(
            canopy_orders, uploaded_url
        )

    updated_orders = 0
    db = get_db_session()
    try:
        sent_by = get_current_user_email() or "system"
        sent_at = _to_utc_iso_z(datetime.utcnow())
        request_metadata = {
            "canopyorders_request_sent_at": sent_at,
            "canopyorders_request_filename": result.get("filename"),
            "canopyorders_request_uploaded_url": uploaded_url,
            "canopyorders_request_sent_by": sent_by,
        }

        missing_orders: list[str] = []
        for th in eligible_orders:
            order = db.query(Order).filter(Order.inflow_order_id == th).first()
            if not order:
                missing_orders.append(th)
                continue

            tag_data = dict(
                cast(dict[str, Any], getattr(order, "tag_data", None) or {})
            )
            for key, value in request_metadata.items():
                tag_data[key] = value

            setattr(order, "tag_data", tag_data)
            updated_orders += 1

        if updated_orders:
            db.commit()
    finally:
        db.close()

    return jsonify(
        {
            "success": True,
            "filename": result.get("filename"),
            "uploaded_url": uploaded_url,
            "count": len(eligible_orders),
            "teams_notified": teams_notified,
            "updated_orders": updated_orders,
            "missing_orders": missing_orders,
            "eligible_orders": eligible_orders,
            "ineligible_orders": [],
        }
    )


def _normalize_canopyorders_bypass_value(raw_value: str) -> str:
    normalized = _normalize_canopyorders_order_value(raw_value)
    return normalized if normalized is not None else raw_value.strip()


def _is_exact_th_order(value: str) -> bool:
    # Accept TH followed by any number of digits (e.g., TH1, TH123, TH12345)
    normalized = _normalize_canopyorders_order_value(value)
    return bool(normalized) and normalized == _base_canopyorders_order(normalized)


@bp.route("/canopyorders/upload-bypass", methods=["POST"])
@require_admin
def upload_canopy_orders_bypass():
    data = request.get_json(silent=True) or {}
    orders_payload = data.get("orders")

    if not isinstance(orders_payload, list):
        return jsonify({"error": "Missing 'orders' list in request body"}), 400

    normalized_orders: list[str] = []
    seen_orders: set[str] = set()

    for raw_order in orders_payload:
        if not isinstance(raw_order, str):
            return jsonify({"error": "Each order must be a string"}), 400

        if not raw_order.strip():
            return jsonify({"error": "Order number cannot be empty"}), 400

        normalized = _normalize_canopyorders_bypass_value(raw_order)
        if normalized in seen_orders:
            continue

        seen_orders.add(normalized)
        normalized_orders.append(normalized)

    if not normalized_orders:
        return jsonify({"error": "No orders provided"}), 400

    canopy_orders = list(
        dict.fromkeys(
            (
                _base_canopyorders_order(order_id)
                if _normalize_canopyorders_order_value(order_id)
                else order_id
            )
            for order_id in normalized_orders
        )
    )

    uploader = CanopyOrdersUploaderService()
    result = uploader.upload_orders(canopy_orders)

    if not result.get("success"):
        response_body = {
            "success": False,
            "filename": result.get("filename"),
            "uploaded_url": result.get("uploaded_url"),
            "count": len(normalized_orders),
            "teams_notified": False,
            "updated_orders": 0,
            "missing_orders": [],
            "error": result.get("error"),
            "error_type": result.get("error_type"),
            "status_code": result.get("status_code"),
        }
        return jsonify(response_body), 502

    uploaded_url = result.get("uploaded_url")
    teams_notified = False
    if uploaded_url:
        teams_notified = uploader.send_teams_notification(
            canopy_orders, uploaded_url
        )

    updated_orders = 0
    missing_orders: list[str] = []
    tracked_orders = normalized_orders

    if tracked_orders:
        db = get_db_session()
        try:
            sent_by = get_current_user_email() or "system"
            sent_at = _to_utc_iso_z(datetime.utcnow())
            request_metadata = {
                "canopyorders_request_sent_at": sent_at,
                "canopyorders_request_filename": result.get("filename"),
                "canopyorders_request_uploaded_url": uploaded_url,
                "canopyorders_request_sent_by": sent_by,
            }

            for order_id in tracked_orders:
                order = (
                    db.query(Order).filter(Order.inflow_order_id == order_id).first()
                )
                if not order:
                    missing_orders.append(order_id)
                    continue

                tag_data = dict(
                    cast(dict[str, Any], getattr(order, "tag_data", None) or {})
                )
                for key, value in request_metadata.items():
                    tag_data[key] = value
                setattr(order, "tag_data", tag_data)
                updated_orders += 1

            if updated_orders:
                try:
                    db.commit()
                except Exception:
                    logger.exception(
                        "Failed to persist CanopyOrders bypass request metadata"
                    )
                    db.rollback()
                    updated_orders = 0
        finally:
            db.close()

    return jsonify(
        {
            "success": True,
            "filename": result.get("filename"),
            "uploaded_url": uploaded_url,
            "count": len(normalized_orders),
            "teams_notified": teams_notified,
            "updated_orders": updated_orders,
            "missing_orders": missing_orders,
        }
    )


@bp.route("/deploy", methods=["POST"])
def deploy_webhook():
    """
    Deprecated.

    This endpoint previously handled GitHub webhook-based auto-deploy.
    Auto-deploy now runs via GitHub Actions (SSH) and this route is intentionally disabled.
    """
    logger.warning("Deprecated deploy endpoint hit from %s", request.remote_addr)
    return (
        jsonify(
            {
                "error": "Deploy webhook removed",
                "message": "Automated deploy now runs via GitHub Actions. See docs/setup/deployment.md.",
            }
        ),
        410,
    )


def _get_saml_status():
    """Get SAML authentication status."""
    enabled = settings.saml_enabled
    configured = saml_auth_service.is_configured()

    if not enabled:
        return {
            "name": "TAMU SSO",
            "enabled": False,
            "configured": False,
            "status": "disabled",
            "details": "SAML authentication disabled",
        }

    if not configured:
        return {
            "name": "TAMU SSO",
            "enabled": True,
            "configured": False,
            "status": "warning",
            "details": "SAML enabled but missing configuration",
        }

    return {
        "name": "TAMU SSO",
        "enabled": True,
        "configured": True,
        "status": "active",
        "details": f"Entity: {settings.saml_sp_entity_id}",
    }


def _get_graph_status():
    """Get Microsoft Graph API status with actual connection test."""
    import logging

    logger = logging.getLogger(__name__)

    configured = graph_service.is_configured()

    if not configured:
        return {
            "name": "Microsoft Graph",
            "enabled": False,
            "configured": False,
            "status": "disabled",
            "details": "Service Principal not configured (AZURE_* env vars)",
        }

    # Try to actually test the authentication
    try:
        # Test getting an access token
        token = graph_service._get_access_token()
        if token:
            return {
                "name": "Microsoft Graph",
                "enabled": True,
                "configured": True,
                "status": "active",
                "details": "Service Principal authenticated",
            }
    except Exception as e:
        error_str = str(e)
        logger.error(f"Graph API status check failed: {error_str}")

        # Parse common Azure AD errors
        if "AADSTS" in error_str:
            if "AADSTS7000215" in error_str:
                return {
                    "name": "Microsoft Graph",
                    "enabled": True,
                    "configured": True,
                    "status": "error",
                    "details": "Invalid client secret",
                    "error": "The client secret is invalid or expired",
                }
            elif "AADSTS700016" in error_str:
                return {
                    "name": "Microsoft Graph",
                    "enabled": True,
                    "configured": True,
                    "status": "error",
                    "details": "App not found in tenant",
                    "error": "Application ID not found in the directory",
                }
            elif "AADSTS65001" in error_str:
                return {
                    "name": "Microsoft Graph",
                    "enabled": True,
                    "configured": True,
                    "status": "warning",
                    "details": "Pending admin consent",
                    "error": "Admin consent required for API permissions",
                }
            elif "AADSTS70011" in error_str:
                return {
                    "name": "Microsoft Graph",
                    "enabled": True,
                    "configured": True,
                    "status": "error",
                    "details": "Invalid scope",
                    "error": "The requested scope is invalid or not configured",
                }
            else:
                return {
                    "name": "Microsoft Graph",
                    "enabled": True,
                    "configured": True,
                    "status": "error",
                    "details": "Azure AD error",
                    "error": error_str[:100],
                }
        else:
            return {
                "name": "Microsoft Graph",
                "enabled": True,
                "configured": True,
                "status": "error",
                "details": "Authentication failed",
                "error": error_str[:100],
            }

    # Shouldn't reach here, but fallback
    return {
        "name": "Microsoft Graph",
        "enabled": True,
        "configured": True,
        "status": "warning",
        "details": "Status unknown",
    }


def _get_sharepoint_status():
    """Get SharePoint storage status with actual connection test."""
    from app.services.sharepoint_service import get_sharepoint_service
    import logging

    logger = logging.getLogger(__name__)

    # Check basic configuration
    graph_configured = graph_service.is_configured()
    site_configured = bool(settings.sharepoint_site_url)

    if not graph_configured:
        return {
            "name": "SharePoint Storage",
            "enabled": False,
            "configured": False,
            "status": "disabled",
            "details": "Requires Azure Service Principal (AZURE_* env vars)",
        }

    if not site_configured:
        return {
            "name": "SharePoint Storage",
            "enabled": True,
            "configured": False,
            "status": "warning",
            "details": "Service Principal ready, site URL not set",
        }

    # Try to actually test the connection
    try:
        sp_service = get_sharepoint_service()

        # Check if we've already successfully authenticated
        if sp_service._site_id:
            return {
                "name": "SharePoint Storage",
                "enabled": True,
                "configured": True,
                "status": "active",
                "details": f"Connected to {settings.sharepoint_site_url}",
            }

        # Try to get an access token (tests MSAL auth without making Graph calls)
        try:
            sp_service._get_access_token()
            return {
                "name": "SharePoint Storage",
                "enabled": True,
                "configured": True,
                "status": "active",
                "details": f"Authenticated, site: {settings.sharepoint_site_url}",
            }
        except Exception as auth_error:
            error_str = str(auth_error)
            # Check for common permission issues
            if "AADSTS" in error_str:
                if "AADSTS7000215" in error_str:
                    return {
                        "name": "SharePoint Storage",
                        "enabled": True,
                        "configured": True,
                        "status": "error",
                        "details": "Invalid client secret",
                        "error": "The client secret is invalid or expired",
                    }
                elif "AADSTS700016" in error_str:
                    return {
                        "name": "SharePoint Storage",
                        "enabled": True,
                        "configured": True,
                        "status": "error",
                        "details": "App not found in tenant",
                        "error": "Application ID not found in the directory",
                    }
                elif "AADSTS65001" in error_str:
                    return {
                        "name": "SharePoint Storage",
                        "enabled": True,
                        "configured": True,
                        "status": "warning",
                        "details": "Pending admin consent",
                        "error": "Admin consent required for API permissions",
                    }
                else:
                    return {
                        "name": "SharePoint Storage",
                        "enabled": True,
                        "configured": True,
                        "status": "error",
                        "details": "Azure AD error",
                        "error": error_str[:100],
                    }
            else:
                return {
                    "name": "SharePoint Storage",
                    "enabled": True,
                    "configured": True,
                    "status": "error",
                    "details": "Authentication failed",
                    "error": error_str[:100],
                }

    except Exception as e:
        logger.error(f"SharePoint status check failed: {e}")
        return {
            "name": "SharePoint Storage",
            "enabled": True,
            "configured": True,
            "status": "error",
            "details": "Connection test failed",
            "error": str(e)[:100],
        }


def _get_inflow_sync_status():
    """Get Inflow polling sync status."""
    enabled = settings.inflow_polling_sync_enabled
    api_key = bool(settings.inflow_api_key)

    if not api_key:
        return {
            "name": "Inflow Sync",
            "enabled": False,
            "configured": False,
            "status": "disabled",
            "details": "Inflow API key not configured",
        }

    if not enabled:
        return {
            "name": "Inflow Sync",
            "enabled": False,
            "configured": True,
            "status": "disabled",
            "details": "Polling sync disabled (using webhooks only)",
        }

    interval = 5
    details = f"Polling every {interval} minutes"

    db = get_db_session()
    try:
        has_active_webhook = bool(
            db.query(InflowWebhook)
            .filter(InflowWebhook.status == WebhookStatus.active)
            .first()
        )
    finally:
        db.close()

    if has_active_webhook and settings.inflow_webhook_enabled:
        interval = 30
        details = f"Polling every {interval} minutes (webhook backup mode)"

    return {
        "name": "Inflow Sync",
        "enabled": True,
        "configured": True,
        "status": "active",
        "details": details,
    }
