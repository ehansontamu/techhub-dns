from pydantic import BaseModel, Field, field_serializer, field_validator
from typing import List, Optional
from datetime import datetime
from uuid import UUID

from app.models.delivery_run import VehicleEnum, DeliveryRunStatus
from app.utils.timezone import to_utc_iso_z


def _serialize_datetime_utc(value):
    if isinstance(value, datetime):
        return to_utc_iso_z(value)

    return value


class CreateDeliveryRunRequest(BaseModel):
    # Deprecated/ignored: identity is derived from the authenticated session.
    runner: Optional[str] = None
    order_ids: List[UUID]
    vehicle: str

    @field_validator("vehicle")
    @classmethod
    def validate_vehicle(cls, v):
        allowed_vehicles = ["van", "golf_cart", "pickup"]
        if v not in allowed_vehicles:
            raise ValueError(f"Vehicle must be one of: {allowed_vehicles}")
        return v


class DeliveryRunResponse(BaseModel):
    id: UUID
    name: str
    runner: str
    vehicle: str
    status: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    order_ids: List[UUID] = Field(default_factory=list)

    model_config = {"from_attributes": True}

    @field_serializer("*", when_used="json", check_fields=False)
    def _serialize_datetimes(self, value):
        return _serialize_datetime_utc(value)


class FinishDeliveryRunRequest(BaseModel):
    create_remainders: bool = True
    expected_updated_at: Optional[datetime] = None


class RecallDeliveryRunOrderRequest(BaseModel):
    reason: str = Field(min_length=1)
    expected_updated_at: Optional[datetime] = None


class AppendDeliveryRunOrdersRequest(BaseModel):
    order_ids: List[UUID] = Field(min_length=1)
    expected_updated_at: Optional[datetime] = None


class ReorderDeliveryRunOrdersRequest(BaseModel):
    order_ids: List[UUID] = Field(default_factory=list)
    expected_updated_at: Optional[datetime] = None


class OrderSummary(BaseModel):
    id: UUID
    inflow_order_id: Optional[str] = None
    recipient_name: Optional[str] = None
    delivery_location: Optional[str] = None
    status: str
    delivery_sequence: Optional[int] = None

    model_config = {"from_attributes": True}

    @field_serializer("*", when_used="json", check_fields=False)
    def _serialize_datetimes(self, value):
        return _serialize_datetime_utc(value)


class DeliveryRunDetailResponse(BaseModel):
    id: UUID
    name: str
    runner: str
    vehicle: str
    status: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    orders: List[OrderSummary] = Field(default_factory=list)

    model_config = {"from_attributes": True}

    @field_serializer("*", when_used="json", check_fields=False)
    def _serialize_datetimes(self, value):
        return _serialize_datetime_utc(value)
