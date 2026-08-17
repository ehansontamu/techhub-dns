import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { inventoryReorderApi } from "../api/inventoryReorder";
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

const mockedInventoryReorderApi = vi.mocked(inventoryReorderApi);
const mockedUseAuth = vi.mocked(useAuth);

describe("InventoryReorder", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
});
