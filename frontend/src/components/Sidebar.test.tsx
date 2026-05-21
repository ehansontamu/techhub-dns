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
        expect(screen.getByLabelText("Open sidebar")).toBeInTheDocument();

        fireEvent.click(screen.getByLabelText("Open sidebar"));
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
        expect(container.querySelector("aside")).toHaveStyle({ transform: "translateX(0px)" });
    });
});
