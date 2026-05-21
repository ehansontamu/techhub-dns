"""
SharePoint API routes for admin configuration and authentication.
"""

from flask import Blueprint, jsonify, request
import logging

from app.config import settings
from app.api.auth_middleware import require_auth, require_admin

logger = logging.getLogger(__name__)

sharepoint_bp = Blueprint("sharepoint", __name__, url_prefix="/api/sharepoint")


@sharepoint_bp.route("/status", methods=["GET"])
@require_auth
def get_sharepoint_status():
    """Get SharePoint configuration status."""
    from app.services.sharepoint_service import get_sharepoint_service

    try:
        sp_service = get_sharepoint_service()

        # Check if authenticated by looking for cached site_id (set after successful auth)
        is_authenticated = sp_service._site_id is not None

        return jsonify({
            "enabled": sp_service.is_enabled,
            "site_url": settings.sharepoint_site_url,
            "folder_path": settings.sharepoint_folder_path,
            "authenticated": is_authenticated,
        })
    except Exception as e:
        logger.error("Error getting SharePoint status: %s", e, exc_info=True)
        return jsonify({
            "enabled": settings.sharepoint_enabled,
            "site_url": settings.sharepoint_site_url,
            "folder_path": settings.sharepoint_folder_path,
            "authenticated": False,
            "error": "Failed to retrieve SharePoint status"
        })


@sharepoint_bp.route("/authenticate", methods=["POST"])
@require_admin
def authenticate_sharepoint():
    """
    Test SharePoint authentication using Service Principal.
    This validates the MSAL client credentials flow and retrieves site/drive IDs.
    """
    from app.services.sharepoint_service import get_sharepoint_service

    if not settings.sharepoint_enabled:
        return jsonify({
            "success": False,
            "error": "SharePoint is not enabled. Set SHAREPOINT_ENABLED=true in .env"
        }), 400

    if not settings.sharepoint_site_url:
        return jsonify({
            "success": False,
            "error": "SharePoint site URL not configured. Set SHAREPOINT_SITE_URL in .env"
        }), 400

    # Check Service Principal credentials
    if not all([settings.azure_tenant_id, settings.azure_client_id, settings.azure_client_secret]):
        return jsonify({
            "success": False,
            "error": "Azure Service Principal not configured. Set AZURE_TENANT_ID, AZURE_CLIENT_ID, and AZURE_CLIENT_SECRET in .env"
        }), 400

    try:
        sp_service = get_sharepoint_service()

        # This will authenticate using MSAL client credentials (no browser needed)
        logger.info("Testing SharePoint authentication via Service Principal...")
        site_id = sp_service._get_site_id()
        drive_id = sp_service._get_drive_id()

        return jsonify({
            "success": True,
            "message": "Successfully authenticated to SharePoint via Service Principal",
            "site_id": site_id,
            "drive_id": drive_id
        })
    except Exception as e:
        logger.error("SharePoint authentication failed: %s", e, exc_info=True)
        return jsonify({
            "success": False,
            "error": "SharePoint authentication failed"
        }), 500


@sharepoint_bp.route("/test-upload", methods=["POST"])
@require_admin
def test_sharepoint_upload():
    """Test SharePoint upload by uploading a small test file."""
    from app.services.sharepoint_service import get_sharepoint_service
    from datetime import datetime

    if not settings.sharepoint_enabled:
        return jsonify({
            "success": False,
            "error": "SharePoint is not enabled"
        }), 400

    try:
        sp_service = get_sharepoint_service()

        # Create a small test file
        test_content = f"Test upload from TechHub Delivery App at {datetime.utcnow().isoformat()}"
        test_filename = f"test-upload-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.txt"

        url = sp_service.upload_file(
            content=test_content.encode("utf-8"),
            subfolder="test",
            filename=test_filename
        )

        return jsonify({
            "success": True,
            "message": "Test file uploaded successfully",
            "url": url,
            "filename": test_filename
        })
    except Exception as e:
        logger.error("SharePoint test upload failed: %s", e, exc_info=True)
        return jsonify({
            "success": False,
            "error": "SharePoint test upload failed"
        }), 500
