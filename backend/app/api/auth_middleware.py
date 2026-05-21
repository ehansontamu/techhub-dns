"""
Authentication middleware.

Validates session cookies and attaches user to request context.
"""

import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta
from functools import wraps
from threading import Lock
from time import monotonic
import uuid
from flask import request, g, jsonify

from app.config import settings
from app.database import get_db
from app.schemas.error import ErrorResponse
from app.services.saml_auth_service import saml_auth_service
from app.services.system_setting_service import (
    SystemSettingService,
    SETTING_ADMIN_EMAILS,
)
from app.services.maintenance_tick_service import schedule_maintenance_tick_if_needed

logger = logging.getLogger(__name__)

_RATE_LIMIT_EVENTS: dict[str, deque[float]] = defaultdict(deque)
_RATE_LIMIT_LOCK = Lock()

# Routes that don't require authentication
PUBLIC_ROUTES = [
    "/auth/",  # Primary auth routes
    "/api/auth/",  # Compatibility alias for older deployments/bundles
    "/health",
    "/api/inflow/webhook",  # Inflow webhook callbacks
    "/api/system/print-agent/",  # Fixed desktop print agent token auth
]

STATIC_ROUTE_PREFIXES = (
    "/assets/",
    "/static/",
)

STATIC_ROUTE_EXACT = {
    "/favicon.ico",
    "/manifest.webmanifest",
    "/site.webmanifest",
    "/sw.js",
    "/robots.txt",
    "/apple-touch-icon.png",
}


def _is_static_asset_request(path: str) -> bool:
    if path.startswith("/api/"):
        return False
    if path in STATIC_ROUTE_EXACT:
        return True
    return path.startswith(STATIC_ROUTE_PREFIXES)


def _compact_rate_limit_queue(
    events: deque[float], now: float, window_seconds: int
) -> None:
    while events and now - events[0] > window_seconds:
        events.popleft()


def _get_rate_limit_rule(path: str, method: str) -> tuple[str, int] | None:
    normalized_method = (method or "GET").upper()
    if normalized_method == "GET" and (
        path.startswith("/api/observability/")
        or path in {"/api/system/status", "/api/system/sync-health"}
    ):
        return ("admin_reads", settings.admin_read_rate_limit_requests)

    if normalized_method in {"POST", "PUT", "PATCH", "DELETE"} and (
        path.startswith("/api/system/")
        or path
        in {
            "/api/inflow/sync",
            "/api/inflow/retry-webhook-registration",
        }
    ):
        return ("admin_writes", settings.admin_write_rate_limit_requests)

    return None


def _consume_rate_limit(
    bucket: str, scope_key: str, limit: int, window_seconds: int
) -> bool:
    if limit <= 0 or window_seconds <= 0:
        return True

    cache_key = f"{bucket}:{scope_key}"
    now = monotonic()
    with _RATE_LIMIT_LOCK:
        events = _RATE_LIMIT_EVENTS[cache_key]
        _compact_rate_limit_queue(events, now, window_seconds)
        if len(events) >= limit:
            return False
        events.append(now)
        return True


def is_dev_auth_bypass_enabled() -> bool:
    """Return True when local development auth bypass is explicitly enabled."""
    return settings.is_dev() and bool(getattr(settings, "dev_auth_bypass", False))


def _set_dev_auth_context() -> None:
    """Populate request context with a synthetic local dev user/session."""
    from app.models.session import Session
    from app.models.user import User

    now = datetime.utcnow()
    email = (getattr(settings, "dev_auth_email", "") or "dev.user@example.com").strip().lower()
    display_name = (
        (getattr(settings, "dev_auth_display_name", "") or "Local Dev User")
        .strip()
        or "Local Dev User"
    )
    department = (getattr(settings, "dev_auth_department", None) or "").strip() or None

    user = User(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"techhub-dns-dev-user:{email}")),
        tamu_oid=f"dev:{email}",
        email=email,
        display_name=display_name,
        department=department,
        created_at=now,
        last_login_at=now,
    )
    session = Session(
        id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"techhub-dns-dev-session:{email}")),
        user_id=user.id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_max_age_hours),
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.remote_addr,
    )

    g.user_id = user.id
    g.session_id = session.id
    g.user_email = user.email
    g.user_data = user.to_dict()
    g.session_data = session.to_dict()
    g.user = user
    g.session = session


def get_rate_limit_snapshot() -> dict:
    now = monotonic()
    window_seconds = settings.rate_limit_window_seconds
    bucket_totals: dict[str, int] = {}
    bucket_scopes: dict[str, int] = {}

    with _RATE_LIMIT_LOCK:
        for cache_key in list(_RATE_LIMIT_EVENTS.keys()):
            events = _RATE_LIMIT_EVENTS[cache_key]
            _compact_rate_limit_queue(events, now, window_seconds)
            if not events:
                _RATE_LIMIT_EVENTS.pop(cache_key, None)
                continue

            bucket, _scope = cache_key.split(":", 1)
            bucket_totals[bucket] = bucket_totals.get(bucket, 0) + len(events)
            bucket_scopes[bucket] = bucket_scopes.get(bucket, 0) + 1

    return {
        "window_seconds": window_seconds,
        "rules": {
            "admin_reads": settings.admin_read_rate_limit_requests,
            "admin_writes": settings.admin_write_rate_limit_requests,
        },
        "active_events": bucket_totals,
        "active_scopes": bucket_scopes,
    }


def init_auth_middleware(app):
    """
    Initialize authentication middleware for the Flask app.

    This runs before every request to validate session and attach auth context.
    Uses short-lived DB sessions to avoid request-wide connection retention.
    """

    @app.before_request
    def authenticate_request():
        """
        Validate session and attach lightweight auth context into request scope.
        """
        # Initialize defaults
        g.user_id = None
        g.session_id = None
        g.user_email = None
        g.user_data = None
        g.session_data = None

        path = request.path

        # Skip auth session checks for static assets served by Flask SPA host.
        if _is_static_asset_request(path):
            return None

        if is_dev_auth_bypass_enabled():
            _set_dev_auth_context()
            return None

        # Check if SAML is configured
        if not saml_auth_service.is_configured():
            return None

        # Get session ID from cookie
        session_id_cookie = request.cookies.get(settings.session_cookie_name)
        if not session_id_cookie:
            # No session cookie - check auth requirements below
            pass
        else:
            with get_db() as db:
                result = saml_auth_service.validate_session(db, session_id_cookie)
                if result:
                    user, session = result
                    g.user_id = str(user.id)
                    g.session_id = str(session.id)
                    g.user_email = str(user.email) if user.email else None
                    g.user_data = user.to_dict()
                    g.session_data = session.to_dict()
                else:
                    logger.debug(f"Invalid session: {session_id_cookie[:8]}...")

        # Check strict auth requirements (Authorization Phase)
        # We do this AFTER attempting to load the session so that public routes
        # (like /auth/me) can still access user info if logged in.
        if any(path.startswith(r) for r in PUBLIC_ROUTES):
            return None

        # Protected route without valid session
        if not g.user_id:
            if path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return None

        if path.startswith("/api/") and is_current_user_admin():
            rate_limit_rule = _get_rate_limit_rule(path, request.method)
            if rate_limit_rule is not None:
                bucket, limit = rate_limit_rule
                scope_key = (
                    getattr(g, "user_email", None) or request.remote_addr or "admin"
                )
                allowed = _consume_rate_limit(
                    bucket=bucket,
                    scope_key=str(scope_key),
                    limit=limit,
                    window_seconds=settings.rate_limit_window_seconds,
                )
                if not allowed:
                    logger.warning(
                        "Admin rate limit exceeded: bucket=%s path=%s method=%s scope=%s",
                        bucket,
                        path,
                        request.method,
                        scope_key,
                    )
                    response = ErrorResponse(
                        error={
                            "code": "RATE_LIMITED",
                            "message": "Too many admin requests. Please retry shortly.",
                            "details": {
                                "bucket": bucket,
                                "limit": limit,
                                "window_seconds": settings.rate_limit_window_seconds,
                            },
                        },
                        request_id=str(uuid.uuid4()),
                    )
                    return jsonify(response.model_dump()), 429

        # Schedule background maintenance tick ONLY for authenticated API traffic.
        if path.startswith("/api/") and getattr(g, "user_id", None):
            schedule_maintenance_tick_if_needed()

        return None


def require_auth(f):
    """
    Decorator to require authentication for a route.

    Use this on routes that absolutely require a logged-in user.
    Returns 401 if no valid session.

    Usage:
        @bp.route("/protected")
        @require_auth
        def protected_route():
            user_id = g.user_id  # Guaranteed to exist
            ...
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not getattr(g, "user_id", None):
            return jsonify({"error": "Authentication required"}), 401
        return f(*args, **kwargs)

    return decorated_function


def is_current_user_admin() -> bool:
    """Return True if current authenticated user is an admin.

    Rules:
    - If ADMIN_EMAILS is configured (non-empty), user email must be in allowlist.
    - If ADMIN_EMAILS is empty, allow any authenticated user ONLY in development.
    - If not authenticated, always False.
    """
    if is_dev_auth_bypass_enabled():
        return True

    if not getattr(g, "user_id", None):
        return False

    email = (
        (getattr(g, "user_email", None) or get_current_user_email() or "")
        .strip()
        .lower()
    )
    if not email:
        return False

    env_allowlist = settings.get_admin_emails()

    with get_db() as db:
        raw = SystemSettingService.get_setting(db, SETTING_ADMIN_EMAILS)
        db_allowlist = settings._parse_admin_emails(raw)

    # Merge env + DB (env entries are immutable; DB entries are app-managed).
    merged = set(env_allowlist or []) | set(db_allowlist or [])
    if merged:
        return email in merged

    # Default behavior when no allowlist is configured.
    if settings.is_dev():
        return True
    return False


def require_admin(f):
    """Decorator to require admin access for a route."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not getattr(g, "user_id", None):
            return jsonify({"error": "Authentication required"}), 401

        if not is_current_user_admin():
            return jsonify({"error": "Admin access required"}), 403

        return f(*args, **kwargs)

    return decorated_function


def get_current_user_email() -> str:
    """
    Get current user's email for audit logging.

    Returns "system" if no user is authenticated (e.g., background jobs).

    Prefers middleware-provided email and falls back to a short DB lookup.
    """
    from app.models.user import User

    user_email = getattr(g, "user_email", None)
    if user_email:
        return str(user_email)

    user_id = getattr(g, "user_id", None)
    if user_id:
        with get_db() as db:
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                email = getattr(user, "email", None)
                if email is None:
                    return "system"
                return str(email)
    return "system"


def get_current_user_display_name() -> str:
    """
    Get current user's display name for human-facing logs and UI labels.

    Returns the user's display name when available, otherwise falls back to a
    DB lookup for the stored display name. Does not fall back to email.
    """
    from app.models.user import User

    user_data = getattr(g, "user_data", None) or {}
    display_name = str(user_data.get("display_name") or "").strip()
    if display_name:
        return display_name

    user_id = getattr(g, "user_id", None)
    if user_id:
        with get_db() as db:
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                db_display_name = str(getattr(user, "display_name", "") or "").strip()
                if db_display_name:
                    return db_display_name

    return "system"
