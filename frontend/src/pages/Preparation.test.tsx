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
        getPickerOptions: vi.fn(),
        bulkOverridePicker: vi.fn(),
        bulkMarkTagged: vi.fn(),
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
                tag_data: {
                    canopyorders_request_sent_at: "2026-05-20T15:30:00Z",
                    canopyorders_request_sent_by: "requester@example.com",
                },
            },
        ]);

        mockedOrdersApi.getOrders.mockImplementation(async (params) => {
            if (params?.status === OrderStatus.QA) {
                return {
                    items: [
                        {
                            id: "qa-1",
                            inflow_order_id: "TH3001",
                            recipient_name: "Picker Override",
                            delivery_location: "Main Lab",
                            status: OrderStatus.QA,
                            created_at: "2026-05-20T12:00:00Z",
                            updated_at: "2026-05-20T12:05:00Z",
                            picklist_generated_at: "2026-05-20T12:15:00Z",
                            picklist_generated_by: "Printer Person",
                        },
                    ],
                    total: 1,
                };
            }

            return {
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
            };
        });

        mockedOrdersApi.getPickerOptions.mockResolvedValue([
            {
                email: "picker@example.com",
                display_name: "Picker Person",
                label: "Picker Person",
            },
        ]);

        mockedOrdersApi.bulkOverridePicker.mockResolvedValue({
            success: true,
            picker_email: "picker@example.com",
            picker_display_name: "Picker Person",
            updated_orders: [{ id: "qa-1", inflow_order_id: "TH3001" }],
        });

        mockedOrdersApi.bulkMarkTagged.mockResolvedValue({
            success: true,
            updated_orders: [{ id: "candidate-1", inflow_order_id: "TH1001" }],
            failed_orders: [],
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
        expect(screen.getByText("Override Recorded Picker")).toBeInTheDocument();
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

    it("marks selected tag-request orders as tagged", async () => {
        renderWithQueryClient(<Preparation />);

        expect(await screen.findByText("TH1001")).toBeInTheDocument();
        fireEvent.click(screen.getByLabelText("Select TH1001"));
        fireEvent.click(screen.getByRole("button", { name: /mark selected as tagged/i }));

        expect(screen.getByText("Mark selected orders as tagged?")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: /^mark as tagged$/i }));

        await waitFor(() => {
            expect(mockedOrdersApi.bulkMarkTagged).toHaveBeenCalledWith({ order_ids: ["candidate-1"] });
        });
        expect(await screen.findByText(/Marked 1 order as tagged/)).toBeInTheDocument();
    });

    it("marks orders that already had an asset tag request sent", async () => {
        renderWithQueryClient(<Preparation />);

        expect(await screen.findByText("Tag request sent")).toBeInTheDocument();
        expect(screen.getByText("May 20, 2026 10:30 AM")).toBeInTheDocument();
        expect(screen.getByText("by requester@example.com")).toBeInTheDocument();
    });

    it("supports overriding the recorded picker for QA orders", async () => {
        renderWithQueryClient(<Preparation />);

        expect(await screen.findByText("TH3001")).toBeInTheDocument();

        fireEvent.click(screen.getByLabelText("Select TH3001"));
        fireEvent.change(screen.getByLabelText("Set picker to"), {
            target: { value: "picker@example.com" },
        });
        fireEvent.click(screen.getByRole("button", { name: /override picker/i }));

        await waitFor(() => {
            expect(mockedOrdersApi.bulkOverridePicker).toHaveBeenCalledWith({
                order_ids: ["qa-1"],
                picker_email: "picker@example.com",
            });
        });

        expect(await screen.findByText(/Updated picker for 1 order/)).toBeInTheDocument();
    });
});
