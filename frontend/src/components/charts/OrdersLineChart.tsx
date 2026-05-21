import type { ReactNode } from "react";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { Activity } from "lucide-react";
import { TimeTrendDataPoint } from "../../api/analytics";
import { CHART_HEIGHT_CLASS, CHART_TOOLTIP_STYLE } from "./chartTheme";
import { formatChartDateLabel } from "./chartDate";

interface OrdersLineChartProps {
  data: TimeTrendDataPoint[];
  loading?: boolean;
}

export default function OrdersLineChart({ data, loading }: OrdersLineChartProps) {
  if (loading) {
    return (
      <div className={`flex w-full items-center justify-center rounded bg-muted/20 ${CHART_HEIGHT_CLASS}`}>
        <p className="text-muted-foreground">Loading chart...</p>
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div className={`flex w-full flex-col items-center justify-center rounded bg-muted/20 ${CHART_HEIGHT_CLASS}`}>
        <Activity className="mb-2 h-6 w-6 text-[hsl(var(--chart-empty))]" />
        <p className="text-muted-foreground">No data available</p>
      </div>
    );
  }

  return (
    <div className={`${CHART_HEIGHT_CLASS} w-full min-w-0 min-h-0 overflow-hidden`}>
      <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 10, right: 24, left: 0, bottom: 5 }}>
        <defs>
          <linearGradient id="lineGradient" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="hsl(var(--accent))" stopOpacity={0.9} />
            <stop offset="100%" stopColor="hsl(var(--accent) / 0.7)" stopOpacity={0.9} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="4 6" className="stroke-muted/40" />
        <XAxis 
          dataKey="date" 
          className="text-xs"
          tickFormatter={(value: string | number) =>
            formatChartDateLabel(value, { month: "short", day: "numeric" })
          }
        />
        <YAxis className="text-xs" />
        <Tooltip 
          contentStyle={CHART_TOOLTIP_STYLE}
          labelFormatter={(label: ReactNode) => {
            if (typeof label !== "string" && typeof label !== "number") {
              return "";
            }
            return formatChartDateLabel(label, {
              weekday: 'short',
              month: 'short',
              day: 'numeric',
              year: 'numeric'
            });
          }}
        />
        <Line 
          type="monotone" 
          dataKey="count" 
          stroke="url(#lineGradient)" 
          strokeWidth={2.5}
          dot={{ fill: "hsl(var(--accent))", r: 4, strokeWidth: 2, stroke: "hsl(var(--card))" }}
          activeDot={{ r: 6, stroke: "hsl(var(--accent))", strokeWidth: 2 }}
          name="Delivered"
        />
      </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
