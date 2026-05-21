import apiClient from "./client";
import { normalizeExpectedUpdatedAt } from "./expectedUpdatedAt";

function safeArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? value : [];
}

export interface CreateDeliveryRunRequest {
  order_ids: string[];
  vehicle: "van" | "golf_cart";
}

export interface RecallOrderRequest {
  reason: string;
  expected_updated_at?: string;
}

export interface DeliveryRunResponse {
  id: string;
  name: string;
  runner: string;
  vehicle: string;
  status: string;
  start_time: string | null;
  end_time?: string | null;
  updated_at?: string | null;
}

export interface OrderSummary {
  id: string;
  inflow_order_id: string | null;
  recipient_name: string | null;
  delivery_location: string | null;
  status: string;
  delivery_sequence?: number | null;
}

export interface DeliveryRunDetailResponse extends DeliveryRunResponse {
  orders: OrderSummary[];
}

export const deliveryRunsApi = {
  getActiveRuns: async (): Promise<Array<DeliveryRunResponse & { order_ids: string[] }>> => {
    const response = await apiClient.get<Array<DeliveryRunResponse & { order_ids: string[] }>>("/delivery-runs/active");
    return safeArray<DeliveryRunResponse & { order_ids: string[] }>(response.data);
  },

  createRun: async (request: CreateDeliveryRunRequest): Promise<DeliveryRunResponse> => {
    const response = await apiClient.post<DeliveryRunResponse>("/delivery-runs", request);
    return response.data;
  },

  getRun: async (runId: string): Promise<DeliveryRunDetailResponse> => {
    const response = await apiClient.get<DeliveryRunDetailResponse>(`/delivery-runs/${runId}`);
    return response.data;
  },

  finishRun: async (
    runId: string,
    createRemainders: boolean = true,
    expectedUpdatedAt?: string | null
  ): Promise<DeliveryRunResponse> => {
    const response = await apiClient.put<DeliveryRunResponse>(
      `/delivery-runs/${runId}/finish`,
      normalizeExpectedUpdatedAt({
        create_remainders: createRemainders,
        expected_updated_at: expectedUpdatedAt ?? undefined,
      })
    );
    return response.data;
  },

  recallOrder: async (
    runId: string,
    orderId: string,
    reason: string,
    expectedUpdatedAt?: string | null
  ): Promise<DeliveryRunResponse> => {
    const response = await apiClient.put<DeliveryRunResponse>(
      `/delivery-runs/${runId}/orders/${orderId}/recall`,
      normalizeExpectedUpdatedAt({
        reason,
        expected_updated_at: expectedUpdatedAt ?? undefined,
      } satisfies RecallOrderRequest)
    );
    return response.data;
  },

  reorderOrders: async (
    runId: string,
    orderIds: string[],
    expectedUpdatedAt?: string | null
  ): Promise<DeliveryRunResponse> => {
    const response = await apiClient.put<DeliveryRunResponse>(
      `/delivery-runs/${runId}/orders/reorder`,
      normalizeExpectedUpdatedAt({
        order_ids: orderIds,
        expected_updated_at: expectedUpdatedAt ?? undefined,
      }),
    );
    return response.data;
  },

  getRuns: async (query?: { status?: string[]; vehicle?: "van" | "golf_cart" }): Promise<DeliveryRunResponse[]> => {
    const params = new URLSearchParams();
    if (query?.status) {
      query.status.forEach((s) => params.append("status", s));
    }
    if (query?.vehicle) {
      params.append("vehicle", query.vehicle);
    }

    const response = await apiClient.get<DeliveryRunResponse[]>("/delivery-runs", { params });
    return response.data;
  },
};
