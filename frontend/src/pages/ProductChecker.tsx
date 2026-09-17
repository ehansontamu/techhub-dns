import { useEffect, useMemo, useState } from "react";
import { Download, Info, Loader2, PackageCheck, RefreshCw, Search, ShieldCheck } from "lucide-react";

import { PRODUCT_CHECKER_SECTIONS, productCheckerApi } from "../api/productChecker";
import type { ProductCheckerReport, ProductCheckerResponse, ProductCheckerSection } from "../api/productChecker";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { useAuth } from "../contexts/AuthContext";
import { cn } from "../lib/utils";
import { extractApiErrorMessage } from "../utils/apiErrors";
import { describeProductCheckerReport, PRODUCT_CHECKER_SCOPE } from "../utils/productChecker";

const PAGE_SIZE = 50;

function downloadReport(report: ProductCheckerReport, format: "json" | "txt") {
  const guide = {
    scope: PRODUCT_CHECKER_SCOPE,
    counts: "Counts are report rows, not unique products or a total of individual field differences.",
    categories: Object.fromEntries(PRODUCT_CHECKER_SECTIONS.map(({ key, label, description, notes }) => [key, { label, description, notes }])),
  };
  const content = format === "json" ? JSON.stringify({ ...report, report_guide: guide }, null, 2) : [
    `Product Checker — scan completed ${new Date(report.completed_at).toLocaleString()}`,
    guide.scope,
    guide.counts,
    "This export includes every category from the saved scan, regardless of the current search.",
    ...PRODUCT_CHECKER_SECTIONS.map(({ key, label, description, notes }) => [
      `\n${label} (${report.reports[key].length} rows)`,
      description,
      ...notes,
      ...report.reports[key].map((row) => ` - ${row.name} (SKU: ${JSON.stringify(row.sku)}): ${row.details.join("; ")}`),
      ...(report.reports[key].length === 0 ? [" No rows met this category's rules in this scan."] : []),
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
  const report = useMemo(() => data?.report ? describeProductCheckerReport(data.report) : null, [data?.report]);
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
          <p className="mt-2 text-sm text-muted-foreground">Compare SKUs from visible BigCommerce products with included inFlow records.</p>
          <p className="mt-1 text-xs text-muted-foreground">Read-only scan using the app’s existing connections. Products, prices, stock, and visibility are never changed.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {report && <>
            <Button variant="outline" title="Download all categories from the displayed scan, regardless of search" onClick={() => downloadReport(report, "json")}>
              <Download className="mr-2 h-4 w-4" />Export JSON
            </Button>
            <Button variant="outline" title="Download all categories from the displayed scan, regardless of search" onClick={() => downloadReport(report, "txt")}>
              <Download className="mr-2 h-4 w-4" />Export text
            </Button>
          </>}
          <Button onClick={() => void start()} disabled={loading || starting || running || !data?.config.configured}>
            {starting || running ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
            {starting ? "Starting scan…" : running ? "Checking products…" : "Run product check"}
          </Button>
        </div>
      </header>

      <aside aria-label="Report scope" className="space-y-3 rounded-lg border bg-card p-4 text-sm">
        <p><strong>Visible BigCommerce catalog only. </strong>{PRODUCT_CHECKER_SCOPE}</p>
        <details>
          <summary className="cursor-pointer font-medium">How products are matched and included</summary>
          <ul className="mt-3 list-disc space-y-2 pl-5 text-muted-foreground">
            <li>inFlow products in Internal, Category Needed, and Testing are excluded. Both active and inactive records are loaded; each category explains which records it checks.</li>
            <li>SKUs are matched exactly after removing leading and trailing whitespace. Letter case matters. Product names are not used to find matches.</li>
            <li>BigCommerce variant SKUs take precedence. The product SKU is used only when no variant has a usable SKU. Blank SKUs are excluded from matching.</li>
            <li>One record per trimmed SKU is compared in each source. If a SKU is duplicated, the last loaded record is used; duplicate SKUs are not reported as a separate issue.</li>
            <li>Results are a snapshot from the last completed scan. Opening this page reads the saved report; Run product check fetches a fresh snapshot.</li>
          </ul>
        </details>
      </aside>

      {error && (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm">
          <span>{error}</span>
          {!running && <Button variant="outline" onClick={() => setReload((value) => value + 1)}>Retry loading</Button>}
        </div>
      )}
      {data && !data.config.configured && (
        <div role="alert" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
          A new scan cannot start because the app is missing these connection settings: {data.config.missing.join(", ")}.
          {" "}Ask the server administrator to configure them. Any saved report below is from an earlier scan.
        </div>
      )}
      {data?.job?.status === "failed" && (
        <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm">
          <p className="font-medium">The latest scan did not complete</p>
          <p>{data.job.error}</p>
          {report && <p className="mt-1">Showing the last successful report from {new Date(report.completed_at).toLocaleString()}.</p>}
          <p className="mt-1">Partial results are not shown. Run product check to try again.</p>
        </div>
      )}
      {running && data?.job && (
        <div role="status" className="space-y-3 rounded-lg border bg-card p-4">
          <div className="flex justify-between gap-3 text-sm">
            <span>{data.job.message}</span><span>Stage progress: {data.job.progress}%</span>
          </div>
          <div
            role="progressbar"
            aria-label="Product scan stage progress"
            aria-valuenow={data.job.progress}
            aria-valuemin={0}
            aria-valuemax={100}
            className="h-2 overflow-hidden rounded-full bg-muted"
          >
            <div className="h-full bg-primary transition-all" style={{ width: `${data.job.progress}%` }} />
          </div>
          <p className="text-xs text-muted-foreground">
            Progress indicates scan stages, not the percentage of products checked. You can leave this page and return to the scan.
            {" "}{report ? "The previous completed report and its exports remain available below until the new scan succeeds." : "Results appear when the entire scan completes."}
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
          <h2 className="text-lg font-medium">{running ? "Your product check is running" : "No completed scan to display"}</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            {running ? "Loading visible BigCommerce products, their variants, and inFlow records before comparing them." : "Run a product check to review unmatched SKUs, compared field differences, closeout quantities, and other catalog checks."}
          </p>
        </div>
      ) : <>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[
            { label: "Visible BigCommerce products", value: report.summary.bigcommerce_products, explanation: `${report.summary.bigcommerce_skus} distinct usable SKUs from these products and their variants. Hidden products are excluded.` },
            { label: "inFlow products included", value: report.summary.inflow_eligible, explanation: "Records after category exclusions, including active, inactive, and blank-SKU products. Individual checks have narrower rules." },
            { label: "SKUs found in both sources", value: report.summary.matched_skus, explanation: "Includes inactive inFlow matches. A SKU match does not mean the compared fields agree." },
            { label: "Total report rows", value: report.summary.total_findings, explanation: "Sum across all categories. A product can appear in several categories, and one row can contain multiple differences." },
          ].map(({ label, value, explanation }) => (
            <div key={label} className="rounded-lg border bg-card p-4">
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">{value.toLocaleString()}</p>
              <p className="mt-2 text-xs text-muted-foreground">{explanation}</p>
            </div>
          ))}
        </div>
        <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
          <span>Displayed scan completed: {new Date(report.completed_at).toLocaleString()} · Started by {report.started_by}</span>
          <span>Fetched {report.summary.inflow_products} inFlow products; excluded {report.summary.inflow_excluded} in Internal, Category Needed, and Testing. Before exclusions, {report.summary.inflow_inactive} of all fetched inFlow products were inactive.</span>
        </div>

        <div className="grid gap-4 lg:grid-cols-[250px_minmax(0,1fr)]">
          <nav aria-label="Product report categories" className="flex flex-col gap-1 rounded-lg border bg-card p-2">
            <p className="px-3 py-2 text-xs text-muted-foreground">Category counts show all rows in this scan, before search.</p>
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
              <details>
                <summary className="cursor-pointer text-sm font-medium">How this check works</summary>
                <ul className="mt-3 list-disc space-y-2 pl-5 text-sm text-muted-foreground">
                  {selectedSection.notes.map((note) => <li key={note}>{note}</li>)}
                </ul>
              </details>
              <div className="relative">
                <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  aria-label="Search this category"
                  placeholder="Search this category by product, SKU, or detail…"
                  value={search}
                  onChange={(event) => { setSearch(event.target.value); setPage(1); }}
                  className="pl-9"
                />
              </div>
              <p className="text-xs text-muted-foreground">Search filters only the selected category. Exports include all categories. Quotes around SKUs are display markers, not part of the SKU.</p>
            </div>
            {rows.length === 0 ? (
              <div className="p-10 text-center">
                <Info className="mx-auto mb-3 h-7 w-7 text-muted-foreground" />
                <p className="text-sm text-muted-foreground">
                  {search.trim() ? "No rows match your search in this category. Clear the search to see all rows." : "No rows met this category's rules in this scan. Products outside its scope were not checked."}
                </p>
              </div>
            ) : <>
              <Table>
                <TableHeader>
                  <TableRow><TableHead>Product name</TableHead><TableHead>SKU (quoted)</TableHead><TableHead>What this row represents</TableHead></TableRow>
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
                              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
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
                <span>{rows.length} of {report.reports[section].length} category rows match the search · Page {currentPage} of {pageCount}</span>
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
