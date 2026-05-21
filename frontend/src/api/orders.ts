import apiClient from "./client";
import { Order, OrderDetail, OrderStatus, OrderStatusUpdate, OrderRollbackUpdate, BulkStatusUpdate, AuditLog, ShippingWorkflowStatus } from "../types/order";
import { normalizeExpectedUpdatedAt } from "./expectedUpdatedAt";

function safeArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? value : [];
}

export const ordersApi = {
  getOrders: async (params?: { status?: OrderStatus; search?: string; skip?: number; limit?: number }): Promise<{ items: Order[]; total: number }> => {
    const response = await apiClient.get<{ items?: Order[]; total?: number; skip?: number; limit?: number }>("/orders", { params });
    return { items: safeArray<Order>(response.data?.items), total: response.data?.total ?? 0 };
  },

  getOrder: async (orderId: string): Promise<OrderDetail> => {
    const response = await apiClient.get<OrderDetail>(`/orders/${orderId}`);
    return response.data;
  },

  getTagRequestCandidates: async (params?: { limit?: number; search?: string }): Promise<Order[]> => {
    const response = await apiClient.get<Order[]>("/orders/tag-request/candidates", { params });
    return response.data;
  },

  updateOrderStatus: async (orderId: string, update: OrderStatusUpdate, changedBy?: string): Promise<Order> => {
    const response = await apiClient.patch<Order>(
      `/orders/${orderId}/status`,
      normalizeExpectedUpdatedAt(update),
      {
        params: changedBy ? { changed_by: changedBy } : undefined,
      }
    );
    return response.data;
  },

  rollbackOrderStatus: async (orderId: string, update: OrderRollbackUpdate, changedBy?: string): Promise<Order> => {
    const response = await apiClient.patch<Order>(
      `/orders/${orderId}/rollback`,
      normalizeExpectedUpdatedAt(update),
      {
        params: changedBy ? { changed_by: changedBy } : undefined,
      }
    );
    return response.data;
  },

  updateOrder: async (
    orderId: string,
    update: {
      recipient_name?: string;
      recipient_contact?: string;
      delivery_location?: string;
      assigned_deliverer?: string;
      issue_reason?: string;
      expected_updated_at?: string;
    },
  ): Promise<Order> => {
    const response = await apiClient.patch<Order>(`/orders/${orderId}`, normalizeExpectedUpdatedAt(update));
    return response.data;
  },

  bulkUpdateStatus: async (payload: BulkStatusUpdate): Promise<Order[]> => {
    const response = await apiClient.post<Order[]>("/orders/bulk-transition", payload);
    return response.data;
  },

  getOrderAudit: async (orderId: string): Promise<AuditLog[]> => {
    const response = await apiClient.get<AuditLog[]>(`/orders/${orderId}/audit`);
    return response.data;
  },

  resolveOrder: async (orderNumber: string): Promise<{ id: string; order_number: string }> => {
    const response = await apiClient.get<{ id: string; order_number: string }>("/orders/resolve", {
      params: { order_number: orderNumber },
    });
    return response.data;
  },

  tagOrder: async (orderId: string, payload: { tag_ids: string[]; technician?: string; expected_updated_at?: string }) => {
    const response = await apiClient.post<Order>(`/orders/${orderId}/tag`, normalizeExpectedUpdatedAt(payload));
    return response.data;
  },

  generatePicklist: async (
    orderId: string,
    payload?: {
      generated_by?: string;
      expected_updated_at?: string;
      create_partial_leg?: boolean;
      confirm_create_partial_leg?: boolean;
    }
  ) => {
    const response = await apiClient.post<Order>(
      `/orders/${orderId}/picklist`,
      normalizeExpectedUpdatedAt(payload || {}),
    );
    return response.data;
  },

  submitQa: async (
    orderId: string,
    payload: { responses: Record<string, unknown>; technician?: string; expected_updated_at?: string }
  ) => {
    const response = await apiClient.post<Order>(`/orders/${orderId}/qa`, normalizeExpectedUpdatedAt(payload));
    return response.data;
  },

  signOrder: async (orderId: string, signatureData?: {
    signature_image: string;
    placements?: Array<{ page_number: number; x: number; y: number; width: number; height: number }>;
    expected_updated_at?: string;
    page_number?: number;
    position?: { x: number; y: number }
  }) => {
    const response = await apiClient.post<{ success: boolean; message: string; bundled_document_path?: string }>(
      `/orders/${orderId}/sign`,
      signatureData ? normalizeExpectedUpdatedAt(signatureData) : signatureData,
    );
    return response.data;
  },

  updateShippingWorkflow: async (
    orderId: string,
    payload: {
      status: ShippingWorkflowStatus;
      carrier_name?: string;
      tracking_number?: string;
      updated_by?: string;
      expected_updated_at?: string;
    }
  ): Promise<Order> => {
    const response = await apiClient.patch<Order>(`/orders/${orderId}/shipping-workflow`, normalizeExpectedUpdatedAt(payload));
    return response.data;
  },

  getShippingWorkflow: async (orderId: string) => {
    const response = await apiClient.get<{
      shipping_workflow_status?: string;
      shipping_workflow_status_updated_at?: string;
      shipping_workflow_status_updated_by?: string;
      shipped_to_carrier_at?: string;
      shipped_to_carrier_by?: string;
      carrier_name?: string;
      tracking_number?: string;
    }>(`/orders/${orderId}/shipping-workflow`);
    return response.data;
  },
};
