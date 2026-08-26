import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "./client";
import { compatibilityEditorApi, type CompatibilityEditorDocument } from "./compatibilityEditor";

vi.mock("./client", () => ({
  apiClient: {
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
  },
}));

const document: CompatibilityEditorDocument = {
  data: {
    computers: {
      C1: {
        name: "Computer",
        compatibilityData: { D1: { compatibilityStatus: "Compatible" } },
      },
    },
    docks: { D1: { name: "Dock" } },
  },
  revision: 3,
  versions: {
    computers: { C1: 1 },
    docks: { D1: 1 },
    cells: { C1: { D1: 2 } },
  },
  publication: {
    configured: true,
    publishedRevision: 2,
    pending: true,
    pendingSince: "2026-08-26T12:00:00Z",
    lastPublishedAt: "2026-08-26T11:59:00Z",
    lastAttemptAt: null,
    lastError: null,
    sha256: null,
    filename: "compatibility_superapp.json",
  },
};

describe("compatibilityEditorApi", () => {
  beforeEach(() => vi.clearAllMocks());

  it("loads the database-backed editor document", async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: document });

    await expect(compatibilityEditorApi.getData()).resolves.toEqual(document);
    expect(apiClient.get).toHaveBeenCalledWith("/system/compatibility-editor");
  });

  it("sends a versioned granular mutation", async () => {
    vi.mocked(apiClient.patch).mockResolvedValueOnce({ data: document });
    const mutation = {
      type: "cell.update" as const,
      computerKey: "C1",
      dockKey: "D1",
      expectedVersion: 2,
      cell: { compatibilityStatus: "Incompatible" as const },
    };

    await compatibilityEditorApi.mutate(mutation, "operation-1");

    expect(apiClient.patch).toHaveBeenCalledWith("/system/compatibility-editor", {
      operationId: "operation-1",
      mutation,
    });
  });
});
