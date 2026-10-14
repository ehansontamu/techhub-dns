from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.delivery_run import VehicleEnum


def _allowed_vehicles() -> list[str]:
    return [v.value for v in VehicleEnum]


class CheckoutRequest(BaseModel):
    vehicle: str
    # Deprecated/ignored: identity is derived from the authenticated session.
    checked_out_by: Optional[str] = None
    checkout_type: Literal["delivery_run", "other"] = "delivery_run"
    purpose: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("vehicle")
    @classmethod
    def validate_vehicle(cls, v: str) -> str:
        allowed = _allowed_vehicles()
        v_norm = (v or "").strip()
        if v_norm not in allowed:
            raise ValueError(f"Vehicle must be one of: {allowed}")
        return v_norm

    @field_validator("checked_out_by")
    @classmethod
    def validate_checked_out_by(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = v.strip()
        return normalized or None

    @field_validator("checkout_type")
    @classmethod
    def validate_checkout_type(cls, v: str) -> str:
        v_norm = (v or "").strip()
        if v_norm not in {"delivery_run", "other"}:
            raise ValueError("checkout_type must be 'delivery_run' or 'other'")
        return v_norm

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = v.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_purpose_required_for_other(self) -> "CheckoutRequest":
        if self.checkout_type == "other" and not (self.purpose or "").strip():
            raise ValueError("purpose is required when checkout_type is 'other'")
        return self

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = v.strip()
        return normalized or None


class CheckinRequest(BaseModel):
    vehicle: str
    checked_in_by: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("vehicle")
    @classmethod
    def validate_vehicle(cls, v: str) -> str:
        allowed = _allowed_vehicles()
        v_norm = (v or "").strip()
        if v_norm not in allowed:
            raise ValueError(f"Vehicle must be one of: {allowed}")
        return v_norm

    @field_validator("checked_in_by")
    @classmethod
    def validate_checked_in_by(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = v.strip()
        return normalized or None

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = v.strip()
        return normalized or None


class ForceReturnRequest(CheckinRequest):
    checkout_id: UUID
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value):
        return value.strip() if isinstance(value, str) else value


class VehicleCheckoutResponse(BaseModel):
    id: UUID
    vehicle: str
    checked_out_by: str
    checked_out_by_email: Optional[str] = None
    checked_out_by_user_id: Optional[str] = None
    checkout_type: str
    purpose: Optional[str] = None
    checked_out_at: datetime
    checked_in_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class VehicleStatusItem(BaseModel):
    vehicle: str
    checked_out: bool
    checked_out_by: Optional[str] = None
    checked_out_by_user_id: Optional[str] = None
    checkout_type: Optional[str] = None
    purpose: Optional[str] = None
    checked_out_at: Optional[datetime] = None
    delivery_run_active: bool


class VehicleStatusResponse(BaseModel):
    vehicles: List[VehicleStatusItem] = Field(default_factory=list)
