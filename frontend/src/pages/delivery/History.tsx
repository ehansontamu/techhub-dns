import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { queryOptions, useQuery } from "@tanstack/react-query";
import { Clock, ExternalLink } from "lucide-react";

import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { getDeliveryRunHistoryQueryOptions } from "../../queries/deliveryRuns";
import { vehicleCheckoutsApi } from "../../api/vehicleCheckouts";
import { formatToCentralTime } from "../../utils/timezone";

// ── Date helpers ──────────────────────────────────────────────────────────────

function toDateInputValue(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function todayLocal() { return new Date(); }
function daysAgo(n: number) { const d = new Date(); d.setDate(d.getDate() - n); return d; }
function startOfMonth() { const d = new Date(); d.setDate(1); return d; }
function startOfLastMonth() { const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1); return d; }
function endOfLastMonth() { const d = new Date(); d.setDate(0); return d; }

const PRESETS = [
  { label: "Today", start: () => todayLocal(), end: () => todayLocal() },
  { label: "Last 7 days", start: () => daysAgo(6), end: () => todayLocal() },
  { label: "Last 30 days", start: () => daysAgo(29), end: () => todayLocal() },
  { label: "This month", start: () => startOfMonth(), end: () => todayLocal() },
  { label: "Last month", start: () => startOfLastMonth(), end: () => endOfLastMonth() },
] as const;

// ── Unified row type ──────────────────────────────────────────────────────────

type HistoryRow = {
  id: string;
  kind: "run" | "checkout";
  typeLabel: string;
  runner: string;
  vehicle: string;
  status: string;
  start_time: string | null;
  end_time: string | null;
  orderCount: number | null;
  runId: string | null;
};

// ── Formatting helpers ────────────────────────────────────────────────────────

function formatVehicle(vehicle: string) {
  return vehicle.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

function formatDuration(start: string | null | undefined, end: string | null | undefined): string {
  if (!start || !end) return "—";
  const diffMs = new Date(end).getTime() - new Date(start).getTime();
  if (diffMs <= 0) return "—";
  const totalMinutes = Math.floor(diffMs / 60_000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours === 0) return `${minutes}m`;
  return minutes === 0 ? `${hours}h` : `${hours}h ${minutes}m`;
}

function getStatusVariant(status: string): "success" | "secondary" | "destructive" | "warning" | "outline" {
  switch (status.toLowerCase()) {
    case "completed": return "success";
    case "cancelled": return "destructive";
    case "active": return "warning";
    default: return "outline";
  }
}

function getTypeVariant(typeLabel: string): "default" | "secondary" | "outline" {
  if (typeLabel === "Delivery" || typeLabel === "Pickup") return "secondary";
  if (typeLabel === "Tech Duty") return "outline";
  return "outline";
}

// ── Query for "other" checkouts (Tech Duty, Administrative, etc.) ─────────────

function getCheckoutHistoryQueryOptions(params: { start_date?: string; end_date?: string }) {
  return queryOptions({
    queryKey: ["vehicle-checkouts", "history", params],
    queryFn: () =>
      vehicleCheckoutsApi.listCheckouts({
        checkout_type: "other",
        start_date: params.start_date,
        end_date: params.end_date,
        page_size: 200,
      }),
    staleTime: 60_000,
  });
}

// ── Status options shown in the filter ───────────────────────────────────────

const STATUS_OPTIONS = [
  { value: "Completed", label: "Completed" },
  { value: "Active", label: "Active" },
  { value: "Cancelled", label: "Cancelled" },
] as const;

// ── Component ─────────────────────────────────────────────────────────────────

export default function DeliveryHistory() {
  const defaultStart = toDateInputValue(daysAgo(29));
  const defaultEnd = toDateInputValue(todayLocal());

  const [startDate, setStartDate] = useState(defaultStart);
  const [endDate, setEndDate] = useState(defaultEnd);
  const [activePreset, setActivePreset] = useState<string>("Last 30 days");
  const [selectedStatuses, setSelectedStatuses] = useState<string[]>(["Completed", "Active", "Cancelled"]);

  const [fetchParams, setFetchParams] = useState({
    start_date: defaultStart,
    end_date: defaultEnd,
  });

  const runsQuery = useQuery(
    getDeliveryRunHistoryQueryOptions({
      status: [],  // fetch all statuses; we filter client-side for unified filtering
      start_date: fetchParams.start_date,
      end_date: fetchParams.end_date,
    })
  );

  const checkoutsQuery = useQuery(
    getCheckoutHistoryQueryOptions({
      start_date: fetchParams.start_date,
      end_date: fetchParams.end_date,
    })
  );

  const isLoading = runsQuery.isLoading || checkoutsQuery.isLoading;
  const isError = runsQuery.isError || checkoutsQuery.isError;

  // Merge delivery runs + "other" checkouts into one sorted list
  const allRows = useMemo((): HistoryRow[] => {
    const rows: HistoryRow[] = [];

    for (const run of runsQuery.data ?? []) {
      const typeLabel = run.vehicle === "pickup" ? "Pickup" : "Delivery";
      rows.push({
        id: run.id,
        kind: "run",
        typeLabel,
        runner: run.runner,
        vehicle: run.vehicle,
        status: run.status,
        start_time: run.start_time ?? null,
        end_time: run.end_time ?? null,
        orderCount: run.order_ids != null ? run.order_ids.length : null,
        runId: run.id,
      });
    }

    for (const checkout of checkoutsQuery.data?.items ?? []) {
      const typeLabel = checkout.purpose?.trim() || "Other";
      const status = checkout.checked_in_at ? "Completed" : "Active";
      rows.push({
        id: checkout.id,
        kind: "checkout",
        typeLabel,
        runner: checkout.checked_out_by,
        vehicle: checkout.vehicle,
        status,
        start_time: checkout.checked_out_at,
        end_time: checkout.checked_in_at,
        orderCount: null,
        runId: null,
      });
    }

    // Sort by start_time descending, nulls last
    rows.sort((a, b) => {
      const aTime = a.start_time ? new Date(a.start_time).getTime() : 0;
      const bTime = b.start_time ? new Date(b.start_time).getTime() : 0;
      return bTime - aTime;
    });

    return rows;
  }, [runsQuery.data, checkoutsQuery.data]);

  // Apply status filter
  const filteredRows = useMemo(
    () => allRows.filter((row) => selectedStatuses.includes(row.status)),
    [allRows, selectedStatuses]
  );

  function applyPreset(preset: typeof PRESETS[number]) {
    const s = toDateInputValue(preset.start());
    const e = toDateInputValue(preset.end());
    setStartDate(s);
    setEndDate(e);
    setActivePreset(preset.label);
    setFetchParams({ start_date: s, end_date: e });
  }

  function handleApply() {
    setActivePreset("");
    setFetchParams({ start_date: startDate, end_date: endDate });
  }

  function toggleStatus(value: string) {
    setSelectedStatuses((prev) =>
      prev.includes(value) ? prev.filter((s) => s !== value) : [...prev, value]
    );
  }

  function handleRefresh() {
    void runsQuery.refetch();
    void checkoutsQuery.refetch();
  }

  return (
    <div className="space-y-5">
      {/* ── Filters ── */}
      <div className="rounded-2xl border border-border/70 bg-card/80 p-4 shadow-none space-y-4">
        <div className="flex flex-wrap gap-2">
          {PRESETS.map((preset) => (
            <Button
              key={preset.label}
              variant={activePreset === preset.label ? "default" : "outline"}
              size="sm"
              onClick={() => applyPreset(preset)}
            >
              {preset.label}
            </Button>
          ))}
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground font-medium">From</label>
            <Input
              type="date"
              value={startDate}
              max={endDate}
              onChange={(e) => { setStartDate(e.target.value); setActivePreset(""); }}
              className="h-9 w-40 text-sm"
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground font-medium">To</label>
            <Input
              type="date"
              value={endDate}
              min={startDate}
              max={toDateInputValue(todayLocal())}
              onChange={(e) => { setEndDate(e.target.value); setActivePreset(""); }}
              className="h-9 w-40 text-sm"
            />
          </div>

          <div className="space-y-1">
            <label className="text-xs text-muted-foreground font-medium">Status</label>
            <div className="flex gap-1.5">
              {STATUS_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => toggleStatus(opt.value)}
                  className={`inline-flex h-9 items-center rounded-md border px-3 text-xs font-medium transition-colors ${
                    selectedStatuses.includes(opt.value)
                      ? "border-accent bg-accent/10 text-foreground"
                      : "border-border text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          <Button size="sm" className="h-9" onClick={handleApply}>
            Apply
          </Button>
        </div>
      </div>

      {/* ── Results ── */}
      <div className="rounded-2xl border border-border/70 bg-card/80 shadow-none overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border/70">
          <div>
            <h2 className="text-sm font-semibold">Vehicle Activity History</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              {isLoading
                ? "Loading..."
                : `${filteredRows.length} record${filteredRows.length !== 1 ? "s" : ""} — delivery runs, tech duty, and administrative checkouts`}
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={handleRefresh} disabled={isLoading}>
            Refresh
          </Button>
        </div>

        {isError ? (
          <div className="px-5 py-10 text-center text-sm text-destructive">
            Failed to load history. Check your date range and try again.
          </div>
        ) : isLoading ? (
          <div className="px-5 py-10 text-center text-sm text-muted-foreground">
            Loading history...
          </div>
        ) : filteredRows.length === 0 ? (
          <div className="px-5 py-10 text-center">
            <Clock className="mx-auto mb-3 h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">No activity found for this date range.</p>
            <p className="text-xs text-muted-foreground mt-1">Try expanding the date range or adjusting the status filter.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/70 bg-muted/30">
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Type</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Runner</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Vehicle</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Status</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Checked Out</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Checked In</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Duration</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Orders</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {filteredRows.map((row) => (
                  <tr key={`${row.kind}-${row.id}`} className="hover:bg-muted/30 transition-colors">
                    <td className="px-4 py-3">
                      <Badge variant={getTypeVariant(row.typeLabel)}>{row.typeLabel}</Badge>
                    </td>
                    <td className="px-4 py-3 font-medium text-foreground">{row.runner}</td>
                    <td className="px-4 py-3 text-muted-foreground">{formatVehicle(row.vehicle)}</td>
                    <td className="px-4 py-3">
                      <Badge variant={getStatusVariant(row.status)}>{row.status}</Badge>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                      {row.start_time ? formatToCentralTime(row.start_time) : "—"}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                      {row.end_time ? formatToCentralTime(row.end_time) : "—"}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {formatDuration(row.start_time, row.end_time)}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {row.orderCount != null ? row.orderCount : "—"}
                    </td>
                    <td className="px-4 py-3">
                      {row.runId ? (
                        <Link
                          to={`/delivery/runs/${row.runId}`}
                          className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                        >
                          View
                          <ExternalLink className="h-3 w-3" />
                        </Link>
                      ) : (
                        <span className="text-xs text-muted-foreground/40">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
