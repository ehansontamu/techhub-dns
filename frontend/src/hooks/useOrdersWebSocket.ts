import { useEffect, useRef, useState } from "react";
import apiClient from "../api/client";
import { io, Socket } from "socket.io-client";
import { OrderSummary } from "../types/websocket";

interface UseOrdersWebSocketOptions {
  socketUrl?: string;
  enableHttpFallback?: boolean;
}

export function useOrdersWebSocket(options?: string | UseOrdersWebSocketOptions) {
  const socketUrl = typeof options === "string" ? options : options?.socketUrl;
  const enableHttpFallback = typeof options === "string" ? false : options?.enableHttpFallback ?? false;
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const socketRef = useRef<Socket | null>(null);

  // Fetch orders via HTTP as fallback
  const fetchOrders = async () => {
    try {
      setError(null);
      setLoading(true);
      // Try to fetch orders via HTTP API as fallback
      const response = await apiClient.get('/orders');
      setOrders(response.data.items);
      setLoading(false);
    } catch (err) {
      console.error("Failed to fetch orders via HTTP:", err);
      setError("Failed to load orders");
      setLoading(false);
    }
  };

  useEffect(() => {
    if (enableHttpFallback) {
      fetchOrders();
    } else {
      setLoading(false);
    }

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
      console.error("Socket.IO init failed:", e);
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
      setError(null);
      // Join orders namespace/room
      socketInstance.emit("join", { room: "orders" });
    });

    socketInstance.on("orders_update", (payload: { type: string; data: OrderSummary[] }) => {
      try {
        if (payload.type === "orders_update") {
          setOrders(payload.data || []);
          setLoading(false);
        }
      } catch (e) {
        console.warn("Failed to parse Socket.IO message", e);
      }
    });

    socketInstance.on("disconnect", () => {
      // Transient disconnects are normal with long-polling fallback.
      // Socket.IO auto-reconnects; only flag if reconnection fails.
    });

    socketInstance.on("reconnect_failed", () => {
      console.error("Socket.IO reconnection failed — real-time updates unavailable");
      setError("Real-time updates disconnected");
    });

    socketInstance.on("connect", () => {
      setError(null);
    });

    return () => {
      const currentSocket = socketRef.current;
      if (currentSocket) {
        currentSocket.disconnect();
        socketRef.current = null;
      }
    };
  }, [enableHttpFallback, socketUrl]);

  return { orders, loading, error, refetch: fetchOrders };
}