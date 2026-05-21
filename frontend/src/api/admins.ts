import { apiClient } from "./client";

export type AdminAllowlistSource = "env" | "db" | "mixed" | "default";

export type GetAdminsResponse = {
    admins: string[];
    source: AdminAllowlistSource;
    env_admins?: string[];
    db_admins?: string[];
};

export const adminsApi = {
    async getAdmins(): Promise<GetAdminsResponse> {
        const res = await apiClient.get("/system/admins");
        return res.data;
    },

    async updateAdmins(admins: string[]): Promise<GetAdminsResponse & { updated_by?: string }> {
        const res = await apiClient.put("/system/admins", { admins });
        return res.data;
    },
};
