import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PRODUCT_CHECKER_SECTIONS, productCheckerApi } from "../api/productChecker";
import type { ProductCheckerReport, ProductCheckerResponse, ProductCheckerStatus } from "../api/productChecker";
import { Sidebar } from "../components/Sidebar";
import ProductChecker from "./ProductChecker";

const auth = vi.hoisted(() => ({ isAdmin: true, isLoading: false }));
vi.mock("../contexts/AuthContext", () => ({ useAuth: () => auth }));

function response(): ProductCheckerResponse {
  return {
    config: { configured: true, missing: [] }, job: null,
    report: {
      job_id: "scan-1", completed_at: "2026-09-17T12:00:00Z", started_by: "admin@example.test",
      summary: { bigcommerce_products: 3, bigcommerce_skus: 3, inflow_products: 5, inflow_eligible: 4, inflow_excluded: 1, inflow_inactive: 1, matched_skus: 2, total_findings: 2 },
      reports: {
        ...Object.fromEntries(PRODUCT_CHECKER_SECTIONS.map(({ key }) => [key, []])) as ProductCheckerReport["reports"],
        missing_in_bigcommerce: [{ name: "Missing laptop", sku: " LT-1 ", details: ["Absent from visible BigCommerce products."] }],
        closeout_y_and_bc_inventory_zero: [{ name: "Retired desktop", sku: "DT-1", details: ["Closeout Y; inFlow inactive; BigCommerce variant inventory: 0."] }],
      },
    },
  };
}

const runningJob: NonNullable<ProductCheckerStatus["job"]> = {
  id: "scan-2", status: "running", progress: 25, message: "Fetching variants…",
  started_at: "2026-09-17T13:00:00Z", finished_at: null, started_by: "admin@example.test", error: null,
};

describe("ProductChecker", () => {
  beforeEach(() => {
    auth.isAdmin = true;
    auth.isLoading = false;
    vi.spyOn(productCheckerApi, "getData").mockResolvedValue(response());
    vi.spyOn(productCheckerApi, "refresh").mockResolvedValue({ config: { configured: true, missing: [] }, job: runningJob });
    vi.spyOn(productCheckerApi, "getStatus").mockResolvedValue({ config: { configured: true, missing: [] }, job: runningJob });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("blocks non-admin direct access without fetching catalog data", () => {
    auth.isAdmin = false;
    render(<ProductChecker />);
    expect(screen.getByRole("heading", { name: "Admin access required" })).toBeInTheDocument();
    expect(productCheckerApi.getData).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Run product check" })).not.toBeInTheDocument();
  });

  it("waits for authentication before fetching", () => {
    auth.isLoading = true;
    render(<ProductChecker />);
    expect(productCheckerApi.getData).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Checking access");
  });

  it("shows all report categories, preserves SKU whitespace, and searches results", async () => {
    render(<ProductChecker />);
    expect(await screen.findByText("Missing laptop")).toBeInTheDocument();
    expect(screen.getByText('" LT-1 "')).toBeInTheDocument();
    for (const { label } of PRODUCT_CHECKER_SECTIONS) {
      expect(screen.getByRole("button", { name: new RegExp(label) })).toBeInTheDocument();
    }
    fireEvent.change(screen.getByRole("textbox", { name: "Search report" }), { target: { value: "unknown" } });
    expect(screen.getByText("No findings match your search in this category.")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Search report" }), { target: { value: "inactive" } });
    fireEvent.click(screen.getByRole("button", { name: /Closeouts at zero/ }));
    expect(screen.getByText("Retired desktop")).toBeInTheDocument();
    expect(screen.getByText(/Includes inactive inFlow products/)).toBeInTheDocument();
  });

  it("runs and polls to completion while retaining the previous report", async () => {
    render(<ProductChecker />);
    await screen.findByText("Missing laptop");
    vi.useFakeTimers();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Run product check" })));
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
    expect(screen.getByText("Missing laptop")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Checking products…" })).toBeDisabled();
    const completed = response();
    completed.job = { ...runningJob, status: "completed", progress: 100 };
    completed.report!.reports.missing_in_bigcommerce = [{ name: "New result", sku: "NEW", details: ["Missing"] }];
    vi.mocked(productCheckerApi.getStatus).mockResolvedValue(completed);
    vi.mocked(productCheckerApi.getData).mockResolvedValue(completed);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(screen.getByText("New result")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run product check" })).toBeEnabled();
  });

  it("recovers polling after a transient failure", async () => {
    vi.mocked(productCheckerApi.getData).mockResolvedValue({ ...response(), job: runningJob });
    render(<ProductChecker />);
    await screen.findByRole("progressbar");
    vi.useFakeTimers();
    // Trigger a new polling effect so its timer is controlled by the fake clock.
    cleanup();
    vi.mocked(productCheckerApi.getStatus).mockRejectedValueOnce(new Error("Temporary connection error"));
    await act(async () => { render(<ProductChecker />); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(screen.getByRole("alert")).toHaveTextContent("Temporary connection error");
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
  });

  it("labels a failed refresh and keeps the last successful report", async () => {
    vi.mocked(productCheckerApi.getData).mockResolvedValue({ ...response(), job: { ...runningJob, status: "failed", error: "Catalog unavailable" } });
    render(<ProductChecker />);
    await screen.findByText("Missing laptop");
    expect(screen.getByRole("alert")).toHaveTextContent("Catalog unavailable");
    expect(screen.getByRole("alert")).toHaveTextContent("last successful report");
  });

  it("disables scans when existing connections are missing", async () => {
    vi.mocked(productCheckerApi.getData).mockResolvedValue({ ...response(), config: { configured: false, missing: ["BigCommerce API token"] } });
    render(<ProductChecker />);
    expect(await screen.findByRole("alert")).toHaveTextContent("BigCommerce API token");
    expect(screen.getByRole("button", { name: "Run product check" })).toBeDisabled();
  });

  it("downloads both full reports even when a category search is active", async () => {
    const create = vi.fn(() => "blob:test");
    vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<ProductChecker />);
    await screen.findByText("Missing laptop");
    fireEvent.change(screen.getByRole("textbox", { name: "Search report" }), { target: { value: "not found" } });
    for (const label of ["JSON", "Text"]) fireEvent.click(screen.getByRole("button", { name: label }));
    expect(click).toHaveBeenCalledTimes(2);
    expect(create.mock.calls).toHaveLength(2);
    const read = (blob: Blob) => new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(blob);
    });
    const blobs = create.mock.calls as unknown as [Blob][];
    expect(JSON.parse(await read(blobs[0][0])).reports.closeout_y_and_bc_inventory_zero[0].name).toBe("Retired desktop");
    expect(await read(blobs[1][0])).toContain("Retired desktop");
  });

  it("exposes the applet link only for admins", async () => {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn() })));
    const { rerender } = render(<MemoryRouter><Sidebar /></MemoryRouter>);
    expect(screen.getByRole("link", { name: "Product Checker" })).toHaveAttribute("href", "/product-checker");
    auth.isAdmin = false;
    rerender(<MemoryRouter><Sidebar /></MemoryRouter>);
    await waitFor(() => expect(screen.queryByRole("link", { name: "Product Checker" })).not.toBeInTheDocument());
  });
});
