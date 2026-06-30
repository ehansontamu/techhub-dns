import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Clock, ExternalLink } from "lucide-react";

import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { getDeliveryRunHistoryQueryOptions } from "../../queries/deliveryRuns";
import { formatToCentralTime } from "../../utils/timezone";
import type { DeliveryRunResponse } from "../../api/deliveryRuns";

// ── Date helpers ──────────────────────────────────────────────────────────────

function toDateInputValue(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function todayLocal(): Date {
  return new Date();
}

function daysAgo(n: number): Date {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d;
}

function startOfMonth(): Date {
  const d = new Date();
  d.setDate(1);
  return d;
}

function startOfLastMonth(): Date {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return d;
}

function endOfLastMonth(): Date {
  const d = new Date();
  d.setDate(0); // last day of previous month
  return d;
}

const PRESETS = [
  { label: "Today", start: () => todayLocal(), end: () => todayLocal() },
  { label: "Last 7 days", start: () => daysAgo(6), end: () => todayLocal() },
  { label: "Last 30 days", start: () => daysAgo(29), end: () => todayLocal() },
  { label: "This month", start: () => startOfMonth(), end: () => todayLocal() },
  { label: "Last month", start: () => startOfLastMonth(), end: () => endOfLastMonth() },
] as const;

const STATUS_OPTIONS = [
  { value: "Completed", label: "Completed" },
  { value: "Cancelled", label: "Cancelled" },
  { value: "Active", label: "Active" },
] as const;

// ── Formatting helpers ────────────────────────────────────────────────────────

function formatVehicle(vehicle: string): string {
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

// ── Component ─────────────────────────────────────────────────────────────────

export default function DeliveryHistory() {
  const defaultStart = toDateInputValue(daysAgo(29));
  const defaultEnd = toDateInputValue(todayLocal());

  const [startDate, setStartDate] = useState(defaultStart);
  const [endDate, setEndDate] = useState(defaultEnd);
  const [activePreset, setActivePreset] = useState<string>("Last 30 days");
  const [selectedStatuses, setSelectedStatuses] = useState<string[]>(["Completed", "Cancelled"]);

  // The query params we actually fetch with — only update when "Apply" is clicked or preset fires
  const [fetchParams, setFetchParams] = useState({
    status: ["Completed", "Cancelled"],
    start_date: defaultStart,
    end_date: defaultEnd,
  });

  const { data: runs, isLoading, isError, refetch } = useQuery(
    getDeliveryRunHistoryQueryOptions(fetchParams)
  );

  function applyPreset(preset: typeof PRESETS[number]) {
    const s = toDateInputValue(preset.start());
    const e = toDateInputValue(preset.end());
    setStartDate(s);
    setEndDate(e);
    setActivePreset(preset.label);
    setFetchParams({ status: selectedStatuses, start_date: s, end_date: e });
  }

  function handleApply() {
    setActivePreset("");
    setFetchParams({ status: selectedStatuses, start_date: startDate, end_date: endDate });
  }

  function toggleStatus(value: string) {
    setSelectedStatuses((prev) =>
      prev.includes(value) ? prev.filter((s) => s !== value) : [...prev, value]
    );
  }

  const displayRuns: DeliveryRunResponse[] = runs ?? [];

  return (
    <div className="space-y-5">
      {/* ── Filters ── */}
      <div className="rounded-2xl border border-border/70 bg-card/80 p-4 shadow-none space-y-4">
        {/* Presets */}
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

        {/* Custom date range */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground font-medium">From</label>
            <Input
              type="date"
              value={startDate}
              max={endDate}
              onChange={(e) => {
                setStartDate(e.target.value);
                setActivePreset("");
              }}
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
              onChange={(e) => {
                setEndDate(e.target.value);
                setActivePreset("");
              }}
              className="h-9 w-40 text-sm"
            />
          </div>

          {/* Status filter */}
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

          <Button size="sm" className="h-9" onClick={handleApply} disabled={selectedStatuses.length === 0}>
            Apply
          </Button>
        </div>
      </div>

      {/* ── Results ── */}
      <div className="rounded-2xl border border-border/70 bg-card/80 shadow-none overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border/70">
          <div>
            <h2 className="text-sm font-semibold">Run History</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              {isLoading
                ? "Loading..."
                : `${displayRuns.length} run${displayRuns.length !== 1 ? "s" : ""} found`}
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isLoading}>
            Refresh
          </Button>
        </div>

        {isError ? (
          <div className="px-5 py-10 text-center text-sm text-destructive">
            Failed to load run history. Check your date range and try again.
          </div>
        ) : isLoading ? (
          <div className="px-5 py-10 text-center text-sm text-muted-foreground">
            Loading run history...
          </div>
        ) : displayRuns.length === 0 ? (
          <div className="px-5 py-10 text-center">
            <Clock className="mx-auto mb-3 h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">No runs found for this date range.</p>
            <p className="text-xs text-muted-foreground mt-1">Try expanding the date range or adjusting the status filter.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/70 bg-muted/30">
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Run</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Runner</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Vehicle</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Status</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Started</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Ended</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Duration</th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Orders</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {displayRuns.map((run) => (
                  <tr key={run.id} className="hover:bg-muted/30 transition-colors">
                    <td className="px-4 py-3 font-medium text-foreground">{run.name}</td>
                    <td className="px-4 py-3 text-muted-foreground">{run.runner}</td>
                    <td className="px-4 py-3 text-muted-foreground">{formatVehicle(run.vehicle)}</td>
                    <td className="px-4 py-3">
                      <Badge variant={getStatusVariant(run.status)}>{run.status}</Badge>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                      {run.start_time ? formatToCentralTime(run.start_time) : "—"}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                      {run.end_time ? formatToCentralTime(run.end_time) : "—"}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {formatDuration(run.start_time, run.end_time ?? undefined)}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {run.order_ids != null ? run.order_ids.length : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <Link
                        to={`/delivery/runs/${run.id}`}
                        className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                      >
                        View
                        <ExternalLink className="h-3 w-3" />
                      </Link>
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
