import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Download, Loader2, PackageCheck, RefreshCw, Search, ShieldCheck } from "lucide-react";

import { PRODUCT_CHECKER_SECTIONS, productCheckerApi } from "../api/productChecker";
import type { ProductCheckerReport, ProductCheckerResponse, ProductCheckerSection } from "../api/productChecker";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { useAuth } from "../contexts/AuthContext";
import { cn } from "../lib/utils";
import { extractApiErrorMessage } from "../utils/apiErrors";

const PAGE_SIZE = 50;

function downloadReport(report: ProductCheckerReport, format: "json" | "txt") {
  const content = format === "json" ? JSON.stringify(report, null, 2) : [
    `Product Checker — ${new Date(report.completed_at).toLocaleString()}`,
    ...PRODUCT_CHECKER_SECTIONS.map(({ key, label }) => [
      `\n${label} (${report.reports[key].length})`,
      ...report.reports[key].map((row) => ` - ${row.name} (SKU: ${JSON.stringify(row.sku)}): ${row.details.join("; ")}`),
      ...(report.reports[key].length === 0 ? [" (none)"] : []),
    ].join("\n")),
  ].join("\n");
  const url = URL.createObjectURL(new Blob([content], { type: format === "json" ? "application/json" : "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `product_differences_${report.completed_at.slice(0, 10)}.${format}`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function ProductChecker() {
  const { isAdmin, isLoading: authLoading } = useAuth();
  const [data, setData] = useState<ProductCheckerResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [section, setSection] = useState<ProductCheckerSection>("missing_in_bigcommerce");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const report = data?.report;
  const running = data?.job?.status === "running";

  useEffect(() => {
    if (authLoading || !isAdmin) return;
    let disposed = false;
    setLoading(true);
    productCheckerApi.getData().then((response) => {
      if (!disposed) { setData(response); setError(null); }
    }).catch((err: unknown) => {
      if (!disposed) setError(extractApiErrorMessage(err, "Failed to load the product checker."));
    }).finally(() => { if (!disposed) setLoading(false); });
    return () => { disposed = true; };
  }, [isAdmin, authLoading, reload]);

  useEffect(() => {
    if (!isAdmin || authLoading || !running) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const status = await productCheckerApi.getStatus();
        if (disposed) return;
        if (status.job?.status === "running") {
          setData((previous) => previous ? { ...previous, ...status } : null);
        } else {
          const response = await productCheckerApi.getData();
          if (disposed) return;
          setData(response);
          setError(null);
          setPage(1);
          return;
        }
        setError(null);
      } catch (err: unknown) {
        if (!disposed) setError(extractApiErrorMessage(err, "Could not check scan progress. Retrying…"));
      }
      if (!disposed) timer = setTimeout(() => void poll(), 3000);
    };
    timer = setTimeout(() => void poll(), 1500);
    return () => { disposed = true; clearTimeout(timer); };
  }, [isAdmin, authLoading, running]);

  const selectedSection = PRODUCT_CHECKER_SECTIONS.find((item) => item.key === section)!;
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (report?.reports[section] ?? []).filter((row) =>
      !needle || [row.name, row.sku, ...row.details].join(" ").toLowerCase().includes(needle)
    );
  }, [report, section, search]);
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      const status = await productCheckerApi.refresh();
      setData((previous) => ({ report: previous?.report ?? null, ...status }));
    } catch (err: unknown) {
      setError(extractApiErrorMessage(err, "Failed to start the product check."));
    } finally {
      setStarting(false);
    }
  };

  if (authLoading) return <div role="status" className="p-8 text-muted-foreground">Checking access…</div>;
  if (!isAdmin) return (
    <div role="alert" className="p-8">
      <h1 className="text-xl font-semibold">Admin access required</h1>
      <p className="mt-2 text-muted-foreground">The Product Checker is available only to administrators.</p>
    </div>
  );

  return (
    <div className="space-y-6 p-4 md:p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <PackageCheck className="h-7 w-7 text-primary" />
            <h1 className="text-2xl font-semibold">Product Checker</h1>
            <Badge variant="secondary"><ShieldCheck className="mr-1 h-3 w-3" />Admin</Badge>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">Compare BigCommerce and inFlow products using the app’s existing connections.</p>
          <p className="mt-1 text-xs text-muted-foreground">Read-only checks. Product data is never changed.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {report && <>
            <Button variant="outline" onClick={() => downloadReport(report, "json")}>
              <Download className="mr-2 h-4 w-4" />JSON
            </Button>
            <Button variant="outline" onClick={() => downloadReport(report, "txt")}>
              <Download className="mr-2 h-4 w-4" />Text
            </Button>
          </>}
          <Button onClick={() => void start()} disabled={loading || starting || running || !data?.config.configured}>
            {starting || running ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}{running ? "Checking products…" : "Run product check"}
          </Button>
        </div>
      </header>

      {error && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm">
          <span>{error}</span>
          {!running && <Button variant="outline" onClick={() => setReload((value) => value + 1)}>Retry loading</Button>}
        </div>
      )}
      {data && !data.config.configured && (
        <div role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
          Existing connections are incomplete: {data.config.missing.join(", ")}.
          Configure them on the server to run the checker.
        </div>
      )}
      {data?.job?.status === "failed" && (
        <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm">
          <p className="font-medium">The latest scan failed</p>
          <p>{data.job.error}</p>
          {report && <p className="mt-1">Showing the last successful report from {new Date(report.completed_at).toLocaleString()}.</p>}
        </div>
      )}
      {running && data?.job && (
        <div role="status" className="space-y-3 rounded-lg border bg-card p-4">
          <div className="flex justify-between gap-3 text-sm">
            <span>{data.job.message}</span><span>{data.job.progress}%</span>
          </div>
          <div
            role="progressbar"
            aria-label="Product scan progress"
            aria-valuenow={data.job.progress}
            aria-valuemin={0}
            aria-valuemax={100}
            className="h-2 overflow-hidden rounded-full bg-muted"
          >
            <div className="h-full bg-primary transition-all" style={{ width: `${data.job.progress}%` }} />
          </div>
          <p className="text-xs text-muted-foreground">
            You can leave this page and return to the scan. {report ? "The previous report remains visible below." : "Results appear when the scan completes."}
          </p>
        </div>
      )}

      {loading && !data ? (
        <div role="status" className="flex items-center justify-center gap-2 py-16 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />Loading product checker…
        </div>
      ) : !report ? (
        <div className="rounded-xl border border-dashed p-12 text-center">
          <PackageCheck className="mx-auto mb-4 h-10 w-10 text-muted-foreground" />
          <h2 className="text-lg font-medium">{running ? "Your product check is running" : "No product report yet"}</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            {running ? "Both catalogs and their variants are being loaded." : "Run a product check to see missing products, price differences, closeouts, and catalog issues."}
          </p>
        </div>
      ) : <>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[
            ["Visible BC products", report.summary.bigcommerce_products],
            ["Eligible inFlow products", report.summary.inflow_eligible],
            ["Matched SKUs", report.summary.matched_skus],
            ["Findings", report.summary.total_findings],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg border bg-card p-4">
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">{value.toLocaleString()}</p>
            </div>
          ))}
        </div>
        <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
          <span>Last completed: {new Date(report.completed_at).toLocaleString()} · Run by {report.started_by}</span>
          <span>{report.summary.inflow_excluded} inFlow products excluded (Internal, Category Needed, Testing). Includes {report.summary.inflow_inactive} inactive products before exclusions.</span>
        </div>

        <div className="grid gap-4 lg:grid-cols-[250px_minmax(0,1fr)]">
          <nav aria-label="Product report categories" className="flex flex-col gap-1 rounded-lg border bg-card p-2">
            {PRODUCT_CHECKER_SECTIONS.map(({ key, label }) => (
              <button
                key={key}
                type="button"
                aria-pressed={section === key}
                onClick={() => { setSection(key); setPage(1); }}
                className={cn(
                  "flex items-center justify-between gap-3 rounded-md px-3 py-3 text-left text-sm transition-colors",
                  section === key ? "bg-accent font-medium text-accent-foreground" : "text-muted-foreground hover:bg-muted/50"
                )}
              >
                <span>{label}</span>
                <Badge variant="secondary" className="tabular-nums">{report.reports[key].length}</Badge>
              </button>
            ))}
          </nav>
          <section aria-label={selectedSection.label} className="min-w-0 overflow-hidden rounded-lg border bg-card">
            <div className="space-y-3 border-b p-4">
              <h2 className="text-lg font-semibold">{selectedSection.label}</h2>
              <p className="text-sm text-muted-foreground">{selectedSection.description}</p>
              <div className="relative">
                <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  aria-label="Search report"
                  placeholder="Search by product, SKU, or issue…"
                  value={search}
                  onChange={(event) => { setSearch(event.target.value); setPage(1); }}
                  className="pl-9"
                />
              </div>
            </div>
            {rows.length === 0 ? (
              <div className="p-10 text-center">
                <CheckCircle2 className="mx-auto mb-3 h-7 w-7 text-muted-foreground" />
                <p className="text-sm text-muted-foreground">
                  {search ? "No findings match your search in this category." : "No findings in this category."}
                </p>
              </div>
            ) : <>
              <Table>
                <TableHeader>
                  <TableRow><TableHead>Product</TableHead><TableHead>SKU</TableHead><TableHead>Details</TableHead></TableRow>
                </TableHeader>
                <TableBody>
                  {rows.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE).map((row, index) => (
                    <TableRow key={`${row.sku}-${index}`}>
                      <TableCell className="min-w-[180px] align-top font-medium">{row.name || "Unnamed product"}</TableCell>
                      <TableCell className="align-top">
                        <code className="whitespace-pre-wrap break-all rounded bg-muted px-1.5 py-1 text-xs">
                          {row.sku ? JSON.stringify(row.sku) : "(no SKU)"}
                        </code>
                      </TableCell>
                      <TableCell className="min-w-[240px] align-top text-sm">
                        <ul className="space-y-1">
                          {row.details.map((detail, detailIndex) => (
                            <li key={detailIndex} className="flex items-start gap-2">
                              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
                              <span className="whitespace-pre-wrap break-words">{detail}</span>
                            </li>
                          ))}
                        </ul>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <div className="flex flex-wrap items-center justify-between gap-2 border-t p-3 text-xs text-muted-foreground">
                <span>{rows.length} findings · Page {currentPage} of {pageCount}</span>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>Previous</Button>
                  <Button variant="outline" size="sm" disabled={currentPage === pageCount} onClick={() => setPage(currentPage + 1)}>Next</Button>
                </div>
              </div>
            </>}
          </section>
        </div>
      </>}
    </div>
  );
}
