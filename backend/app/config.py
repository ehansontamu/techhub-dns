import os
import json
from typing import Optional, List, Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    database_url: str

    # Inflow API
    inflow_api_url: str = "https://cloudapi.inflowinventory.com"
    inflow_company_id: str = "6eb6abe4-d92a-4130-a15e-64d3b7278b81"
    inflow_api_key: Optional[str] = None
    azure_key_vault_url: Optional[str] = None

    # Inflow Webhooks
    inflow_webhook_enabled: bool = False
    inflow_webhook_secret: Optional[str] = None
    inflow_webhook_url: Optional[str] = None
    inflow_webhook_events: List[str] = ["orderCreated", "orderUpdated"]
    inflow_webhook_auto_register: bool = False  # Auto-register webhook on app startup

    # Inflow Polling Sync (fallback when webhooks are enabled)
    inflow_polling_sync_enabled: bool = True
    inflow_polling_sync_interval_minutes: Optional[int] = None

    # Storage
    storage_root: str = "storage"

    # Local document storage (set TECHUB_DNS_LOCAL_STORAGE to override)
    local_document_storage: str = os.getenv("TECHUB_DNS_LOCAL_STORAGE", "/tmp/techhub-dns")
    picklist_template_path: str = "frontend/public/pdfs/sample.pdf"

    # SharePoint Storage
    sharepoint_enabled: bool = False  # Safety: disabled by default
    sharepoint_site_url: Optional[str] = (
        None  # e.g., https://tamucs.sharepoint.com/teams/Team-TechHub
    )
    sharepoint_folder_path: str = (
        "General/delivery-storage"  # Folder within Documents library
    )
    sharepoint_tenant_id: Optional[str] = None  # Azure AD tenant ID (e.g., from TAMU)
    sharepoint_client_id: Optional[str] = (
        None  # Azure AD app client ID (register in Azure portal)
    )

    # CORS
    frontend_url: str = "http://localhost:5173"
    cors_allowed_origins: Optional[str] = None

    # Auth (structure only)
    secret_key: Optional[str] = None

    # Admin authorization
    # Allowlist of admin emails. See ADMIN_EMAILS env var.
    # IMPORTANT: this is intentionally a raw string to avoid pydantic-settings
    # "complex" env parsing (json.loads) which can crash on empty strings.
    admin_emails: Optional[str] = None
    allowed_user_emails: Optional[str] = None
    picklist_print_agent_token: Optional[str] = None

    # TAMU SMTP Email Configuration
    smtp_enabled: bool = False  # Safety: disabled by default
    smtp_host: str = "relay.tamu.edu"
    smtp_port: int = 587
    smtp_username: Optional[str] = None  # shared_netid@tamu.edu
    smtp_password: Optional[str] = None
    smtp_from_address: Optional[str] = None  # e.g., techhub@tamu.edu
    email_from_name: str = "TechHub Technology Services"

    # Teams Recipient Notifications (Graph API)
    teams_recipient_notifications_enabled: bool = False
    teams_notification_queue_folder: str = (
        "teams-queue"  # Relative to sharepoint_folder_path
    )

    # Shared WebDAV credentials for imported editor workflows
    webdav_username: Optional[str] = None
    webdav_password: Optional[str] = None
    vetting_editor_webdav_username: Optional[str] = None
    vetting_editor_webdav_password: Optional[str] = None

    # ===========================================
    # TAMU Entra ID Authentication (SAML + Service Principal)
    # ===========================================

    # Local development auth bypass
    dev_auth_bypass: bool = False
    dev_auth_email: str = "dev.user@example.com"
    dev_auth_display_name: str = "Local Dev User"
    dev_auth_department: Optional[str] = "Development"

    # SAML Configuration (User Authentication)
    saml_enabled: bool = False
    saml_idp_entity_id: Optional[str] = None  # From Azure: Microsoft Entra Identifier
    saml_idp_sso_url: Optional[str] = None  # From Azure: Login URL
    saml_idp_cert_path: Optional[str] = None  # Path to downloaded certificate file
    saml_sp_entity_id: str = "https://techhub.pythonanywhere.com"
    saml_acs_url: str = "https://techhub.pythonanywhere.com/auth/saml/callback"

    # OIDC Configuration (User Authentication)
    oidc_tenant_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("OIDC_TENANT_ID", "AZURE_TENANT_ID"),
    )
    oidc_client_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("OIDC_CLIENT_ID", "AZURE_CLIENT_ID"),
    )
    oidc_client_secret: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("OIDC_CLIENT_SECRET", "AZURE_CLIENT_SECRET"),
    )

    # Service Principal Configuration (Backend Graph API)
    azure_tenant_id: Optional[str] = None
    azure_client_id: Optional[str] = None  # Service Principal client ID
    azure_client_secret: Optional[str] = None  # Service Principal secret

    # Session Configuration
    session_cookie_name: str = "techhub_session"
    session_max_age_hours: int = 168  # 7 days

    # Traffic-driven maintenance tick (runs only on authenticated /api traffic)
    maintenance_tick_enabled: bool = True
    maintenance_tick_min_interval_seconds: int = 60

    # Lightweight in-process API throttling for admin-heavy endpoints
    rate_limit_window_seconds: int = 60
    admin_read_rate_limit_requests: int = 30
    admin_write_rate_limit_requests: int = 10

    # Background scheduler runner (used by backend/run_scheduler.py)
    scheduler_enabled: bool = False

    maintenance_sessions_purge_interval_hours: int = 24
    maintenance_sessions_purge_batch_size: int = 5000

    maintenance_audit_archive_interval_hours: int = 24
    maintenance_audit_archive_max_batches_per_tick: int = 3

    # System audit archive (used by maintenance_service; optionally env-configured)
    system_audit_archive_days: int = 90
    system_audit_archive_batch_size: int = 1000
    system_audit_archive_retention_days: int = 365

    # Flask environment
    flask_env: str = "development"

    def is_dev(self) -> bool:
        """True when running in development mode."""
        return (self.flask_env or "").strip().lower() == "development"

    def get_admin_emails(self) -> list[str]:
        """Return normalized admin email allowlist.

        Parsing rules:
        - None/empty/whitespace -> []
        - JSON list string (starts with '[') -> parsed list
        - otherwise -> comma-separated list
        """
        return self._parse_admin_emails(self.admin_emails)

    def get_allowed_user_emails(self) -> list[str]:
        """Return normalized app-access allowlist."""
        return self._parse_string_list(self.allowed_user_emails)

    def get_cors_allowed_origins(self) -> list[str]:
        origins = self._parse_string_list(self.cors_allowed_origins)
        if origins:
            return origins

        frontend_url = (self.frontend_url or "").strip()
        if frontend_url:
            return [frontend_url]

        return ["http://localhost:5173"]

    @staticmethod
    def _parse_admin_emails(raw_value: Optional[str]) -> list[str]:
        return Settings._parse_string_list(raw_value)

    @staticmethod
    def _parse_string_list(raw_value: Optional[str]) -> list[str]:
        if raw_value is None:
            return []

        raw = str(raw_value).strip()
        if not raw:
            return []

        items: list[Any]

        if raw.startswith("["):
            parsed: Any = None
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None

            if isinstance(parsed, list):
                items = parsed
            else:
                # Common misconfig: looks like JSON but isn't valid JSON.
                # Fall back to comma-splitting the bracket contents.
                stripped = raw
                if stripped.endswith("]"):
                    stripped = stripped[1:-1]
                items = stripped.split(",")
        else:
            items = raw.split(",")

        normalized: list[str] = []
        for item in items:
            if item is None:
                continue
            email = str(item).strip().lower()
            if not email:
                continue
            normalized.append(email)
        return normalized

    # Canopy Orders Uploader (asset tagging)
    canopyorders_store_base: Optional[str] = None
    canopyorders_dav_root_path: str = "/dav"
    canopyorders_base_dir: str = "/content/canopyorders"
    canopyorders_username: Optional[str] = None
    canopyorders_password: Optional[str] = None
    canopyorders_password_secret_name: str = "ehanson-webdav"
    canopyorders_user_agent: str = "Cyberduck/9.0.0 (Windows 10/10.0) (x86_64) (WebDAV)"
    canopyorders_teams_workflow_url: Optional[str] = None
    canopyorders_teams_shared_secret: Optional[str] = None

    # Vetting Editor (env-only, no Key Vault integration)
    vetting_editor_download_url: Optional[str] = None
    vetting_editor_upload_url: Optional[str] = None

    # Compatibility Editor Staging (env-only, no Key Vault integration)
    compatibility_editor_staging_download_url: Optional[str] = None
    compatibility_editor_staging_upload_url: Optional[str] = None

    # Inventory Reorder Tool (manual admin refresh, env-only)
    inventory_reorder_location_id: str = "e5945760-568d-4f4b-be3c-47d3587851a7"
    inventory_reorder_bigcommerce_base_url: str = "https://api.bigcommerce.com/stores"
    inventory_reorder_bigcommerce_store_id: str = Field(
        default="jsj7fos9p1",
        validation_alias=AliasChoices(
            "INVENTORY_REORDER_BIGCOMMERCE_STORE_ID",
            "BC_STORE_HASH",
            "BC_STORE_ID",
        ),
    )
    inventory_reorder_bigcommerce_token: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "INVENTORY_REORDER_BIGCOMMERCE_TOKEN",
            "BC_ACCESS_TOKEN",
            "BIGCOMMERCE_TOKEN",
            "BC_TOKEN",
        ),
    )
    inventory_reorder_batch_size: int = 100
    inventory_reorder_request_delay_seconds: float = 0.2
    inventory_reorder_refresh_cooldown_seconds: int = 180
    inventory_reorder_scheduled_refresh_enabled: bool = True
    inventory_reorder_scheduled_refresh_times: str = "7:30,12:00,15:00"
    inventory_reorder_scheduled_refresh_timezone: str = "America/Chicago"
    inventory_reorder_teams_notifications_enabled: bool = False
    inventory_reorder_teams_recipient_email: Optional[str] = None
    inventory_reorder_teams_recipient_name: str = "Inventory Team"
    inventory_reorder_teams_minimum_order_quantity: int = 10
    inventory_reorder_bigcommerce_order_lookback_days: int = 7

    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore"
    )

    @field_validator("inflow_webhook_events", mode="before")
    @classmethod
    def parse_inflow_webhook_events(cls, value: Any) -> List[str]:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass
            return [item.strip() for item in raw.split(",") if item.strip()]
        return value


settings = Settings()
