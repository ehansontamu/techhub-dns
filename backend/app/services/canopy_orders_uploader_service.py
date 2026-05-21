import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

import requests
from requests.auth import HTTPDigestAuth, HTTPBasicAuth

from app.config import settings


logger = logging.getLogger(__name__)


class CanopyOrdersUploaderService:
    def __init__(self) -> None:
        self.store_base = settings.canopyorders_store_base
        self.dav_root_path = settings.canopyorders_dav_root_path
        self.base_dir = settings.canopyorders_base_dir
        self.username = settings.webdav_username
        self.user_agent = settings.canopyorders_user_agent
        self.teams_workflow_url = settings.canopyorders_teams_workflow_url
        self.teams_shared_secret = settings.canopyorders_teams_shared_secret
        self._webdav_password: Optional[str] = None
        self.session = requests.Session()

    @property
    def webdav_password(self) -> str:
        if self._webdav_password is None:
            self._webdav_password = self._get_webdav_password()
        return self._webdav_password

    def _get_webdav_password(self) -> str:
        password = getattr(settings, "webdav_password", None)
        if password is not None and str(password).strip():
            return str(password)

        raise ValueError(
            "WebDAV password is not configured. Set WEBDAV_PASSWORD."
        )

    def upload_orders(self, orders: List[str]) -> Dict[str, Any]:
        if not self.store_base:
            return {
                "success": False,
                "error": "Canopy Orders store base is not configured",
                "uploaded_url": None,
            }

        filename = self._build_filename()
        upload_url = self._build_upload_url(filename)

        warmup_result = self._warm_up_connection()
        if not warmup_result["success"]:
            warmup_result["filename"] = filename
            warmup_result["uploaded_url"] = upload_url
            return warmup_result

        payload_bytes = json.dumps(orders, ensure_ascii=False).encode("utf-8")
        headers = self._base_headers()
        headers["Content-Type"] = "application/json; charset=utf-8"

        try:
            response = self._request_with_legacy_auth(
                "PUT",
                upload_url,
                headers=headers,
                data=payload_bytes,
                timeout=(30, 120),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.error(f"Canopy Orders upload failed: {exc}")
            return {
                "success": False,
                "error": f"Canopy Orders upload failed: {exc}",
                "uploaded_url": upload_url,
            }

        if self._is_cloudflare_challenge(response):
            return {
                "success": False,
                "error": self._cloudflare_error_message(),
                "error_type": "cloudflare",
                "status_code": response.status_code,
                "uploaded_url": upload_url,
            }

        if 200 <= response.status_code < 300:
            return {
                "success": True,
                "filename": filename,
                "uploaded_url": upload_url,
            }

        body = response.text or ""
        return {
            "success": False,
            "error": (
                f"Upload failed. HTTP {response.status_code}. "
                f"Body (first 500): {body[:500]}"
            ),
            "status_code": response.status_code,
            "uploaded_url": upload_url,
        }

    def send_teams_notification(self, orders: List[str], uploaded_url: str) -> bool:
        if not self.teams_workflow_url:
            return False

        orders_value = ", ".join(orders) if orders else "(none)"
        payload = {
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "type": "AdaptiveCard",
                        "body": [
                            {
                                "type": "TextBlock",
                                "size": "Medium",
                                "weight": "Bolder",
                                "text": "Canopy orders uploaded",
                            },
                            {
                                "type": "FactSet",
                                "facts": [
                                    {"title": "Count", "value": str(len(orders))},
                                    {"title": "Orders", "value": orders_value},
                                    {"title": "File", "value": uploaded_url},
                                    {"title": "Source", "value": "Canopy Orders Uploader"},
                                ],
                            },
                        ],
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "version": "1.5",
                    },
                }
            ]
        }

        headers = {"Content-Type": "application/json"}
        if self.teams_shared_secret:
            headers["x-shared-secret"] = self.teams_shared_secret

        try:
            response = self.session.post(
                self.teams_workflow_url,
                json=payload,
                headers=headers,
                timeout=20,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            logger.error(f"Failed to send Canopy Orders Teams notification: {exc}")
            return False

    def _warm_up_connection(self) -> Dict[str, Any]:
        warmup_url = self._build_dav_root_url(ensure_trailing_slash=True)
        xml_body = (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
            "<D:propfind xmlns:D=\"DAV:\"><D:prop><D:displayname/></D:prop></D:propfind>"
        )
        headers = self._base_headers()
        headers.update({
            "Depth": "1",
            "Content-Type": "application/xml; charset=utf-8",
        })

        try:
            response = self._request_with_legacy_auth(
                "PROPFIND",
                warmup_url,
                headers=headers,
                data=xml_body.encode("utf-8"),
                timeout=(30, 120),
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.error(f"Canopy Orders warm-up failed: {exc}")
            return {
                "success": False,
                "error": f"Canopy Orders warm-up failed: {exc}",
                "uploaded_url": warmup_url,
            }

        if self._is_cloudflare_challenge(response):
            return {
                "success": False,
                "error": self._cloudflare_error_message(),
                "error_type": "cloudflare",
                "status_code": response.status_code,
                "uploaded_url": warmup_url,
            }

        if 200 <= response.status_code < 300:
            return {"success": True}

        return {
            "success": False,
            "error": (
                "Authentication failed at /dav/ "
                f"({response.status_code}). Check credentials or WebDAV password."
            ),
            "status_code": response.status_code,
            "uploaded_url": warmup_url,
        }

    def _request_with_legacy_auth(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        if not self.username:
            return self.session.request(method, url, **kwargs)

        digest_auth = HTTPDigestAuth(self.username, self.webdav_password)
        response = self.session.request(method, url, auth=digest_auth, **kwargs)

        if response.status_code == 401:
            basic_auth = HTTPBasicAuth(self.username, self.webdav_password)
            response = self.session.request(method, url, auth=basic_auth, **kwargs)

        return response

    def _build_upload_url(self, filename: str) -> str:
        root = self._build_dav_root_url()
        base_dir = self.base_dir.strip("/") if self.base_dir else ""
        if base_dir:
            return f"{root}/{base_dir}/{filename}"
        return f"{root}/{filename}"

    def _build_filename(self) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return f"canopyorders_{timestamp}.json"

    def _build_dav_root_url(self, ensure_trailing_slash: bool = False) -> str:
        if self.store_base is None:
            raise ValueError("Canopy Orders store base is not configured")

        store_base = self.store_base
        root = (
            f"{store_base.rstrip('/')}/{self.dav_root_path.strip('/')}"
            if self.dav_root_path
            else store_base.rstrip("/")
        )
        if ensure_trailing_slash:
            return f"{root}/"
        return root

    def _base_headers(self) -> Dict[str, str]:
        return {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
            "Expect": "100-continue",
            "User-Agent": self.user_agent,
        }

    def _is_cloudflare_challenge(self, response: requests.Response) -> bool:
        if response.status_code != 403:
            return False
        body = response.text or ""
        return "cdn-cgi/challenge" in body or "Enable JavaScript" in body

    def _cloudflare_error_message(self) -> str:
        return (
            "Upload blocked by Cloudflare Managed Challenge. Ask to allowlist your "
            "egress IP for /dav/* or run from the allowlisted network."
        )
