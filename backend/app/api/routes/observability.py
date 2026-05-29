import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from flask import Blueprint, g, jsonify, request
from sqlalchemy import and_, func, inspect, or_, select, union_all
from sqlalchemy.orm import Session

from app.api.auth_middleware import get_rate_limit_snapshot, require_admin
from app.config import settings
from app.database import engine, get_db, get_runtime_db_pool_settings
from app.models.audit_log import AuditLog, SystemAuditLog, SystemAuditLogArchive
from app.models.delivery_run import DeliveryRun
from app.models.inflow_webhook import InflowWebhook, WebhookStatus
from app.models.order import Order
from app.models.session import Session as UserSession
from app.models.user import User


bp = Blueprint("observability", __name__)
logger = logging.getLogger(__name__)


_SENSITIVE_COLUMN_TOKENS = (
    "token",
    "secret",
    "password",
    "assertion",
    "cookie",
)


def _to_iso_z(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        value = value.replace(tzinfo=timezone.utc)
    value_utc = value.astimezone(timezone.utc)
    return value_utc.isoformat().replace("+00:00", "Z")


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def _parse_int(value: Optional[str], default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_since(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    raw = value.strip()
    # Epoch seconds or milliseconds
    if raw.isdigit():
        num = int(raw)
        # If it's too large, assume ms
        if num > 1_000_000_000_000:
            num = num // 1000
        try:
            return datetime.fromtimestamp(num, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    # ISO timestamp
    try:
        normalized = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _is_sensitive_column_name(name: str) -> bool:
    lowered = (name or "").strip().lower()
    if not lowered:
        return False
    return any(token in lowered for token in _SENSITIVE_COLUMN_TOKENS)


def _truncate_json(
    value: Any, *, max_depth: int = 4, max_items: int = 50, max_string_len: int = 500
) -> Any:
    if max_depth <= 0:
        return "<truncated>"

    if value is None:
        return None

    if isinstance(value, str):
        if len(value) <= max_string_len:
            return value
        return value[: max_string_len - 12] + "...<truncated>"

    if isinstance(value, (int, float, bool)):
        return value

    if isinstance(value, list):
        items = value[:max_items]
        return [
            _truncate_json(
                v,
                max_depth=max_depth - 1,
                max_items=max_items,
                max_string_len=max_string_len,
            )
            for v in items
        ]

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for idx, (k, v) in enumerate(value.items()):
            if idx >= max_items:
                out["<truncated>"] = f"{len(value) - max_items} more item(s)"
                break
            key = str(k)
            out[key] = _truncate_json(
                v,
                max_depth=max_depth - 1,
                max_items=max_items,
                max_string_len=max_string_len,
            )
        return out

    # Fallback: ensure JSON-serializable
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _truncate_string(value: Optional[str], *, max_len: int) -> Optional[str]:
    if value is None:
        return None
    if len(value) <= max_len:
        return value
    return value[: max_len - 12] + "...<truncated>"


def _parse_frontend_error_payload(raw_payload: Any) -> dict[str, Any]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    context = payload.get("context")
    context_dict = context if isinstance(context, dict) else {}

    return {
        "type": _truncate_string(
            str(payload.get("type") or "react_error_boundary"), max_len=100
        ),
        "name": _truncate_string(str(payload.get("name") or "Error"), max_len=200),
        "message": _truncate_string(str(payload.get("message") or ""), max_len=2000),
        "stack": _truncate_string(
            payload.get("stack") if isinstance(payload.get("stack"), str) else None,
            max_len=8000,
        ),
        "component_stack": _truncate_string(
            payload.get("component_stack")
            if isinstance(payload.get("component_stack"), str)
            else None,
            max_len=8000,
        ),
        "context": _truncate_json(
            context_dict, max_depth=3, max_items=20, max_string_len=500
        ),
        "url": _truncate_string(
            payload.get("url") if isinstance(payload.get("url"), str) else None,
            max_len=1000,
        ),
        "user_agent": _truncate_string(
            payload.get("user_agent")
            if isinstance(payload.get("user_agent"), str)
            else request.headers.get("User-Agent"),
            max_len=1000,
        ),
    }


def _encode_cursor(ts: datetime, row_id: str) -> str:
    payload = f"{_to_iso_z(ts) or ''}|{row_id}"
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> Optional[tuple[datetime, str]]:
    if not cursor:
        return None
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        ts_str, row_id = decoded.split("|", 1)
        ts = _parse_since(ts_str)
        if ts is None or not row_id:
            return None
        return ts, row_id
    except Exception:
        return None


def _row_get(row: Any, key: str) -> Any:
    """Get a value from either an ORM row or a RowMapping."""
    if row is None:
        return None
    if hasattr(row, "get"):
        try:
            return row.get(key)
        except Exception:
            return None
    return getattr(row, key, None)


def _system_audit_select(model):
    return select(
        model.id.label("id"),
        model.timestamp.label("timestamp"),
        model.entity_type.label("entity_type"),
        model.entity_id.label("entity_id"),
        model.action.label("action"),
        model.description.label("description"),
        model.user_id.label("user_id"),
        model.user_role.label("user_role"),
        model.ip_address.label("ip_address"),
        model.user_agent.label("user_agent"),
        model.old_value.label("old_value"),
        model.new_value.label("new_value"),
    )


@bp.route("/frontend-error", methods=["POST"])
def ingest_frontend_error():
    """Accept authenticated frontend crash reports for server-side logging."""

    payload = _parse_frontend_error_payload(request.get_json(silent=True))
    logger.error(
        "Frontend error boundary crash: type=%s name=%s user=%s path=%s message=%s context=%s",
        payload["type"],
        payload["name"],
        getattr(g, "user_email", None) or getattr(g, "user_id", None) or "anonymous",
        payload["url"],
        payload["message"],
        payload["context"],
        extra={
            "frontend_error": {
                **payload,
                "reported_at": _to_iso_z(datetime.now(timezone.utc)),
            }
        },
    )
    return jsonify({"accepted": True}), 202


@bp.route("/table-stats", methods=["GET"])
@require_admin
def get_table_stats():
    """Return allowlisted table row counts and freshness timestamps."""

    def table_stat(
        db: Session, *, name: str, row_count_query, last_updated_query=None
    ) -> dict[str, Any]:
        row_count = int(row_count_query.scalar() or 0)
        last_updated_value = None
        if last_updated_query is not None:
            last_updated_value = last_updated_query.scalar()
        return {
            "table": name,
            "row_count": row_count,
            "last_updated": _to_iso_z(last_updated_value)
            if isinstance(last_updated_value, datetime)
            else None,
        }

    with get_db() as db:
        tables: list[dict[str, Any]] = []

        tables.append(
            table_stat(
                db,
                name="orders",
                row_count_query=db.query(func.count()).select_from(Order),
                last_updated_query=db.query(func.max(Order.updated_at)).select_from(
                    Order
                ),
            )
        )
        tables.append(
            table_stat(
                db,
                name="audit_logs",
                row_count_query=db.query(func.count()).select_from(AuditLog),
                last_updated_query=db.query(func.max(AuditLog.timestamp)).select_from(
                    AuditLog
                ),
            )
        )
        tables.append(
            table_stat(
                db,
                name="system_audit_logs",
                row_count_query=db.query(func.count()).select_from(SystemAuditLog),
                last_updated_query=db.query(
                    func.max(SystemAuditLog.timestamp)
                ).select_from(SystemAuditLog),
            )
        )
        tables.append(
            table_stat(
                db,
                name="system_audit_logs_archive",
                row_count_query=db.query(func.count()).select_from(
                    SystemAuditLogArchive
                ),
                last_updated_query=db.query(
                    func.max(SystemAuditLogArchive.timestamp)
                ).select_from(SystemAuditLogArchive),
            )
        )
        tables.append(
            table_stat(
                db,
                name="delivery_runs",
                row_count_query=db.query(func.count()).select_from(DeliveryRun),
                last_updated_query=db.query(
                    func.max(DeliveryRun.updated_at)
                ).select_from(DeliveryRun),
            )
        )
        tables.append(
            table_stat(
                db,
                name="users",
                row_count_query=db.query(func.count()).select_from(User),
                last_updated_query=db.query(func.max(User.last_login_at)).select_from(
                    User
                ),
            )
        )
        tables.append(
            table_stat(
                db,
                name="sessions",
                row_count_query=db.query(func.count()).select_from(UserSession),
                last_updated_query=db.query(
                    func.max(UserSession.last_seen_at)
                ).select_from(UserSession),
            )
        )

    return jsonify(
        {"generated_at": _to_iso_z(datetime.now(tz=timezone.utc)), "tables": tables}
    )


@bp.route("/schema-summary", methods=["GET"])
@require_admin
def get_schema_summary():
    """Return a curated, allowlisted schema model for DB visualization."""

    allowlisted_tables = [
        "users",
        "sessions",
        "delivery_runs",
        "orders",
        "audit_logs",
        "system_audit_logs",
        "system_audit_logs_archive",
    ]

    inspector = inspect(engine)
    relationships: list[dict[str, str]] = []
    tables_out: list[dict[str, Any]] = []

    for table_name in allowlisted_tables:
        try:
            columns = inspector.get_columns(table_name)
        except Exception:
            # Table missing in this environment; skip quietly.
            continue

        pk_columns = set(
            (inspector.get_pk_constraint(table_name) or {}).get("constrained_columns")
            or []
        )
        fk_columns: set[str] = set()
        foreign_keys = inspector.get_foreign_keys(table_name) or []
        for fk in foreign_keys:
            referred_table = fk.get("referred_table")
            referred_columns = fk.get("referred_columns") or []
            constrained_columns = fk.get("constrained_columns") or []

            if not referred_table or referred_table not in allowlisted_tables:
                continue

            for from_col, to_col in zip(constrained_columns, referred_columns):
                if not from_col or not to_col:
                    continue
                fk_columns.add(from_col)
                relationships.append(
                    {
                        "from_table": table_name,
                        "from_column": from_col,
                        "to_table": referred_table,
                        "to_column": to_col,
                    }
                )

        cols_out: list[dict[str, Any]] = []
        for col in columns:
            col_name = str(col.get("name") or "")
            if not col_name or _is_sensitive_column_name(col_name):
                continue

            col_type = col.get("type")
            cols_out.append(
                {
                    "name": col_name,
                    "type": str(col_type) if col_type is not None else "unknown",
                    "is_pk": col_name in pk_columns,
                    "is_fk": col_name in fk_columns,
                    "nullable": bool(col.get("nullable", True)),
                }
            )

        tables_out.append({"name": table_name, "columns": cols_out})

    # Deduplicate relationships
    seen_rel = set()
    rel_out: list[dict[str, str]] = []
    for rel in relationships:
        key = (rel["from_table"], rel["from_column"], rel["to_table"], rel["to_column"])
        if key in seen_rel:
            continue
        seen_rel.add(key)
        rel_out.append(rel)

    return jsonify({"tables": tables_out, "relationships": rel_out})


@bp.route("/system-audit", methods=["GET"])
@require_admin
def get_system_audit():
    """Paginated system audit log feed (admin-only)."""
    limit = _parse_int(request.args.get("limit"), 50)
    limit = max(1, min(limit, 200))

    entity_type = (request.args.get("entity_type") or "").strip()
    entity_id = (request.args.get("entity_id") or "").strip()
    action = (request.args.get("action") or "").strip()
    since = _parse_since(request.args.get("since"))
    cursor = (request.args.get("cursor") or "").strip()
    include_values = _parse_bool(request.args.get("include_values"), default=False)
    include_archive = _parse_bool(request.args.get("include_archive"), default=False)

    cursor_value = _decode_cursor(cursor) if cursor else None

    with get_db() as db:
        if not include_archive:
            query = db.query(SystemAuditLog)

            if entity_type:
                query = query.filter(SystemAuditLog.entity_type == entity_type)
            if entity_id:
                query = query.filter(SystemAuditLog.entity_id == entity_id)
            if action:
                query = query.filter(SystemAuditLog.action == action)
            if since is not None:
                query = query.filter(SystemAuditLog.timestamp >= since)

            if cursor_value is not None:
                cursor_ts, cursor_id = cursor_value
                query = query.filter(
                    or_(
                        SystemAuditLog.timestamp < cursor_ts,
                        and_(
                            SystemAuditLog.timestamp == cursor_ts,
                            SystemAuditLog.id < cursor_id,
                        ),
                    )
                )

            query = query.order_by(
                SystemAuditLog.timestamp.desc(), SystemAuditLog.id.desc()
            ).limit(limit + 1)
            rows_any: list[Any] = query.all()
        else:
            hot = _system_audit_select(SystemAuditLog)
            archived = _system_audit_select(SystemAuditLogArchive)
            combined = union_all(hot, archived).subquery("system_audit_union")

            stmt = select(combined)

            if entity_type:
                stmt = stmt.where(combined.c.entity_type == entity_type)
            if entity_id:
                stmt = stmt.where(combined.c.entity_id == entity_id)
            if action:
                stmt = stmt.where(combined.c.action == action)
            if since is not None:
                stmt = stmt.where(combined.c.timestamp >= since)

            if cursor_value is not None:
                cursor_ts, cursor_id = cursor_value
                stmt = stmt.where(
                    or_(
                        combined.c.timestamp < cursor_ts,
                        and_(
                            combined.c.timestamp == cursor_ts, combined.c.id < cursor_id
                        ),
                    )
                )

            stmt = stmt.order_by(
                combined.c.timestamp.desc(), combined.c.id.desc()
            ).limit(limit + 1)
            rows_any = [r._mapping for r in db.execute(stmt).all()]

        rows = rows_any

        # Batch lookup order entity_id -> inflow_order_id for display purposes.
        order_entity_ids = {
            str(_row_get(r, "entity_id") or "")
            for r in rows
            if str(_row_get(r, "entity_type") or "").strip().lower() == "order"
        }
        order_entity_ids = {oid for oid in order_entity_ids if oid}
        order_entity_ids_lower = sorted({oid.lower() for oid in order_entity_ids})

        order_number_by_id: dict[str, str] = {}
        if order_entity_ids:
            order_rows = (
                db.query(Order.id, Order.inflow_order_id)
                .filter(Order.id.in_(sorted(order_entity_ids)))
                .all()
            )
            for oid, order_number in order_rows:
                if oid and order_number:
                    order_number_by_id[str(oid).lower()] = str(order_number)

            # If we missed rows due to case-sensitive UUID comparison, retry with lower().
            if len(order_number_by_id) < len(order_entity_ids_lower):
                order_rows_fallback = (
                    db.query(Order.id, Order.inflow_order_id)
                    .filter(func.lower(Order.id).in_(order_entity_ids_lower))
                    .all()
                )
                for oid, order_number in order_rows_fallback:
                    if oid and order_number:
                        order_number_by_id.setdefault(
                            str(oid).lower(), str(order_number)
                        )

        next_cursor: Optional[str] = None
        if len(rows) > limit:
            last = rows[limit - 1]
            last_ts = _row_get(last, "timestamp")
            last_id = _row_get(last, "id")
            if isinstance(last_ts, datetime) and isinstance(last_id, str):
                next_cursor = _encode_cursor(last_ts, last_id)
            rows = rows[:limit]

        items: list[dict[str, Any]] = []
        for row in rows:
            row_ts = _row_get(row, "timestamp")
            row_description = _row_get(row, "description")
            row_user_agent = _row_get(row, "user_agent")
            item: dict[str, Any] = {
                "id": _row_get(row, "id"),
                "timestamp": _to_iso_z(
                    row_ts if isinstance(row_ts, datetime) else None
                ),
                "entity_type": _row_get(row, "entity_type"),
                "entity_id": _row_get(row, "entity_id"),
                "action": _row_get(row, "action"),
                "description": _truncate_string(
                    row_description if isinstance(row_description, str) else None,
                    max_len=2000,
                ),
                "user_id": (_row_get(row, "user_id") or None),
                "user_role": (_row_get(row, "user_role") or None),
                "ip": (_row_get(row, "ip_address") or None),
                "user_agent": _truncate_string(
                    row_user_agent if isinstance(row_user_agent, str) else None,
                    max_len=500,
                ),
            }

            if str(item.get("entity_type") or "").strip().lower() == "order":
                entity_id = str(item.get("entity_id") or "")
                item["order_number"] = order_number_by_id.get(entity_id.lower())

            # Optionally include state change payloads (bounded).
            if include_values:
                item["old_values"] = _truncate_json(_row_get(row, "old_value"))
                item["new_values"] = _truncate_json(_row_get(row, "new_value"))

            items.append(item)

    return jsonify({"items": items, "next_cursor": next_cursor})


@bp.route("/runtime-summary", methods=["GET"])
@require_admin
def get_runtime_summary():
    """Return lightweight live runtime diagnostics for admin troubleshooting."""
    generated_at = _to_iso_z(datetime.now(tz=timezone.utc))
    pool_settings = get_runtime_db_pool_settings()

    with get_db() as db:
        active_webhook = (
            db.query(InflowWebhook)
            .filter(InflowWebhook.status == WebhookStatus.active)
            .order_by(InflowWebhook.updated_at.desc())
            .first()
        )
        active_sessions = int(
            db.query(func.count())
            .select_from(UserSession)
            .filter(UserSession.expires_at > datetime.utcnow())
            .scalar()
            or 0
        )
        active_delivery_runs = int(
            db.query(func.count())
            .select_from(DeliveryRun)
            .filter(DeliveryRun.status == "active")
            .scalar()
            or 0
        )
        open_orders = int(
            db.query(func.count())
            .select_from(Order)
            .filter(Order.hidden_from_ops.is_(False))
            .filter(Order.status.notin_(["delivered", "cancelled"]))
            .scalar()
            or 0
        )

    effective_poll_interval_minutes = None
    if settings.inflow_polling_sync_enabled:
        effective_poll_interval_minutes = (
            30 if (active_webhook and settings.inflow_webhook_enabled) else 5
        )

    return jsonify(
        {
            "generated_at": generated_at,
            "app": {
                "scheduler_enabled": settings.scheduler_enabled,
                "inflow_polling_sync_enabled": settings.inflow_polling_sync_enabled,
                "inflow_webhook_enabled": settings.inflow_webhook_enabled,
                "cors_allowed_origins": settings.get_cors_allowed_origins(),
            },
            "database": pool_settings,
            "rate_limits": get_rate_limit_snapshot(),
            "workload": {
                "active_sessions": active_sessions,
                "active_delivery_runs": active_delivery_runs,
                "open_orders": open_orders,
            },
            "inflow": {
                "active_webhook": bool(active_webhook),
                "webhook_id": active_webhook.webhook_id if active_webhook else None,
                "last_webhook_received_at": _to_iso_z(
                    active_webhook.last_received_at if active_webhook else None
                ),
                "effective_poll_interval_minutes": effective_poll_interval_minutes,
            },
        }
    )
