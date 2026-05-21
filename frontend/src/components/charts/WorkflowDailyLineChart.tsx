import type { ReactNode } from "react";
import { Activity } from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { WorkflowDailyTrendDataPoint } from "../../api/analytics";
import { CHART_COLORS, CHART_HEIGHT_CLASS, CHART_TOOLTIP_STYLE } from "./chartTheme";
import { formatChartDateLabel } from "./chartDate";

interface WorkflowDailyLineChartProps {
  data: WorkflowDailyTrendDataPoint[];
  loading?: boolean;
}

export default function WorkflowDailyLineChart({ data, loading }: WorkflowDailyLineChartProps) {
  if (loading) {
    return (
      <div className={`flex w-full items-center justify-center rounded bg-muted/20 ${CHART_HEIGHT_CLASS} overflow-hidden`}>
        <p className="text-muted-foreground">Loading chart...</p>
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div className={`flex w-full flex-col items-center justify-center rounded bg-muted/20 ${CHART_HEIGHT_CLASS} overflow-hidden`}>
        <Activity className="mb-2 h-6 w-6 text-[hsl(var(--chart-empty))]" />
        <p className="text-muted-foreground">No data available</p>
      </div>
    );
  }

  return (
    <div className={`${CHART_HEIGHT_CLASS} w-full min-w-0 min-h-0 overflow-hidden`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 10, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="4 6" className="stroke-muted/40" />
          <XAxis
            dataKey="date"
            className="text-xs"
            tickFormatter={(value: string | number) =>
              formatChartDateLabel(value, { month: "short", day: "numeric" })
            }
          />
          <YAxis className="text-xs" allowDecimals={false} />
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            labelFormatter={(label: ReactNode) => {
              if (typeof label !== "string" && typeof label !== "number") {
                return "";
              }
              return formatChartDateLabel(label, {
                weekday: "short",
                month: "short",
                day: "numeric",
                year: "numeric",
              });
            }}
          />
          <Legend />
          <Line
            type="monotone"
            dataKey="shipped_count"
            stroke={CHART_COLORS.shipped}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 5 }}
            name="Shipped"
          />
          <Line
            type="monotone"
            dataKey="delivered_count"
            stroke={CHART_COLORS.delivered}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 5 }}
            name="Delivered"
          />
          <Line
            type="monotone"
            dataKey="fulfilled_count"
            stroke={CHART_COLORS.fulfilled}
            strokeWidth={2.5}
            dot={false}
            activeDot={{ r: 5 }}
            name="Fulfilled/Completed"
          />
          <Line
            type="monotone"
            dataKey="picked_count"
            stroke={CHART_COLORS.picked}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 5 }}
            name="Picked"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
