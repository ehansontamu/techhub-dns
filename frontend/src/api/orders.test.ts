import { beforeEach, describe, expect, it, vi } from "vitest";

import apiClient from "./client";
import { ordersApi } from "./orders";
import { normalizeExpectedUpdatedAt } from "./expectedUpdatedAt";

const { patchMock, normalizeExpectedUpdatedAtMock } = vi.hoisted(() => ({
  patchMock: vi.fn(),
  normalizeExpectedUpdatedAtMock: vi.fn((value: unknown) => value),
}));

vi.mock("./client", () => ({
  default: {
    post: vi.fn(),
    patch: patchMock,
  },
}));

vi.mock("./expectedUpdatedAt", () => ({
  normalizeExpectedUpdatedAt: normalizeExpectedUpdatedAtMock,
}));

describe("ordersApi.generatePicklist", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("posts the generated_by and expected_updated_at payload to the picklist endpoint", async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { id: "order-1" },
    } as never);

    const payload = {
      generated_by: "Taylor Tech",
      expected_updated_at: "2026-04-29T15:53:00",
    };

    await ordersApi.generatePicklist("order-1", payload);

    expect(apiClient.post).toHaveBeenCalledWith(
      "/orders/order-1/picklist",
      normalizeExpectedUpdatedAt(payload),
    );
  });
});

describe("ordersApi.rollbackOrderStatus", () => {
  beforeEach(() => {
    patchMock.mockReset();
  });

  it("calls the rollback endpoint with normalized payload and changed_by query param", async () => {
    patchMock.mockResolvedValue({ data: { id: "order-1" } });

    const result = await ordersApi.rollbackOrderStatus(
      "order-1",
      {
        status: "qa",
        reason: "re-open for review",
        expected_updated_at: "2026-04-29T20:00:00Z",
      },
      "ops@example.com",
    );

    expect(patchMock).toHaveBeenCalledWith(
      "/orders/order-1/rollback",
      {
        status: "qa",
        reason: "re-open for review",
        expected_updated_at: "2026-04-29T20:00:00Z",
      },
      {
        params: {
          changed_by: "ops@example.com",
        },
      },
    );
    expect(result).toEqual({ id: "order-1" });
  });

  it("omits changed_by params when not provided", async () => {
    patchMock.mockResolvedValue({ data: { id: "order-2" } });

    await ordersApi.rollbackOrderStatus("order-2", {
      status: "picked",
    });

    expect(patchMock).toHaveBeenCalledWith(
      "/orders/order-2/rollback",
      {
        status: "picked",
      },
      {
        params: undefined,
      },
    );
  });
});
