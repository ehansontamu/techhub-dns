from pydantic import BaseModel, Field, field_serializer
from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID
from app.models.order import OrderStatus, ShippingWorkflowStatus
from app.utils.timezone import to_utc_iso_z


def _serialize_datetime_utc(value):
    if isinstance(value, datetime):
        return to_utc_iso_z(value)

    return value


class OrderBase(BaseModel):
    inflow_order_id: str
    recipient_name: Optional[str] = None
    recipient_contact: Optional[str] = None
    delivery_location: Optional[str] = None
    po_number: Optional[str] = None
    assigned_deliverer: Optional[str] = None


class OrderCreate(OrderBase):
    inflow_sales_order_id: Optional[str] = None
    inflow_data: Optional[Dict[str, Any]] = None


class PickStatusItem(BaseModel):
    """Individual item that was not fully picked"""

    product_id: str
    product_name: str
    ordered: int
    picked: int


class PickStatus(BaseModel):
    """Pick status for an order - indicates if order is fully picked"""

    is_fully_picked: bool
    total_ordered: int
    total_picked: int
    missing_items: List[PickStatusItem] = []


class OrderUpdate(BaseModel):
    recipient_name: Optional[str] = None
    recipient_contact: Optional[str] = None
    delivery_location: Optional[str] = None
    assigned_deliverer: Optional[str] = None
    issue_reason: Optional[str] = None
    expected_updated_at: Optional[datetime] = None


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
    reason: Optional[str] = None
    expected_updated_at: Optional[datetime] = None


class OrderRollbackUpdate(BaseModel):
    status: OrderStatus
    reason: Optional[str] = None
    expected_updated_at: Optional[datetime] = None


class AssetTagUpdate(BaseModel):
    tag_ids: List[str] = Field(default_factory=list)
    technician: Optional[str] = None
    expected_updated_at: Optional[datetime] = None


class PicklistGenerationRequest(BaseModel):
    generated_by: Optional[str] = None
    expected_updated_at: Optional[datetime] = None
    create_partial_leg: bool = False
    confirm_create_partial_leg: bool = False


class QASubmission(BaseModel):
    responses: Dict[str, Any] = Field(default_factory=dict)
    technician: Optional[str] = None
    expected_updated_at: Optional[datetime] = None


class SignaturePlacement(BaseModel):
    page_number: int = 1
    x: float
    y: float
    width: float
    height: float


class SignatureData(BaseModel):
    signature_image: str  # Base64 encoded PNG
    placements: List[SignaturePlacement] = Field(default_factory=list)
    expected_updated_at: Optional[datetime] = None
    page_width: Optional[float] = None  # Frontend PDF.js viewport width — used for overlay sizing
    page_height: Optional[float] = None  # Frontend PDF.js viewport height
    # Backward compatibility (optional)
    page_number: Optional[int] = None
    position: Optional[Dict[str, float]] = None


class PrintJobSummary(BaseModel):
    id: UUID
    document_type: str
    status: str
    trigger_source: str
    requested_by: Optional[str] = None
    attempt_count: int = 0
    claimed_at: Optional[datetime] = None
    claim_expires_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("*", when_used="json", check_fields=False)
    def _serialize_datetimes(self, value):
        return _serialize_datetime_utc(value)


class OrderResponse(OrderBase):
    id: UUID
    inflow_sales_order_id: Optional[str] = None
    status: OrderStatus
    issue_reason: Optional[str] = None
    tagged_at: Optional[datetime] = None
    tagged_by: Optional[str] = None
    tag_data: Optional[Dict[str, Any]] = None
    picklist_generated_at: Optional[datetime] = None
    picklist_generated_by: Optional[str] = None
    picklist_path: Optional[str] = None
    qa_completed_at: Optional[datetime] = None
    qa_completed_by: Optional[str] = None
    qa_data: Optional[Dict[str, Any]] = None
    qa_path: Optional[str] = None
    qa_method: Optional[str] = None
    signature_captured_at: Optional[datetime] = None
    signed_picklist_path: Optional[str] = None
    parent_order_id: Optional[str] = None
    has_remainder: Optional[str] = None
    remainder_order_id: Optional[str] = None
    shipping_workflow_status: Optional[ShippingWorkflowStatus] = None
    shipping_workflow_status_updated_at: Optional[datetime] = None
    shipping_workflow_status_updated_by: Optional[str] = None
    shipped_to_carrier_at: Optional[datetime] = None
    shipped_to_carrier_by: Optional[str] = None
    carrier_name: Optional[str] = None
    tracking_number: Optional[str] = None
    pick_status: Optional[PickStatus] = None
    latest_picklist_print_job: Optional[PrintJobSummary] = None
    delivery_run_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("status")
    def serialize_status(self, value):
        return value

    @field_serializer("shipping_workflow_status")
    def serialize_shipping_workflow_status(self, value):
        return value

    @field_serializer("*", when_used="json", check_fields=False)
    def _serialize_datetimes(self, value):
        return _serialize_datetime_utc(value)


class OrderDetailResponse(OrderResponse):
    inflow_data: Optional[Dict[str, Any]] = None
    asset_tag_serials: Optional[List[Dict[str, Any]]] = None
    asset_tag_required: Optional[bool] = None
    parent_inflow_order_id: Optional[str] = None
    remainder_inflow_order_id: Optional[str] = None


class BulkStatusUpdate(BaseModel):
    order_ids: List[UUID]
    status: OrderStatus
    reason: Optional[str] = None


class ShippingWorkflowUpdateRequest(BaseModel):
    status: ShippingWorkflowStatus
    carrier_name: Optional[str] = None
    tracking_number: Optional[str] = None
    updated_by: Optional[str] = None
    expected_updated_at: Optional[datetime] = None


class ShippingWorkflowResponse(BaseModel):
    shipping_workflow_status: Optional[str] = None
    shipping_workflow_status_updated_at: Optional[datetime] = None
    shipping_workflow_status_updated_by: Optional[str] = None
    shipped_to_carrier_at: Optional[datetime] = None
    shipped_to_carrier_by: Optional[str] = None
    carrier_name: Optional[str] = None
    tracking_number: Optional[str] = None

    @field_serializer("*", when_used="json", check_fields=False)
    def _serialize_datetimes(self, value):
        return _serialize_datetime_utc(value)
