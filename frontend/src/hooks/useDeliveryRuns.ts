import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { io, Socket } from "socket.io-client";

import { deliveryRunsQueryKeys, getActiveDeliveryRunsQueryOptions, type ActiveDeliveryRun } from "../queries/deliveryRuns";

function safeArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? value : [];
}

export type DeliveryRun = ActiveDeliveryRun;

export function useDeliveryRuns(socketUrl?: string) {
  const [socketError, setSocketError] = useState<string | null>(null);
  const socketRef = useRef<Socket | null>(null);
  const queryClient = useQueryClient();

  const query = useQuery(getActiveDeliveryRunsQueryOptions());

  const runs = query.data ?? [];
  const loading = query.isPending && runs.length === 0;
  const error = query.isError ? "Failed to load delivery runs" : socketError;

  useEffect(() => {
    // Build Socket.IO URL from current host
    const baseUrl = socketUrl || `${window.location.protocol}//${window.location.host}`;

    let socket: Socket | null = null;

    try {
      socket = io(baseUrl, {
        path: "/socket.io",
        transports: ["websocket", "polling"],
        reconnection: true,
        reconnectionAttempts: 5,
        reconnectionDelay: 1000,
      });
      socketRef.current = socket;
    } catch (e) {
      console.error("Socket.IO init failed (delivery runs):", e);
    }

    if (!socket) {
      return () => {
        const currentSocket = socketRef.current;
        if (currentSocket) {
          currentSocket.disconnect();
          socketRef.current = null;
        }
      };
    }

    const socketInstance = socket;

    socketInstance.on("connect", () => {
      setSocketError(null);
      // Join delivery-runs namespace/room
      socketInstance.emit("join", { room: "delivery-runs" });
    });

    socketInstance.on("active_runs", (payload: { type: string; data: DeliveryRun[] }) => {
      try {
        if (payload.type === "active_runs") {
          queryClient.setQueryData(deliveryRunsQueryKeys.active(), safeArray<DeliveryRun>(payload.data));
        }
      } catch (e) {
        console.warn("Failed to parse Socket.IO message", e);
      }
    });

    socketInstance.on("disconnect", () => {
      // Transient disconnects are normal with long-polling fallback.
    });

    socketInstance.on("connect_error", () => {
      // Transient errors expected during reconnection cycle.
    });

    socketInstance.on("reconnect_failed", () => {
      setSocketError("Socket.IO reconnection failed — using cached data");
    });

    socketInstance.on("connect", () => {
      setSocketError(null);
    });

    return () => {
      const currentSocket = socketRef.current;
      if (currentSocket) {
        currentSocket.disconnect();
        socketRef.current = null;
        }
      };
  }, [queryClient, socketUrl]);

  return {
    runs,
    loading,
    error,
    refetch: async () => {
      await query.refetch();
    },
  };
}