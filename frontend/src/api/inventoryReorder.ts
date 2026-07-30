import { apiClient } from "./client";

export type InventoryReorderJobStatus = "queued" | "running" | "done" | "error";

export interface InventoryReorderJob {
  job_id: string;
  status: InventoryReorderJobStatus;
  progress: number;
  message: string;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  result_path: string | null;
  trigger?: "manual" | "scheduled" | string;
}

export interface InventoryReorderConfigStatus {
  configured: boolean;
  missing: string[];
  scheduled_refresh: {
    enabled: boolean;
    times?: string;
    hours?: string;
    timezone: string;
  };
}

export interface InventoryReorderCooldown {
  active: boolean;
  cooldown_seconds: number;
  remaining_seconds: number;
  ends_at: string | null;
}

export interface InventoryReorderRow {
  name: string;
  sku: string;
  quantityAvailable: string;
  quantityOnOrder: string;
  bigCommerceStatus9: string;
  available: number;
  status9: number;
  finalQty: number;
  onOrder: number;
  combined: number;
  reorderPoint: number;
  reorderQty: number;
  needsReorder: boolean;
  critical: boolean;
  [key: string]: unknown;
}

export interface InventoryReorderSummary {
  total: number;
  needs_reorder: number;
  critical: number;
}

export interface InventoryReorderResponse {
  rows: InventoryReorderRow[];
  summary: InventoryReorderSummary;
  latest_job: InventoryReorderJob | null;
  has_data: boolean;
  config: InventoryReorderConfigStatus;
  cooldown: InventoryReorderCooldown;
}

export const inventoryReorderApi = {
  async getData(showAll: boolean): Promise<InventoryReorderResponse> {
    const response = await apiClient.get("/system/inventory-reorder", {
      params: showAll ? { all: "1" } : undefined,
    });
    return response.data;
  },

  async refresh(): Promise<{ job: InventoryReorderJob; created: boolean; cooldown: InventoryReorderCooldown }> {
    const response = await apiClient.post("/system/inventory-reorder/refresh");
    return response.data;
  },

  async getJob(jobId: string): Promise<{ job: InventoryReorderJob }> {
    const response = await apiClient.get(`/system/inventory-reorder/jobs/${jobId}`);
    return response.data;
  },

  async downloadLatest(): Promise<void> {
    const response = await apiClient.get("/system/inventory-reorder/download", {
      responseType: "blob",
    });
    const blob = response.data as Blob;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "inventory_summary_simple.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  },
};
