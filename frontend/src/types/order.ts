export enum OrderStatus {
  PICKED = "picked",
  QA = "qa",
  PRE_DELIVERY = "pre-delivery",
  IN_DELIVERY = "in-delivery",
  SHIPPING = "shipping",
  DELIVERED = "delivered",
  ISSUE = "issue",
}

export enum ShippingWorkflowStatus {
  WORK_AREA = "work_area",
  DOCK = "dock",
  SHIPPED = "shipped",
}

export const ShippingWorkflowStatusDisplayNames: Record<ShippingWorkflowStatus, string> = {
  [ShippingWorkflowStatus.WORK_AREA]: "Work Area",
  [ShippingWorkflowStatus.DOCK]: "At Dock",
  [ShippingWorkflowStatus.SHIPPED]: "Shipped to Carrier",
};

export const OrderStatusDisplayNames: Record<OrderStatus, string> = {
  [OrderStatus.PICKED]: "Picked",
  [OrderStatus.QA]: "QA",
  [OrderStatus.PRE_DELIVERY]: "Pre-Delivery",
  [OrderStatus.IN_DELIVERY]: "In Delivery",
  [OrderStatus.SHIPPING]: "Shipping",
  [OrderStatus.DELIVERED]: "Delivered",
  [OrderStatus.ISSUE]: "Issue",
};

export interface PickStatusItem {
  product_id: string;
  product_name: string;
  ordered: number;
  picked: number;
}

export interface PickStatus {
  is_fully_picked: boolean;
  total_ordered: number;
  total_picked: number;
  missing_items: PickStatusItem[];
}

export interface PrintJobSummary {
  id: string;
  document_type: string;
  status: string;
  trigger_source: string;
  requested_by?: string;
  attempt_count: number;
  claimed_at?: string;
  claim_expires_at?: string;
  completed_at?: string;
  last_error?: string;
  created_at: string;
  updated_at: string;
}

export interface Order {
  id: string;
  inflow_order_id: string;
  inflow_sales_order_id?: string;
  recipient_name?: string;
  recipient_contact?: string;
  delivery_location?: string;
  po_number?: string;
  status: OrderStatus;
  assigned_deliverer?: string;
  delivery_run_id?: string;
  delivery_sequence?: number | null;
  issue_reason?: string;
  tagged_at?: string;
  tagged_by?: string;
  tag_data?: TagData;
  picklist_generated_at?: string;
  picklist_generated_by?: string;
  picklist_path?: string;
  order_details_path?: string;
  order_details_generated_at?: string;
  order_details_email_status?: "sent" | "not_sent" | "failed";
  order_details_email_status_updated_at?: string;
  qa_completed_at?: string;
  qa_completed_by?: string;
  qa_data?: Record<string, unknown>;
  qa_path?: string;
  qa_method?: string;
  signature_captured_at?: string;
  signed_picklist_path?: string;
  bundle_path?: string;
  // Shipping workflow fields
  shipping_workflow_status?: ShippingWorkflowStatus;
  shipping_workflow_status_updated_at?: string;
  shipping_workflow_status_updated_by?: string;
  shipped_to_carrier_at?: string;
  shipped_to_carrier_by?: string;
  carrier_name?: string;
  tracking_number?: string;
  parent_order_id?: string;
  parent_inflow_order_id?: string;
  has_remainder?: string | boolean | null;
  remainder_order_id?: string | null;
  remainder_inflow_order_id?: string | null;
  inflow_data?: Record<string, unknown>;
  asset_tag_required?: boolean;
  pick_status?: PickStatus;
  latest_picklist_print_job?: PrintJobSummary;
  asset_tag_serials?: AssetTagSerial[];
  created_at: string;
  updated_at: string;
}

export interface TagData {
  tag_ids?: string[];
  tag_request_sent_at?: string;
  tag_request_filename?: string;
  tag_request_status?: string;
  canopyorders_request_sent_at?: string;
  canopyorders_request_filename?: string;
  canopyorders_request_uploaded_url?: string;
  canopyorders_request_sent_by?: string;
  [key: string]: any;
}

export interface OrderDetail extends Order {
  inflow_data?: Record<string, unknown>;
  asset_tag_required?: boolean;
  teams_notifications?: TeamsNotification[];
}

export interface AssetTagSerial {
  product_id?: string;
  product_name: string;
  category_id?: string;
  category_name?: string;
  serials: string[];
}

export interface OrderStatusUpdate {
  status: OrderStatus;
  reason?: string;
  expected_updated_at?: string;
}

export interface OrderRollbackUpdate {
  status: OrderStatus;
  reason?: string;
  expected_updated_at?: string;
}

export interface OrderMutationPayload {
  expected_updated_at?: string;
}

export interface OrderDismissUpdate {
  reason: string;
  remove_sharepoint_files?: boolean;
  expected_updated_at?: string;
}

export interface BulkStatusUpdate {
  order_ids: string[];
  status: OrderStatus;
  reason?: string;
}

export interface AuditLog {
  id: string;
  order_id: string;
  changed_by?: string;
  from_status?: string;
  to_status: string;
  reason?: string;
  timestamp: string;
  metadata?: Record<string, unknown>;
}

export interface TeamsNotification {
  id: string;
  order_id: string;
  teams_message_id?: string;
  sent_at?: string;
  status: "pending" | "sent" | "failed";
  notification_type?: string;
  error_message?: string;
  retry_count: number;
  created_at: string;
}

export interface TeamsConfig {
  webhook_url?: string;
  updated_at: string;
  updated_by?: string;
}

export interface InflowSyncResponse {
  success: boolean;
  orders_synced: number;
  orders_created: number;
  orders_updated: number;
  message: string;
}
