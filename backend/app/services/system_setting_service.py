from sqlalchemy.orm import Session
from typing import Optional, Dict, Any

from app.database import get_db_session
from app.models.system_setting import SystemSetting
from app.utils.timezone import to_utc_iso_z

SETTING_TYPE_BOOLEAN = "boolean"
SETTING_TYPE_INTEGER = "integer"
SETTING_TYPE_JSON = "json"
SETTING_TYPE_STRING = "string"

# Notification / integration toggles
SETTING_EMAIL_ENABLED = "email_notifications_enabled"
SETTING_TEAMS_RECIPIENT_ENABLED = "teams_recipient_notifications_enabled"
SETTING_DOCUMENT_SIGNING_ENABLED = "document_signing_enabled"

# Admin allowlist
SETTING_ADMIN_EMAILS = "admin_emails"

# Picklist / tagging workflow controls
SETTING_PICKLIST_AUTO_PRINT_ENABLED = "picklist_auto_print_enabled"
SETTING_REQUIRE_ASSET_TAGS_BEFORE_PICKLIST = "require_asset_tags_before_picklist"
SETTING_REQUIRE_SAME_USER_FOR_TAGGING_AND_PICKLIST = "require_same_user_for_tagging_and_picklist"
SETTING_REQUIRE_PARTIAL_PICKLIST_CONFIRMATION = "require_partial_picklist_confirmation"

# Queue policy controls
SETTING_PICKLIST_PRINT_CLAIM_TIMEOUT_SECONDS = "picklist_print_claim_timeout_seconds"

DEFAULT_SETTINGS = {
    SETTING_EMAIL_ENABLED: {
        "value": "true",
        "type": SETTING_TYPE_BOOLEAN,
        "description": "Enable sending email notifications (Order Details PDFs)",
    },
    SETTING_TEAMS_RECIPIENT_ENABLED: {
        "value": "false",
        "type": SETTING_TYPE_BOOLEAN,
        "description": "Enable sending delivery notifications to recipients via Teams",
    },
    SETTING_DOCUMENT_SIGNING_ENABLED: {
        "value": "true",
        "type": SETTING_TYPE_BOOLEAN,
        "description": "Enable the document signing workflow for delivered orders",
    },
    SETTING_ADMIN_EMAILS: {
        "value": "[]",
        "type": SETTING_TYPE_JSON,
        "description": "Admin email allowlist (JSON array string preferred; CSV accepted)",
    },
    SETTING_PICKLIST_AUTO_PRINT_ENABLED: {
        "value": "false",
        "type": SETTING_TYPE_BOOLEAN,
        "description": "Automatically queue the first generated picklist for ops printing",
    },
    SETTING_REQUIRE_ASSET_TAGS_BEFORE_PICKLIST: {
        "value": "true",
        "type": SETTING_TYPE_BOOLEAN,
        "description": "Require asset tags before an order can proceed through picklist generation",
    },
    SETTING_REQUIRE_SAME_USER_FOR_TAGGING_AND_PICKLIST: {
        "value": "true",
        "type": SETTING_TYPE_BOOLEAN,
        "description": "Require the same user to perform asset tagging and picklist generation",
    },
    SETTING_REQUIRE_PARTIAL_PICKLIST_CONFIRMATION: {
        "value": "true",
        "type": SETTING_TYPE_BOOLEAN,
        "description": "Require a manual confirmation before creating a partial-leg picklist",
    },
    SETTING_PICKLIST_PRINT_CLAIM_TIMEOUT_SECONDS: {
        "value": "300",
        "type": SETTING_TYPE_INTEGER,
        "description": "Seconds a claimed picklist print job remains reserved before it can be reclaimed",
    },
}


class SystemSettingService:
    @staticmethod
    def get_setting(db: Session, key: str) -> str:
        """Get a setting value from DB, or default if not set."""
        setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if setting:
            return setting.value
        return DEFAULT_SETTINGS.get(key, {}).get("value", "false")

    @staticmethod
    def set_setting(
        key: str, value: str, updated_by: Optional[str] = None
    ) -> SystemSetting:
        """Set a setting value in the DB."""
        db = get_db_session()
        try:
            setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
            if not setting:
                setting = SystemSetting(
                    key=key,
                    value=value,
                    description=DEFAULT_SETTINGS.get(key, {}).get("description"),
                    updated_by=updated_by,
                )
                db.add(setting)
            else:
                setting.value = value
                setting.updated_by = updated_by
            db.commit()
            db.refresh(setting)
            return setting
        finally:
            db.close()

    @staticmethod
    def is_setting_enabled(key: str) -> bool:
        """Check if a boolean setting is enabled."""
        db = get_db_session()
        try:
            value = SystemSettingService.get_setting(db, key)
            return value.lower() in ("true", "1", "yes", "on")
        finally:
            db.close()

    @staticmethod
    def get_all_settings() -> dict:
        """Get all settings with their metadata."""
        db = get_db_session()
        try:
            result = {}
            for key, defaults in DEFAULT_SETTINGS.items():
                setting = (
                    db.query(SystemSetting).filter(SystemSetting.key == key).first()
                )
                result[key] = {
                    "value": setting.value if setting else defaults["value"],
                    "type": defaults.get("type", SETTING_TYPE_STRING),
                    "description": defaults["description"],
                    "updated_at": to_utc_iso_z(setting.updated_at) if setting else None,
                    "updated_by": setting.updated_by if setting else None,
                }
            return result
        finally:
            db.close()
