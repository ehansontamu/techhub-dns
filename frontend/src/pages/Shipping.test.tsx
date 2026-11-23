import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { shippingApi } from "../api/shipping";
import type { ShippingQueue, ShippingQueueOrder } from "../api/shipping";
import Shipping from "./Shipping";

vi.mock("../api/shipping", () => ({ shippingApi: { queue: vi.fn() } }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const row = (id: string, number: string, city: string): ShippingQueueOrder => ({
    id, order_number: number, city, recipient_name: "Example Requester",
    products: [{ product_id: "laptop", name: "Laptop", quantity: 1 }],
    shipment_id: null, shipment_order_id: null, shipment_status: null, shipping_workflow_status: "work_area",
});
const first = row("order-1", "TH000178", "Houston");
const second = row("order-2", "TH000196-P", "Galveston");
const reserved = { ...row("reserved", "TH-RESERVED", "Dallas"), shipment_id: "saved-shipment", shipment_order_id: "reserved", shipment_status: "draft" };
const empty = { ...row("empty", "TH-EMPTY", ""), products: [] };
const clients: QueryClient[] = [];
function Destination() {
    const location = useLocation();
    return <output aria-label="Prepared order batch">{location.pathname}{location.search}</output>;
}
function openQueue() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/shipping"]}><Routes>
        <Route path="/shipping" element={<Shipping />} /><Route path="/shipping/orders/:id" element={<Destination />} />
    </Routes></MemoryRouter></QueryClientProvider>);
    const user = userEvent.setup();
    return { client, click: (...args: Parameters<typeof user.click>) => act(async () => { await user.click(...args); }),
        type: (...args: Parameters<typeof user.type>) => act(async () => { await user.type(...args); }),
        clear: (...args: Parameters<typeof user.clear>) => act(async () => { await user.clear(...args); }) };
}
beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(shippingApi.queue).mockResolvedValue({ orders: [first, second, reserved, empty] });
});
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); });

describe("Order-first shipping queue", () => {
    it("shows orders directly without destination matching and prevents selecting reserved or empty orders", async () => {
        openQueue();
        expect(await screen.findByLabelText("Select TH000178")).toBeEnabled();
        expect(screen.getByLabelText("Select TH000196-P")).toBeEnabled();
        expect(screen.queryByLabelText("Destination city")).not.toBeInTheDocument();
        expect(screen.queryByText("Sandbox API key")).not.toBeInTheDocument();
        expect(screen.queryByText(/Address needs review/)).not.toBeInTheDocument();
        expect(screen.getByLabelText("Select TH-RESERVED")).toBeDisabled();
        expect(screen.getByLabelText("Select TH-EMPTY")).toBeDisabled();
    });

    it("starts one packing batch for selected orders from different source cities without choosing a contact", async () => {
        const user = openQueue();
        await user.click(await screen.findByLabelText("Select TH000178"));
        await user.click(screen.getByLabelText("Select TH000196-P"));
        await user.click(screen.getByRole("button", { name: "Pack 2 orders together" }));
        const url = new URL(screen.getByLabelText("Prepared order batch").textContent!, "http://example.test");
        expect(url.pathname).toBe("/shipping/orders/order-1");
        expect(url.searchParams.get("orders")).toBe("order-1,order-2");
        expect(url.searchParams.has("contact")).toBe(false);
    });

    it("keeps selected orders while searching and selects only the shown orders", async () => {
        const user = openQueue(); await screen.findByLabelText("Select TH000178");
        await user.type(screen.getByLabelText("Search order number"), "196");
        await user.click(screen.getByRole("button", { name: "Select shown orders" }));
        await user.clear(screen.getByLabelText("Search order number"));
        expect(screen.getByLabelText("Select TH000196-P")).toBeChecked();
        expect(screen.getByLabelText("Select TH000178")).not.toBeChecked();
        await user.click(screen.getByLabelText("Select TH000178"));
        await user.type(screen.getByLabelText("Search order number"), "178");
        expect(screen.getByRole("button", { name: "Pack 2 orders together" })).toBeEnabled();
    });

    it("drops a selection if another user reserves that order", async () => {
        const user = openQueue(); await user.click(await screen.findByLabelText("Select TH000178"));
        const next: ShippingQueue = { orders: [{ ...first, shipment_id: "other-shipment", shipment_order_id: first.id, shipment_status: "draft" }, second] };
        await act(async () => { user.client.setQueryData(["shipping-queue"], next); });
        await waitFor(() => expect(screen.getByLabelText("Select TH000178")).not.toBeChecked());
        expect(screen.getByLabelText("Select TH000178")).toBeDisabled();
        expect(screen.getByRole("button", { name: "Pack selected orders" })).toBeDisabled();
    });

    it("limits a bulk selection to fifty orders", async () => {
        vi.mocked(shippingApi.queue).mockResolvedValue({ orders: Array.from({ length: 51 }, (_, i) => row(`id-${i}`, `TH-${i}`, "")) });
        const user = openQueue(); await screen.findByLabelText("Select TH-50");
        await user.click(screen.getByRole("button", { name: "Select shown orders" }));
        expect(screen.getByRole("button", { name: "Pack 50 orders together" })).toBeEnabled();
        expect(screen.getByLabelText("Select TH-50")).toBeDisabled();
        expect(screen.getAllByRole("checkbox").filter(input => (input as HTMLInputElement).checked)).toHaveLength(50);
    });

    it("allows retrying a failed queue load", async () => {
        vi.mocked(shippingApi.queue).mockRejectedValueOnce(new Error("offline")).mockResolvedValue({ orders: [first] });
        const user = openQueue(); await user.click(await screen.findByRole("button", { name: "Retry" }));
        await waitFor(() => expect(screen.getByLabelText("Select TH000178")).toBeEnabled());
    });
});
