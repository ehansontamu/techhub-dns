import { describe, expect, it, vi } from "vitest";

import { retryOnceOnConflict } from "./retryOnConflict";

const conflictError = {
  isAxiosError: true,
  response: { status: 409 },
};

describe("retryOnceOnConflict", () => {
  it("retries once with the latest order version", async () => {
    const attempt = vi.fn()
      .mockRejectedValueOnce(conflictError)
      .mockResolvedValueOnce("generated");
    const loadLatestExpectedUpdatedAt = vi.fn()
      .mockResolvedValue("2026-07-30T15:03:00Z");

    await expect(retryOnceOnConflict({
      initialExpectedUpdatedAt: "2026-07-30T15:02:00Z",
      loadLatestExpectedUpdatedAt,
      attempt,
    })).resolves.toBe("generated");

    expect(attempt).toHaveBeenNthCalledWith(1, "2026-07-30T15:02:00Z");
    expect(attempt).toHaveBeenNthCalledWith(2, "2026-07-30T15:03:00Z");
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
