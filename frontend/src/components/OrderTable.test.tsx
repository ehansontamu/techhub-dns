import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OrderStatus } from "../types/order";
import OrderTable from "./OrderTable";

describe("OrderTable", () => {
    it("renders the status pill inline and removes the old status and updated columns", () => {
        render(
            <OrderTable
                orders={[
                    {
                        id: "order-1",
                        inflow_order_id: "TH1001",
                        status: OrderStatus.PICKED,
                        created_at: "2026-05-20T12:00:00Z",
                        updated_at: "2026-05-20T13:00:00Z",
                        recipient_name: "Ada Lovelace",
                        delivery_location: "Engineering Building",
                    },
                ]}
                onViewDetail={vi.fn()}
            />
        );

        expect(screen.getByRole("button", { name: "TH1001" })).toBeInTheDocument();
        expect(screen.getByText("Picked")).toBeInTheDocument();
        expect(screen.queryByRole("columnheader", { name: /status/i })).not.toBeInTheDocument();
        expect(screen.queryByRole("columnheader", { name: /updated/i })).not.toBeInTheDocument();
    });
});
