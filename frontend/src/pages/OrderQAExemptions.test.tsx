import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ordersApi } from "../api/orders";
import { settingsApi } from "../api/settings";
import { OrderStatus } from "../types/order";
import type { Order } from "../types/order";
import OrderQAChecklist from "./OrderQAChecklist";
import OrderQAPage from "./OrderQAPage";

vi.mock("../api/orders", () => ({ ordersApi: { getOrder: vi.fn(), getOrders: vi.fn(), submitQa: vi.fn() } }));
vi.mock("../api/settings", () => ({ settingsApi: { getWorkflowSettings: vi.fn() } }));
vi.mock("../contexts/AuthContext", () => ({
    useAuth: () => ({ user: { email: "qa@example.com", display_name: "QA Technician" } }),
}));

const tti = "TTI - Texas A&M Transportation Institute";
const order = (overrides: Partial<Order> = {}): Order => ({
    id: "order-1",
    inflow_order_id: "TH5727",
    status: OrderStatus.QA,
    created_at: "2026-10-08T12:00:00Z",
    updated_at: "2026-10-08T12:00:00Z",
    picklist_generated_at: "2026-10-08T12:00:00Z",
    picklist_generated_by: "picker@example.com",
    ...overrides,
});

function renderPage(qaForm = true) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
        <QueryClientProvider client={client}>
            <MemoryRouter initialEntries={[qaForm ? "/orders/order-1/qa" : "/order-qa"]}>
                <Routes>
                    <Route path="/orders/:orderId/qa" element={<OrderQAPage />} />
                    <Route path="/order-qa" element={qaForm ? <p>QA submitted</p> : <OrderQAChecklist />} />
                </Routes>
            </MemoryRouter>
        </QueryClientProvider>,
    );
    return client;
}

beforeEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
    vi.mocked(settingsApi.getWorkflowSettings).mockResolvedValue({} as never);
    vi.mocked(ordersApi.submitQa).mockResolvedValue(order());
});

describe("College/Unit exemptions in QA", () => {
    it("requires the other four checks and submits exempt tagging as false", async () => {
        vi.mocked(ordersApi.getOrder).mockResolvedValue(order({ college_unit: tti, asset_tag_exempt: true, asset_tag_required: false }));
        renderPage();
        expect(await screen.findByText(/Asset tag verification not required/)).toHaveTextContent(tti);
        expect(screen.queryByLabelText(/Confirm the asset tag is applied/)).not.toBeInTheDocument();
        const checks = screen.getAllByRole("checkbox");
        expect(checks).toHaveLength(4);
        const submit = screen.getByRole("button", { name: "Submit QA Checklist" });
        for (const check of checks.slice(0, 3)) fireEvent.click(check);
        expect(submit).toBeDisabled();
        fireEvent.click(checks[3]);
        expect(submit).toBeEnabled();
        await act(async () => { fireEvent.click(submit); });
        await waitFor(() => expect(ordersApi.submitQa).toHaveBeenCalledWith("order-1", expect.objectContaining({
            responses: expect.objectContaining({
                verifyAssetTagSerialMatch: false,
                verifyPackagedProperly: true,
                verifyPackingSlipSerialsMatch: true,
                verifyBoxesLabeledCorrectly: true,
                verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved: true,
            }),
        })));
        expect(await screen.findByText("QA submitted")).toBeInTheDocument();
    });

    it("still requires tag verification for a normal order", async () => {
        vi.mocked(ordersApi.getOrder).mockResolvedValue(order({ asset_tag_exempt: false, asset_tag_required: true }));
        renderPage();
        const tagCheck = await screen.findByLabelText(/Confirm the asset tag is applied/);
        for (const check of screen.getAllByRole("checkbox").filter((check) => check !== tagCheck)) fireEvent.click(check);
        expect(screen.getByRole("button", { name: "Submit QA Checklist" })).toBeDisabled();
        fireEvent.click(tagCheck);
        expect(screen.getByRole("button", { name: "Submit QA Checklist" })).toBeEnabled();
    });

    it("keeps the different-picker QA restriction for exempt orders", async () => {
        vi.mocked(ordersApi.getOrder).mockResolvedValue(order({ asset_tag_exempt: true, picklist_generated_by: "qa@example.com" }));
        renderPage();
        expect(await screen.findByText(/QA must be completed by another logged-in user/)).toBeInTheDocument();
        for (const check of screen.getAllByRole("checkbox")) expect(check).toBeDisabled();
        expect(screen.getByRole("button", { name: "QA unavailable" })).toBeDisabled();
    });

    it("includes picked exempt orders with picklists across pages and refreshes after policy changes", async () => {
        const ready = order({ id: "ready", inflow_order_id: "TH6001", status: OrderStatus.PICKED, asset_tag_exempt: true });
        vi.mocked(ordersApi.getOrders).mockImplementation(async (params) => {
            if (params?.status === OrderStatus.QA) return { items: [order()], total: 1 };
            if (params?.skip === 0) return {
                items: [order({ id: "normal", inflow_order_id: "TH6002", status: OrderStatus.PICKED }),
                    order({ id: "no-picklist", inflow_order_id: "TH6003", status: OrderStatus.PICKED, asset_tag_exempt: true, picklist_generated_at: undefined })],
                total: 3,
            };
            return { items: [ready], total: 3 };
        });
        const client = renderPage(false);
        expect(await screen.findByRole("button", { name: "TH6001" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "TH5727" })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "TH6002" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "TH6003" })).not.toBeInTheDocument();
        ready.asset_tag_exempt = false;
        await act(async () => { await client.invalidateQueries({ queryKey: ["orders"] }); });
        await waitFor(() => expect(screen.queryByRole("button", { name: "TH6001" })).not.toBeInTheDocument());
    });

    it("shows a loading failure instead of claiming there are no QA orders", async () => {
        vi.mocked(ordersApi.getOrders).mockRejectedValue(new Error("offline"));
        renderPage(false);
        expect(await screen.findByRole("alert")).toHaveTextContent("Unable to load orders");
        expect(screen.queryByText("No orders need QA at this time.")).not.toBeInTheDocument();
    });
});
