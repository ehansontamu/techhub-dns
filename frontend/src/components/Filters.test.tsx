import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import Filters from "./Filters";
import { OrderStatus } from "../types/order";

describe("Filters", () => {
    it("keeps the search input interactive while loading", async () => {
        const user = userEvent.setup();
        const onSearchChange = vi.fn();
        const { rerender } = render(
            <Filters
                status={[OrderStatus.PICKED, OrderStatus.QA]}
                onStatusChange={vi.fn()}
                search=""
                onSearchChange={onSearchChange}
                loading={false}
            />
        );

        const searchInput = screen.getByRole("textbox", { name: "Search" });
        await user.click(searchInput);
        expect(searchInput).toHaveFocus();

        rerender(
            <Filters
                status={[OrderStatus.PICKED, OrderStatus.QA]}
                onStatusChange={vi.fn()}
                search="th"
                onSearchChange={onSearchChange}
                loading={true}
            />
        );

        expect(searchInput).not.toBeDisabled();
        expect(searchInput).toHaveFocus();
        expect(searchInput).toHaveAttribute("aria-busy", "true");
    });
});
