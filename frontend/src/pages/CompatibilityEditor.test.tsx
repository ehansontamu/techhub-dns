import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  compatibilityEditorApi,
  type CompatibilityEditorChange,
  type CompatibilityEditorDocument,
} from "../api/compatibilityEditor";
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

const readyComputerChange: CompatibilityEditorChange = {
  id: "ready-computer-1",
  target: "computer:C2",
  mutationType: "computer.add",
  baseVersion: 0,
  version: 1,
  proposedData: {
    name: "New Computer",
    url: "https://example.test/computer",
    hidden: false,
  },
  currentData: null,
  status: "pending",
  readyForReview: true,
  bundle: {
    axis: "computer",
    itemKey: "C2",
    completedCells: 1,
    requiredCells: 1,
    missingTargets: [],
    ready: true,
    cells: [{
      computerKey: "C2",
      dockKey: "D1",
      proposedData: {
        compatibilityStatus: "Partially Compatible",
        display: "Functional",
        notes: "One display only",
      },
      updatedBy: "student@example.test",
      updatedAt: "2026-08-28T12:01:00Z",
    }],
  },
  submittedBy: "student@example.test",
  updatedBy: "student@example.test",
  submittedAt: "2026-08-28T12:00:00Z",
  updatedAt: "2026-08-28T12:01:00Z",
  reviewedBy: null,
  reviewedAt: null,
  reviewNote: null,
};

const reviewDocument: CompatibilityEditorDocument = {
  ...document,
  approval: {
    pendingCount: 1,
    pendingChanges: [readyComputerChange],
    draftCount: 0,
    draftBundles: [],
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
    mockedApi.review.mockResolvedValue(document);
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

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Computers" }));
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByRole("button", { name: "Create Draft" })).toBeInTheDocument();
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

    expect(await screen.findByTitle("New Computer / Dock: Draft saved · Compatible")).toHaveTextContent("✓ Yes");
    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Computers" }));
    fireEvent.click(screen.getByRole("button", { name: "Submit item for review" }));

    await waitFor(() => expect(mockedApi.submitBundle).toHaveBeenCalledWith("bundle-1"));
  });

  it.each([
    { role: "contributor", isAdmin: false },
    { role: "admin", isAdmin: true },
  ])("lets a $role correct pending computer name and URL", async ({ isAdmin }) => {
    mockedUseAuth.mockReturnValue({
      ...mockedUseAuth.mock.results[0]?.value,
      user: null,
      session: null,
      isAuthenticated: true,
      isAdmin,
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
      refreshAuth: vi.fn(),
    });
    const pendingChange: CompatibilityEditorChange = isAdmin
      ? readyComputerChange
      : {
          ...readyComputerChange,
          readyForReview: false,
          bundle: { ...readyComputerChange.bundle!, ready: false },
        };
    const pendingComputerDocument: CompatibilityEditorDocument = {
      ...document,
      data: {
        ...document.data,
        computers: {
          ...document.data.computers,
          C2: {
            name: "New Computer",
            url: "https://example.test/original",
            hidden: false,
            studentEdited: true,
            compatibilityData: {
              D1: { compatibilityStatus: "Compatible", studentEdited: true },
            },
          },
        },
      },
      versions: {
        ...document.versions,
        computers: { ...document.versions.computers, C2: 1 },
        cells: { ...document.versions.cells, C2: { D1: 1 } },
      },
      approval: {
        pendingCount: isAdmin ? 1 : 0,
        pendingChanges: isAdmin ? [pendingChange] : [],
        draftCount: isAdmin ? 0 : 1,
        draftBundles: isAdmin ? [] : [pendingChange],
      },
    };
    mockedApi.getData.mockResolvedValueOnce(pendingComputerDocument);
    mockedApi.mutate.mockResolvedValue(pendingComputerDocument);

    render(<CompatibilityEditor />);

    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Computers" }));
    const nameInput = screen.getByLabelText("Name for C2");
    const urlInput = screen.getByLabelText("URL for C2");
    expect(nameInput).toBeEnabled();
    expect(urlInput).toBeEnabled();

    fireEvent.change(nameInput, { target: { value: "Corrected Computer" } });
    fireEvent.blur(nameInput);

    await waitFor(() => expect(mockedApi.mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "computer.update",
        computerKey: "C2",
        expectedVersion: 1,
        computer: expect.objectContaining({
          name: "Corrected Computer",
          url: "https://example.test/original",
        }),
      }),
      expect.any(String)
    ));
  });

  it("lets a contributor correct pending dock name and URL", async () => {
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
    const pendingDockChange: CompatibilityEditorChange = {
      id: "pending-dock-1",
      target: "dock:D2",
      mutationType: "dock.add",
      baseVersion: 0,
      version: 2,
      proposedData: {
        name: "New Dock",
        url: "https://example.test/original-dock",
        hidden: false,
      },
      currentData: null,
      status: "pending",
      readyForReview: false,
      bundle: {
        axis: "dock",
        itemKey: "D2",
        completedCells: 0,
        requiredCells: 1,
        missingTargets: ["cell:C1:D2"],
        ready: false,
      },
      submittedBy: "student@example.test",
      updatedBy: "student@example.test",
      submittedAt: "2026-08-28T12:00:00Z",
      updatedAt: "2026-08-28T12:01:00Z",
      reviewedBy: null,
      reviewedAt: null,
      reviewNote: null,
    };
    const pendingDockDocument: CompatibilityEditorDocument = {
      ...document,
      data: {
        ...document.data,
        docks: {
          ...document.data.docks,
          D2: {
            name: "New Dock",
            url: "https://example.test/original-dock",
            hidden: false,
            studentEdited: true,
          },
        },
      },
      versions: {
        ...document.versions,
        docks: { ...document.versions.docks, D2: 2 },
      },
      approval: {
        pendingCount: 0,
        pendingChanges: [],
        draftCount: 1,
        draftBundles: [pendingDockChange],
      },
    };
    mockedApi.getData.mockResolvedValueOnce(pendingDockDocument);
    mockedApi.mutate.mockResolvedValue(pendingDockDocument);

    render(<CompatibilityEditor />);

    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Docks" }));
    const nameInput = screen.getByLabelText("Name for D2");
    const urlInput = screen.getByLabelText("URL for D2");
    expect(nameInput).toBeEnabled();
    expect(urlInput).toBeEnabled();

    fireEvent.change(urlInput, { target: { value: "https://example.test/corrected-dock" } });
    fireEvent.blur(urlInput);

    await waitFor(() => expect(mockedApi.mutate).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "dock.update",
        dockKey: "D2",
        expectedVersion: 2,
        dock: expect.objectContaining({
          name: "New Dock",
          url: "https://example.test/corrected-dock",
        }),
      }),
      expect.any(String)
    ));
  });

  it("shows readable new-item settings with raw JSON available on demand", async () => {
    mockedApi.getData.mockResolvedValueOnce(reviewDocument);

    render(<CompatibilityEditor />);

    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Review (1)" }));

    expect(screen.getByRole("heading", { name: "New computer" })).toBeInTheDocument();
    expect(screen.getByText(/No approved computer with this SKU exists yet/i)).toBeInTheDocument();
    expect(screen.getByText("Product URL")).toBeInTheDocument();
    expect(screen.getByText("Website visibility")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Included compatibility results" })).toBeInTheDocument();
    expect(screen.getByText("Partially Compatible")).toBeInTheDocument();
    expect(screen.getByText("One display only")).toBeInTheDocument();
    expect(screen.getByText("View included cell JSON")).toBeInTheDocument();
    expect(screen.getByText("View raw JSON")).toBeInTheDocument();
  });

  it("requires confirmation before rejecting a proposed item", async () => {
    mockedApi.getData.mockResolvedValueOnce(reviewDocument);
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);

    render(<CompatibilityEditor />);
    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Review (1)" }));

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/discard the item and all 1 saved compatibility cell/i));
    expect(mockedApi.review).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(mockedApi.review).toHaveBeenCalledWith("ready-computer-1", "reject"));
  });

  it("warns before closing a cell editor with unsaved changes", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);

    render(<CompatibilityEditor />);

    fireEvent.click(await screen.findByTitle("Computer / Dock: Compatible"));
    fireEvent.change(screen.getByLabelText("Notes"), { target: { value: "Unsaved test result" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(confirm).toHaveBeenCalledWith("Discard the unsaved changes in this compatibility cell?");
    expect(screen.getByRole("button", { name: "Save and Approve" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Save and Approve" })).not.toBeInTheDocument();
    });
  });

  it("offers review once when a contributor completes the final bundle cell", async () => {
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
    const incompleteChange: CompatibilityEditorChange = {
      ...readyComputerChange,
      id: "completion-prompt-bundle",
      readyForReview: false,
      bundle: {
        ...readyComputerChange.bundle!,
        completedCells: 0,
        missingTargets: ["D1"],
        ready: false,
      },
    };
    const completedChange: CompatibilityEditorChange = {
      ...incompleteChange,
      bundle: {
        ...incompleteChange.bundle!,
        completedCells: 1,
        missingTargets: [],
      },
    };
    const incompleteDocument: CompatibilityEditorDocument = {
      ...document,
      data: {
        ...document.data,
        computers: {
          ...document.data.computers,
          C2: {
            name: "New Computer",
            studentEdited: true,
            compatibilityData: { D1: { studentEdited: true } },
          },
        },
      },
      versions: {
        ...document.versions,
        computers: { ...document.versions.computers, C2: 1 },
        cells: { ...document.versions.cells, C2: { D1: 0 } },
      },
      approval: {
        pendingCount: 0,
        pendingChanges: [],
        draftCount: 1,
        draftBundles: [incompleteChange],
      },
    };
    const completedDocument: CompatibilityEditorDocument = {
      ...incompleteDocument,
      workspaceRevision: 2,
      data: {
        ...incompleteDocument.data,
        computers: {
          ...incompleteDocument.data.computers,
          C2: {
            name: "New Computer",
            studentEdited: true,
            compatibilityData: {
              D1: { compatibilityStatus: "Compatible", studentEdited: true },
            },
          },
        },
      },
      versions: {
        ...incompleteDocument.versions,
        cells: { ...incompleteDocument.versions.cells, C2: { D1: 1 } },
      },
      approval: {
        pendingCount: 0,
        pendingChanges: [],
        draftCount: 1,
        draftBundles: [completedChange],
      },
    };
    mockedApi.getData.mockResolvedValueOnce(incompleteDocument);
    mockedApi.mutate.mockResolvedValue(completedDocument);

    render(<CompatibilityEditor />);

    fireEvent.click(await screen.findByTitle("New Computer / Dock: Draft cell not completed"));
    fireEvent.click(screen.getByRole("button", { name: "Save Cell to Draft" }));
    expect(await screen.findByRole("heading", { name: "New computer is complete" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    fireEvent.click(await screen.findByTitle("New Computer / Dock: Draft saved · Compatible"));
    fireEvent.click(screen.getByRole("button", { name: "Save Cell to Draft" }));

    await waitFor(() => expect(mockedApi.mutate).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("heading", { name: "New computer is complete" })).not.toBeInTheDocument();
  });

  it("lets an admin approve a complete new-item draft without submitting it", async () => {
    const completedDraft: CompatibilityEditorChange = {
      ...readyComputerChange,
      id: "admin-completed-draft",
      submittedBy: "admin@example.test",
      updatedBy: "admin@example.test",
      readyForReview: false,
      bundle: {
        ...readyComputerChange.bundle!,
        ready: false,
      },
    };
    const completedDraftDocument: CompatibilityEditorDocument = {
      ...document,
      data: {
        ...document.data,
        computers: {
          ...document.data.computers,
          C2: {
            name: "Admin Computer",
            hidden: false,
            studentEdited: true,
            compatibilityData: {
              D1: { compatibilityStatus: "Compatible", studentEdited: true },
            },
          },
        },
      },
      versions: {
        ...document.versions,
        computers: { ...document.versions.computers, C2: 1 },
        cells: { ...document.versions.cells, C2: { D1: 1 } },
      },
      approval: {
        pendingCount: 0,
        pendingChanges: [],
        draftCount: 1,
        draftBundles: [completedDraft],
      },
    };
    mockedApi.getData.mockResolvedValueOnce(completedDraftDocument);

    render(<CompatibilityEditor />);

    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Computers" }));
    fireEvent.click(screen.getByRole("button", { name: "Approve item" }));

    await waitFor(() => expect(mockedApi.review).toHaveBeenCalledWith(
      "admin-completed-draft",
      "approve"
    ));
  });

  it("does not let an admin approve another contributor's unsubmitted draft", async () => {
    const studentDraft: CompatibilityEditorChange = {
      ...readyComputerChange,
      id: "student-completed-draft",
      readyForReview: false,
      bundle: {
        ...readyComputerChange.bundle!,
        ready: false,
      },
    };
    mockedApi.getData.mockResolvedValueOnce({
      ...document,
      approval: {
        pendingCount: 0,
        pendingChanges: [],
        draftCount: 1,
        draftBundles: [studentDraft],
      },
    });

    render(<CompatibilityEditor />);
    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Review (1)" }));

    expect(screen.getByText(/Waiting for the contributor to submit this item/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve item" })).not.toBeInTheDocument();
  });
});
