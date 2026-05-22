import { describe, expect, it } from "vitest";

import type { Order } from "../types/order";
import { getCompletedTodayOrders } from "./dashboardCompletedToday";

const order = (id: string, signatureCapturedAt?: string): Order =>
  ({
    id,
    inflow_order_id: id,
    signature_captured_at: signatureCapturedAt,
  }) as Order;

describe("getCompletedTodayOrders", () => {
  it("uses delivered order items and sorts today's signed deliveries newest first", () => {
    const ordersResponse = {
      items: [
        order("TH-OLDER", "2026-05-22T14:30:00.000Z"),
        order("TH-YESTERDAY", "2026-05-21T23:59:00.000Z"),
        order("TH-NEWER", "2026-05-22T16:30:00.000Z"),
        order("TH-NO-SIGNATURE"),
      ],
      total: 4,
    };

    expect(
      getCompletedTodayOrders(ordersResponse.items, new Date("2026-05-22T12:00:00")),
    ).toEqual([ordersResponse.items[2], ordersResponse.items[0]]);
  });
});
