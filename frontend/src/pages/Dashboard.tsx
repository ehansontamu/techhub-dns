import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { io, Socket } from "socket.io-client";

import { Button } from "../components/ui/button";
import { Skeleton } from "../components/Skeleton";
import { Order } from "../types/order";
import { Activity, Package, CheckCircle2, Truck } from "lucide-react";
import { SectionErrorBoundary } from "../components/error-boundaries/AppErrorBoundaries";
import {
  analyticsQueryKeys,
  getDeliveredOrdersQueryOptions,
  getDeliveryPerformanceQueryOptions,
  getMonthlyFulfilledTotalsQueryOptions,
  getOrderStatusCountsQueryOptions,
  getWorkflowDailyTrendsQueryOptions,
  getYearlyFulfilledTotalsQueryOptions,
} from "../queries/analytics";
import { type FulfilledTotalDataPoint, type WorkflowDailyTrendDataPoint } from "../api/analytics";
import { shouldThrowToBoundary } from "../utils/apiErrors";
import { getCompletedTodayOrders } from "./dashboardCompletedToday";

/** Guard against API returning non-array data (error objects, null, etc.) */
function safeArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? value : [];
}

const LiveDeliveryDashboard = lazy(() => import("../components/LiveDeliveryDashboard"));
const WorkflowDailyLineChart = lazy(() => import("../components/charts/WorkflowDailyLineChart"));
const FulfilledTotalsBarChart = lazy(() => import("../components/charts/FulfilledTotalsBarChart"));

function useAnimatedCounter(target: number, duration: number = 900) {
  const [count, setCount] = useState(0);
  const countRef = useRef(0);

  useEffect(() => {
    const startTime = Date.now();
    const startValue = countRef.current;
    const diff = target - startValue;

    const animate = () => {
      const elapsed = Date.now() - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const easeOutQuart = 1 - Math.pow(1 - progress, 4);
      const current = Math.floor(startValue + diff * easeOutQuart);

      countRef.current = current;
      setCount(current);

      if (progress < 1) {
        requestAnimationFrame(animate);
      }
    };

    requestAnimationFrame(animate);
  }, [target, duration]);

  return count;
}

interface StatCardProps {
  title: string;
  value: number;
  icon: React.ElementType;
  loading: boolean;
  accent?: "maroon" | "slate" | "green";
  to?: string;
}

function StatCard({ title, value, icon: Icon, loading, accent = "slate", to }: StatCardProps) {
  const animatedValue = useAnimatedCounter(loading ? 0 : value);

  const accentClasses = {
    maroon: "bg-maroon-700 text-white",
    slate: "bg-foreground text-background",
    green: "bg-emerald-600 text-white",
  };

  const cardContent = (
    <div className="group relative h-full overflow-hidden rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none transition-colors hover:bg-card">
      <div className={`absolute bottom-0 right-0 rounded-tl-2xl p-3 ${accentClasses[accent]}`}>
        <Icon className="h-5 w-5" />
      </div>
      <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">{title}</p>
      <div className="mt-3">
        {loading ? (
          <Skeleton className="h-9 w-16" />
        ) : (
          <div className="text-3xl font-semibold tracking-tight text-foreground tabular-nums" key={value}>
            {animatedValue}
          </div>
        )}
      </div>
    </div>
  );

  if (!to) {
    return <div className="h-full">{cardContent}</div>;
  }

  return (
    <Link
      to={to}
      className="block h-full rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
    >
      {cardContent}
    </Link>
  );
}

export default function Dashboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [workflowTrendDays, setWorkflowTrendDays] = useState<7 | 30>(() => {
    const value = searchParams.get("workflowRange");
    return value === "30" ? 30 : 7;
  });
  const [socketStatus, setSocketStatus] = useState<"connecting" | "connected" | "disconnected">("connecting");

  const socketRef = useRef<Socket | null>(null);
  const socketReconnectTimeoutRef = useRef<number | null>(null);
  const lastSocketRefreshRef = useRef(0);
  const socketRefreshInFlightRef = useRef(false);
  const workflowTrendDaysRef = useRef<7 | 30>(7);
  const queryClient = useQueryClient();

  const statusCountsQuery = useQuery({
    ...getOrderStatusCountsQueryOptions(),
    throwOnError: shouldThrowToBoundary,
  });
  const deliveryPerformanceQuery = useQuery({
    ...getDeliveryPerformanceQueryOptions(),
    throwOnError: shouldThrowToBoundary,
  });
  const workflowDailyTrendsQuery = useQuery({
    ...getWorkflowDailyTrendsQueryOptions(workflowTrendDays),
    throwOnError: shouldThrowToBoundary,
  });
  const monthlyFulfilledTotalsQuery = useQuery({
    ...getMonthlyFulfilledTotalsQueryOptions(),
    throwOnError: shouldThrowToBoundary,
  });
  const yearlyFulfilledTotalsQuery = useQuery({
    ...getYearlyFulfilledTotalsQueryOptions(),
    throwOnError: shouldThrowToBoundary,
  });
  const deliveredOrdersQuery = useQuery({
    ...getDeliveredOrdersQueryOptions(),
    throwOnError: shouldThrowToBoundary,
  });

  const statusCounts = statusCountsQuery.data ?? {};
  const deliveryPerf = deliveryPerformanceQuery.data ?? {
    active_runs: 0,
    completed_today: 0,
    ready_for_delivery: 0,
  };
  const workflowDailyTrends = safeArray<WorkflowDailyTrendDataPoint>(workflowDailyTrendsQuery.data?.data);
  const monthlyFulfilledTotals = safeArray<FulfilledTotalDataPoint>(monthlyFulfilledTotalsQuery.data?.data);
  const yearlyFulfilledTotals = safeArray<FulfilledTotalDataPoint>(yearlyFulfilledTotalsQuery.data?.data);

  const completedTodayOrders = useMemo((): Order[] => {
    return getCompletedTodayOrders(safeArray<Order>(deliveredOrdersQuery.data?.items));
  }, [deliveredOrdersQuery.data]);

  const statusLoading = statusCountsQuery.isPending;
  const perfLoading = deliveryPerformanceQuery.isPending;
  const trendsLoading = workflowDailyTrendsQuery.isPending || monthlyFulfilledTotalsQuery.isPending || yearlyFulfilledTotalsQuery.isPending;
  const completedLoading = deliveredOrdersQuery.isPending;
  const error =
    statusCountsQuery.isError
    || deliveryPerformanceQuery.isError
    || workflowDailyTrendsQuery.isError
    || monthlyFulfilledTotalsQuery.isError
    || yearlyFulfilledTotalsQuery.isError
    || deliveredOrdersQuery.isError
      ? "Failed to load dashboard data"
      : null;

  useEffect(() => {
    workflowTrendDaysRef.current = workflowTrendDays;
  }, [workflowTrendDays]);

  useEffect(() => {
    const value = searchParams.get("workflowRange");
    const next = value === "30" ? 30 : 7;
    if (next !== workflowTrendDays) {
      setWorkflowTrendDays(next);
    }
  }, [searchParams]);

  const updateWorkflowTrendDays = useCallback(
    (next: 7 | 30) => {
      if (next === workflowTrendDays) {
        return;
      }

      setWorkflowTrendDays(next);

      const updated = new URLSearchParams(searchParams);
      updated.set("workflowRange", String(next));
      setSearchParams(updated, { replace: true });
    },
    [searchParams, setSearchParams, workflowTrendDays],
  );

  const refetchDashboardData = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: analyticsQueryKeys.all }),
      queryClient.invalidateQueries({ queryKey: getDeliveredOrdersQueryOptions().queryKey }),
    ]);
  }, [queryClient]);

  const refreshFromSocket = () => {
    const now = Date.now();
    if (socketRefreshInFlightRef.current || now - lastSocketRefreshRef.current < 2000) {
      return;
    }
    socketRefreshInFlightRef.current = true;
    lastSocketRefreshRef.current = now;
    refetchDashboardData().finally(() => {
      socketRefreshInFlightRef.current = false;
    });
  };

  useEffect(() => {
    const intervalMs = socketStatus === "connected" ? 15 * 60 * 1000 : 60 * 1000;
    const interval = window.setInterval(() => {
      void refetchDashboardData();
    }, intervalMs);

    return () => {
      window.clearInterval(interval);
    };
  }, [refetchDashboardData, socketStatus]);

  // Setup Socket.IO for real-time updates
  useEffect(() => {
    // Build Socket.IO URL
    const baseUrl = `${window.location.protocol}//${window.location.host}`;

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
    }

    if (!socket) {
      setSocketStatus("disconnected");
      return () => {
        if (socketReconnectTimeoutRef.current) {
          window.clearTimeout(socketReconnectTimeoutRef.current);
        }
        const currentSocket = socketRef.current;
        if (currentSocket) {
          currentSocket.disconnect();
          socketRef.current = null;
        }
      };
    }

    const socketInstance = socket;

    socketInstance.on("connect", () => {
      setSocketStatus("connected");
      if (socketReconnectTimeoutRef.current) {
        window.clearTimeout(socketReconnectTimeoutRef.current);
        socketReconnectTimeoutRef.current = null;
      }
      socketInstance.emit("join", { room: "orders" });
    });

    // Listen for orders_update and active_runs events - refetch all metrics
    socketInstance.on("orders_update", () => {
      refreshFromSocket();
    });

    socketInstance.on("active_runs", () => {
      refreshFromSocket();
    });

    socketInstance.on("disconnect", () => {
      setSocketStatus("disconnected");
    });

    socketInstance.on("connect_error", () => {
      setSocketStatus("connecting");
      if (socketReconnectTimeoutRef.current) {
        window.clearTimeout(socketReconnectTimeoutRef.current);
        socketReconnectTimeoutRef.current = null;
      }
      socketReconnectTimeoutRef.current = window.setTimeout(() => {
        if (!socketRef.current?.connected) {
          setSocketStatus("disconnected");
        }
      }, 6000);
    });

    return () => {
      if (socketReconnectTimeoutRef.current) {
        window.clearTimeout(socketReconnectTimeoutRef.current);
      }
      try {
        const currentSocket = socketRef.current;
        if (currentSocket) {
          currentSocket.disconnect();
          socketRef.current = null;
        }
      } catch (_e) {}
    };
  }, [refetchDashboardData]);

  const formatSignatureTime = (timestamp?: string) => {
    if (!timestamp) return "-";
    try {
      const date = new Date(timestamp);
      return date.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
    } catch {
      return "-";
    }
  };

  return (
    <div className="container mx-auto py-8 space-y-6">
      <div className="flex flex-col gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight text-foreground">Dashboard</h1>
          <p className="text-sm text-muted-foreground">Live metrics for QA, fulfillment, and delivery status.</p>
        </div>
      </div>

      {error && (
        <div className="bg-destructive/10 border border-destructive text-destructive rounded-lg p-4">
          <p className="font-medium">{error}</p>
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 items-stretch min-w-0">
        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none xl:col-span-2 h-full min-w-0">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <h2 className="text-base">Live Status</h2>
            <div className="flex flex-col items-end gap-1 text-right">
              <span
                className={`text-xs text-muted-foreground${socketStatus === "connected" ? " status-live" : ""}`}
              >
                {socketStatus === "connecting" && "Connecting"}
                {socketStatus === "connected" && "Connected"}
                {socketStatus === "disconnected" && "Disconnected"}
              </span>
              {socketStatus !== "connected" && (
                <span className="text-[11px] text-muted-foreground">Fallback refresh every 60 seconds</span>
              )}
            </div>
          </div>
          <div className="mt-4">
            <SectionErrorBoundary
              title="Live delivery widget failed"
              message="Try the live status panel again. The rest of the dashboard is still available."
            >
              <Suspense fallback={<Skeleton className="h-40 w-full rounded-lg" />}>
                <LiveDeliveryDashboard />
              </Suspense>
            </SectionErrorBoundary>
          </div>
        </section>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 h-full min-w-0">
          <StatCard
            title="Picked"
            value={statusCounts.picked ?? 0}
            icon={Package}
            loading={statusLoading}
            accent="slate"
            to="/orders"
          />
          <StatCard
            title="Ready for QA"
            value={statusCounts.qa ?? 0}
            icon={CheckCircle2}
            loading={statusLoading}
            accent="maroon"
            to="/order-qa"
          />
          <StatCard
            title="Completed Today"
            value={deliveryPerf.completed_today}
            icon={Truck}
            loading={perfLoading}
            accent="green"
          />
          <StatCard
            title="Ready for Delivery"
            value={deliveryPerf.ready_for_delivery}
            icon={Activity}
            loading={perfLoading}
            accent="slate"
            to="/delivery/dispatch"
          />
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 min-w-0">
        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none min-w-0">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <h2 className="text-base">Completed Today</h2>
            <span className="text-xs text-muted-foreground">Last 24h</span>
          </div>
          <div className="mt-4 min-w-0">
            {completedLoading ? (
              <div className="space-y-3">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-4 w-48" />
              </div>
            ) : completedTodayOrders.length === 0 ? (
              <div className="flex flex-col items-center justify-center min-h-[320px] text-muted-foreground">
                <CheckCircle2 className="h-7 w-7 text-muted-foreground/40 mb-3" />
                <p className="text-sm">No completed orders today</p>
              </div>
            ) : (
              <div className="space-y-3 max-h-[320px] overflow-y-auto pr-1 custom-scrollbar">
                {completedTodayOrders.map((order) => (
                  <div key={order.id} className="flex items-center justify-between text-sm">
                    <Link
                      to={`/orders/${order.inflow_order_id || order.id}`}
                      state={{ fromPath: "/" }}
                      className="font-medium text-foreground underline-offset-2 hover:text-primary hover:underline"
                    >
                      {order.inflow_order_id || order.id.slice(0, 8)}
                    </Link>
                    <div className="text-muted-foreground">
                      {formatSignatureTime(order.signature_captured_at)}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none min-w-0">
          <div className="flex flex-row items-center justify-between gap-3">
            <div>
              <h2 className="text-base">Orders Daily Workflow</h2>
              <p className="text-xs text-muted-foreground">Last {workflowTrendDays} days</p>
            </div>
            <div className="flex items-center gap-1 rounded-md border p-1">
                <Button
                  type="button"
                  size="sm"
                  variant={workflowTrendDays === 7 ? "default" : "ghost"}
                  onClick={() => updateWorkflowTrendDays(7)}
                  disabled={workflowDailyTrendsQuery.isFetching && workflowTrendDays !== 7}
                  className={`min-h-11 px-3 ${workflowDailyTrendsQuery.isFetching && workflowTrendDays !== 7 ? "opacity-75 cursor-not-allowed" : ""}`}
                >
                  7d
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={workflowTrendDays === 30 ? "default" : "ghost"}
                  onClick={() => updateWorkflowTrendDays(30)}
                  disabled={workflowDailyTrendsQuery.isFetching && workflowTrendDays !== 30}
                  className={`min-h-11 px-3 ${workflowDailyTrendsQuery.isFetching && workflowTrendDays !== 30 ? "opacity-75 cursor-not-allowed" : ""}`}
                >
                  30d
                </Button>
            </div>
          </div>
          <div className="mt-4 min-w-0">
            <SectionErrorBoundary
              title="Workflow chart failed"
              message="Try reloading this chart. The rest of the dashboard data is still available."
              resetKeys={[workflowTrendDays]}
            >
              <Suspense fallback={<Skeleton className="h-72 w-full rounded-lg sm:h-80" />}>
                <WorkflowDailyLineChart data={workflowDailyTrends} loading={trendsLoading} />
              </Suspense>
            </SectionErrorBoundary>
          </div>
        </section>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none min-w-0">
          <div>
            <h2 className="text-base">Monthly Fulfilled Totals</h2>
            <p className="text-xs text-muted-foreground">Last 12 months</p>
          </div>
          <div className="mt-4 min-w-0">
            <SectionErrorBoundary
              title="Monthly totals chart failed"
              message="Try reloading the monthly fulfilled totals chart."
            >
              <Suspense fallback={<Skeleton className="h-72 w-full rounded-lg sm:h-80" />}>
                <FulfilledTotalsBarChart data={monthlyFulfilledTotals} loading={trendsLoading} />
              </Suspense>
            </SectionErrorBoundary>
          </div>
        </section>

        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
          <div>
            <h2 className="text-base">Yearly Fulfilled Totals</h2>
            <p className="text-xs text-muted-foreground">Last 5 years</p>
          </div>
          <div className="mt-4">
            <SectionErrorBoundary
              title="Yearly totals chart failed"
              message="Try reloading the yearly fulfilled totals chart."
            >
              <Suspense fallback={<Skeleton className="h-72 w-full rounded-lg sm:h-80" />}>
                <FulfilledTotalsBarChart data={yearlyFulfilledTotals} loading={trendsLoading} />
              </Suspense>
            </SectionErrorBoundary>
          </div>
        </section>
      </div>

    </div>
  );
}// rebuild trigger
