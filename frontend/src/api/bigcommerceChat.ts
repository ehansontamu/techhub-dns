import { apiClient } from "./client";

export type BigCommerceChatRole = "user" | "assistant";

export type BigCommerceChartType = "line" | "bar" | "pie";
export type BigCommerceChartValueKind = "number" | "percent" | "currency";

export interface BigCommerceChartSeries {
  key: string;
  label: string;
}

export interface BigCommerceChartData {
  type: BigCommerceChartType;
  title?: string;
  xKey: string;
  series: BigCommerceChartSeries[];
  data: Record<string, string | number | null>[];
  valueKind?: BigCommerceChartValueKind;
}

export interface BigCommerceChatMessage {
  role: BigCommerceChatRole;
  content: string;
  chart?: BigCommerceChartData | null;
}

export interface BigCommerceChatResponse {
  answer: string;
  chart?: BigCommerceChartData | null;
  messages: BigCommerceChatMessage[];
}

export interface BigCommerceCacheStatus {
  last_successful_sync: {
    completed_at: string | null;
    status: string;
    orders_upserted: number;
  } | null;
  order_count: number;
  line_item_count: number;
  latest_order_modified_at: string | null;
  is_stale: boolean;
  stale_after_minutes: number;
}

export const bigcommerceChatApi = {
  async ask(
    question: string,
    messages: BigCommerceChatMessage[]
  ): Promise<BigCommerceChatResponse> {
    const response = await apiClient.post("/bigcommerce-chat", {
      question,
      messages,
    });
    return response.data;
  },

  async cacheStatus(): Promise<BigCommerceCacheStatus> {
    const response = await apiClient.get("/bigcommerce-chat/cache/status");
    return response.data;
  },
};
