import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import OrderDetail from "./OrderDetail";
import type { OrderDetail as OrderDetailType } from "../types/order";
import { OrderStatus } from "../types/order";

describe("OrderDetail", () => {
  it("copies the complete Inflow payload as formatted JSON", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    const inflowData = {
      orderNumber: "TH1234",
      shippingAddress: { city: "College Station" },
      lines: [{ productId: "computer-1", quantity: 2 }],
    };
    const order: OrderDetailType = {
      id: "order-1",
      inflow_order_id: "TH1234",
      status: OrderStatus.PICKED,
      inflow_data: inflowData,
      created_at: "2026-07-22T12:00:00Z",
      updated_at: "2026-07-22T12:00:00Z",
    };

    render(
      <MemoryRouter>
        <OrderDetail
          order={order}
          auditLogs={[]}
          notifications={[]}
          canDismissOrder={false}
          onStatusChange={vi.fn()}
          onRmaReopen={vi.fn().mockResolvedValue(undefined)}
          onDismissOrder={vi.fn().mockResolvedValue(undefined)}
          onArchiveOrder={vi.fn().mockResolvedValue(undefined)}
          onRollbackStatus={vi.fn()}
          onTagOrder={vi.fn().mockResolvedValue(undefined)}
          onRequestTags={vi.fn().mockResolvedValue(undefined)}
          onGeneratePicklist={vi.fn().mockResolvedValue(undefined)}
          generatingPicklist={false}
        />
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "Copy Inflow payload" }));

    expect(writeText).toHaveBeenCalledWith(JSON.stringify(inflowData, null, 2));
  });
});
