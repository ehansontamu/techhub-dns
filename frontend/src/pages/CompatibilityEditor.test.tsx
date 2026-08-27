import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { compatibilityEditorApi, type CompatibilityEditorDocument } from "../api/compatibilityEditor";
import { useAuth } from "../contexts/AuthContext";
import CompatibilityEditor from "./CompatibilityEditor";


vi.mock("../api/compatibilityEditor", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/compatibilityEditor")>();
  return {
    ...actual,
    compatibilityEditorApi: {
      getData: vi.fn(),
      mutate: vi.fn(),
      publish: vi.fn(),
      review: vi.fn(),
      submitBundle: vi.fn(),
    },
  };
});

vi.mock("../contexts/AuthContext", () => ({ useAuth: vi.fn() }));
vi.mock("socket.io-client", () => ({
  io: vi.fn(() => ({ on: vi.fn(), emit: vi.fn(), disconnect: vi.fn() })),
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mockedApi = vi.mocked(compatibilityEditorApi);
const mockedUseAuth = vi.mocked(useAuth);

const document: CompatibilityEditorDocument = {
  data: {
    computers: {
      C1: {
        name: "Computer",
        hidden: false,
        compatibilityData: { D1: { compatibilityStatus: "Compatible" } },
      },
    },
    docks: { D1: { name: "Dock", hidden: false } },
  },
  revision: 1,
  workspaceRevision: 1,
  versions: {
    computers: { C1: 1 },
    docks: { D1: 1 },
    cells: { C1: { D1: 4 } },
  },
  approvedVersions: {
    computers: { C1: 1 },
    docks: { D1: 1 },
    cells: { C1: { D1: 4 } },
  },
  approval: {
    pendingCount: 0,
    pendingChanges: [],
    draftCount: 0,
    draftBundles: [],
  },
  publication: {
    configured: true,
    publishedRevision: 1,
    pending: false,
    pendingSince: null,
    lastPublishedAt: "2026-08-26T12:00:00Z",
    lastAttemptAt: "2026-08-26T12:00:00Z",
    lastError: null,
    sha256: "abc",
    filename: "compatibility_superapp.json",
  },
};

describe("CompatibilityEditor", () => {
  afterEach(() => vi.restoreAllMocks());

  beforeEach(() => {
    vi.clearAllMocks();
    mockedUseAuth.mockReturnValue({
      user: {
        id: "admin-1",
        email: "admin@example.test",
        display_name: "Admin",
        department: "TechHub",
        created_at: "2026-08-26T00:00:00Z",
        last_login_at: "2026-08-26T00:00:00Z",
      },
      session: null,
      isAuthenticated: true,
      isAdmin: true,
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
      refreshAuth: vi.fn(),
    });
    mockedApi.getData.mockResolvedValue(document);
    mockedApi.mutate.mockResolvedValue({ ...document, revision: 2 });
    mockedApi.submitBundle.mockResolvedValue(document);
    mockedApi.publish.mockResolvedValue({
      attempted: true,
      success: true,
      revision: 1,
      pending: false,
      error: null,
      filename: "compatibility_superapp.json",
      snapshotId: "snapshot-1",
    });
  });

  it("saves a cell as a versioned database mutation", async () => {
    render(<CompatibilityEditor />);

    const cell = await screen.findByTitle("Computer / Dock: Compatible");
    fireEvent.click(cell);
    fireEvent.click(screen.getByRole("button", { name: "Save and Approve" }));

    await waitFor(() => expect(mockedApi.mutate).toHaveBeenCalledTimes(1));
    expect(mockedApi.mutate.mock.calls[0][0]).toMatchObject({
      type: "cell.update",
      computerKey: "C1",
      dockKey: "D1",
      expectedVersion: 4,
    });
    expect(mockedApi.publish).not.toHaveBeenCalled();
  });

  it("publishes only when an admin clicks Save to WebDAV", async () => {
    vi.spyOn(window, "confirm").mockReturnValueOnce(true);
    render(<CompatibilityEditor />);

    fireEvent.click(await screen.findByRole("button", { name: "Save to WebDAV" }));

    await waitFor(() => expect(mockedApi.publish).toHaveBeenCalledTimes(1));
  });

  it("lets a non-admin load the contributor editor", async () => {
    mockedUseAuth.mockReturnValue({
      ...mockedUseAuth.mock.results[0]?.value,
      user: null,
      session: null,
      isAuthenticated: true,
      isAdmin: false,
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
      refreshAuth: vi.fn(),
    });

    render(<CompatibilityEditor />);

    expect(await screen.findByText(/cannot update the website JSON directly/i)).toBeInTheDocument();
    expect(mockedApi.getData).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Save to WebDAV" })).not.toBeInTheDocument();
  });

  it("lets a contributor submit a completed new-item bundle", async () => {
    mockedUseAuth.mockReturnValue({
      ...mockedUseAuth.mock.results[0]?.value,
      user: null,
      session: null,
      isAuthenticated: true,
      isAdmin: false,
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
      refreshAuth: vi.fn(),
    });
    mockedApi.getData.mockResolvedValueOnce({
      ...document,
      data: {
        ...document.data,
        computers: {
          ...document.data.computers,
          C2: {
            name: "New Computer",
            studentEdited: true,
            compatibilityData: {
              D1: { compatibilityStatus: "Compatible", studentEdited: true },
            },
          },
        },
      },
      approval: {
        pendingCount: 0,
        pendingChanges: [],
        draftCount: 1,
        draftBundles: [{
          id: "bundle-1",
          target: "computer:C2",
          mutationType: "computer.add",
          baseVersion: 0,
          version: 1,
          proposedData: { name: "New Computer" },
          currentData: null,
          status: "pending",
          readyForReview: false,
          bundle: {
            axis: "computer",
            itemKey: "C2",
            completedCells: 1,
            requiredCells: 1,
            missingTargets: [],
            ready: false,
          },
          submittedBy: "student@example.test",
          updatedBy: "student@example.test",
          submittedAt: "2026-08-27T12:00:00Z",
          updatedAt: "2026-08-27T12:01:00Z",
          reviewedBy: null,
          reviewedAt: null,
          reviewNote: null,
        }],
      },
    });

    render(<CompatibilityEditor />);

    fireEvent.click(await screen.findByRole("tab", { name: "Computers" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit item for review" }));

    await waitFor(() => expect(mockedApi.submitBundle).toHaveBeenCalledWith("bundle-1"));
  });
});
