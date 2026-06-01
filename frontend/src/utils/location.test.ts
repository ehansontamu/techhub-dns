import { describe, expect, it } from "vitest";

import type { Order } from "../types/order";
import { isLocalDelivery, isLocalDeliveryCity } from "./location";

function buildOrder(city: string): Order {
  return {
    id: "order-1",
    inflow_order_id: "TH123",
    status: "qa",
    inflow_data: {
      shippingAddress: {
        city,
        address1: "474 Agronomy Rd",
        address2: "",
        state: "TX",
        postalCode: "77843",
      },
    },
    created_at: "2026-06-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
  } as Order;
}

describe("isLocalDeliveryCity", () => {
  it("treats obvious College Station typos as local delivery", () => {
    expect(isLocalDeliveryCity("College Station")).toBe(true);
    expect(isLocalDeliveryCity("College Staion")).toBe(true);
    expect(isLocalDeliveryCity("Colleghe Station")).toBe(true);
  });

  it("keeps non-local cities as shipping", () => {
    expect(isLocalDeliveryCity("Houston")).toBe(false);
  });
});

describe("isLocalDelivery", () => {
  it("uses typo-tolerant local city matching for order data", () => {
    expect(isLocalDelivery(buildOrder("Colleghe Station"))).toBe(true);
    expect(isLocalDelivery(buildOrder("Houston"))).toBe(false);
  });
});
