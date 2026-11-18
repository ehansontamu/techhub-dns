"""Shipping contracts. Package weights are calculated on the server in pounds."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ShippingSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class ProductWeightUpdate(ShippingSchema):
    name: str = Field(min_length=1, max_length=500)
    sku: str = Field(default="", max_length=255)
    weight_lb: float = Field(gt=0, le=150)


class ShipmentRecipient(ShippingSchema):
    name: str = Field(min_length=1, max_length=70)
    company: str = Field(default="", max_length=70)
    phone: str = Field(min_length=10, max_length=20, pattern=r"^[0-9+(). -]+$")
    address1: str = Field(min_length=1, max_length=35)
    address2: str = Field(default="", max_length=35)
    city: str = Field(min_length=1, max_length=35)
    state: str = Field(pattern=r"^[A-Z]{2}$")
    postal_code: str = Field(pattern=r"^\d{5}(-\d{4})?$")
    country: Literal["US"] = "US"
    residential: bool = False


class PackedItem(ShippingSchema):
    order_id: str | None = Field(default=None, max_length=36)
    product_id: str = Field(min_length=1, max_length=255)
    quantity: int = Field(strict=True, gt=0, le=10000)


class ShipmentBox(ShippingSchema):
    name: str = Field(min_length=1, max_length=80)
    use_as_box: bool = False
    items: list[PackedItem] = Field(min_length=1, max_length=200)


ServiceType = Literal["FEDEX_GROUND", "GROUND_HOME_DELIVERY", "FEDEX_EXPRESS_SAVER", "FEDEX_2_DAY", "FEDEX_2_DAY_AM",
                      "STANDARD_OVERNIGHT", "PRIORITY_OVERNIGHT", "FIRST_OVERNIGHT"]


class PackingDraft(ShippingSchema):
    expected_revision: int = Field(strict=True, ge=0)
    order_ids: list[str] = Field(default_factory=list, max_length=50)
    recipient: ShipmentRecipient
    contact_id: str | None = Field(default=None, max_length=64)
    manual_address_override: bool = Field(default=False, strict=True)
    service_type: ServiceType = "FEDEX_GROUND"
    boxes: list[ShipmentBox] = Field(min_length=1, max_length=40)


class ShipmentCreate(ShippingSchema):
    expected_revision: int = Field(strict=True, gt=0)



class ShipmentCreationRequest(ShipmentCreate):
    confirm_high_shipping_cost: bool = Field(default=False, strict=True)
