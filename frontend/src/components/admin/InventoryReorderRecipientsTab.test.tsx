import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { inventoryReorderRecipientsApi } from "../../api/inventoryReorderRecipients";
import InventoryReorderRecipientsTab from "./InventoryReorderRecipientsTab";

vi.mock("../../api/inventoryReorderRecipients", () => ({
    inventoryReorderRecipientsApi: {
        getRecipients: vi.fn(),
        updateRecipients: vi.fn(),
    },
}));

vi.mock("sonner", () => ({
    toast: {
        error: vi.fn(),
        success: vi.fn(),
    },
}));

const mockedApi = vi.mocked(inventoryReorderRecipientsApi);

describe("InventoryReorderRecipientsTab", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mockedApi.getRecipients.mockResolvedValue({
            recipients: ["added@example.com", "pinned@example.com"],
            source: "mixed",
            env_recipients: ["pinned@example.com"],
            db_recipients: ["added@example.com"],
        });
        mockedApi.updateRecipients.mockImplementation(async (recipients) => ({
            recipients: ["pinned@example.com", ...recipients],
            source: recipients.length > 0 ? "mixed" : "env",
            env_recipients: ["pinned@example.com"],
            db_recipients: recipients,
        }));
    });

    it("keeps env recipients pinned and saves changes to DB recipients", async () => {
        render(<InventoryReorderRecipientsTab />);

        expect(await screen.findByText("pinned@example.com")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Pinned" })).toBeDisabled();

        fireEvent.click(screen.getByRole("button", { name: "Remove" }));
        fireEvent.change(screen.getByPlaceholderText("inventory-team@example.com"), {
            target: { value: "New.Person@Example.com" },
        });
        fireEvent.click(screen.getByRole("button", { name: "Add" }));
        fireEvent.click(screen.getByRole("button", { name: "Save" }));

        await waitFor(() => {
            expect(mockedApi.updateRecipients).toHaveBeenCalledWith([
                "new.person@example.com",
            ]);
        });
    });
});
