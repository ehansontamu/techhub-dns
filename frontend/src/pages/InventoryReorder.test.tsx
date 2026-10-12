import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import { inventoryReorderApi } from "../api/inventoryReorder";
import type { InventoryReorderJob } from "../api/inventoryReorder";
import { useAuth } from "../contexts/AuthContext";
import InventoryReorder from "./InventoryReorder";

vi.mock("../api/inventoryReorder", () => ({
  inventoryReorderApi: {
    getData: vi.fn(),
    refresh: vi.fn(),
    getJob: vi.fn(),
    downloadLatest: vi.fn(),
  },
}));

vi.mock("../contexts/AuthContext", () => ({
  useAuth: vi.fn(),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const mockedInventoryReorderApi = vi.mocked(inventoryReorderApi);
const mockedUseAuth = vi.mocked(useAuth);

const runningJob: InventoryReorderJob = {
  job_id: "scheduled-job", status: "running", progress: 0.01,
  message: "Starting inventory refresh...", error: null,
  started_at: "2026-10-12T17:00:00Z", finished_at: null, result_path: null,
  trigger: "scheduled",
};
const completedJob: InventoryReorderJob = {
  ...runningJob, status: "done", progress: 1, message: "Refresh complete",
  finished_at: "2026-10-12T17:05:00Z", result_path: "summary.json",
};

async function renderRunningRefresh() {
  const payload = await mockedInventoryReorderApi.getData(false);
  mockedInventoryReorderApi.getData.mockClear();
  mockedInventoryReorderApi.getData.mockResolvedValue({ ...payload, latest_job: runningJob });
  vi.useFakeTimers();
  const view = render(<InventoryReorder />);
  await act(async () => {});
  return { ...view, payload };
}

async function advanceTime(milliseconds: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); });
}

describe("InventoryReorder", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockedUseAuth.mockReturnValue({
      user: {
        id: "admin-1",
        email: "admin@example.com",
        display_name: "Admin User",
        department: "TechHub",
        created_at: "2026-08-17T00:00:00Z",
        last_login_at: "2026-08-17T00:00:00Z",
      },
      session: null,
      isAuthenticated: true,
      isAdmin: true,
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
      refreshAuth: vi.fn(),
    });

    mockedInventoryReorderApi.getData.mockResolvedValue({
      rows: [
        {
          name: "Laptop",
          sku: "LT-1",
          quantityAvailable: "20",
          quantityOnOrder: "3",
          bigCommerceStatus9: "12",
          available: 20,
          status9: 12,
          finalQty: 8,
          onOrder: 3,
          combined: 11,
          reorderPoint: 5,
          reorderQty: 10,
          needsReorder: false,
          critical: false,
          orders: {
            bigCommerce: [
              {
                orderId: "901",
                orderNumber: "901",
                quantity: 12,
                status: "Aggiebuy Approval (Status 9)",
              },
            ],
            inflow: [
              {
                orderId: "inflow-1",
                orderNumber: "TH1001",
                quantity: 3,
                status: "started",
              },
              {
                orderId: "fulfilled-guid",
                orderNumber: "TH0999",
                quantity: 20,
                status: "fulfilled",
              },
            ],
          },
        },
        {
          name: "Mouse",
          sku: "MS-1",
          quantityAvailable: "20",
          quantityOnOrder: "0",
          bigCommerceStatus9: "9",
          available: 20,
          status9: 9,
          finalQty: 11,
          onOrder: 0,
          combined: 11,
          reorderPoint: 5,
          reorderQty: 10,
          needsReorder: false,
          critical: false,
          orders: {
            bigCommerce: [
              {
                orderId: "902",
                orderNumber: "902",
                quantity: 9,
                status: "Aggiebuy Approval (Status 9)",
              },
            ],
            inflow: [],
          },
        },
      ],
      summary: { total: 2, needs_reorder: 0, critical: 0, ten_plus_bc_order_items: 1 },
      latest_job: null,
      has_data: true,
      config: {
        configured: true,
        missing: [],
        scheduled_refresh: { enabled: false, timezone: "America/Chicago" },
      },
      cooldown: {
        active: false,
        cooldown_seconds: 180,
        remaining_seconds: 0,
        ends_at: null,
      },
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("expands a product into BigCommerce and InFlow order quantities", async () => {
    render(<InventoryReorder />);

    await waitFor(() => expect(screen.getByText("Laptop")).toBeInTheDocument());
    expect(screen.getByText("BC Aggiebuy Approval (Status 9)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "10+ BC orders 1" })).toBeInTheDocument();
    expect(screen.queryByText("Bulk order in BC")).not.toBeInTheDocument();
    expect(screen.getAllByText("10+ order")).toHaveLength(1);
    expect(screen.queryByText("10+ units")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show orders for Laptop" }));

    expect(screen.getAllByText("10+ units")).toHaveLength(1);
    expect(screen.getByText("BigCommerce Aggiebuy Approval (Status 9)")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Order 901" })).toHaveAttribute(
      "href",
      "https://store-jsj7fos9p1.mybigcommerce.com/manage/orders/901"
    );
    expect(screen.getByRole("link", { name: "Order 901" })).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "Order TH1001" })).toHaveAttribute(
      "href",
      "https://app.inflowinventory.com/sales-orders/inflow-1"
    );
    expect(screen.queryByText("Order TH0999")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "10+ BC orders 1" }));
    await waitFor(() => expect(mockedInventoryReorderApi.getData).toHaveBeenLastCalledWith(true));
    await waitFor(() => {
      expect(screen.getByText("Laptop")).toBeInTheDocument();
      expect(screen.queryByText("Mouse")).not.toBeInTheDocument();
    });
  });

  it("reloads inventory and stops polling when a scheduled refresh finishes", async () => {
    const { payload } = await renderRunningRefresh();
    mockedInventoryReorderApi.getJob.mockResolvedValue({ job: completedJob });
    mockedInventoryReorderApi.getData.mockResolvedValue({ ...payload, latest_job: completedJob });

    await advanceTime(2000);
    expect(toast.success).toHaveBeenCalledExactlyOnceWith("Inventory reorder refresh complete");
    expect(mockedInventoryReorderApi.getData).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Refresh Inventory" })).toBeEnabled();
    await advanceTime(10000);
    expect(mockedInventoryReorderApi.getJob).toHaveBeenCalledTimes(1);
  });

  it("recovers from a missing job by loading the current inventory", async () => {
    const { payload } = await renderRunningRefresh();
    mockedInventoryReorderApi.getJob.mockRejectedValue({
      isAxiosError: true, response: { status: 404, data: { error: "Inventory reorder refresh job not found." } },
    });
    mockedInventoryReorderApi.getData.mockResolvedValue({
      ...payload, latest_job: completedJob,
      rows: [{ ...payload.rows[0], name: "Updated Laptop" }],
    });

    await advanceTime(2000);
    expect(screen.getByText("Updated Laptop")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh Inventory" })).toBeEnabled();
    expect(toast.error).not.toHaveBeenCalled();
    await advanceTime(120000);
    expect(mockedInventoryReorderApi.getJob).toHaveBeenCalledTimes(1);
    expect(mockedInventoryReorderApi.getData.mock.calls.length).toBeGreaterThan(2);
  });

  it("does not resume polling a missing job still listed in old metadata", async () => {
    await renderRunningRefresh();
    mockedInventoryReorderApi.getJob.mockRejectedValue({ isAxiosError: true, response: { status: 404 } });

    await advanceTime(2000);
    await advanceTime(120000);
    expect(mockedInventoryReorderApi.getJob).toHaveBeenCalledTimes(1);
    expect(mockedInventoryReorderApi.getData.mock.calls.length).toBeGreaterThan(2);
    expect(screen.getByRole("button", { name: "Refresh Inventory" })).toBeEnabled();
    expect(toast.error).not.toHaveBeenCalled();

    const payload = await mockedInventoryReorderApi.getData(false);
    mockedInventoryReorderApi.getData.mockResolvedValue({
      ...payload, latest_job: { ...runningJob, job_id: "next-scheduled-job" },
    });
    mockedInventoryReorderApi.getJob.mockResolvedValue({
      job: { ...runningJob, job_id: "next-scheduled-job" },
    });
    await act(async () => { fireEvent.focus(window); });
    await advanceTime(2000);
    expect(mockedInventoryReorderApi.getJob).toHaveBeenLastCalledWith("next-scheduled-job");
  });

  it("limits repeated status errors and retries until status recovers", async () => {
    const { payload } = await renderRunningRefresh();
    mockedInventoryReorderApi.getJob.mockRejectedValue(new Error("Connection unavailable"));

    await advanceTime(22000);
    expect(mockedInventoryReorderApi.getJob).toHaveBeenCalledTimes(3);
    expect(toast.error).toHaveBeenCalledTimes(1);
    mockedInventoryReorderApi.getJob.mockResolvedValue({ job: completedJob });
    mockedInventoryReorderApi.getData.mockResolvedValue({ ...payload, latest_job: completedJob });
    await advanceTime(10000);
    expect(toast.success).toHaveBeenCalledWith("Inventory reorder refresh complete");
    expect(screen.getByRole("button", { name: "Refresh Inventory" })).toBeEnabled();
  });

  it("keeps slow status requests from overlapping and ignores results after leaving the page", async () => {
    const { unmount } = await renderRunningRefresh();
    let finishRequest!: (value: { job: InventoryReorderJob }) => void;
    mockedInventoryReorderApi.getJob.mockReturnValue(new Promise((resolve) => { finishRequest = resolve; }));

    await advanceTime(20000);
    expect(mockedInventoryReorderApi.getJob).toHaveBeenCalledTimes(1);
    unmount();
    await act(async () => { finishRequest({ job: completedJob }); });
    expect(toast.success).not.toHaveBeenCalled();
    expect(mockedInventoryReorderApi.getData).toHaveBeenCalledTimes(1);
  });

  it("reloads finished refresh status when the user returns to an idle tab", async () => {
    const { payload } = await renderRunningRefresh();
    mockedInventoryReorderApi.getData.mockResolvedValue({ ...payload, latest_job: completedJob });

    await act(async () => { fireEvent.focus(window); });
    expect(mockedInventoryReorderApi.getData).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Refresh Inventory" })).toBeEnabled();
    await advanceTime(10000);
    expect(mockedInventoryReorderApi.getJob).not.toHaveBeenCalled();
  });

  it("shows a failed refresh once and resumes normal inventory reloads", async () => {
    const { payload } = await renderRunningRefresh();
    const failedJob: InventoryReorderJob = {
      ...completedJob, status: "error", error: "Upstream unavailable", message: "Refresh failed",
    };
    mockedInventoryReorderApi.getJob.mockResolvedValue({ job: failedJob });
    mockedInventoryReorderApi.getData.mockResolvedValue({ ...payload, latest_job: failedJob });

    await advanceTime(2000);
    expect(toast.error).toHaveBeenCalledExactlyOnceWith("Inventory reorder refresh failed", {
      description: "Upstream unavailable",
    });
    expect(screen.getByText("Upstream unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh Inventory" })).toBeEnabled();
    await advanceTime(120000);
    expect(mockedInventoryReorderApi.getJob).toHaveBeenCalledTimes(1);
    expect(mockedInventoryReorderApi.getData.mock.calls.length).toBeGreaterThan(2);
    expect(toast.error).toHaveBeenCalledTimes(1);
  });

  it("reloads when a hidden tab becomes visible", async () => {
    const { payload } = await renderRunningRefresh();
    mockedInventoryReorderApi.getData.mockResolvedValue({ ...payload, latest_job: completedJob });
    const visibility = vi.spyOn(document, "visibilityState", "get");
    visibility.mockReturnValue("hidden");
    await act(async () => { fireEvent(document, new Event("visibilitychange")); });
    expect(mockedInventoryReorderApi.getData).toHaveBeenCalledTimes(1);

    visibility.mockReturnValue("visible");
    await act(async () => { fireEvent(document, new Event("visibilitychange")); });
    expect(mockedInventoryReorderApi.getData).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "Refresh Inventory" })).toBeEnabled();
  });

  it("tracks a manual refresh from queued through completion", async () => {
    const payload = await mockedInventoryReorderApi.getData(false);
    mockedInventoryReorderApi.getData.mockClear();
    vi.useFakeTimers();
    render(<InventoryReorder />);
    await act(async () => {});
    const queuedJob: InventoryReorderJob = {
      ...runningJob, job_id: "manual-job", status: "queued", trigger: "manual",
    };
    mockedInventoryReorderApi.refresh.mockResolvedValue({
      job: queuedJob, created: true, cooldown: payload.cooldown,
    });
    mockedInventoryReorderApi.getJob.mockResolvedValue({ job: { ...queuedJob, status: "running" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Refresh Inventory" })); });
    expect(mockedInventoryReorderApi.refresh).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Refresh Inventory" })).toBeDisabled();
    await advanceTime(2000);
    expect(mockedInventoryReorderApi.getJob).toHaveBeenLastCalledWith("manual-job");

    const doneJob: InventoryReorderJob = { ...completedJob, job_id: "manual-job", trigger: "manual" };
    mockedInventoryReorderApi.getJob.mockResolvedValue({ job: doneJob });
    mockedInventoryReorderApi.getData.mockResolvedValue({ ...payload, latest_job: doneJob });
    await advanceTime(2000);
    expect(toast.success).toHaveBeenCalledWith("Inventory reorder refresh complete");
    expect(screen.getByRole("button", { name: "Refresh Inventory" })).toBeEnabled();
  });

  it("does not let an older inventory response stop a newly started manual refresh", async () => {
    const payload = await mockedInventoryReorderApi.getData(false);
    mockedInventoryReorderApi.getData.mockClear();
    vi.useFakeTimers();
    render(<InventoryReorder />);
    await act(async () => {});

    let finishReload!: (value: typeof payload) => void;
    mockedInventoryReorderApi.getData.mockReturnValueOnce(new Promise((resolve) => { finishReload = resolve; }));
    await act(async () => { fireEvent.focus(window); });
    const manualJob: InventoryReorderJob = { ...runningJob, job_id: "new-manual-job", trigger: "manual" };
    mockedInventoryReorderApi.refresh.mockResolvedValue({ job: manualJob, created: true, cooldown: payload.cooldown });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Refresh Inventory" })); });
    mockedInventoryReorderApi.getJob.mockResolvedValue({ job: manualJob });
    await act(async () => { finishReload(payload); });

    expect(screen.getByRole("button", { name: "Refresh Inventory" })).toBeDisabled();
    await advanceTime(2000);
    expect(mockedInventoryReorderApi.getJob).toHaveBeenCalledWith("new-manual-job");
  });

  it("keeps the newest inventory when return-to-tab reloads finish out of order", async () => {
    const { payload } = await renderRunningRefresh();
    let finishOlderReload!: (value: typeof payload) => void;
    mockedInventoryReorderApi.getData.mockReturnValueOnce(new Promise((resolve) => { finishOlderReload = resolve; }));
    await act(async () => { fireEvent.focus(window); });
    mockedInventoryReorderApi.getData.mockResolvedValue({
      ...payload, latest_job: completedJob, rows: [{ ...payload.rows[0], name: "Newest Laptop" }],
    });
    await act(async () => { fireEvent.focus(window); });
    await act(async () => { finishOlderReload({ ...payload, latest_job: runningJob }); });

    expect(screen.getByText("Newest Laptop")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh Inventory" })).toBeEnabled();
    await advanceTime(2000);
    expect(mockedInventoryReorderApi.getJob).not.toHaveBeenCalled();
  });

  it("allows a slow inventory reload to finish while job progress is polling", async () => {
    const { payload } = await renderRunningRefresh();
    let finishReload!: (value: typeof payload) => void;
    mockedInventoryReorderApi.getData.mockReturnValueOnce(new Promise((resolve) => { finishReload = resolve; }));
    mockedInventoryReorderApi.getJob.mockResolvedValue({ job: runningJob });
    await act(async () => { fireEvent.focus(window); });
    await advanceTime(6000);
    await act(async () => {
      finishReload({ ...payload, latest_job: runningJob, rows: [{ ...payload.rows[0], name: "Updated Laptop" }] });
    });
    expect(screen.getByText("Updated Laptop")).toBeInTheDocument();
    expect(mockedInventoryReorderApi.getJob).toHaveBeenCalledTimes(3);
  });
});
