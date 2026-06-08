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
  latest_sync?: {
    completed_at: string | null;
    status: string;
    error?: string | null;
  } | null;
  order_count: number;
  line_item_count: number;
  product_count?: number;
  variant_count?: number;
  product_intelligence_count?: number;
  product_intelligence_price_row_count?: number;
  catalog_tables_available?: boolean;
  product_intelligence_tables_available?: boolean;
  last_catalog_sync?: Record<string, unknown> | null;
  last_product_intelligence_sync?: Record<string, unknown> | null;
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

  async syncCatalog(maxProducts = 5000): Promise<{
    brands_upserted: number;
    categories_upserted: number;
    products_upserted: number;
    variants_upserted: number;
  }> {
    const response = await apiClient.post("/bigcommerce-chat/cache/catalog-sync", {
      max_products: maxProducts,
    });
    return response.data;
  },

  async syncProductIntelligence(): Promise<{
    items_upserted: number;
    items_deleted: number;
    price_rows_upserted: number;
    source_url: string;
    synced_at: string;
  }> {
    const response = await apiClient.post("/bigcommerce-chat/cache/product-intelligence-sync");
    return response.data;
  },
};
