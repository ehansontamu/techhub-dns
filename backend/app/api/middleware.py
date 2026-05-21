from flask import jsonify
from app.schemas.error import ErrorResponse
from app.utils.exceptions import DNSApiError
from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError
import logging
import uuid

logger = logging.getLogger(__name__)


def register_error_handlers(app):
    """Register Flask error handlers for consistent error responses."""

    def _iter_exception_chain(error):
        current = error
        seen = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            yield current
            current = (
                getattr(current, "original_exception", None)
                or getattr(current, "orig", None)
                or getattr(current, "__cause__", None)
                or getattr(current, "__context__", None)
            )

    def _database_capacity_dns_error(error):
        for candidate in _iter_exception_chain(error):
            if isinstance(candidate, SQLAlchemyTimeoutError):
                return DNSApiError(
                    code="DATABASE_TEMPORARILY_UNAVAILABLE",
                    message="Database temporarily unavailable. Please retry shortly.",
                    status_code=503,
                    details={"reason": "pool_timeout"},
                )

            if isinstance(candidate, OperationalError):
                orig = getattr(candidate, "orig", None)
                if getattr(orig, "args", None):
                    mysql_error_code = str(orig.args[0]).strip()
                    if mysql_error_code == "1226":
                        return DNSApiError(
                            code="DATABASE_TEMPORARILY_UNAVAILABLE",
                            message="Database temporarily unavailable. Please retry shortly.",
                            status_code=503,
                            details={"reason": "max_user_connections"},
                        )

        return None

    @app.errorhandler(DNSApiError)
    def handle_dns_api_error(error):
        """Handle custom DNS API errors."""
        logger.warning(f"DNS API Error: {error.code} - {error.message}")
        response = ErrorResponse(
            error={
                "code": error.code,
                "message": error.message,
                "field": error.field,
                "details": error.details
            },
            request_id=str(uuid.uuid4())
        )
        return jsonify(response.model_dump()), error.status_code

    @app.errorhandler(400)
    def handle_bad_request(error):
        """Handle 400 Bad Request errors."""
        logger.warning(f"Bad Request: {error.description}")
        response = ErrorResponse(
            error={
                "code": "BAD_REQUEST",
                "message": error.description or "Bad request",
                "details": {}
            },
            request_id=str(uuid.uuid4())
        )
        return jsonify(response.model_dump()), 400

    @app.errorhandler(404)
    def handle_not_found(error):
        """Handle 404 Not Found errors."""
        logger.warning(f"Not Found: {error.description}")
        response = ErrorResponse(
            error={
                "code": "NOT_FOUND",
                "message": error.description or "Resource not found",
                "details": {}
            },
            request_id=str(uuid.uuid4())
        )
        return jsonify(response.model_dump()), 404

    @app.errorhandler(500)
    def handle_internal_error(error):
        """Handle 500 Internal Server errors."""
        database_error = _database_capacity_dns_error(error)
        if database_error is not None:
            logger.warning("Database capacity error handled from 500 handler")
            return handle_dns_api_error(database_error)

        logger.error(f"Internal Server Error: {error}", exc_info=True)
        response = ErrorResponse(
            error={
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred",
                "details": {}
            },
            request_id=str(uuid.uuid4())
        )
        return jsonify(response.model_dump()), 500

    @app.errorhandler(Exception)
    def handle_exception(error):
        """Catch-all for unexpected errors."""
        database_error = _database_capacity_dns_error(error)
        if database_error is not None:
            logger.warning("Database capacity error handled from exception handler")
            return handle_dns_api_error(database_error)

        logger.error(f"Unexpected error: {str(error)}", exc_info=True)
        response = ErrorResponse(
            error={
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred",
                "details": {}
            },
            request_id=str(uuid.uuid4())
        )
        return jsonify(response.model_dump()), 500
