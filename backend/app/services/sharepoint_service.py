"""
SharePoint Storage Service for file and QA data storage.
Uses Microsoft Graph API with Service Principal authentication (MSAL client credentials).
No user interaction required - works on headless servers.
"""

import json
import logging
from typing import Optional, Dict, Any
from urllib.parse import urlparse

import httpx
import msal

from app.config import settings

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class SharePointService:
    """Service for uploading and managing files in SharePoint via Microsoft Graph API."""

    def __init__(self):
        self._msal_app = None
        self._site_id: Optional[str] = None
        self._drive_id: Optional[str] = None

    @property
    def is_enabled(self) -> bool:
        """SharePoint storage is enabled only when configured in env/system settings."""
        return bool(
            settings.sharepoint_enabled
            and settings.sharepoint_site_url
            and settings.azure_tenant_id
            and settings.azure_client_id
            and settings.azure_client_secret
        )

    def _get_msal_app(self) -> msal.ConfidentialClientApplication:
        """Get or create MSAL confidential client application."""
        if self._msal_app is None:
            authority = f"https://login.microsoftonline.com/{settings.azure_tenant_id}"
            self._msal_app = msal.ConfidentialClientApplication(
                settings.azure_client_id,
                authority=authority,
                client_credential=settings.azure_client_secret,
            )
        return self._msal_app

    def _get_access_token(self) -> str:
        """Get access token for Microsoft Graph API using client credentials."""
        if not self.is_enabled:
            raise RuntimeError(
                "SharePoint not enabled in System Settings."
            )

        app = self._get_msal_app()
        scopes = ["https://graph.microsoft.com/.default"]
        result = app.acquire_token_for_client(scopes=scopes)

        if "access_token" in result:
            return result["access_token"]
        else:
            error = result.get("error_description", result.get("error", "Unknown error"))
            logger.error(f"Failed to acquire Graph token for SharePoint: {error}")
            raise RuntimeError(f"Failed to acquire Graph token: {error}")

    def _get_headers(self) -> Dict[str, str]:
        """Get authorization headers for Graph API requests."""
        token = self._get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    def _get_site_id(self) -> str:
        """Get SharePoint site ID from the configured URL."""
        if self._site_id:
            return self._site_id

        site_url = settings.sharepoint_site_url.rstrip("/")
        if not site_url:
            raise ValueError("SharePoint Site URL not configured in .env")

        # Parse site URL: https://tamucs.sharepoint.com/teams/Team-TechHub
        parsed = urlparse(site_url)
        site_path = parsed.path  # e.g., /teams/Team-TechHub

        # Get site ID via Graph API
        url = f"{GRAPH_BASE_URL}/sites/{parsed.netloc}:{site_path}"

        with httpx.Client() as client:
            response = client.get(url, headers=self._get_headers())
            response.raise_for_status()
            data = response.json()
            self._site_id = data["id"]
            logger.info(f"Retrieved SharePoint site ID: {self._site_id}")
            return self._site_id

    def _get_drive_id(self) -> str:
        """Get the default document library (drive) ID."""
        if self._drive_id:
            return self._drive_id

        site_id = self._get_site_id()
        url = f"{GRAPH_BASE_URL}/sites/{site_id}/drive"

        with httpx.Client() as client:
            response = client.get(url, headers=self._get_headers())
            response.raise_for_status()
            data = response.json()
            self._drive_id = data["id"]
            logger.info(f"Retrieved SharePoint drive ID: {self._drive_id}")
            return self._drive_id

    def _get_folder_path(self, subfolder: str) -> str:
        """Build the full folder path within the document library."""
        base_path = settings.sharepoint_folder_path.strip("/")
        subfolder = subfolder.strip("/")
        return f"{base_path}/{subfolder}" if subfolder else base_path

    def test_connection(self) -> Dict[str, Any]:
        """
        Test the SharePoint connection and return status details.

        Returns:
            dict with 'success', 'site_id', 'drive_id', and optionally 'error'
        """
        if not self.is_enabled:
            return {
                "success": False,
                "error": "SharePoint not configured (missing AZURE_* or SHAREPOINT_* env vars)"
            }

        try:
            # Test authentication
            self._get_access_token()

            # Try to get site ID (tests Graph API access)
            site_id = self._get_site_id()
            drive_id = self._get_drive_id()

            return {
                "success": True,
                "site_id": site_id,
                "drive_id": drive_id,
            }
        except Exception as e:
            error_str = str(e)
            logger.error(f"SharePoint connection test failed: {error_str}")

            # Parse common Azure AD errors
            if "AADSTS65001" in error_str:
                return {
                    "success": False,
                    "error": "Admin consent required for Graph API permissions"
                }
            elif "AADSTS7000215" in error_str:
                return {
                    "success": False,
                    "error": "Invalid or expired client secret"
                }
            elif "AADSTS700016" in error_str:
                return {
                    "success": False,
                    "error": "Application not found in tenant"
                }
            elif "403" in error_str or "Forbidden" in error_str:
                return {
                    "success": False,
                    "error": "Access denied - check Sites.ReadWrite.All permission"
                }
            else:
                return {
                    "success": False,
                    "error": error_str[:200]
                }

    def upload_file_safe(
        self,
        content: bytes,
        subfolder: str,
        filename: str
    ) -> Optional[str]:
        """
        Upload a file to SharePoint with graceful error handling.

        Unlike upload_file(), this method returns None on failure instead of raising.
        Use this when SharePoint storage is optional.

        Returns:
            Web URL to the uploaded file, or None if upload failed
        """
        if not self.is_enabled:
            logger.warning(f"SharePoint disabled, skipping upload: {filename}")
            return None

        try:
            return self.upload_file(content, subfolder, filename)
        except Exception as e:
            logger.error(f"SharePoint upload failed for {filename}: {e}")
            return None

    def upload_file(
        self,
        content: bytes,
        subfolder: str,
        filename: str
    ) -> str:
        """
        Upload a file to SharePoint.

        Args:
            content: File content as bytes
            subfolder: Subfolder within the base folder (e.g., "qa", "picklists")
            filename: Name of the file

        Returns:
            Web URL to the uploaded file
        """
        if not self.is_enabled:
            raise RuntimeError("SharePoint storage is not enabled or not fully configured")

        drive_id = self._get_drive_id()
        folder_path = self._get_folder_path(subfolder)

        # Build upload URL
        # For files < 4MB, use simple PUT
        upload_url = f"{GRAPH_BASE_URL}/drives/{drive_id}/root:/{folder_path}/{filename}:/content"

        headers = self._get_headers()
        headers["Content-Type"] = "application/octet-stream"

        logger.info(f"Uploading file to SharePoint: {folder_path}/{filename}")

        with httpx.Client(timeout=60.0) as client:
            response = client.put(upload_url, headers=headers, content=content)
            response.raise_for_status()
            data = response.json()

            web_url = data.get("webUrl", "")
            logger.info(f"File uploaded successfully: {web_url}")
            return web_url

    def upload_json(
        self,
        data: Dict[str, Any],
        subfolder: str,
        filename: str
    ) -> str:
        """
        Upload JSON data as a file to SharePoint.

        Args:
            data: Dictionary to serialize as JSON
            subfolder: Subfolder within the base folder
            filename: Name of the file (should end with .json)

        Returns:
            Web URL to the uploaded file
        """
        json_content = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
        return self.upload_file(json_content, subfolder, filename)

    def upload_pdf(
        self,
        pdf_path: str,
        subfolder: str,
        filename: Optional[str] = None
    ) -> str:
        """
        Upload a PDF file from disk to SharePoint.

        Args:
            pdf_path: Path to the PDF file on disk
            subfolder: Subfolder within the base folder
            filename: Optional filename (uses original if not provided)

        Returns:
            Web URL to the uploaded file
        """
        from pathlib import Path

        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        if filename is None:
            filename = path.name

        content = path.read_bytes()
        return self.upload_file(content, subfolder, filename)

    def get_file_url(self, subfolder: str, filename: str) -> Optional[str]:
        """
        Get the web URL for a file in SharePoint.

        Returns None if the file doesn't exist.
        """
        if not self.is_enabled:
            return None

        try:
            drive_id = self._get_drive_id()
            folder_path = self._get_folder_path(subfolder)
            url = f"{GRAPH_BASE_URL}/drives/{drive_id}/root:/{folder_path}/{filename}"

            with httpx.Client() as client:
                response = client.get(url, headers=self._get_headers())
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                data = response.json()
                return data.get("webUrl")
        except Exception as e:
            logger.error(f"Error getting file URL: {e}")
            return None

    def download_file(self, subfolder: str, filename: str) -> Optional[bytes]:
        """
        Download file content from SharePoint.

        Returns None if the file doesn't exist.
        """
        if not self.is_enabled:
            return None

        try:
            drive_id = self._get_drive_id()
            folder_path = self._get_folder_path(subfolder)
            # Use :/content to download the file content
            url = f"{GRAPH_BASE_URL}/drives/{drive_id}/root:/{folder_path}/{filename}:/content"

            with httpx.Client(timeout=60.0) as client:
                response = client.get(url, headers=self._get_headers(), follow_redirects=True)
                if response.status_code == 404:
                    logger.debug(f"File not found in SharePoint: {folder_path}/{filename}")
                    return None
                response.raise_for_status()
                logger.info(f"Downloaded file from SharePoint: {folder_path}/{filename}")
                return response.content
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            logger.error(f"Error downloading file from SharePoint: {e}")
            return None
        except Exception as e:
            logger.error(f"Error downloading file from SharePoint: {e}")
            return None

    def file_exists(self, subfolder: str, filename: str) -> bool:
        """Check if a file exists in SharePoint."""
        if not self.is_enabled:
            return False

        try:
            drive_id = self._get_drive_id()
            folder_path = self._get_folder_path(subfolder)
            url = f"{GRAPH_BASE_URL}/drives/{drive_id}/root:/{folder_path}/{filename}"

            with httpx.Client() as client:
                response = client.get(url, headers=self._get_headers())
                return response.status_code == 200
        except Exception:
            return False


# Singleton instance (lazy initialization)
_sharepoint_service: Optional[SharePointService] = None


def get_sharepoint_service() -> SharePointService:
    """Get the SharePoint service singleton."""
    global _sharepoint_service
    if _sharepoint_service is None:
        _sharepoint_service = SharePointService()
    return _sharepoint_service
