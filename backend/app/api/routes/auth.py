"""
Authentication API routes.

Provides endpoints for interactive Microsoft Entra login, SAML fallback,
logout, and session management.
"""

import logging
import secrets
from datetime import datetime
from urllib.parse import urlparse
from flask import Blueprint, request, redirect, make_response, jsonify, g, session as flask_session
import msal

from app.config import settings
from app.database import get_db
from app.services.saml_auth_service import saml_auth_service
from app.api.auth_middleware import is_current_user_admin, is_dev_auth_bypass_enabled
from app.utils.exceptions import DNSApiError
from app.utils.csrf import csrf_protect

logger = logging.getLogger(__name__)

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

OIDC_SCOPES = ["openid", "profile"]
OIDC_SESSION_STATE_KEY = "oidc_login_state"
OIDC_SESSION_NONCE_KEY = "oidc_login_nonce"
OIDC_SESSION_NEXT_KEY = "oidc_login_next"
OIDC_SESSION_FLOW_KEY = "oidc_login_flow"


def _sanitize_relay_state(relay_state: str | None, request_host: str | None) -> str:
    """Return a safe internal redirect target after SAML auth.

    Keeps same-origin paths, strips absolute URLs back to their path when the
    host matches the current request, and prevents loops back to the login page.
    """
    raw_state = (relay_state or "").strip()
    if not raw_state or raw_state == "None":
        return "/"

    parsed = urlparse(raw_state)

    # Allow absolute URLs only when they stay on the current host and remain HTTPS.
    if parsed.scheme or parsed.netloc:
        current_host = (request_host or "").strip().lower()
        if parsed.scheme.lower() != "https" or not current_host or parsed.netloc.strip().lower() != current_host:
            return "/"
        raw_state = f"{parsed.path or '/'}{f'?{parsed.query}' if parsed.query else ''}{f'#{parsed.fragment}' if parsed.fragment else ''}"
        parsed = urlparse(raw_state)

    path = parsed.path or raw_state
    if not path.startswith("/"):
        return "/"

    if path == "/login" or path.startswith("/login?") or path.startswith("/login#"):
        return "/"

    return raw_state


def _oidc_is_configured() -> bool:
    return bool(
        settings.oidc_tenant_id
        and settings.oidc_client_id
        and settings.oidc_client_secret
    )


def _get_browser_origin() -> str:
    """Return the public-facing origin the browser should return to."""
    frontend_url = (settings.frontend_url or "").strip().rstrip("/")
    if frontend_url:
        return frontend_url

    return f"{request.scheme}://{request.host}"


def _build_oidc_redirect_uri() -> str:
    """Build the absolute redirect URI for the current login route."""
    if request.path.endswith("/login"):
        callback_path = f"{request.path[:-len('/login')]}/oidc/callback"
    elif request.path.endswith("/oidc/callback"):
        callback_path = request.path
    else:
        raise DNSApiError(
            code="OIDC_CONFIG_ERROR",
            message="OIDC login redirect URI could not be determined",
            status_code=500,
        )

    return f"{_get_browser_origin()}{callback_path}"


def _build_oidc_client() -> msal.ConfidentialClientApplication:
    if not _oidc_is_configured():
        raise DNSApiError(
            code="OIDC_CONFIG_ERROR",
            message="Microsoft Entra login is not fully configured",
            status_code=503,
            details={"reason": "missing_required_settings"},
        )

    authority = f"https://login.microsoftonline.com/{settings.oidc_tenant_id}"
    return msal.ConfidentialClientApplication(
        settings.oidc_client_id,
        authority=authority,
        client_credential=settings.oidc_client_secret,
    )


def _start_saml_login(relay_state: str) -> str:
    from onelogin.saml2.auth import OneLogin_Saml2_Auth

    req = _prepare_flask_request()
    auth = OneLogin_Saml2_Auth(req, saml_auth_service.get_saml_settings())
    redirect_url = auth.login(return_to=relay_state, force_authn=True)
    return redirect_url


def _start_oidc_login(relay_state: str):
    oidc_client = _build_oidc_client()
    state = secrets.token_urlsafe(32)
    redirect_uri = _build_oidc_redirect_uri()

    flask_session[OIDC_SESSION_STATE_KEY] = state
    flask_session[OIDC_SESSION_NEXT_KEY] = relay_state

    flow = oidc_client.initiate_auth_code_flow(
        scopes=[],
        state=state,
        redirect_uri=redirect_uri,
        prompt="select_account",
    )
    flask_session[OIDC_SESSION_FLOW_KEY] = flow

    return redirect(flow["auth_uri"])


def _oidc_login_debug_context() -> dict:
    """Return a small, non-sensitive snapshot to help debug initiation failures."""
    redirect_uri = None
    try:
        redirect_uri = _build_oidc_redirect_uri()
    except Exception:
        redirect_uri = None

    return {
        "tenant_configured": bool(settings.oidc_tenant_id),
        "client_configured": bool(settings.oidc_client_id),
        "secret_configured": bool(settings.oidc_client_secret),
        "frontend_url": settings.frontend_url,
        "request_host": request.host,
        "request_scheme": request.scheme,
        "redirect_uri": redirect_uri,
    }


def _clear_oidc_login_state() -> None:
    flask_session.pop(OIDC_SESSION_STATE_KEY, None)
    flask_session.pop(OIDC_SESSION_NEXT_KEY, None)
    flask_session.pop(OIDC_SESSION_FLOW_KEY, None)


def _complete_oidc_login() -> tuple[str, dict]:
    if request.args.get("error"):
        error = request.args.get("error")
        description = request.args.get("error_description")
        raise DNSApiError(
            code="OIDC_AUTH_ERROR",
            message="Authentication was cancelled or rejected",
            status_code=401,
            details={"error": error, "error_description": description},
        )

    code = (request.args.get("code") or "").strip()
    state = (request.args.get("state") or "").strip()
    expected_state = (flask_session.get(OIDC_SESSION_STATE_KEY) or "").strip()
    if not code or not state or state != expected_state:
        raise DNSApiError(
            code="OIDC_AUTH_ERROR",
            message="Invalid Microsoft Entra login state",
            status_code=401,
            details={"reason": "state_mismatch"},
        )

    relay_state = _sanitize_relay_state(
        flask_session.get(OIDC_SESSION_NEXT_KEY),
        request.host,
    )

    oidc_client = _build_oidc_client()
    redirect_uri = _build_oidc_redirect_uri()
    flow = flask_session.get(OIDC_SESSION_FLOW_KEY) or {
        "auth_uri": "",
        "state": expected_state,
        "redirect_uri": redirect_uri,
    }
    result = oidc_client.acquire_token_by_auth_code_flow(
        flow,
        {"code": code, "state": state},
        scopes=[],
    )

    if "error" in result:
        raise DNSApiError(
            code="OIDC_AUTH_ERROR",
            message="Microsoft Entra login failed",
            status_code=401,
            details={
                "error": result.get("error"),
                "error_description": result.get("error_description"),
            },
        )

    id_token_claims = result.get("id_token_claims") or {}

    _clear_oidc_login_state()
    return relay_state, id_token_claims


def _finalize_oidc_login(relay_state: str, oidc_claims: dict):
    with get_db() as db:
        user = saml_auth_service.get_or_create_user_from_oidc_claims(db, oidc_claims)
        session = saml_auth_service.create_session(
            db,
            user,
            user_agent=request.headers.get("User-Agent"),
            ip_address=request.remote_addr,
        )

    response = make_response(redirect(relay_state))
    is_secure = settings.flask_env != "development"
    logger.info(
        "Setting session cookie: name=%s, session_id=%s..., secure=%s, flask_env=%s, relay_state=%s",
        settings.session_cookie_name,
        session.id[:8],
        is_secure,
        settings.flask_env,
        relay_state,
    )
    response.set_cookie(
        settings.session_cookie_name,
        session.id,
        max_age=settings.session_max_age_hours * 3600,
        httponly=True,
        secure=is_secure,
        samesite="Lax",
    )
    return response


@bp.route("/saml/login", methods=["GET"])
def saml_login():
    """
    Initiate SAML login flow.

    Redirects user to TAMU Entra ID login page.
    """
    if is_dev_auth_bypass_enabled():
        relay_state = _sanitize_relay_state(request.args.get("next"), request.host)
        return redirect(relay_state)

    if not saml_auth_service.is_configured():
        return jsonify({"error": "SAML not configured"}), 503

    try:
        relay_state = request.args.get("next", "/")
        redirect_url = _start_saml_login(relay_state)
        return redirect(redirect_url)
    except DNSApiError:
        raise
    except Exception as e:
        logger.exception(f"SAML login error: {e}")
        return jsonify({"error": "Failed to initiate login"}), 500


@bp.route("/login", methods=["GET"])
def login():
    """
    Initiate interactive Microsoft Entra login.

    Prefers OIDC with account selection when configured, then falls back to
    SAML reauthentication when only the legacy SAML configuration is present.
    """
    if is_dev_auth_bypass_enabled():
        relay_state = _sanitize_relay_state(request.args.get("next"), request.host)
        return redirect(relay_state)

    relay_state = _sanitize_relay_state(request.args.get("next"), request.host)

    if _oidc_is_configured():
        try:
            return _start_oidc_login(relay_state)
        except DNSApiError:
            raise
        except Exception as exc:
            logger.exception("OIDC login error: %s context=%s", exc, _oidc_login_debug_context())
            return (
                jsonify(
                    {
                        "error": "Failed to initiate Microsoft Entra login",
                        "details": {
                            "type": exc.__class__.__name__,
                            "message": str(exc),
                            "context": _oidc_login_debug_context(),
                        },
                    }
                ),
                500,
            )

    if saml_auth_service.is_configured():
        try:
            return redirect(_start_saml_login(relay_state))
        except DNSApiError:
            raise
        except Exception as exc:
            logger.exception("SAML fallback login error: %s", exc)
            return jsonify({"error": "Failed to initiate login"}), 500

    return jsonify({"error": "Authentication not configured"}), 503


@bp.route("/saml/callback", methods=["POST"])
def saml_callback():
    """
    Handle SAML response from IdP.

    Validates the SAML assertion, creates/updates user, creates session,
    and sets session cookie.
    """
    if is_dev_auth_bypass_enabled():
        relay_state = _sanitize_relay_state(request.form.get("RelayState"), request.host)
        return redirect(relay_state)

    if not saml_auth_service.is_configured():
        return jsonify({"error": "SAML not configured"}), 503

    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth

        req = _prepare_flask_request()
        auth = OneLogin_Saml2_Auth(req, saml_auth_service.get_saml_settings())

        # Process the SAML response
        auth.process_response()
        errors = auth.get_errors()

        if errors:
            logger.error(f"SAML errors: {errors}")
            logger.error(f"SAML last error reason: {auth.get_last_error_reason()}")
            return jsonify({"error": "Authentication failed"}), 401

        if not auth.is_authenticated():
            reason = auth.get_last_error_reason()
            logger.error(f"SAML authentication failed: {errors}")
            logger.error(f"Failure reason: {reason}")
            return jsonify({"error": "Authentication failed"}), 401

        # Extract SAML attributes
        saml_attributes = auth.get_attributes()
        name_id = auth.get_nameid()

        logger.info(f"SAML login successful for: {name_id}")
        logger.debug(f"SAML attributes: {saml_attributes}")

        # Create/update user and session
        with get_db() as db:
            user = saml_auth_service.get_or_create_user(db, saml_attributes)
            session = saml_auth_service.create_session(
                db,
                user,
                user_agent=request.headers.get("User-Agent"),
                ip_address=request.remote_addr,
            )

        # Get redirect target from RelayState and normalize it to a safe internal path.
        relay_state = _sanitize_relay_state(request.form.get("RelayState"), request.host)

        # Create response with session cookie
        response = make_response(redirect(relay_state))

        # Determine if secure cookie (HTTPS)
        is_secure = settings.flask_env != "development"

        # Debug logging for cookie setting
        logger.info(f"Setting session cookie: name={settings.session_cookie_name}, "
                    f"session_id={session.id[:8]}..., secure={is_secure}, "
                    f"flask_env={settings.flask_env}, relay_state={relay_state}")

        response.set_cookie(
            settings.session_cookie_name,
            session.id,
            max_age=settings.session_max_age_hours * 3600,
            httponly=True,
            secure=is_secure,
            samesite="Lax",
        )

        return response

    except DNSApiError:
        raise
    except Exception as e:
        logger.exception(f"SAML callback error: {e}")
        return jsonify({"error": "Authentication processing failed"}), 500


@bp.route("/oidc/callback", methods=["GET"])
def oidc_callback():
    """Handle the Microsoft Entra OIDC authorization-code callback."""
    if is_dev_auth_bypass_enabled():
        relay_state = _sanitize_relay_state(request.args.get("next"), request.host)
        return redirect(relay_state)

    if not _oidc_is_configured():
        return jsonify({"error": "Microsoft Entra login not configured"}), 503

    try:
        relay_state, oidc_claims = _complete_oidc_login()
        return _finalize_oidc_login(relay_state, oidc_claims)
    except DNSApiError:
        raise
    except Exception as exc:
        _clear_oidc_login_state()
        logger.exception("OIDC callback error: %s", exc)
        return jsonify({"error": "Authentication processing failed"}), 500


@bp.route("/me", methods=["GET"])
def get_current_user():
    """
    Get current authenticated user.

    Returns user info if authenticated, 401 otherwise.

    Uses middleware-populated request context when available to reduce DB load.
    """
    # Check if user is authenticated (middleware sets g.user_id)
    user_id = getattr(g, 'user_id', None)
    session_id = getattr(g, 'session_id', None)

    if not user_id:
        # Not authenticated - return null (not 401, let frontend handle redirect)
        return jsonify({"user": None, "session": None, "is_admin": False})

    middleware_user_data = getattr(g, "user_data", None)
    middleware_session_data = getattr(g, "session_data", None)
    if middleware_user_data is not None:
        return jsonify({
            "user": middleware_user_data,
            "session": middleware_session_data,
            "is_admin": is_current_user_admin(),
        })

    # Query fresh from database using properly scoped session
    from app.models.user import User
    from app.models.session import Session

    with get_db() as db:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return jsonify({"user": None, "session": None, "is_admin": False})

        # Build user dict while session is open - no detached instance issues
        user_dict = user.to_dict()

        # Get session if available
        session_dict = None
        if session_id:
            session_obj = db.query(Session).filter(Session.id == session_id).first()
            if session_obj:
                session_dict = session_obj.to_dict()

    return jsonify({
        "user": user_dict,
        "session": session_dict,
        "is_admin": is_current_user_admin(),
    })


@bp.route("/logout", methods=["POST"])
@csrf_protect
def logout():
    """
    Log out current session.

    Revokes the current session and clears the cookie.
    """
    session_id = request.cookies.get(settings.session_cookie_name)

    if session_id:
        with get_db() as db:
            saml_auth_service.revoke_session(db, session_id)

    response = make_response(jsonify({"message": "Logged out"}))
    response.delete_cookie(settings.session_cookie_name)

    return response


@bp.route("/sessions", methods=["GET"])
def list_sessions():
    """
    List all active sessions for the current user.
    """
    user_id = getattr(g, 'user_id', None)
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    current_session_id = request.cookies.get(settings.session_cookie_name)

    with get_db() as db:
        sessions = saml_auth_service.get_user_sessions(db, user_id)
        result = []
        for s in sessions:
            if s.is_valid():
                session_dict = s.to_dict()
                session_dict["is_current"] = (s.id == current_session_id)
                result.append(session_dict)

    return jsonify({"sessions": result})


@bp.route("/sessions/revoke", methods=["POST"])
@csrf_protect
def revoke_session():
    """
    Revoke a specific session.
    """
    user_id = getattr(g, 'user_id', None)
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.get_json()
    session_id = data.get("session_id")

    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    with get_db() as db:
        # Verify session belongs to user
        from app.models.session import Session
        session = db.query(Session).filter(
            Session.id == session_id,
            Session.user_id == user_id
        ).first()

        if not session:
            return jsonify({"error": "Session not found"}), 404

        saml_auth_service.revoke_session(db, session_id)

    return jsonify({"message": "Session revoked"})


@bp.route("/sessions/revoke_all", methods=["POST"])
@csrf_protect
def revoke_all_sessions():
    """
    Revoke all sessions except current one (sign out everywhere else).
    """
    user_id = getattr(g, 'user_id', None)
    if not user_id:
        return jsonify({"error": "Not authenticated"}), 401

    current_session_id = request.cookies.get(settings.session_cookie_name)

    with get_db() as db:
        count = saml_auth_service.revoke_all_sessions(
            db,
            user_id,
            except_session_id=current_session_id
        )

    return jsonify({"message": f"Revoked {count} sessions"})


def _prepare_flask_request():
    """
    Prepare request dict for python3-saml from Flask request.

    IMPORTANT: python3-saml uses these values to compute the "received at" URL
    when validating the SAML response's Destination attribute. The library
    constructs the URL as: {https}://{http_host}{script_name}

    NOTE: Do NOT pass server_port explicitly when behind a reverse proxy.
    This causes issues with URL computation. Let the library determine port from http_host.
    See: https://github.com/onelogin/python3-saml/issues/
    """
    # PythonAnywhere (and other proxies) send X-Forwarded-Proto
    # We must explicitly check this because python3-saml validates the destination URL
    forwarded_proto = request.headers.get("X-Forwarded-Proto", request.scheme)

    # CRITICAL: PythonAnywhere internal proxy sends X-Forwarded-Proto: http even for HTTPS
    # We force HTTPS for pythonanywhere.com hosts since they are always served over HTTPS externally
    host = request.host.lower()
    is_pythonanywhere = "pythonanywhere.com" in host
    is_https = is_pythonanywhere or forwarded_proto.lower() == "https" or request.scheme == "https"

    # Log the detected protocol for debugging
    logger.info(f"Preparing SAML request. is_https={is_https}, is_pythonanywhere={is_pythonanywhere}, "
                f"forwarded_proto={forwarded_proto}, host={host}")

    # Return SAML request dict - do NOT include server_port to avoid proxy issues
    return {
        "https": "on" if is_https else "off",
        "http_host": request.host,
        "script_name": request.path,
        "get_data": request.args.copy(),
        "post_data": request.form.copy(),
        "query_string": request.query_string.decode("utf-8"),
    }
