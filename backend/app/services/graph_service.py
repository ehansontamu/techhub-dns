"""
Microsoft Graph Service using Service Principal.

Provides unified access to Microsoft Graph API for:
- Sending emails (Mail.Send)
- SharePoint file operations (Sites.ReadWrite.All)
- Teams messaging (Chat.ReadWrite.All)

Uses MSAL client credentials flow (no user interaction required).
"""

import logging
from typing import Optional
import json

import msal
import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class GraphService:
    """
    Service for Microsoft Graph API operations using Service Principal.

    All operations are performed as the application, not on behalf of a user.
    User context is passed for audit logging purposes only.
    """

    def __init__(self):
        self._msal_app = None
        self._access_token = None
        self._token_expires_at = 0

    def is_configured(self) -> bool:
        """Check if Graph API is properly configured."""
        return bool(
            settings.azure_tenant_id and
            settings.azure_client_id and
            settings.azure_client_secret
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

    def _get_access_token(self, scopes: list[str] = None) -> str:
        """
        Get an access token for Microsoft Graph API.

        Uses client credentials flow (application permissions).
        Token is cached and reused until expiry.
        """
        if not self.is_configured():
            raise RuntimeError("Graph API not configured. Set AZURE_* environment variables.")

        if scopes is None:
            scopes = ["https://graph.microsoft.com/.default"]

        app = self._get_msal_app()
        result = app.acquire_token_for_client(scopes=scopes)

        if "access_token" in result:
            return result["access_token"]
        else:
            error = result.get("error_description", result.get("error", "Unknown error"))
            logger.error(f"Failed to acquire Graph token: {error}")
            raise RuntimeError(f"Failed to acquire Graph token: {error}")

    def _graph_request(
        self,
        method: str,
        endpoint: str,
        json_data: dict = None,
        content: bytes = None,
        content_type: str = None,
        timeout: float = 30.0
    ) -> dict:
        """
        Make a request to Microsoft Graph API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            endpoint: Graph API endpoint (e.g., /me/sendMail)
            json_data: JSON payload for request body
            content: Binary content for file uploads
            content_type: Content-Type header for binary uploads
            timeout: Request timeout in seconds

        Returns:
            Response JSON (or empty dict for 204 responses)
        """
        token = self._get_access_token()
        url = f"https://graph.microsoft.com/v1.0{endpoint}"
        logger.info(f"Graph Request: {method} {url}")

        headers = {
            "Authorization": f"Bearer {token}",
        }

        if content_type:
            headers["Content-Type"] = content_type

        with httpx.Client(timeout=timeout) as client:
            if content:
                response = client.request(method, url, headers=headers, content=content)
            else:
                response = client.request(method, url, headers=headers, json=json_data)

            if response.status_code == 204:
                return {}

            if response.status_code >= 400:
                logger.error(f"Graph API error: {response.status_code} - {response.text}")
                response.raise_for_status()

            return response.json() if response.text else {}

    # =========================================================================
    # EMAIL OPERATIONS
    # =========================================================================

    def send_email(
        self,
        to_address: str,
        subject: str,
        body_html: str,
        body_text: str = None,
        from_address: str = None,
        from_name: str = None,
        attachment_name: str = None,
        attachment_content: bytes = None,
        initiated_by: str = "system"
    ) -> bool:
        """
        Send an email via Microsoft Graph API.

        Args:
            to_address: Recipient email address
            subject: Email subject
            body_html: HTML body content
            body_text: Optional plain text (not used by Graph, but accepted for compatibility)
            from_address: Sender email (must be authorized in Azure)
            from_name: Optional display name for the sender
            attachment_name: Optional attachment filename
            attachment_content: Optional attachment as bytes
            initiated_by: User who triggered this action (for audit)

        Returns:
            True if email sent successfully
        """
        import base64

        if not from_address:
            from_address = settings.smtp_from_address or "techhub@tamu.edu"

        message = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML",
                    "content": body_html
                },
                "toRecipients": [
                    {"emailAddress": {"address": to_address}}
                ],
            },
            "saveToSentItems": True
        }

        # Add sender name if provided
        if from_name:
            message["message"]["from"] = {
                "emailAddress": {
                    "name": from_name,
                    "address": from_address
                }
            }

        # Add attachment if provided
        if attachment_name and attachment_content:
            message["message"]["attachments"] = [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": attachment_name,
                    "contentType": "application/octet-stream",
                    "contentBytes": base64.b64encode(attachment_content).decode("utf-8")
                }
            ]

        try:
            # Use /users/{email}/sendMail for application permissions
            self._graph_request("POST", f"/users/{from_address}/sendMail", json_data=message)
            logger.info(f"Email sent to {to_address} (initiated by: {initiated_by})")
            return True
        except Exception as e:
            logger.error(f"Failed to send email to {to_address}: {e}")
            return False

    # =========================================================================
    # SHAREPOINT OPERATIONS
    # =========================================================================

    def upload_file_to_sharepoint(
        self,
        file_content: bytes,
        file_name: str,
        folder_path: str = None,
        initiated_by: str = "system"
    ) -> Optional[str]:
        """
        Upload a file to SharePoint by resolving Site -> Drive (Documents) -> Path.
        """
        if not settings.sharepoint_site_url:
            logger.warning("SharePoint not configured")
            return None

        from urllib.parse import urlparse
        import requests

        try:
            token = self._get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }

            # 1. Get Site ID
            site_url = settings.sharepoint_site_url
            parsed = urlparse(site_url)
            hostname = parsed.netloc
            site_path = parsed.path

            site_endpoint = f"https://graph.microsoft.com/v1.0/sites/{hostname}:{site_path}"
            logger.info(f"Resolving Site ID: {site_endpoint}")
            site_resp = requests.get(site_endpoint, headers=headers)
            if site_resp.status_code != 200:
                logger.error(f"Failed to get site: {site_resp.text}")
                return None
            site_id = site_resp.json().get('id')

            # 2. Get Drive ID (Documents)
            drives_endpoint = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives"
            drives_resp = requests.get(drives_endpoint, headers=headers)
            if drives_resp.status_code != 200:
                logger.error(f"Failed to list drives: {drives_resp.text}")
                return None

            drives = drives_resp.json().get('value', [])
            drive_id = None
            for d in drives:
                if d.get('name') == "Documents":
                    drive_id = d.get('id')
                    break

            if not drive_id and drives:
                drive_id = drives[0].get('id')
                logger.warning("Documents drive not found, using first available drive.")

            if not drive_id:
                logger.error("No drives found for site.")
                return None

            # 3. Construct Path
            base_folder = settings.sharepoint_folder_path.strip("/")
            if folder_path:
                full_path = f"{base_folder}/{folder_path.strip('/')}/{file_name}"
            else:
                full_path = f"{base_folder}/{file_name}"

            # Normalize path
            full_path = full_path.replace("\\", "/").replace("//", "/")

            # 4. Upload via Drive ID
            endpoint = f"/drives/{drive_id}/root:/{full_path}:/content"

            # Use _graph_request to handle token refresh if needed, but endpoint needs to be relative to v1.0
            # Note: _graph_request expects endpoint starting with /

            result = self._graph_request(
                "PUT",
                endpoint,
                content=file_content,
                content_type="application/octet-stream" # Use octet-stream for raw bytes
            )

            web_url = result.get("webUrl")
            logger.info(f"File uploaded to SharePoint: {file_name} (initiated by: {initiated_by})")
            return web_url

        except Exception as e:
            logger.error(f"Failed to upload file to SharePoint: {e}")
            return None

    # =========================================================================
    # TEAMS MESSAGING
    # =========================================================================

    def send_teams_message(
        self,
        recipient_email: str,
        message_content: str,
        initiated_by: str = "system"
    ) -> bool:
        """
        [DEPRECATED] Send a Teams chat message via Graph API.
        This method is deprecated in favor of the Folder Queue strategy.
        """
        logger.warning(f"[DEPRECATED] send_teams_message called for {recipient_email}. This method is no longer supported.")
        return False

    # =====================================================================
    # USER PROFILE LOOKUP
    # =====================================================================

    def get_user_profile(self, user_oid: str) -> Optional[dict]:
        """
        Fetch a user profile from Microsoft Graph by Entra ID object ID.

        Args:
            user_oid: The Entra ID object identifier (from SAML oid claim)

        Returns:
            Dict with user properties (displayName, givenName, surname, mail, etc.)
            or None if the user is not found or Graph API is unavailable.
        """
        if not self.is_configured():
            logger.debug("Graph API not configured; skipping user profile lookup.")
            return None

        try:
            # $select keeps the response small
            endpoint = f"/users/{user_oid}?$select=displayName,givenName,surname,mail,department,employeeId"
            result = self._graph_request("GET", endpoint)
            logger.info(f"Graph user profile fetched for OID {user_oid}: displayName={result.get('displayName')}")
            return result
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.warning(f"Graph user not found for OID {user_oid}")
            elif exc.response.status_code in (401, 403):
                logger.warning(
                    f"Graph API permission denied ({exc.response.status_code}) for user lookup. "
                    "Ensure the Service Principal has User.Read.All application permission and admin consent."
                )
            else:
                logger.error(f"Graph API error fetching user profile: {exc.response.status_code} - {exc.response.text}")
            return None
        except Exception as exc:
            logger.error(f"Unexpected error fetching user profile from Graph: {exc}")
            return None


# Singleton instance
graph_service = GraphService()
