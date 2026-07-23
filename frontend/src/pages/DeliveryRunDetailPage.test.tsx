import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { deliveryRunsApi, type DeliveryRunDetailResponse } from "../api/deliveryRuns";
import { ordersApi } from "../api/orders";
import { useDeliveryRun } from "../hooks/useDeliveryRun";
import { OrderStatus, type Order } from "../types/order";
import DeliveryRunDetailPage from "./DeliveryRunDetailPage";

vi.mock("../api/deliveryRuns", () => ({
  deliveryRunsApi: {
    appendOrders: vi.fn(),
    finishRun: vi.fn(),
    recallOrder: vi.fn(),
    reorderOrders: vi.fn(),
  },
}));

vi.mock("../api/orders", () => ({
  ordersApi: {
    getOrders: vi.fn(),
  },
}));

vi.mock("../hooks/useDeliveryRun", () => ({
  useDeliveryRun: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

const runId = "00000000-0000-4000-8000-000000000001";
const candidateIds = {
  th2: "00000000-0000-4000-8000-000000000002",
  th10: "00000000-0000-4000-8000-000000000010",
  th100: "00000000-0000-4000-8000-000000000100",
};

const activeRun: DeliveryRunDetailResponse = {
  id: runId,
  name: "Delivery Run 1",
  runner: "Runner One",
  vehicle: "van",
  status: "Active",
  start_time: "2026-07-23T14:00:00Z",
  updated_at: "2026-07-23T14:05:00Z",
  order_ids: [],
  orders: [],
};

const makeCandidate = (
  id: string,
  inflowOrderId: string,
): Order => ({
  id,
  inflow_order_id: inflowOrderId,
  status: OrderStatus.PRE_DELIVERY,
  recipient_name: `Recipient ${inflowOrderId}`,
  delivery_location: "Building 1",
  created_at: "2026-07-23T13:00:00Z",
  updated_at: "2026-07-23T13:00:00Z",
});

describe("DeliveryRunDetailPage", () => {
  const refetch = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useDeliveryRun).mockReturnValue({
      run: activeRun,
      loading: false,
      error: null,
      refetch,
    });
    vi.mocked(ordersApi.getOrders).mockResolvedValue({
      items: [
        makeCandidate(candidateIds.th100, "TH100"),
        makeCandidate(candidateIds.th10, "TH10"),
        makeCandidate(candidateIds.th2, "TH2"),
      ],
      total: 3,
    });
    vi.mocked(deliveryRunsApi.appendOrders).mockResolvedValue(activeRun);
  });

  it("sorts selectable orders numerically and appends selected orders in that order", async () => {
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={[`/delivery/runs/${runId}`]}>
        <Routes>
          <Route path="/delivery/runs/:runId" element={<DeliveryRunDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "Add Orders" }));

    const dialog = await screen.findByRole("dialog", {
      name: "Add Orders to Delivery Run 1",
    });
    const labels = within(dialog)
      .getAllByText(/^TH(?:2|10|100)$/)
      .map((element) => element.textContent);
    expect(labels).toEqual(["TH2", "TH10", "TH100"]);

    await user.click(within(dialog).getByLabelText("Select order TH10"));
    await user.click(within(dialog).getByLabelText("Select order TH2"));
    await user.click(within(dialog).getByRole("button", { name: "Add 2 Orders" }));

    await waitFor(() => {
      expect(deliveryRunsApi.appendOrders).toHaveBeenCalledWith(
        runId,
        [candidateIds.th2, candidateIds.th10],
        activeRun.updated_at,
      );
    });
    expect(refetch).toHaveBeenCalled();
  });
});
