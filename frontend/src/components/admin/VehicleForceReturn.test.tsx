import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { vehicleCheckoutsApi } from "../../api/vehicleCheckouts";
import VehicleForceReturn from "./VehicleForceReturn";

vi.mock("../../api/vehicleCheckouts", () => ({
    vehicleCheckoutsApi: { getActive: vi.fn(), getStatuses: vi.fn(), forceReturn: vi.fn() },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

const api = vi.mocked(vehicleCheckoutsApi);
const checkout = {
    id: "11111111-1111-1111-1111-111111111111",
    vehicle: "golf_cart" as const,
    checked_out_by: "Student One",
    checkout_type: "other" as const,
    purpose: "Tech Duty",
    checked_out_at: "2026-10-12T14:00:00Z",
};

function renderPanel() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
        <QueryClientProvider client={client}>
            <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
                <VehicleForceReturn />
            </MemoryRouter>
        </QueryClientProvider>
    );
    return client;
}

describe("VehicleForceReturn", () => {
    beforeEach(() => {
        vi.resetAllMocks();
        api.getActive.mockResolvedValue([checkout]);
        api.getStatuses.mockResolvedValue({ vehicles: [
            { vehicle: "van", checked_out: false, delivery_run_active: false },
            { vehicle: "golf_cart", checked_out: true, checkout_type: "other", delivery_run_active: false },
        ] });
        api.forceReturn.mockResolvedValue({ ...checkout, checked_in_at: "2026-10-12T16:00:00Z" });
    });

    it("requires a reason and explicit confirmation before returning another user's checkout", async () => {
        renderPanel();
        fireEvent.click(await screen.findByRole("button", { name: "Force Return Golf Cart" }));
        const confirm = screen.getByRole("button", { name: "Confirm Force Return" });
        expect(confirm).toBeDisabled();
        fireEvent.change(screen.getByLabelText("Reason (required)"), { target: { value: "   " } });
        expect(confirm).toBeDisabled();
        expect(api.forceReturn).not.toHaveBeenCalled();
        fireEvent.change(screen.getByLabelText("Reason (required)"), { target: { value: "  Forgotten check-in  " } });
        fireEvent.click(confirm);
        await waitFor(() => expect(api.forceReturn.mock.calls[0]?.[0]).toEqual({
            vehicle: "golf_cart", checkout_id: checkout.id, reason: "Forgotten check-in",
        }));
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("blocks a delivery run even when the checkout purpose says Tech Duty", async () => {
        api.getStatuses.mockResolvedValue({ vehicles: [
            { vehicle: "golf_cart", checked_out: true, delivery_run_active: true },
        ] });
        renderPanel();
        expect(await screen.findByRole("button", { name: "Force Return Golf Cart" })).toBeDisabled();
        expect(screen.getByText(/Force Return is blocked/)).toBeInTheDocument();
        expect(screen.getByRole("link", { name: "Delivery" })).toHaveAttribute("href", "/delivery/dispatch");
    });

    it("disables an open confirmation if a new checkout replaces it", async () => {
        const client = renderPanel();
        fireEvent.click(await screen.findByRole("button", { name: "Force Return Golf Cart" }));
        fireEvent.change(screen.getByLabelText("Reason (required)"), { target: { value: "Returned" } });
        api.getActive.mockResolvedValue([{ ...checkout, id: "22222222-2222-2222-2222-222222222222" }]);
        await client.invalidateQueries({ queryKey: ["admin", "vehicle-force-return"] });
        await waitFor(() => expect(screen.getByRole("button", { name: "Confirm Force Return" })).toBeDisabled());
        expect(api.forceReturn).not.toHaveBeenCalled();
    });

    it("shows server rejection without clearing the checkout", async () => {
        api.forceReturn.mockRejectedValue({ response: { data: { error: { message: "A delivery run is now active" } } } });
        renderPanel();
        fireEvent.click(await screen.findByRole("button", { name: "Force Return Golf Cart" }));
        fireEvent.change(screen.getByLabelText("Reason (required)"), { target: { value: "Returned" } });
        fireEvent.click(screen.getByRole("button", { name: "Confirm Force Return" }));
        expect(await screen.findByRole("alert")).toHaveTextContent("A delivery run is now active");
        expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
});
