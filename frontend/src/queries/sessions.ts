import { queryOptions } from "@tanstack/react-query";
import apiClient from "../api/client";

import type { Session } from "../contexts/AuthContext";

interface SessionsResponse {
  sessions: Session[];
}

const fetchSessions = async (): Promise<Session[]> => {
  const response = await apiClient.get<SessionsResponse>("/auth/sessions", {
    withCredentials: true,
    headers: {
      "Cache-Control": "no-cache, no-store, must-revalidate",
      Pragma: "no-cache",
    },
    params: { _t: Date.now() },
  });

  const data = response.data as unknown;
  if (!data || typeof data !== "object" || !Array.isArray((data as SessionsResponse).sessions)) {
    throw new Error("Invalid sessions response");
  }

  return (data as SessionsResponse).sessions;
};

export const sessionsQueryKeys = {
  all: ["sessions"] as const,
};

export const getSessionsQueryOptions = () =>
  queryOptions({
    queryKey: sessionsQueryKeys.all,
    queryFn: fetchSessions,
  });

export const revokeSession = async (sessionId: string): Promise<void> => {
  await apiClient.post("/auth/sessions/revoke", { session_id: sessionId }, { withCredentials: true });
};

export const revokeAllOtherSessions = async (): Promise<void> => {
  await apiClient.post("/auth/sessions/revoke_all", {}, { withCredentials: true });
};
