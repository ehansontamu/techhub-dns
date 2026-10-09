"""College/Unit exemptions shared by order preparation and tag requests."""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.database import get_db_session
from app.services.system_setting_service import (
    SETTING_ASSET_TAG_EXEMPT_COLLEGE_UNITS,
    SystemSettingService,
)

logger = logging.getLogger(__name__)


class AssetTagPolicyService:
    @staticmethod
    def college_unit(inflow_data: Any) -> str | None:
        if not isinstance(inflow_data, dict):
            return None
        custom_fields = inflow_data.get("customFields")
        if not isinstance(custom_fields, dict):
            return None
        value = custom_fields.get("custom1")
        if not isinstance(value, str):
            return None
        return " ".join(value.split()) or None

    @staticmethod
    def is_college_unit_exempt(inflow_data: Any, db: Session | None = None) -> bool:
        college_unit = AssetTagPolicyService.college_unit(inflow_data)
        if not college_unit:
            return False

        session = db if db is not None else get_db_session()
        try:
            value = SystemSettingService.get_setting(
                session, SETTING_ASSET_TAG_EXEMPT_COLLEGE_UNITS
            )
            try:
                exempt_units = SystemSettingService.normalize_college_units(value)
            except ValueError:
                logger.warning("Invalid College/Unit tagging exemptions; retaining product tagging rules")
                return False
            return college_unit.casefold() in {unit.casefold() for unit in exempt_units}
        finally:
            if db is None:
                session.close()
