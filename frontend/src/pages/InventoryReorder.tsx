import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowUpDown, Download, Loader2, PackageSearch, RefreshCw, Search } from "lucide-react";
import { toast } from "sonner";
import {
  inventoryReorderApi,
  type InventoryReorderJob,
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

type SortKey = "name" | "sku" | "available" | "status9" | "finalQty" | "onOrder" | "combined" | "reorderPoint" | "reorderQty";
type SortDirection = "asc" | "desc";

const sortableColumns: Array<{ key: SortKey; label: string; align?: "right" }> = [
  { key: "name", label: "Product Name" },
  { key: "sku", label: "SKU" },
  { key: "available", label: "InFlow Available", align: "right" },
  { key: "status9", label: "BC Status 9", align: "right" },
  { key: "finalQty", label: "Final Qty", align: "right" },
  { key: "onOrder", label: "On Order", align: "right" },
  { key: "combined", label: "Final + On Order", align: "right" },
  { key: "reorderPoint", label: "Reorder Point", align: "right" },
  { key: "reorderQty", label: "Reorder Qty", align: "right" },
];

const isRunningJob = (job: InventoryReorderJob | null): boolean =>
  job?.status === "queued" || job?.status === "running";

const formatTimestamp = (value: string | null | undefined): string => {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
};

const compareRows = (left: InventoryReorderRow, right: InventoryReorderRow, sortKey: SortKey) => {
  if (sortKey === "name" || sortKey === "sku") {
    return String(left[sortKey] ?? "").localeCompare(String(right[sortKey] ?? ""));
  }

  return Number(left[sortKey] ?? 0) - Number(right[sortKey] ?? 0);
};

export default function InventoryReorder() {
  const { isAdmin, isLoading: authLoading, user } = useAuth();
  const currentUserLabel = getUserDisplayName(user, "you");
  const [data, setData] = useState<InventoryReorderResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("combined");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [activeJob, setActiveJob] = useState<InventoryReorderJob | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [downloading, setDownloading] = useState(false);

  const loadData = useCallback(async () => {
    if (!isAdmin) {
      setLoading(false);
      return;
    }

    setLoading(true);
    try {
      const payload = await inventoryReorderApi.getData(showAll);
      setData(payload);
      if (isRunningJob(payload.latest_job)) {
        setActiveJob(payload.latest_job);
      }
    } catch (error: unknown) {
      const message = extractApiErrorMessage(error, "Failed to load inventory reorder data.");
      toast.error("Failed to load Inventory Reorder", { description: message });
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [isAdmin, showAll]);

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

  const startRefresh = async () => {
    setRefreshing(true);
    try {
      const response = await inventoryReorderApi.refresh();
      setActiveJob(response.job);
      toast.success(response.created ? "Inventory refresh started" : "Inventory refresh already running");
    } catch (error: unknown) {
      const message = extractApiErrorMessage(error, "Failed to start inventory refresh.");
      toast.error("Failed to start Inventory Reorder refresh", { description: message });
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
    const searched = query
      ? rows.filter((row) =>
          `${row.name} ${row.sku}`.toLowerCase().includes(query)
        )
      : rows;

    return [...searched].sort((left, right) => {
      const comparison = compareRows(left, right, sortKey);
      return sortDirection === "asc" ? comparison : -comparison;
    });
  }, [rows, search, sortDirection, sortKey]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(key);
    setSortDirection(key === "name" || key === "sku" ? "asc" : "desc");
  };

  const job = activeJob ?? data?.latest_job ?? null;
  const refreshRunning = isRunningJob(job);
  const configMissing = data?.config.configured === false;

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
          <Button type="button" onClick={() => void startRefresh()} disabled={refreshing || refreshRunning} className="btn-lift">
            {refreshing || refreshRunning ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
            Refresh Inventory
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
        <InventoryMetric label="Last Refresh" value={formatTimestamp(data?.latest_job?.finished_at)} compact />
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
            <Checkbox checked={showAll} onChange={(event) => setShowAll(event.target.checked)} label="Show all items" />
          </div>
        </div>

        <div className="mt-4 rounded-lg border bg-card">
          {filteredRows.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted-foreground">
              {data?.has_data ? "No matching inventory rows." : "No inventory summary has been refreshed yet."}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  {sortableColumns.map((column) => (
                    <TableHead key={column.key} className={cn(column.align === "right" && "text-right")}>
                      <button
                        type="button"
                        onClick={() => toggleSort(column.key)}
                        className={cn(
                          "inline-flex items-center gap-1 text-left transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                          column.align === "right" && "justify-end text-right"
                        )}
                      >
                        {column.label}
                        <ArrowUpDown className="h-3.5 w-3.5" />
                      </button>
                    </TableHead>
                  ))}
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredRows.map((row) => (
                  <TableRow
                    key={`${row.sku}-${row.name}`}
                    className={cn(
                      row.critical && "bg-red-50 hover:bg-red-100/80",
                      row.needsReorder && !row.critical && "bg-amber-50 hover:bg-amber-100/80"
                    )}
                  >
                    <TableCell className="min-w-[18rem] font-medium">{row.name}</TableCell>
                    <TableCell className="min-w-[7rem] text-muted-foreground">{row.sku || "-"}</TableCell>
                    <NumberCell value={row.available} />
                    <NumberCell value={row.status9} />
                    <NumberCell value={row.finalQty} />
                    <NumberCell value={row.onOrder} />
                    <NumberCell value={row.combined} />
                    <NumberCell value={row.reorderPoint} />
                    <NumberCell value={row.reorderQty} />
                    <TableCell>
                      {row.critical ? (
                        <Badge variant="destructive">Critical</Badge>
                      ) : row.needsReorder ? (
                        <Badge variant="warning">Reorder</Badge>
                      ) : (
                        <Badge variant="secondary">Stocked</Badge>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </section>
    </div>
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
  return <TableCell className="text-right tabular-nums">{value.toLocaleString()}</TableCell>;
}
