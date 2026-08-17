import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowUpDown, ChevronRight, Download, Loader2, PackageSearch, RefreshCw, Search } from "lucide-react";
import { toast } from "sonner";
import {
  inventoryReorderApi,
  type InventoryReorderJob,
  type InventoryReorderOrderDetail,
  type InventoryReorderResponse,
  type InventoryReorderRow,
} from "../api/inventoryReorder";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Checkbox } from "../components/ui/checkbox";
import { Input } from "../components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { useAuth } from "../contexts/AuthContext";
import { cn } from "../lib/utils";
import { extractApiErrorMessage } from "../utils/apiErrors";
import { getUserDisplayName } from "../utils/userDisplay";

type SortKey = "name" | "sku" | "available" | "status9" | "finalQty" | "onOrder" | "combined" | "reorderPoint" | "reorderQty" | "status";
type SortDirection = "asc" | "desc";

const BIGCOMMERCE_ORDER_ADMIN_BASE = "https://store-jsj7fos9p1.mybigcommerce.com/manage/orders";
const INFLOW_SALES_ORDER_BASE = "https://app.inflowinventory.com/sales-orders";

const sortableColumns: Array<{ key: SortKey; label: string; align?: "right"; width: string }> = [
  { key: "name", label: "Product Name", width: "w-[18%]" },
  { key: "sku", label: "SKU", width: "w-[7%]" },
  { key: "available", label: "InFlow Available", align: "right", width: "w-[8%]" },
  { key: "status9", label: "BC Aggiebuy Approval (Status 9)", align: "right", width: "w-[12%]" },
  { key: "finalQty", label: "Final Qty", align: "right", width: "w-[7%]" },
  { key: "onOrder", label: "On Order", align: "right", width: "w-[7%]" },
  { key: "combined", label: "Final + On Order", align: "right", width: "w-[9%]" },
  { key: "reorderPoint", label: "Reorder Point", align: "right", width: "w-[8%]" },
  { key: "reorderQty", label: "Reorder Qty", align: "right", width: "w-[8%]" },
  { key: "status", label: "Status", width: "w-[13%]" },
];

const isRunningJob = (job: InventoryReorderJob | null): boolean =>
  job?.status === "queued" || job?.status === "running";

const formatTimestamp = (value: string | null | undefined): string => {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
};

const formatRefreshLabel = (job: InventoryReorderJob | null | undefined): string => {
  const timestamp = formatTimestamp(job?.finished_at);
  if (!job?.trigger || timestamp === "Never") {
    return timestamp;
  }

  return `${timestamp} (${job.trigger})`;
};

const formatDuration = (seconds: number): string => {
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
};

const formatScheduleTimes = (times: string): string => {
  const labels = times
    .split(",")
    .map((value) => {
      const [rawHour, rawMinute] = value.trim().split(":");
      return {
        hour: Number(rawHour),
        minute: rawMinute == null ? 0 : Number(rawMinute),
      };
    })
    .filter(({ hour, minute }) => Number.isInteger(hour) && hour >= 0 && hour <= 23 && Number.isInteger(minute) && minute >= 0 && minute <= 59)
    .map(({ hour, minute }) => {
      const suffix = minute === 0 ? "" : `:${String(minute).padStart(2, "0")}`;
      if (hour === 0) return "12 AM";
      if (hour === 12) return `12${suffix} PM`;
      return hour > 12 ? `${hour - 12}${suffix} PM` : `${hour}${suffix} AM`;
    });

  return labels.length > 0 ? labels.join(", ") : "7:30 AM, 12 PM, 3 PM";
};

const hasTenPlusBigCommerceOrder = (row: InventoryReorderRow): boolean =>
  Boolean(row.orders?.bigCommerce.some((order) => order.quantity >= 10));

const compareRows = (left: InventoryReorderRow, right: InventoryReorderRow, sortKey: SortKey) => {
  if (sortKey === "name" || sortKey === "sku") {
    return String(left[sortKey] ?? "").localeCompare(String(right[sortKey] ?? ""));
  }

  if (sortKey === "status") {
    const getRank = (row: InventoryReorderRow) => {
      if (row.critical) return 0;
      if (row.needsReorder) return 1;
      if (hasTenPlusBigCommerceOrder(row)) return 2;
      return 3;
    };

    return getRank(left) - getRank(right);
  }

  return Number(left[sortKey] ?? 0) - Number(right[sortKey] ?? 0);
};

export default function InventoryReorder() {
  const { isAdmin, isLoading: authLoading, user } = useAuth();
  const currentUserLabel = getUserDisplayName(user, "you");
  const [data, setData] = useState<InventoryReorderResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const [showTenPlusOnly, setShowTenPlusOnly] = useState(false);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("status");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [activeJob, setActiveJob] = useState<InventoryReorderJob | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [expandedRows, setExpandedRows] = useState<Set<string>>(() => new Set());

  const loadData = useCallback(async (options?: { silent?: boolean }) => {
    if (!isAdmin) {
      setLoading(false);
      return;
    }

    if (!options?.silent) {
      setLoading(true);
    }
    try {
      const payload = await inventoryReorderApi.getData(showAll || showTenPlusOnly);
      setData(payload);
      if (isRunningJob(payload.latest_job)) {
        setActiveJob(payload.latest_job);
      }
    } catch (error: unknown) {
      if (!options?.silent) {
        const message = extractApiErrorMessage(error, "Failed to load inventory reorder data.");
        toast.error("Failed to load Inventory Reorder", { description: message });
        setData(null);
      }
    } finally {
      if (!options?.silent) {
        setLoading(false);
      }
    }
  }, [isAdmin, showAll, showTenPlusOnly]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    if (!activeJob || !isRunningJob(activeJob)) {
      return;
    }

    const interval = window.setInterval(() => {
      void inventoryReorderApi
        .getJob(activeJob.job_id)
        .then(({ job }) => {
          setActiveJob(job);
          if (job.status === "done") {
            toast.success("Inventory reorder refresh complete");
            void loadData();
          }
          if (job.status === "error") {
            toast.error("Inventory reorder refresh failed", {
              description: job.error ?? "The refresh job failed.",
            });
          }
        })
        .catch((error: unknown) => {
          const message = extractApiErrorMessage(error, "Failed to check refresh status.");
          toast.error("Inventory reorder refresh status failed", { description: message });
        });
    }, 2000);

    return () => window.clearInterval(interval);
  }, [activeJob, loadData]);

  useEffect(() => {
    if (!isAdmin) {
      return;
    }

    const interval = window.setInterval(() => {
      if (!isRunningJob(activeJob)) {
        void loadData({ silent: true });
      }
    }, 60000);

    return () => window.clearInterval(interval);
  }, [activeJob, isAdmin, loadData]);

  useEffect(() => {
    const endsAt = data?.cooldown.ends_at;
    if (!endsAt) {
      return;
    }

    const interval = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, [data?.cooldown.ends_at]);

  const startRefresh = async () => {
    setRefreshing(true);
    try {
      const response = await inventoryReorderApi.refresh();
      setActiveJob(response.job);
      toast.success(response.created ? "Inventory refresh started" : "Inventory refresh already running");
    } catch (error: unknown) {
      const message = extractApiErrorMessage(error, "Failed to start inventory refresh.");
      toast.error("Failed to start Inventory Reorder refresh", { description: message });
      void loadData({ silent: true });
    } finally {
      setRefreshing(false);
    }
  };

  const downloadLatest = async () => {
    setDownloading(true);
    try {
      await inventoryReorderApi.downloadLatest();
    } catch (error: unknown) {
      const message = extractApiErrorMessage(error, "Failed to download inventory summary.");
      toast.error("Download failed", { description: message });
    } finally {
      setDownloading(false);
    }
  };

  const rows = data?.rows ?? [];
  const filteredRows = useMemo(() => {
    const query = search.trim().toLowerCase();
    const scopedRows = showTenPlusOnly
      ? rows.filter(hasTenPlusBigCommerceOrder)
      : rows;
    const searched = query
      ? scopedRows.filter((row) =>
          `${row.name} ${row.sku}`.toLowerCase().includes(query)
        )
      : scopedRows;

    return [...searched].sort((left, right) => {
      const comparison = compareRows(left, right, sortKey);
      return sortDirection === "asc" ? comparison : -comparison;
    });
  }, [rows, search, showTenPlusOnly, sortDirection, sortKey]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(key);
    setSortDirection(key === "name" || key === "sku" || key === "status" ? "asc" : "desc");
  };

  const toggleExpanded = (rowKey: string) => {
    setExpandedRows((current) => {
      const next = new Set(current);
      if (next.has(rowKey)) {
        next.delete(rowKey);
      } else {
        next.add(rowKey);
      }
      return next;
    });
  };

  const job = activeJob ?? data?.latest_job ?? null;
  const refreshRunning = isRunningJob(job);
  const configMissing = data?.config.configured === false;
  const scheduledRefresh = data?.config.scheduled_refresh;
  const cooldownEndsAtMs = data?.cooldown.ends_at ? new Date(data.cooldown.ends_at).getTime() : Number.NaN;
  const cooldownRemainingSeconds = Number.isFinite(cooldownEndsAtMs)
    ? Math.max(Math.ceil((cooldownEndsAtMs - nowMs) / 1000), 0)
    : Math.max(data?.cooldown.remaining_seconds ?? 0, 0);
  const cooldownActive = cooldownRemainingSeconds > 0;
  const refreshDisabled = refreshing || refreshRunning || cooldownActive;

  if (authLoading || loading) {
    return (
      <div className="container mx-auto py-6">
        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading inventory reorder...
          </div>
        </section>
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="container mx-auto space-y-4 py-6">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Inventory Reorder</h1>
          <p className="text-sm text-muted-foreground">Admin-only inventory reorder summary.</p>
        </div>
        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
          <h2 className="text-base font-semibold tracking-tight">Access denied</h2>
          <p className="mt-1 text-sm text-muted-foreground">Admin access is required to view this page.</p>
          <p className="mt-4 text-sm text-muted-foreground">
            {currentUserLabel ? `Signed in as ${currentUserLabel}.` : "You are not signed in."}
          </p>
        </section>
      </div>
    );
  }

  return (
    <div className="container mx-auto space-y-4 py-6">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Inventory Reorder</h1>
          <p className="text-sm text-muted-foreground">InFlow availability, BigCommerce status 9 demand, and reorder thresholds.</p>
          {scheduledRefresh?.enabled ? (
            <p className="text-xs text-muted-foreground">
              Auto-refreshes daily at {formatScheduleTimes(scheduledRefresh.times ?? scheduledRefresh.hours ?? "7:30,12:00,15:00")} {scheduledRefresh.timezone}.
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">Automatic refresh is disabled. Use Refresh Inventory to update data.</p>
          )}
          <p className="text-xs text-muted-foreground">
            Manual refresh has a {Math.round((data?.cooldown.cooldown_seconds ?? 180) / 60)} minute cooldown.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="outline" onClick={() => void loadData()} disabled={refreshing || refreshRunning}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Reload
          </Button>
          <Button type="button" variant="outline" onClick={() => void downloadLatest()} disabled={!data?.has_data || downloading}>
            {downloading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
            Download JSON
          </Button>
          <Button type="button" onClick={() => void startRefresh()} disabled={refreshDisabled} className="btn-lift">
            {refreshing || refreshRunning ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
            {cooldownActive ? `Cooldown ${formatDuration(cooldownRemainingSeconds)}` : "Refresh Inventory"}
          </Button>
        </div>
      </div>

      {configMissing ? (
        <section className="rounded-2xl border border-destructive/30 bg-destructive/5 p-5 shadow-none">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 text-destructive" />
            <div>
              <h2 className="text-base font-semibold tracking-tight text-foreground">Configuration missing</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {data?.config.missing.join(", ")}
              </p>
            </div>
          </div>
        </section>
      ) : null}

      <section className="grid gap-3 md:grid-cols-4">
        <InventoryMetric label="Rows" value={data?.summary.total ?? 0} />
        <InventoryMetric label="Needs Reorder" value={data?.summary.needs_reorder ?? 0} tone="warning" />
        <InventoryMetric label="Critical" value={data?.summary.critical ?? 0} tone="destructive" />
        <InventoryMetric label="Last Refresh" value={formatRefreshLabel(data?.latest_job)} compact />
      </section>

      {job ? (
        <section className="rounded-2xl border border-border/70 bg-card/80 p-4 shadow-none">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <Badge variant={job.status === "error" ? "destructive" : job.status === "done" ? "success" : "warning"}>
                  {job.status}
                </Badge>
                <p className="truncate text-sm font-medium text-foreground">{job.message}</p>
              </div>
              {job.error ? <p className="mt-1 text-sm text-destructive">{job.error}</p> : null}
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted sm:max-w-xs">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${Math.round((job.progress ?? 0) * 100)}%` }}
              />
            </div>
          </div>
        </section>
      ) : null}

      <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-md bg-secondary text-secondary-foreground">
              <PackageSearch className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-base font-semibold tracking-tight">Inventory</h2>
              <p className="text-sm text-muted-foreground">{filteredRows.length} shown</p>
            </div>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <div className="relative sm:w-80">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search name or SKU"
                className="pl-9"
              />
            </div>
            <Button
              type="button"
              variant={showTenPlusOnly ? "default" : "outline"}
              aria-pressed={showTenPlusOnly}
              onClick={() => setShowTenPlusOnly((current) => !current)}
              className="whitespace-nowrap"
            >
              10+ BC orders
              <Badge
                variant="outline"
                className="ml-2 min-w-6 justify-center border-transparent bg-[#d97706] px-1.5 text-white tabular-nums"
              >
                {data?.summary.ten_plus_bc_order_items ?? 0}
              </Badge>
            </Button>
            <div className="space-y-1">
              <Checkbox checked={showAll} onChange={(event) => setShowAll(event.target.checked)} label="Show all items" />
              <p className="text-xs text-muted-foreground">Includes products with reorder qty 0.</p>
            </div>
          </div>
        </div>

        <div className="mt-4 rounded-lg border bg-card">
          {filteredRows.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted-foreground">
              {data?.has_data ? "No matching inventory rows." : "No inventory summary has been refreshed yet."}
            </div>
          ) : (
            <Table className="table-fixed text-xs">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-9 px-1 py-2 md:px-1">
                    <span className="sr-only">Order details</span>
                  </TableHead>
                  {sortableColumns.map((column) => (
                    <TableHead
                      key={column.key}
                      className={cn(
                        "h-auto whitespace-normal px-1.5 py-2 align-bottom leading-tight md:px-2",
                        column.width,
                        column.align === "right" && "text-right"
                      )}
                    >
                      <button
                        type="button"
                        onClick={() => toggleSort(column.key)}
                        className={cn(
                          "inline-flex items-end gap-0.5 text-left leading-tight transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                          column.align === "right" && "justify-end text-right"
                        )}
                      >
                        {column.label}
                        <ArrowUpDown className="h-3 w-3 shrink-0" />
                      </button>
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredRows.map((row) => {
                  const rowKey = `${row.sku}-${row.name}`;
                  const isExpanded = expandedRows.has(rowKey);
                  const hasBulkBigCommerceOrder = hasTenPlusBigCommerceOrder(row);
                  return (
                    <Fragment key={rowKey}>
                      <TableRow
                        className={cn(
                          row.critical && "bg-red-50 hover:bg-red-100/80",
                          row.needsReorder && !row.critical && "bg-amber-50 hover:bg-amber-100/80"
                        )}
                      >
                        <TableCell className="w-9 px-0.5 py-1.5">
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            aria-label={`${isExpanded ? "Hide" : "Show"} orders for ${row.name}`}
                            aria-expanded={isExpanded}
                            onClick={() => toggleExpanded(rowKey)}
                          >
                            <ChevronRight className={cn("h-4 w-4 transition-transform", isExpanded && "rotate-90")} />
                          </Button>
                        </TableCell>
                        <TableCell className="break-words px-1.5 py-2 font-medium leading-tight md:px-2">{row.name}</TableCell>
                        <TableCell className="break-words px-1.5 py-2 text-muted-foreground md:px-2">{row.sku || "-"}</TableCell>
                        <NumberCell value={row.available} />
                        <NumberCell value={row.status9} />
                        <NumberCell value={row.finalQty} />
                        <NumberCell value={row.onOrder} />
                        <NumberCell value={row.combined} />
                        <NumberCell value={row.reorderPoint} />
                        <NumberCell value={row.reorderQty} />
                        <TableCell className="px-1.5 py-2 md:px-2">
                          <div className="flex flex-col items-start gap-1">
                          {row.critical ? (
                            <Badge variant="destructive" className="px-2 py-0 text-[11px]">Critical</Badge>
                          ) : row.needsReorder ? (
                            <Badge variant="warning" className="px-2 py-0 text-[11px]">Reorder</Badge>
                          ) : (
                            <Badge variant="secondary" className="px-2 py-0 text-[11px]">Stocked</Badge>
                          )}
                          {hasBulkBigCommerceOrder ? (
                            <Badge variant="warning" className="px-2 py-0 text-[10px]">10+ order</Badge>
                          ) : null}
                          </div>
                        </TableCell>
                      </TableRow>
                      {isExpanded ? (
                        <TableRow className="bg-muted/20 hover:bg-muted/20">
                          <TableCell colSpan={sortableColumns.length + 1} className="p-4 md:p-5">
                            <OrderDetails orders={row.orders} />
                          </TableCell>
                        </TableRow>
                      ) : null}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </div>
      </section>
    </div>
  );
}

type OrderDetailsProps = {
  orders: InventoryReorderRow["orders"];
};

function OrderDetails({ orders }: OrderDetailsProps) {
  if (!orders) {
    return (
      <p className="text-sm text-muted-foreground">
        Order-level details will be available after the next inventory refresh.
      </p>
    );
  }

  const activeInflowOrders = orders.inflow.filter(
    (order) => !order.status.trim().toLowerCase().startsWith("fulfilled")
  );

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <OrderSourceList
        title="BigCommerce Aggiebuy Approval (Status 9)"
        orders={orders.bigCommerce}
        emptyMessage="This product is not on any Status 9 BigCommerce orders."
        getOrderHref={(order) => `${BIGCOMMERCE_ORDER_ADMIN_BASE}/${encodeURIComponent(order.orderNumber)}`}
        showBulkQuantityBadge
      />
      <OrderSourceList
        title="InFlow active sales orders"
        orders={activeInflowOrders}
        emptyMessage="This product is not on any active InFlow sales orders."
        getOrderHref={(order) => `${INFLOW_SALES_ORDER_BASE}/${encodeURIComponent(order.orderId)}`}
      />
    </div>
  );
}

type OrderSourceListProps = {
  title: string;
  orders: InventoryReorderOrderDetail[];
  emptyMessage: string;
  getOrderHref?: (order: InventoryReorderOrderDetail) => string;
  showBulkQuantityBadge?: boolean;
};

function OrderSourceList({
  title,
  orders,
  emptyMessage,
  getOrderHref,
  showBulkQuantityBadge = false,
}: OrderSourceListProps) {
  const totalQuantity = orders.reduce((total, order) => total + order.quantity, 0);

  return (
    <section className="rounded-lg border bg-background/80 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        <span className="text-xs tabular-nums text-muted-foreground">
          {orders.length} {orders.length === 1 ? "order" : "orders"} · {totalQuantity.toLocaleString()} units
        </span>
      </div>
      {orders.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">{emptyMessage}</p>
      ) : (
        <div className="mt-3 divide-y rounded-md border">
          {orders.map((order, index) => (
            <div
              key={`${order.orderId}-${index}`}
              className={cn(
                "flex flex-wrap items-center justify-between gap-3 px-3 py-2.5",
                showBulkQuantityBadge && order.quantity >= 10 && "bg-amber-50"
              )}
            >
              <div className="min-w-0">
                {getOrderHref ? (
                  <a
                    href={getOrderHref(order)}
                    target="_blank"
                    rel="noreferrer"
                    className="font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  >
                    Order {order.orderNumber}
                  </a>
                ) : (
                  <p className="font-medium">Order {order.orderNumber}</p>
                )}
                <p className="text-xs capitalize text-muted-foreground">{order.status}</p>
              </div>
              <div className="flex items-center gap-2">
                {showBulkQuantityBadge && order.quantity >= 10 ? <Badge variant="warning">10+ units</Badge> : null}
                <span className="font-semibold tabular-nums">{order.quantity.toLocaleString()}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

type InventoryMetricProps = {
  label: string;
  value: number | string;
  tone?: "warning" | "destructive";
  compact?: boolean;
};

function InventoryMetric({ label, value, tone, compact = false }: InventoryMetricProps) {
  return (
    <div className="rounded-2xl border border-border/70 bg-card/80 p-4 shadow-none">
      <div className="text-xs font-medium uppercase text-muted-foreground">{label}</div>
      <div
        className={cn(
          "mt-1 font-semibold text-foreground",
          compact ? "text-sm" : "text-2xl",
          tone === "warning" && "text-amber-700",
          tone === "destructive" && "text-destructive"
        )}
      >
        {value}
      </div>
    </div>
  );
}

function NumberCell({ value }: { value: number }) {
  return <TableCell className="px-1.5 py-2 text-right tabular-nums md:px-2">{value.toLocaleString()}</TableCell>;
}
