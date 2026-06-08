import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Bot,
  ExternalLink,
  Loader2,
  RotateCcw,
  Send,
  UserRound,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  bigcommerceChatApi,
  type BigCommerceChartData,
  type BigCommerceCacheStatus,
  type BigCommerceChatMessage,
} from "../api/bigcommerceChat";
import { Button } from "../components/ui/button";
import { extractApiErrorMessage } from "../utils/apiErrors";
import { cn } from "../lib/utils";

const ORDER_ADMIN_BASE_URL = "https://store-jsj7fos9p1.mybigcommerce.com/manage/orders";
const ORDER_LINK_SECTION_RE = /\n{0,2}Order links:\n(?:- Order \d{1,}: https?:\/\/[^\s\n]+\n?)+\s*$/i;
const ORDER_URL_AFTER_LABEL_RE = /\s*\(https:\/\/store-jsj7fos9p1\.mybigcommerce\.com\/manage\/orders\/\d{1,}\)/g;
const ORDER_URL_AFTER_COLON_RE = /\bOrder\s+#?(\d{1,})[:\s-]+https:\/\/store-jsj7fos9p1\.mybigcommerce\.com\/manage\/orders\/\1/g;
const ORDER_URL_RE = /https:\/\/store-jsj7fos9p1\.mybigcommerce\.com\/manage\/orders\/(\d{1,})/g;
const TOOL_CALL_TEXT_RE = /\bto=functions\.[A-Za-z_]\w*\b.*?(?=\n\n|$)/gs;
const MESSAGE_TOKEN_RE = /\*\*([^*\n]+)\*\*|\bOrder\s+#?(\d{1,})\b|(https?:\/\/[^\s)]+)/gi;
const MARKDOWN_TABLE_SEPARATOR_RE = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/;
const MARKDOWN_HEADING_RE = /^(#{1,6})\s+(.+)$/;
const DISPLAY_TIME_ZONE = "America/Chicago";
const CHART_COLORS = [
  "#2563eb",
  "#16a34a",
  "#dc2626",
  "#9333ea",
  "#ea580c",
  "#0891b2",
  "#4f46e5",
  "#65a30d",
];

function chatErrorMessage(error: unknown): string {
  if (typeof error === "object" && error !== null && "response" in error) {
    const candidate = error as { response?: { status?: unknown } };
    if (candidate.response?.status === 504) {
      return "Store Intelligence timed out. Try a narrower question or a shorter date range.";
    }
  }

  return extractApiErrorMessage(error, "Store Intelligence is unavailable.");
}

function cleanMessageText(text: string) {
  return text
    .replace(ORDER_LINK_SECTION_RE, "")
    .replace(ORDER_URL_AFTER_LABEL_RE, "")
    .replace(ORDER_URL_AFTER_COLON_RE, "Order $1")
    .replace(ORDER_URL_RE, "Order $1")
    .replace(TOOL_CALL_TEXT_RE, "")
    .trimEnd();
}

function renderMessageTokens(text: string, keyPrefix: string, enableBold = true): JSX.Element[] {
  const parts: JSX.Element[] = [];
  let position = 0;

  for (const match of text.matchAll(MESSAGE_TOKEN_RE)) {
    const start = match.index ?? 0;
    if (start > position) {
      parts.push(
        <span key={`${keyPrefix}-text-${start}`}>{text.slice(position, start)}</span>
      );
    }

    const boldText = match[1];
    const orderId = match[2];
    const url = match[3];
    if (boldText && enableBold) {
      parts.push(
        <strong key={`${keyPrefix}-bold-${start}`} className="font-semibold">
          {renderMessageTokens(boldText, `${keyPrefix}-bold-${start}`, false)}
        </strong>
      );
    } else if (orderId) {
      const label = match[0];
      parts.push(
        <a
          key={`${keyPrefix}-order-${orderId}-${start}`}
          href={`${ORDER_ADMIN_BASE_URL}/${orderId}`}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
        >
          {label}
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      );
    } else if (url) {
      parts.push(
        <a
          key={`${keyPrefix}-url-${start}`}
          href={url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline"
        >
          Open link
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      );
    }

    position = start + match[0].length;
  }

  if (position < text.length) {
    parts.push(<span key={`${keyPrefix}-text-end`}>{text.slice(position)}</span>);
  }

  return parts;
}

function parseMarkdownTable(lines: string[]) {
  if (lines.length < 3 || !lines[0].includes("|") || !MARKDOWN_TABLE_SEPARATOR_RE.test(lines[1])) {
    return null;
  }

  const splitRow = (line: string) =>
    line
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((cell) => cell.trim());

  const headers = splitRow(lines[0]);
  const rows = lines.slice(2).map(splitRow);
  if (!headers.length || rows.some((row) => row.length !== headers.length)) {
    return null;
  }

  return { headers, rows };
}

function renderMarkdownTable(lines: string[], keyPrefix: string) {
  const table = parseMarkdownTable(lines);
  if (!table) {
    return <span key={keyPrefix}>{lines.join("\n")}</span>;
  }

  return (
    <div key={keyPrefix} className="my-2 max-w-full overflow-x-auto rounded-md border border-border">
      <table className="min-w-full border-collapse text-left text-xs">
        <thead className="bg-muted/60 text-muted-foreground">
          <tr>
            {table.headers.map((header, index) => (
              <th
                key={`${keyPrefix}-head-${index}`}
                scope="col"
                className="whitespace-nowrap border-b border-border px-3 py-2 font-semibold"
              >
                {renderMessageTokens(header, `${keyPrefix}-head-${index}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, rowIndex) => (
            <tr key={`${keyPrefix}-row-${rowIndex}`} className="odd:bg-background even:bg-muted/20">
              {row.map((cell, cellIndex) => (
                <td
                  key={`${keyPrefix}-cell-${rowIndex}-${cellIndex}`}
                  className="whitespace-nowrap border-b border-border/70 px-3 py-2 align-top last:border-b"
                >
                  {renderMessageTokens(cell, `${keyPrefix}-cell-${rowIndex}-${cellIndex}`)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderTextBlock(lines: string[], keyPrefix: string) {
  return lines.map((line, lineIndex) => {
    const headingMatch = line.match(MARKDOWN_HEADING_RE);
    if (headingMatch) {
      return (
        <div
          key={`${keyPrefix}-heading-${lineIndex}`}
          className="mt-3 font-semibold first:mt-0"
        >
          {renderMessageTokens(headingMatch[2], `${keyPrefix}-heading-${lineIndex}`)}
        </div>
      );
    }

    return (
      <span key={`${keyPrefix}-line-${lineIndex}`}>
        {renderMessageTokens(line, `${keyPrefix}-line-${lineIndex}`)}
        {lineIndex < lines.length - 1 ? "\n" : null}
      </span>
    );
  });
}

function renderMessageText(text: string) {
  const cleanedText = cleanMessageText(text);
  const lines = cleanedText.split("\n");
  const parts: JSX.Element[] = [];
  let index = 0;

  while (index < lines.length) {
    if (
      index + 2 < lines.length &&
      lines[index].includes("|") &&
      MARKDOWN_TABLE_SEPARATOR_RE.test(lines[index + 1])
    ) {
      const tableLines = [lines[index], lines[index + 1]];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        tableLines.push(lines[index]);
        index += 1;
      }
      parts.push(renderMarkdownTable(tableLines, `message-table-${parts.length}`));
      continue;
    }

    const start = index;
    while (
      index < lines.length &&
      !(
        index + 2 < lines.length &&
        lines[index].includes("|") &&
        MARKDOWN_TABLE_SEPARATOR_RE.test(lines[index + 1])
      )
    ) {
      index += 1;
    }
    parts.push(
      <span key={`message-text-${parts.length}`}>
        {renderTextBlock(lines.slice(start, index), `message-text-${parts.length}`)}
      </span>
    );
  }

  return parts;
}

function formatCacheTimestamp(value: string | null) {
  if (!value) {
    return "not synced yet";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString([], {
    timeZone: DISPLAY_TIME_ZONE,
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

function formatChartValue(value: unknown, valueKind: BigCommerceChartData["valueKind"]) {
  const numericValue = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numericValue)) {
    return String(value ?? "");
  }

  if (valueKind === "percent") {
    return `${numericValue.toFixed(2)}%`;
  }
  if (valueKind === "currency") {
    return numericValue.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0,
    });
  }
  return numericValue.toLocaleString();
}

function BigCommerceChatChart({ chart }: { chart: BigCommerceChartData }) {
  if (!chart.data.length || !chart.series.length) {
    return null;
  }

  const tooltipFormatter = (value: unknown, name: unknown) => [
    formatChartValue(value, chart.valueKind),
    String(name),
  ];
  const yTickFormatter = (value: unknown) => formatChartValue(value, chart.valueKind);
  const xTickFormatter = (value: unknown) => formatChartValue(value, chart.xValueKind);

  if (chart.type === "pie") {
    const firstSeries = chart.series[0];
    return (
      <div className="mt-3 h-72 rounded-md border border-border bg-muted/10 p-3">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Tooltip formatter={tooltipFormatter} />
            <Legend />
            <Pie
              data={chart.data}
              dataKey={firstSeries.key}
              nameKey={chart.xKey}
              innerRadius={52}
              outerRadius={92}
              paddingAngle={2}
            >
              {chart.data.map((entry, index) => (
                <Cell
                  key={`chart-slice-${String(entry[chart.xKey])}-${index}`}
                  fill={CHART_COLORS[index % CHART_COLORS.length]}
                />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
      </div>
    );
  }

  if (chart.type === "scatter") {
    const firstSeries = chart.series[0];
    return (
      <div className="mt-3 h-80 rounded-md border border-border bg-muted/10 p-3">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 10, right: 18, left: 8, bottom: 28 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
            <XAxis
              type="number"
              dataKey={chart.xKey}
              name={chart.xKey}
              tick={{ fontSize: 11 }}
              tickFormatter={xTickFormatter}
            />
            <YAxis
              type="number"
              dataKey={firstSeries.key}
              name={firstSeries.label}
              tick={{ fontSize: 11 }}
              tickFormatter={yTickFormatter}
            />
            <Tooltip
              cursor={{ strokeDasharray: "3 3" }}
              formatter={tooltipFormatter}
              labelFormatter={(_, payload) => {
                const record = payload?.[0]?.payload as Record<string, unknown> | undefined;
                const label = chart.labelKey ? record?.[chart.labelKey] : undefined;
                const xValue = record?.[chart.xKey];
                return label
                  ? `${String(label)} (${chart.xKey}: ${formatChartValue(xValue, chart.xValueKind)})`
                  : `${chart.xKey}: ${formatChartValue(xValue, chart.xValueKind)}`;
              }}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Scatter
              name={firstSeries.label}
              data={chart.data}
              fill={CHART_COLORS[0]}
              line={false}
            />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    );
  }

  const ChartComponent = chart.type === "bar" ? BarChart : LineChart;
  return (
    <div className="mt-3 h-80 rounded-md border border-border bg-muted/10 p-3">
      <ResponsiveContainer width="100%" height="100%">
        <ChartComponent data={chart.data} margin={{ top: 10, right: 16, left: 8, bottom: 28 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
          <XAxis
            dataKey={chart.xKey}
            tick={{ fontSize: 11 }}
            angle={-25}
            textAnchor="end"
            height={52}
            interval={0}
          />
          <YAxis tick={{ fontSize: 11 }} tickFormatter={yTickFormatter} />
          <Tooltip formatter={tooltipFormatter} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {chart.series.map((series, index) =>
            chart.type === "bar" ? (
              <Bar
                key={series.key}
                dataKey={series.key}
                name={series.label}
                fill={CHART_COLORS[index % CHART_COLORS.length]}
                radius={[3, 3, 0, 0]}
              />
            ) : (
              <Line
                key={series.key}
                type="monotone"
                dataKey={series.key}
                name={series.label}
                stroke={CHART_COLORS[index % CHART_COLORS.length]}
                strokeWidth={2}
                dot={{ r: 2 }}
                activeDot={{ r: 4 }}
                connectNulls
              />
            )
          )}
        </ChartComponent>
      </ResponsiveContainer>
    </div>
  );
}

export default function BigCommerceChat() {
  const [messages, setMessages] = useState<BigCommerceChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);
  const [cacheStatus, setCacheStatus] = useState<BigCommerceCacheStatus | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const canSend = useMemo(() => draft.trim().length > 0 && !isSending, [draft, isSending]);
  const cacheHealthNotes = useMemo(() => {
    if (!cacheStatus) {
      return [];
    }

    const notes: string[] = [];
    if (cacheStatus.latest_sync?.status === "failed") {
      notes.push(`latest sync failed${cacheStatus.latest_sync.error ? `: ${cacheStatus.latest_sync.error}` : ""}`);
    }
    if (cacheStatus.catalog_tables_available === false) {
      notes.push("catalog tables are missing");
    } else if ((cacheStatus.product_count ?? 0) === 0) {
      notes.push("catalog cache is empty");
    }
    const catalogSync = cacheStatus.last_catalog_sync;
    if (catalogSync && typeof catalogSync.error === "string") {
      notes.push(`catalog sync failed: ${catalogSync.error}`);
    }
    return notes;
  }, [cacheStatus]);

  useEffect(() => {
    transcriptRef.current?.scrollTo({
      top: transcriptRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, isSending]);

  useEffect(() => {
    let isMounted = true;

    const loadStatus = async () => {
      try {
        const status = await bigcommerceChatApi.cacheStatus();
        if (isMounted) {
          setCacheStatus(status);
        }
      } catch {
        if (isMounted) {
          setCacheStatus(null);
        }
      }
    };

    void loadStatus();
    const interval = window.setInterval(() => {
      void loadStatus();
    }, 60000);

    return () => {
      isMounted = false;
      window.clearInterval(interval);
    };
  }, []);

  const sendQuestion = async () => {
    const question = draft.trim();
    if (!question || isSending) {
      return;
    }

    const nextMessages: BigCommerceChatMessage[] = [
      ...messages,
      { role: "user", content: question },
    ];

    setDraft("");
    setMessages(nextMessages);
    setErrorMessage(null);
    setIsSending(true);

    try {
      const response = await bigcommerceChatApi.ask(question, messages);
      const responseMessages = response.messages.map((message, index) =>
        index === response.messages.length - 1 && message.role === "assistant"
          ? { ...message, chart: response.chart ?? null }
          : message
      );
      setMessages(responseMessages);
    } catch (error) {
      setMessages(nextMessages);
      setErrorMessage(chatErrorMessage(error));
    } finally {
      setIsSending(false);
      inputRef.current?.focus();
    }
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    void sendQuestion();
  };

  return (
    <div className="mx-auto flex h-[calc(100vh-8rem)] min-h-[560px] w-full max-w-6xl flex-col gap-4 py-4 sm:py-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Store Intelligence
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Read-only store assistant; information can and will be wrong. Double check what you can. This is a preliminary test of functionality.
          </p>
          {cacheStatus && (
            <p
              className={cn(
                "mt-1 text-xs",
                cacheStatus.is_stale ? "text-amber-600" : "text-muted-foreground"
              )}
            >
              Data last synced {formatCacheTimestamp(cacheStatus.last_successful_sync?.completed_at ?? null)}
              {" | "}
              {cacheStatus.order_count.toLocaleString()} orders
              {" | "}
              {cacheStatus.line_item_count.toLocaleString()} line items
              {" | "}
              {(cacheStatus.product_count ?? 0).toLocaleString()} products
              {" | "}
              {(cacheStatus.variant_count ?? 0).toLocaleString()} variants
              {" | "}
              {(cacheStatus.product_intelligence_count ?? 0).toLocaleString()} intelligence items
            </p>
          )}
          {cacheHealthNotes.length > 0 && (
            <p className="mt-1 max-w-4xl text-xs text-amber-700">
              Cache warning: {cacheHealthNotes.join("; ")}
            </p>
          )}
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => {
            setMessages([]);
            setErrorMessage(null);
            inputRef.current?.focus();
          }}
          disabled={isSending || messages.length === 0}
          aria-label="Start over"
          title="Start over"
        >
          <RotateCcw className="mr-2 h-4 w-4" />
          Start over
        </Button>
        <Button
          type="button"
          variant="maroon"
          onClick={() => {
            window.open("/chat-popout.html", "MCPChatPopout", "width=600,height=800,toolbar=no,location=no,status=no,menubar=no,scrollbars=yes,resizable=yes");
          }}
          aria-label="Pop out Store Intelligence"
          title="Pop out"
          className="ml-2"
        >
          <ExternalLink className="mr-2 h-4 w-4" />
          Pop out
        </Button>
      </div>

      <section className="flex min-h-0 flex-1 flex-col rounded-lg border border-border bg-card shadow-sm">
        <div
          ref={transcriptRef}
          className="custom-scrollbar min-h-0 flex-1 space-y-4 overflow-y-auto p-4 sm:p-5"
          aria-live="polite"
        >
          {messages.length === 0 ? (
            <div className="flex h-full min-h-[280px] items-center justify-center text-center">
              <div className="max-w-sm space-y-3">
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-lg bg-secondary text-secondary-foreground">
                  <Bot className="h-6 w-6" />
                </div>
                <div>
                  <p className="text-base font-medium text-foreground">
                    Store Intelligence is ready.
                  </p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Ask a store question to begin.
                  </p>
                </div>
              </div>
            </div>
          ) : (
            messages.map((message, index) => {
              const isUser = message.role === "user";
              const Icon = isUser ? UserRound : Bot;
              return (
                <article
                  key={`${message.role}-${index}-${message.content.slice(0, 20)}`}
                  className={cn(
                    "flex gap-3",
                    isUser ? "justify-end" : "justify-start"
                  )}
                >
                  {!isUser && (
                    <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-secondary text-secondary-foreground">
                      <Icon className="h-4 w-4" />
                    </div>
                  )}
                  <div
                    className={cn(
                      "max-w-[min(42rem,92%)] whitespace-pre-wrap rounded-lg border px-4 py-3 text-sm leading-6",
                      message.chart && !isUser && "max-w-[min(56rem,96%)]",
isUser
    ? "border-maroon-800 bg-maroon-700 text-white"
    : "border-border bg-background text-foreground"
                    )}
                  >
                    {renderMessageText(message.content)}
                    {!isUser && message.chart && <BigCommerceChatChart chart={message.chart} />}
                  </div>
                  {isUser && (
                    <div className="mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                      <Icon className="h-4 w-4" />
                    </div>
                  )}
                </article>
              );
            })
          )}

          {isSending && (
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-secondary text-secondary-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
              </div>
              Thinking...
            </div>
          )}
        </div>

        {errorMessage && (
          <div className="mx-4 mb-3 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive sm:mx-5">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{errorMessage}</span>
          </div>
        )}

        <form
          onSubmit={handleSubmit}
          className="border-t border-border p-3 sm:p-4"
        >
          <div className="flex gap-3">
            <textarea
              ref={inputRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void sendQuestion();
                }
              }}
              rows={2}
              maxLength={4000}
              className="min-h-12 flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground outline-none ring-offset-background placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              placeholder="Ask Store Intelligence..."
              aria-label="Ask Store Intelligence"
              disabled={isSending}
            />
<Button
               type="submit"
               size="icon"
               variant="maroon"
               disabled={!canSend}
               aria-label="Send"
               title="Send"
               className="h-auto min-h-12 w-12"
             >
              {isSending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          </div>
        </form>
      </section>
    </div>
  );
}
