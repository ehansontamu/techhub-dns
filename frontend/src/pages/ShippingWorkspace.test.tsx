import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { shippingApi } from "../api/shipping";
import type { Recipient, ShippingContact, ShippingWorkspace as Workspace } from "../api/shipping";
import ShippingWorkspace from "./ShippingWorkspace";

const auth = vi.hoisted(() => ({ isAdmin: false }));
vi.mock("../contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("../api/shipping", () => ({ shippingApi: { workspace: vi.fn(), contacts: vi.fn(), save: vi.fn() } }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const destination: Recipient = { name: "Saved Galveston Receiver", company: "Example Office", phone: "4095550100",
    address1: "100 Example Dock", address2: "", city: "Galveston", state: "TX", postal_code: "77550", country: "US", residential: false };
const contact: ShippingContact = { id: "galveston-contact", label: "Galveston office", recipient: destination };
const workspace: Workspace = {
    order_ids: ["order-1"], orders: [{ id: "order-1", order_number: "TH-EXAMPLE" }], contact_id: null,
    order_number: "TH-EXAMPLE", order_status: "shipping", shipping_workflow_status: "work_area", eligible: true,
    items: [{ order_id: "order-1", order_number: "TH-EXAMPLE", product_id: "laptop", name: "Laptop", sku: "LT", quantity: 1, weight_lb: 3 }],
    shipment: null, recipient: { ...destination, name: "Different Order Requester", company: "", phone: "", address1: "", city: "Houston", postal_code: "" },
    config: { configured: true, environment: "sandbox", missing: [] },
};
const clients: QueryClient[] = [];
function actions() {
    const user = userEvent.setup();
    return {
        click: (...args: Parameters<typeof user.click>) => act(async () => { await user.click(...args); }),
        selectOptions: (...args: Parameters<typeof user.selectOptions>) => act(async () => { await user.selectOptions(...args); }),
        type: (...args: Parameters<typeof user.type>) => act(async () => { await user.type(...args); }),
    };
}
function openWorkspace() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/shipping/orders/order-1"]}>
        <Routes><Route path="/shipping/orders/:orderId" element={<ShippingWorkspace />} /></Routes>
    </MemoryRouter></QueryClientProvider>);
    return actions();
}
async function picker() {
    await screen.findByRole("option", { name: /Galveston office/ });
    return screen.getByLabelText("Ship to saved FedEx contact", { exact: true });
}
async function pack(user: ReturnType<typeof actions>) {
    await user.selectOptions(screen.getByLabelText("Package for TH-EXAMPLE Laptop unit 1"), "box-1");
}

beforeEach(() => {
    vi.resetAllMocks(); auth.isAdmin = false;
    vi.mocked(shippingApi.workspace).mockResolvedValue(workspace);
    vi.mocked(shippingApi.contacts).mockResolvedValue([contact]);
    vi.mocked(shippingApi.save).mockResolvedValue({} as Awaited<ReturnType<typeof shippingApi.save>>);
});
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); });

describe("Saved shipping recipient selection", () => {
    it("loads contacts without a search and lets a regular user replace a mismatched order recipient", async () => {
        const user = openWorkspace(); const select = await picker();
        expect(shippingApi.contacts).toHaveBeenCalledWith("order-1", "");
        expect(select).toHaveValue("");
        expect(screen.queryByLabelText("Recipient name", { exact: true })).not.toBeInTheDocument();
        expect(screen.queryByLabelText("One-off address override (admin only)")).not.toBeInTheDocument();
        await user.selectOptions(select, contact.id);
        expect(screen.getByLabelText("Recipient name", { exact: true })).toHaveValue(destination.name);
        expect(screen.getByLabelText("Street address", { exact: true })).toHaveValue(destination.address1);
        expect(screen.getByLabelText("Phone", { exact: true })).toHaveValue(destination.phone);
        expect(screen.getByLabelText("City", { exact: true })).toHaveValue("Galveston");
        expect(screen.getByLabelText("Recipient name", { exact: true })).toHaveAttribute("readonly");
        await pack(user);
        await user.click(screen.getByRole("button", { name: "Save packing & review totals" }));
        await waitFor(() => expect(shippingApi.save).toHaveBeenCalledWith("order-1", expect.objectContaining({
            contact_id: contact.id, manual_address_override: false, recipient: destination, order_ids: ["order-1"],
        }), 0));
    });

    it("keeps the chosen recipient while searching for a different contact", async () => {
        vi.mocked(shippingApi.contacts).mockImplementation(async (_orderId, search) => search ? [] : [contact]);
        const user = openWorkspace(); await user.selectOptions(await picker(), contact.id);
        await user.type(screen.getByLabelText("Search FedEx contacts"), "not-found");
        await waitFor(() => expect(shippingApi.contacts).toHaveBeenCalledWith("order-1", "not-found"));
        await screen.findByText(/No matching contacts/);
        expect(screen.getByLabelText("Ship to saved FedEx contact")).toHaveValue(contact.id);
        expect(screen.getByLabelText("Recipient name", { exact: true })).toHaveValue(destination.name);
    });

    it("requires reviewing updated details when the saved contact changes before the next save", async () => {
        const user = openWorkspace(); await user.selectOptions(await picker(), contact.id); await pack(user);
        const updated = { ...contact, recipient: { ...destination, phone: "4095550199" } };
        vi.mocked(shippingApi.contacts).mockResolvedValue([updated]);
        await user.click(screen.getByRole("button", { name: "Refresh contacts" }));
        await screen.findByText(/This saved contact changed/);
        expect(screen.getByRole("button", { name: "Save packing & review totals" })).toBeDisabled();
        await user.click(screen.getByRole("button", { name: "Use updated contact" }));
        expect(screen.getByLabelText("Phone", { exact: true })).toHaveValue("4095550199");
        expect(screen.getByRole("button", { name: "Save packing & review totals" })).toBeEnabled();
    });

    it("offers a retry if the address book fails to load", async () => {
        vi.mocked(shippingApi.contacts).mockRejectedValueOnce(new Error("offline")).mockResolvedValue([contact]);
        const user = openWorkspace();
        await user.click(await screen.findByRole("button", { name: "Retry contacts" }));
        await picker();
    });

    it("keeps manual entry admin-only and exits the override when a saved contact is chosen", async () => {
        auth.isAdmin = true;
        const user = openWorkspace(); await picker();
        const override = screen.getByLabelText("One-off address override (admin only)");
        await user.click(override);
        expect(screen.getByLabelText("Street address", { exact: true })).not.toHaveAttribute("readonly");
        await user.type(screen.getByLabelText("Street address", { exact: true }), "One-off address");
        await user.selectOptions(screen.getByLabelText("Ship to saved FedEx contact"), contact.id);
        expect(override).not.toBeChecked();
        expect(screen.getByLabelText("Street address", { exact: true })).toHaveValue(destination.address1);
        expect(screen.getByLabelText("Street address", { exact: true })).toHaveAttribute("readonly");
    });

    it("offers Box 2 immediately even while product weights are missing", async () => {
        vi.mocked(shippingApi.workspace).mockResolvedValue({ ...workspace, items: [{ ...workspace.items[0], quantity: 3, weight_lb: null }] });
        const user = openWorkspace(); await picker();
        const select = screen.getByLabelText("Package for TH-EXAMPLE Laptop unit 2");
        await user.selectOptions(select, "box-2");
        expect(select).toHaveValue("box-2");
        expect(screen.getByText(/Available numbered boxes:/)).toHaveTextContent("Box 1, Box 2");
        expect(screen.getByRole("button", { name: "Save packing & review totals" })).toBeDisabled();
    });

    it("creates and assigns Box 3 directly in a unit selector without sending empty boxes", async () => {
        const user = openWorkspace(); await user.selectOptions(await picker(), contact.id);
        await user.selectOptions(screen.getByLabelText("Package for TH-EXAMPLE Laptop unit 1"), "new");
        expect(screen.getByText(/Available numbered boxes:/)).toHaveTextContent("Box 1, Box 2, Box 3");
        await user.click(screen.getByRole("button", { name: "Save packing & review totals" }));
        await waitFor(() => expect(shippingApi.save).toHaveBeenCalled());
        expect(vi.mocked(shippingApi.save).mock.calls[0][1].boxes).toEqual([{ name: "Box 3", use_as_box: false,
            items: [{ order_id: "order-1", product_id: "laptop", quantity: 1 }] }]);
    });

    it("keeps creating numbered boxes beyond Box 2 and exposes them in every selector", async () => {
        const user = openWorkspace(); await picker();
        for (let number = 3; number <= 7; number++) {
            await user.click(screen.getByRole("button", { name: "Add another box", exact: true }));
            expect(screen.getByRole("option", { name: `Box ${number}`, exact: true })).toBeInTheDocument();
        }
        expect(screen.getByText(/Available numbered boxes:/)).toHaveTextContent("Box 1, Box 2, Box 3, Box 4, Box 5, Box 6, Box 7");
    });

    it("saves units split between Box 1 and Box 7 while leaving unused boxes out", async () => {
        vi.mocked(shippingApi.workspace).mockResolvedValue({ ...workspace, items: [{ ...workspace.items[0], quantity: 2 }] });
        const user = openWorkspace(); await user.selectOptions(await picker(), contact.id);
        for (let number = 3; number <= 7; number++) await user.click(screen.getByRole("button", { name: "Add another box", exact: true }));
        await user.selectOptions(screen.getByLabelText("Package for TH-EXAMPLE Laptop unit 1"), "box-1");
        const last = screen.getAllByRole("option", { name: "Box 7", exact: true })[0] as HTMLOptionElement;
        await user.selectOptions(screen.getByLabelText("Package for TH-EXAMPLE Laptop unit 2"), last.value);
        await user.click(screen.getByRole("button", { name: "Save packing & review totals" }));
        await waitFor(() => expect(shippingApi.save).toHaveBeenCalled());
        expect(vi.mocked(shippingApi.save).mock.calls[0][1].boxes.map(box => box.name)).toEqual(["Box 1", "Box 7"]);
    });


    it("keeps detailed credential setup fields hidden from regular packing staff", async () => {
        vi.mocked(shippingApi.workspace).mockResolvedValue({ ...workspace,
            config: { configured: false, environment: "sandbox", missing: ["fedex_client_id"] } });
        openWorkspace(); await picker();
        expect(screen.getByText(/FedEx setup needs an administrator/)).toBeInTheDocument();
        expect(screen.queryByText(/fedex_client_id/)).not.toBeInTheDocument();
    });

});
