import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { Navigate, MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ordersApi } from "../api/orders";
import { settingsApi } from "../api/settings";
import { OrderStatus } from "../types/order";
import Preparation from "./Preparation";

vi.mock("../api/orders", () => ({
    ordersApi: {
        getTagRequestCandidates: vi.fn(),
        getOrders: vi.fn(),
        getOrder: vi.fn(),
        generatePicklist: vi.fn(),
    },
}));

vi.mock("../api/settings", () => ({
    settingsApi: {
        uploadCanopyOrders: vi.fn(),
    },
}));

const mockedOrdersApi = vi.mocked(ordersApi);
const mockedSettingsApi = vi.mocked(settingsApi);

function renderWithQueryClient(ui: ReactElement) {
    const client = new QueryClient({
        defaultOptions: {
            queries: {
                retry: false,
            },
        },
    });

    return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("Preparation", () => {
    beforeEach(() => {
        mockedOrdersApi.getTagRequestCandidates.mockResolvedValue([
            {
                id: "candidate-1",
                inflow_order_id: "TH1001",
                recipient_name: "Ada Lovelace",
                delivery_location: "Engineering Building",
                picklist_generated_at: null,
            },
        ]);

        mockedOrdersApi.getOrders.mockResolvedValue({
            items: [
                {
                    id: "prep-1",
                    inflow_order_id: "TH2001",
                    recipient_name: "Grace Hopper",
                    delivery_location: "Main Lab",
                    status: OrderStatus.PICKED,
                    created_at: "2026-05-20T12:00:00Z",
                    updated_at: "2026-05-20T12:05:00Z",
                    tagged_at: "2026-05-20T12:10:00Z",
                    picklist_generated_at: null,
                },
                {
                    id: "prep-2",
                    inflow_order_id: "TH2002",
                    recipient_name: "No Tag Required",
                    delivery_location: "Annex",
                    status: OrderStatus.PICKED,
                    created_at: "2026-05-20T12:00:00Z",
                    updated_at: "2026-05-20T12:05:00Z",
                    tagged_at: null,
                    asset_tag_required: false,
                    picklist_generated_at: null,
                },
                {
                    id: "prep-3",
                    inflow_order_id: "TH2003",
                    recipient_name: "Blocked User",
                    delivery_location: "Main Lab",
                    status: OrderStatus.PICKED,
                    created_at: "2026-05-20T12:00:00Z",
                    updated_at: "2026-05-20T12:05:00Z",
                    tagged_at: null,
                    asset_tag_required: true,
                    picklist_generated_at: null,
                },
            ],
            total: 3,
        });

        mockedOrdersApi.getOrder.mockResolvedValue({
            id: "prep-1",
            inflow_order_id: "TH2001",
            recipient_name: "Grace Hopper",
            delivery_location: "Main Lab",
            status: OrderStatus.PICKED,
            created_at: "2026-05-20T12:00:00Z",
            updated_at: "2026-05-20T12:05:00Z",
            tagged_at: "2026-05-20T12:10:00Z",
            asset_tag_required: true,
            picklist_generated_at: null,
        });

        mockedOrdersApi.generatePicklist.mockResolvedValue({
            id: "prep-1",
            inflow_order_id: "TH2001",
            status: OrderStatus.PICKED,
        });

        mockedSettingsApi.uploadCanopyOrders.mockResolvedValue({
            success: true,
            count: 1,
            eligible_orders: ["TH1001"],
            ineligible_orders: [],
            updated_orders: 1,
            teams_notified: false,
        });
    });

    it("supports selecting prep orders and batch generating picklists", async () => {
        renderWithQueryClient(<Preparation />);

        expect(await screen.findByText("Preparation")).toBeInTheDocument();
        expect(await screen.findByText("TH2001")).toBeInTheDocument();
        expect(await screen.findByText("TH2002")).toBeInTheDocument();
        expect(screen.getByText("Tag Request Actions")).toBeInTheDocument();
        expect(screen.getByText("Generate Picklist & Order Details")).toBeInTheDocument();
        expect(screen.queryByText("TH2003")).not.toBeInTheDocument();

        fireEvent.click(screen.getByLabelText("Select TH2001"));

        fireEvent.click(screen.getByRole("button", { name: /^generate$/i }));
        expect(screen.getByText("Generate picklists for selected orders?")).toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: /generate now/i }));

        await waitFor(() => {
            expect(mockedOrdersApi.getOrder).toHaveBeenCalledWith("prep-1");
            expect(mockedOrdersApi.generatePicklist).toHaveBeenCalledWith("prep-1", {
                expected_updated_at: "2026-05-20T12:05:00Z",
            });
        });

        expect(await screen.findByText(/Prepared 1 order/)).toBeInTheDocument();
    });

    it("redirects the legacy tag-request route to preparation", async () => {
        render(
            <MemoryRouter initialEntries={["/tag-request"]}>
                <Routes>
                    <Route path="/preparation" element={<div>Preparation Route</div>} />
                    <Route path="/tag-request" element={<Navigate to="/preparation" replace />} />
                </Routes>
            </MemoryRouter>
        );

        expect(await screen.findByText("Preparation Route")).toBeInTheDocument();
    });
});
