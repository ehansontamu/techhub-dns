import { apiClient } from "./client";

export type InventoryReorderRecipientSource = "env" | "db" | "mixed" | "default";

export type GetInventoryReorderRecipientsResponse = {
    recipients: string[];
    source: InventoryReorderRecipientSource;
    env_recipients: string[];
    db_recipients: string[];
};

export const inventoryReorderRecipientsApi = {
    async getRecipients(): Promise<GetInventoryReorderRecipientsResponse> {
        const response = await apiClient.get("/system/inventory-reorder-recipients");
        return response.data;
    },

    async updateRecipients(
        recipients: string[]
    ): Promise<GetInventoryReorderRecipientsResponse & { updated_by?: string }> {
        const response = await apiClient.put("/system/inventory-reorder-recipients", {
            recipients,
        });
        return response.data;
    },
};
