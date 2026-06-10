import { apiClient } from "./client";

export type AllowedUsersSource = "env" | "db" | "mixed" | "default";

export type GetAllowedUsersResponse = {
    allowed_users: string[];
    source: AllowedUsersSource;
    env_allowed_users?: string[];
    db_allowed_users?: string[];
    restriction_enabled: boolean;
    admins_are_always_allowed: boolean;
};

export const allowedUsersApi = {
    async getAllowedUsers(): Promise<GetAllowedUsersResponse> {
        const res = await apiClient.get("/system/allowed-users");
        return res.data;
    },

    async updateAllowedUsers(
        allowedUsers: string[]
    ): Promise<GetAllowedUsersResponse & { updated_by?: string }> {
        const res = await apiClient.put("/system/allowed-users", {
            allowed_users: allowedUsers,
        });
        return res.data;
    },
};
