import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
  versions: {
    computers: { C1: 1 },
    docks: { D1: 1 },
    cells: { C1: { D1: 4 } },
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
  });

  it("saves a cell as a versioned database mutation", async () => {
    render(<CompatibilityEditor />);

    const cell = await screen.findByTitle("Computer / Dock: Compatible");
    fireEvent.click(cell);
    fireEvent.click(screen.getByRole("button", { name: "Save Cell" }));

    await waitFor(() => expect(mockedApi.mutate).toHaveBeenCalledTimes(1));
    expect(mockedApi.mutate.mock.calls[0][0]).toMatchObject({
      type: "cell.update",
      computerKey: "C1",
      dockKey: "D1",
      expectedVersion: 4,
    });
  });

  it("does not load editor data for a non-admin", async () => {
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

    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(mockedApi.getData).not.toHaveBeenCalled();
  });
});
