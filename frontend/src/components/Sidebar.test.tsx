import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Sidebar } from "./Sidebar";

vi.mock("../contexts/AuthContext", () => ({
    useAuth: () => ({
        isAdmin: false,
    }),
}));

describe("Sidebar", () => {
    let currentMatches = true;

    beforeEach(() => {
        currentMatches = true;

        vi.stubGlobal("matchMedia", vi.fn(() => ({
            media: "(max-width: 1023px)",
            get matches() {
                return currentMatches;
            },
            onchange: null,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
            addListener: vi.fn(),
            removeListener: vi.fn(),
            dispatchEvent: vi.fn(() => true),
        } as unknown as MediaQueryList)));
    });

    it("resets the mobile sidebar when crossing the breakpoint", async () => {
        const { container } = render(
            <MemoryRouter>
                <Sidebar />
            </MemoryRouter>
        );

        expect(document.documentElement.style.getPropertyValue("--sidebar-width")).toBe("0px");
        const mobileSidebarLauncher = screen.getAllByLabelText("Open sidebar")[0];
        expect(mobileSidebarLauncher).toBeInTheDocument();

        fireEvent.click(mobileSidebarLauncher);
        expect(screen.getByLabelText("Close sidebar overlay")).toBeInTheDocument();

        currentMatches = false;
        act(() => {
            window.dispatchEvent(new Event("resize"));
            window.dispatchEvent(new Event("orientationchange"));
        });

        await waitFor(() => {
            expect(document.documentElement.style.getPropertyValue("--sidebar-width")).toBe("256px");
        });

        expect(screen.queryByLabelText("Open sidebar")).not.toBeInTheDocument();
        expect(screen.getByLabelText("Collapse sidebar")).toBeInTheDocument();
        expect(screen.queryByLabelText("Close sidebar overlay")).not.toBeInTheDocument();
        expect(screen.queryByRole("link", { name: "Compatibility Editor" })).not.toBeInTheDocument();
        await waitFor(() => {
            expect(container.querySelector("aside")).toHaveStyle({ transform: "none", width: "256px" });
        });
    });

    it.each([
        ["/", "Dashboard"],
        ["/settings", "Settings"],
    ])("uses the maroon selected style for %s", (path, label) => {
        render(
            <MemoryRouter initialEntries={[path]}>
                <Sidebar />
            </MemoryRouter>
        );

        expect(screen.getByRole("link", { name: label })).toHaveClass(
            "bg-accent",
            "text-accent-foreground",
            "shadow-accent/25"
        );
    });
});
