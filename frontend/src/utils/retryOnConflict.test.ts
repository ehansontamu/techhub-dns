import { describe, expect, it, vi } from "vitest";

import { retryOnceOnConflict } from "./retryOnConflict";

const conflictError = {
  isAxiosError: true,
  response: {
    status: 409,
    data: {
      error: {
        details: {
          actual_updated_at: "2026-07-30T15:03:00Z",
        },
      },
    },
  },
};

describe("retryOnceOnConflict", () => {
  it("retries once with the exact version returned by the conflict response", async () => {
    const attempt = vi.fn()
      .mockRejectedValueOnce(conflictError)
      .mockResolvedValueOnce("generated");
    const loadLatestExpectedUpdatedAt = vi.fn();

    await expect(retryOnceOnConflict({
      initialExpectedUpdatedAt: "2026-07-30T15:02:00Z",
      loadLatestExpectedUpdatedAt,
      attempt,
    })).resolves.toBe("generated");

    expect(attempt).toHaveBeenNthCalledWith(1, "2026-07-30T15:02:00Z");
    expect(attempt).toHaveBeenNthCalledWith(2, "2026-07-30T15:03:00Z");
    expect(loadLatestExpectedUpdatedAt).not.toHaveBeenCalled();
  });

  it("falls back to loading the latest order when conflict details are unavailable", async () => {
    const conflictWithoutDetails = {
      isAxiosError: true,
      response: { status: 409 },
    };
    const attempt = vi.fn()
      .mockRejectedValueOnce(conflictWithoutDetails)
      .mockResolvedValueOnce("generated");
    const loadLatestExpectedUpdatedAt = vi.fn()
      .mockResolvedValue("2026-07-30T15:04:00Z");

    await expect(retryOnceOnConflict({
      initialExpectedUpdatedAt: "2026-07-30T15:02:00Z",
      loadLatestExpectedUpdatedAt,
      attempt,
    })).resolves.toBe("generated");

    expect(attempt).toHaveBeenNthCalledWith(2, "2026-07-30T15:04:00Z");
    expect(loadLatestExpectedUpdatedAt).toHaveBeenCalledOnce();
  });

  it("preserves a real retry failure instead of replacing it with the conflict", async () => {
    const sharePointError = {
      isAxiosError: true,
      response: {
        status: 422,
        data: { error: "SharePoint upload failed" },
      },
    };
    const attempt = vi.fn()
      .mockRejectedValueOnce(conflictError)
      .mockRejectedValueOnce(sharePointError);

    await expect(retryOnceOnConflict({
      initialExpectedUpdatedAt: "2026-07-30T15:02:00Z",
      loadLatestExpectedUpdatedAt: async () => "2026-07-30T15:03:00Z",
      attempt,
    })).rejects.toBe(sharePointError);
  });
});
