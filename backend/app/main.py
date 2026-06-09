from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix
from app.config import settings
from app.database import get_db_session, get_runtime_db_pool_settings
from app.api.routes import (
    orders,
    inflow,
    audit,
    delivery_runs,
    sharepoint,
    auth,
    system,
    analytics,
    observability,
    vehicle_checkouts,
)
from app.api.middleware import register_error_handlers
from app.api.auth_middleware import init_auth_middleware
import logging
import os
import mimetypes

# Ensure common web file types have correct MIME types
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("text/html", ".html")
mimetypes.add_type("application/wasm", ".wasm")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if settings.secret_key is None:
    import secrets
    settings.secret_key = secrets.token_hex(32)
    logger.warning("No SECRET_KEY configured — using ephemeral random key (sessions will not persist across restarts)")

app = Flask(__name__)
app.secret_key = settings.secret_key
app.url_map.strict_slashes = False  # Prevent 308 redirects that break CORS

# Fix for running behind proxy (PythonAnywhere) - ensures correct URL generation
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

ALLOWED_ORIGINS = settings.get_cors_allowed_origins()

# Configure CORS with specific origins
CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)

# Configure Flask-SocketIO with specific origins
socketio = SocketIO(app, cors_allowed_origins=ALLOWED_ORIGINS, async_mode='threading')

# Register Socket.IO events
from app.api.socket_events import register_socket_events

register_socket_events(socketio)

# Register error handlers
register_error_handlers(app)

# Initialize authentication middleware
init_auth_middleware(app)

_startup_settings_logged = False


# Frontend static files path (for production deployment)
# Check multiple possible locations for the dist folder
def get_frontend_dist_path():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    possible_paths = [
        # Current checkout path (works for local, prod, and dev repos)
        os.path.join(repo_root, "frontend", "dist"),
        # PythonAnywhere explicit paths
        "/home/techhub/techhub-dns-dev/frontend/dist",
        "/home/techhub/techhub-dns/frontend/dist",
    ]
    for path in possible_paths:
        if os.path.exists(path) and os.path.isdir(path):
            return path
    return possible_paths[0]  # Fallback to PythonAnywhere path


FRONTEND_DIST_PATH = get_frontend_dist_path()


def _log_runtime_startup_settings() -> None:
    global _startup_settings_logged
    if _startup_settings_logged:
        return

    _startup_settings_logged = True
    pool_settings = get_runtime_db_pool_settings()
    logger.info(
        "Runtime settings: scheduler_enabled=%s db_backend=%s pool_size=%s max_overflow=%s pool_timeout=%s pool_recycle=%s cors_allowed_origins=%s",
        settings.scheduler_enabled,
        pool_settings.get("database_backend"),
        pool_settings.get("pool_size"),
        pool_settings.get("max_overflow"),
        pool_settings.get("pool_timeout"),
        pool_settings.get("pool_recycle"),
        ALLOWED_ORIGINS,
    )


_log_runtime_startup_settings()


# Register blueprints with full path prefixes
app.register_blueprint(orders.bp, url_prefix="/api/orders")
app.register_blueprint(inflow.bp, url_prefix="/api/inflow")
app.register_blueprint(audit.bp, url_prefix="/api/audit")
app.register_blueprint(delivery_runs.bp, url_prefix="/api/delivery-runs")
app.register_blueprint(vehicle_checkouts.vehicle_checkouts_bp)
app.register_blueprint(vehicle_checkouts.vehicles_bp)
app.register_blueprint(analytics.bp, url_prefix="/api/analytics")
app.register_blueprint(observability.bp, url_prefix="/api/observability")
app.register_blueprint(sharepoint.sharepoint_bp)
app.register_blueprint(auth.bp)
# Compatibility alias for older frontend bundles that still hit /auth/*.
app.register_blueprint(auth.bp, url_prefix="/auth", name_prefix="legacy_auth")
app.register_blueprint(system.bp)


@app.route("/health")
def health():
    pool_settings = get_runtime_db_pool_settings()
    db = get_db_session()
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        logger.exception("Health check failed")
        return (
            jsonify(
                {
                    "status": "unhealthy",
                    "database": "error",
                    "error": str(exc),
                }
            ),
            503,
        )
    finally:
        db.close()

    return jsonify(
        {
            "status": "healthy",
            "database": "ok",
            "database_backend": pool_settings.get("database_backend"),
            "db_pool_size": pool_settings.get("pool_size"),
            "db_max_overflow": pool_settings.get("max_overflow"),
        }
    )


@app.route("/api")
def api_root():
    return jsonify({"message": "TechHub Delivery Workflow API", "version": "1.0.0"})


# Serve React frontend static files (production only)
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    """Serve React frontend. Falls back to index.html for client-side routing."""
    # Don't return SPA HTML for missing API endpoints.
    if path == "api" or path.startswith("api/"):
        return jsonify({"error": "Not found"}), 404

    # Check if frontend dist exists (production mode)
    if os.path.exists(FRONTEND_DIST_PATH):
        # Serve static assets (js, css, images, etc.)
        if path:
            # Check if the file exists (use os.path.join for filesystem check)
            full_path = os.path.join(FRONTEND_DIST_PATH, path.replace("/", os.path.sep))
            if os.path.exists(full_path):
                # Determine the correct MIME type for the file
                mime_type, _ = mimetypes.guess_type(path)
                # send_from_directory expects forward slashes, not OS separators
                return send_from_directory(FRONTEND_DIST_PATH, path, mimetype=mime_type)

        # Fallback to index.html for SPA routing
        return send_from_directory(
            FRONTEND_DIST_PATH, "index.html", mimetype="text/html"
        )
    else:
        # Development mode - frontend served by Vite
        return jsonify(
            {
                "message": "TechHub Delivery Workflow API",
                "version": "1.0.0",
                "note": "Frontend not built. Run 'npm run build' in frontend directory.",
            }
        )


if __name__ == "__main__":
    # Development server - use waitress for production
    socketio.run(app, host="0.0.0.0", port=8000, debug=True)
