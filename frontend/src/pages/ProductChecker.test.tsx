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
      link_metadata_version: 1, inventory_check_version: 2, bigcommerce_store_id: "jsj7fos9p1",
      summary: { bigcommerce_products: 3, bigcommerce_skus: 3, inflow_products: 5, inflow_eligible: 4, inflow_excluded: 1, inflow_inactive: 1, matched_skus: 2, total_findings: 2 },
      reports: {
        ...Object.fromEntries(PRODUCT_CHECKER_SECTIONS.map(({ key }) => [key, []])) as ProductCheckerReport["reports"],
        missing_in_bigcommerce: [{ name: "Missing laptop", sku: " LT-1 ", inflow_product_id: "8b2a80ff-6bbb-4653-80a0-781af7c9fb97", details: ["Absent from visible BigCommerce products."] }],
        closeout_y_and_bc_inventory_zero: [{ name: "Retired desktop", sku: "DT-1", inflow_active: false, inventory_tracking: "variant", inventory_source: "variant", inventory_level: 0, product_inventory_level: 5, variant_inventory_level: 0, bigcommerce_discontinued: true, details: ["Closeout Y; inFlow inactive; BigCommerce variant inventory: 0."] }],
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

  it("opens known records in new tabs and labels hidden BigCommerce matches", async () => {
    const data = response();
    data.report!.reports.missing_in_bigcommerce[0].bigcommerce_product_id = 583;
    data.report!.reports.missing_in_bigcommerce[0].bigcommerce_is_visible = false;
    vi.mocked(productCheckerApi.getData).mockResolvedValue(data);
    render(<ProductChecker />);
    await screen.findByText("Missing laptop");
    const bcLink = screen.getByRole("link", { name: "Open Missing laptop in BigCommerce (hidden) (new tab)" });
    expect(bcLink).toHaveAttribute("href", "https://store-jsj7fos9p1.mybigcommerce.com/manage/products/edit/583");
    expect(bcLink).toHaveAttribute("target", "_blank");
    expect(bcLink).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: "Open Missing laptop in inFlow (new tab)" })).toHaveAttribute("href", "https://app.inflowinventory.com/products/8b2a80ff-6bbb-4653-80a0-781af7c9fb97");
    expect(screen.getByText(/Active in inFlow; a matching SKU was found in a hidden/)).toBeInTheDocument();
  });

  it("does not invent record links for unknown IDs or old scans", async () => {
    const { unmount } = render(<ProductChecker />);
    await screen.findByText("Missing laptop");
    expect(screen.getByText("BigCommerce: no record link")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /BigCommerce/ })).not.toBeInTheDocument();
    unmount();
    const data = response();
    delete data.report!.link_metadata_version;
    delete data.report!.bigcommerce_store_id;
    vi.mocked(productCheckerApi.getData).mockResolvedValue(data);
    render(<ProductChecker />);
    expect(await screen.findByText(/This saved scan predates product record links/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /inFlow/ })).not.toBeInTheDocument();
  });

  it("marks old closeout quantities for rechecking rather than relabeling them as verified", async () => {
    const data = response();
    delete data.report!.inventory_check_version;
    delete data.report!.reports.closeout_y_and_bc_inventory_zero[0].inventory_source;
    vi.mocked(productCheckerApi.getData).mockResolvedValue(data);
    render(<ProductChecker />);
    expect(await screen.findByText(/This saved scan used the earlier closeout stock check/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Closeouts with zero BC stock/ }));
    expect(screen.getByText(/Earlier scan: tracking variant/)).toHaveTextContent("Run a new check");
    expect(screen.queryByText(/SKU variant quantity checked:/)).not.toBeInTheDocument();
  });

  it("shows all report categories, preserves SKU whitespace, and searches results", async () => {
    render(<ProductChecker />);
    expect(await screen.findByText("Missing laptop")).toBeInTheDocument();
    expect(screen.getByText('" LT-1 "')).toBeInTheDocument();
    expect(screen.getByLabelText("Report scope")).toHaveTextContent("Hidden products are not checked");
    expect(screen.getByText(/Active in inFlow; no matching SKU among visible/)).toHaveTextContent("visibility disabled");
    expect(screen.queryByText("Absent from visible BigCommerce products.")).not.toBeInTheDocument();
    for (const { label } of PRODUCT_CHECKER_SECTIONS) {
      expect(screen.getByRole("button", { name: new RegExp(label) })).toBeInTheDocument();
    }
    fireEvent.change(screen.getByRole("textbox", { name: "Search this category" }), { target: { value: "unknown" } });
    expect(screen.getByText("No rows match your search in this category. Clear the search to see all rows.")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Search this category" }), { target: { value: "inactive" } });
    fireEvent.click(screen.getByRole("button", { name: /Closeouts with zero BC stock/ }));
    expect(screen.getByText("Retired desktop")).toBeInTheDocument();
    expect(screen.getByText(/Includes active and inactive inFlow products/)).toBeInTheDocument();
    expect(screen.getByText(/^inFlow closeout flag/)).toHaveTextContent("inFlow status: inactive");
    expect(screen.getByText(/SKU variant quantity checked: 0/)).toBeInTheDocument();
    expect(screen.getByText(/API quantities/)).toHaveTextContent("parent product: 5; SKU variant: 0");
    expect(screen.getByText(/^In a BigCommerce discontinued category/)).toHaveTextContent("does not turn off storefront visibility");
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
    fireEvent.change(screen.getByRole("textbox", { name: "Search this category" }), { target: { value: "not found" } });
    for (const label of ["Export JSON", "Export text"]) fireEvent.click(screen.getByRole("button", { name: label }));
    expect(click).toHaveBeenCalledTimes(2);
    expect(create.mock.calls).toHaveLength(2);
    const read = (blob: Blob) => new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(blob);
    });
    const blobs = create.mock.calls as unknown as [Blob][];
    const json = JSON.parse(await read(blobs[0][0]));
    expect(json.reports.closeout_y_and_bc_inventory_zero[0].name).toBe("Retired desktop");
    expect(json.reports.missing_in_bigcommerce[0].details[0]).toContain("visibility disabled");
    expect(json.report_guide.scope).toContain("Hidden products are not checked");
    expect(json.reports.missing_in_bigcommerce[0].record_links.inflow).toBe("https://app.inflowinventory.com/products/8b2a80ff-6bbb-4653-80a0-781af7c9fb97");
    const text = await read(blobs[1][0]);
    expect(text).toContain("Retired desktop");
    expect(text).toContain("No visible BigCommerce match");
    expect(text).toContain("visibility disabled");
    expect(text).toContain("https://app.inflowinventory.com/products/8b2a80ff-6bbb-4653-80a0-781af7c9fb97");
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
