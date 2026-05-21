"""
CSRF protection utilities for state-changing endpoints.
Provides Origin/Referer header verification against allowed CORS origins.
"""

from functools import wraps
from flask import request, abort
from app.config import settings


def csrf_protect(f):
    """Verify request Origin/Referer against allowed CORS origins.

    Apply to state-changing (POST/PUT/PATCH/DELETE) endpoints that are
    called by the browser frontend. Do NOT apply to external webhook
    endpoints which have their own signature verification.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        origin = request.headers.get("Origin", "")
        referer = request.headers.get("Referer", "")
        allowed = settings.get_cors_allowed_origins()
        # Check if request comes from allowed origin
        source = origin or referer
        if source and not any(source.startswith(a) for a in allowed):
            abort(403, description="CSRF check failed")
        return f(*args, **kwargs)
    return decorated
