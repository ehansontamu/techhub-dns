/**
 * System Settings API client.
 */

import { apiClient } from "./client";

export interface SystemSettingValue {
    value: string;
    description: string;
    updated_at: string | null;
    updated_by: string | null;
}

export interface SystemSettings {
    email_notifications_enabled: SystemSettingValue;
    teams_recipient_notifications_enabled: SystemSettingValue;
    document_signing_enabled: SystemSettingValue;
    picklist_auto_print_enabled: SystemSettingValue;
    require_asset_tags_before_picklist: SystemSettingValue;
    require_same_user_for_tagging_and_picklist: SystemSettingValue;
    require_partial_picklist_confirmation: SystemSettingValue;
    picklist_print_claim_timeout_seconds: SystemSettingValue;
}

export interface SyncHealthResponse {
    server_time: string;
    inflow: {
        webhook_enabled: boolean;
        webhook_failed: boolean;
        last_webhook_received_at: string | null;
    };
}

export interface SystemFeatureStatus {
    name: string;
    enabled: boolean;
    configured: boolean;
    status: "active" | "warning" | "disabled" | "error";
    details: string;
}

export interface SystemStatusResponse {
    saml_auth: SystemFeatureStatus;
    graph_api: SystemFeatureStatus;
    sharepoint: SystemFeatureStatus;
    inflow_sync: SystemFeatureStatus;
}

export interface PrintJobRecord {
    id: string;
    order_id: string;
    order_inflow_order_id?: string | null;
    document_type: string;
    status: string;
    trigger_source: string;
    requested_by?: string | null;
    file_path: string;
    attempt_count: number;
    claimed_at?: string | null;
    claim_expires_at?: string | null;
    completed_at?: string | null;
    last_error?: string | null;
    created_at?: string | null;
    updated_at?: string | null;
}

export interface TestResult {
    success: boolean;
    message?: string;
    error?: string;
}

export interface CanopyOrdersUploadResult {
    success: boolean;
    filename?: string | null;
    uploaded_url?: string | null;
    count?: number;
    teams_notified?: boolean;
    updated_orders?: number;
    eligible_orders?: string[];
    ineligible_orders?: Array<{ order: string; reason: string }>;
    missing_orders?: string[];
    error?: string;
    error_type?: string;
    status_code?: number;
}

export interface CanopyOrdersBypassUploadResult {
    success: boolean;
    filename?: string | null;
    uploaded_url?: string | null;
    count?: number;
    teams_notified?: boolean;
    updated_orders?: number;
    missing_orders?: string[];
    error?: string;
    error_type?: string;
    status_code?: number;
}

export const settingsApi = {
    /**
     * Get all system settings.
     */
    async getSettings(): Promise<SystemSettings> {
        const response = await apiClient.get("/system/settings");
        return response.data;
    },

    /**
     * Update a single setting.
     */
    async updateSetting(key: string, value: string, updatedBy?: string): Promise<{ key: string; value: string }> {
        const response = await apiClient.put(`/system/settings/${key}`, {
            value,
            updated_by: updatedBy,
        });
        return response.data;
    },

    /**
     * Get sync health for operator recovery.
     */
    async getSyncHealth(): Promise<SyncHealthResponse> {
        const response = await apiClient.get("/system/sync-health");
        return response.data;
    },

    /**
     * Get backend feature status diagnostics.
     */
    async getSystemStatus(): Promise<SystemStatusResponse> {
        const response = await apiClient.get("/system/status");
        return response.data;
    },

    // ========== Testing Endpoints ==========

    /**
     * Send a test email.
     */
    async testEmail(toAddress: string): Promise<TestResult> {
        const response = await apiClient.post("/system/test/email", { to_address: toAddress });
        return response.data;
    },

    /**
     * Send a test Teams message to recipient.
     */
    async testTeamsRecipient(recipientEmail: string): Promise<TestResult> {
        const response = await apiClient.post("/system/test/teams-recipient", { recipient_email: recipientEmail });
        return response.data;
    },

    /**
     * Test Inflow API connection.
     */
    async testInflow(): Promise<TestResult> {
        const response = await apiClient.post("/system/test/inflow");
        return response.data;
    },

    /**
     * Test SharePoint connection.
     */
    async testSharePoint(): Promise<TestResult> {
        const response = await apiClient.post("/system/test/sharepoint");
        return response.data;
    },

    /**
     * Upload Canopy orders (legacy uploader).
     */
    async uploadCanopyOrders(orders: string[]): Promise<CanopyOrdersUploadResult> {
        const response = await apiClient.post("/system/canopyorders/upload", { orders });
        return response.data;
    },

    /**
     * Upload Canopy orders (bypass eligibility checks).
     */
    async uploadCanopyOrdersBypass(orders: string[]): Promise<CanopyOrdersBypassUploadResult> {
        const response = await apiClient.post("/system/canopyorders/upload-bypass", { orders });
        return response.data;
    },

    async getPrintJobs(status?: string, limit = 25): Promise<{ jobs: PrintJobRecord[] }> {
        const response = await apiClient.get("/system/print-jobs", {
            params: { status, limit },
        });
        return response.data;
    },

    async retryPicklistPrint(orderId: string): Promise<{ success: boolean; job: PrintJobRecord }> {
        const response = await apiClient.post(`/system/orders/${orderId}/reprint-picklist`);
        return response.data;
    },
};
