import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { inflowApi } from "../api/inflow";
import { ordersApi } from "../api/orders";
import { settingsApi } from "../api/settings";
import { useAuth } from "../contexts/AuthContext";
import { OperatorOverrideCard } from "../components/settings/OperatorOverrideCard";
import Settings from "./Settings";

vi.mock("../api/inflow", () => ({
    inflowApi: {
        getWebhookDefaults: vi.fn(),
        listWebhooks: vi.fn(),
        registerWebhook: vi.fn(),
        deleteWebhook: vi.fn(),
        testWebhook: vi.fn(),
        sync: vi.fn(),
    },
}));

vi.mock("../api/orders", () => ({
    ordersApi: {
        signOrder: vi.fn(),
        rollbackOrderStatus: vi.fn(),
        updateOrder: vi.fn(),
    },
}));

vi.mock("../api/settings", () => ({
    settingsApi: {
        getSyncHealth: vi.fn(),
        getSystemStatus: vi.fn(),
        getPrintJobs: vi.fn(),
        testEmail: vi.fn(),
        testTeamsRecipient: vi.fn(),
        testInflow: vi.fn(),
        testSharePoint: vi.fn(),
        retryPicklistPrint: vi.fn(),
    },
}));

vi.mock("../contexts/AuthContext", () => ({
    useAuth: vi.fn(),
}));

const mockedInflowApi = vi.mocked(inflowApi);
const mockedOrdersApi = vi.mocked(ordersApi);
const mockedSettingsApi = vi.mocked(settingsApi);
const mockedUseAuth = vi.mocked(useAuth);

function renderWithProviders(ui: ReactElement) {
    const client = new QueryClient({
        defaultOptions: {
            queries: {
                retry: false,
            },
        },
    });

    return render(
        <QueryClientProvider client={client}>
            <MemoryRouter>{ui}</MemoryRouter>
        </QueryClientProvider>,
    );
}

describe("Settings", () => {
    beforeEach(() => {
        vi.clearAllMocks();

        mockedUseAuth.mockReturnValue({
            user: { id: "user-1", email: "operator@tamu.edu", display_name: "Operator", department: "Ops", created_at: "2026-05-20T00:00:00Z", last_login_at: "2026-05-20T00:00:00Z" },
            session: null,
            isAuthenticated: true,
            isAdmin: true,
            isLoading: false,
            login: vi.fn(),
            logout: vi.fn(),
            refreshAuth: vi.fn(),
        });

        mockedSettingsApi.getSyncHealth.mockResolvedValue({
            server_time: "2026-05-20T14:00:00Z",
            inflow: {
                webhook_enabled: true,
                webhook_failed: false,
                last_webhook_received_at: "2026-05-20T13:45:00Z",
            },
        } as never);

        mockedSettingsApi.getSystemStatus.mockResolvedValue({
            saml_auth: {
                name: "TAMU SSO",
                enabled: true,
                configured: true,
                status: "active",
                details: "Entity: https://example.test",
            },
            graph_api: {
                name: "Microsoft Graph",
                enabled: true,
                configured: true,
                status: "active",
                details: "Connected",
            },
            sharepoint: {
                name: "SharePoint",
                enabled: true,
                configured: true,
                status: "warning",
                details: "Configured with limited access",
            },
            inflow_sync: {
                name: "Inflow Sync",
                enabled: true,
                configured: true,
                status: "active",
                details: "Polling fallback healthy",
            },
        } as never);

        mockedSettingsApi.getPrintJobs.mockResolvedValue({
            jobs: [
                {
                    id: "job-1",
                    order_id: "TH1001",
                    document_type: "picklist",
                    status: "pending",
                    trigger_source: "manual",
                    file_path: "/tmp/picklist.pdf",
                    attempt_count: 1,
                    created_at: "2026-05-20T13:00:00Z",
                    updated_at: "2026-05-20T13:05:00Z",
                },
            ],
        } as never);

        mockedInflowApi.getWebhookDefaults.mockResolvedValue({
            url: "https://example.test/api/inflow/webhook",
            events: ["orderCreated", "orderUpdated"],
        } as never);
        mockedInflowApi.listWebhooks.mockResolvedValue({
            webhooks: [
                {
                    id: "wh-1",
                    webhook_id: "webhook-1",
                    url: "https://example.test/api/inflow/webhook",
                    events: ["orderCreated", "orderUpdated"],
                    status: "active",
                    last_received_at: "2026-05-20T13:40:00Z",
                    failure_count: 0,
                    created_at: "2026-05-19T00:00:00Z",
                    updated_at: "2026-05-20T13:40:00Z",
                },
            ],
        } as never);

        mockedSettingsApi.testEmail.mockResolvedValue({ success: true, message: "Queued" } as never);
        mockedSettingsApi.testTeamsRecipient.mockResolvedValue({ success: true, message: "Queued" } as never);
        mockedSettingsApi.testInflow.mockResolvedValue({ success: true, message: "OK" } as never);
        mockedSettingsApi.testSharePoint.mockResolvedValue({ success: true, message: "OK" } as never);
        mockedSettingsApi.retryPicklistPrint.mockResolvedValue({
            success: true,
            job: {
                id: "job-2",
                order_id: "TH2001",
                document_type: "picklist",
                status: "pending",
                trigger_source: "manual",
                file_path: "/tmp/picklist.pdf",
                attempt_count: 1,
            },
        } as never);
        mockedInflowApi.testWebhook.mockResolvedValue({ success: true, message: "Queued" } as never);
        mockedInflowApi.sync.mockResolvedValue({ success: true, message: "Done" } as never);
        mockedInflowApi.registerWebhook.mockResolvedValue({
            id: "wh-2",
            webhook_id: "webhook-2",
            url: "https://example.test/api/inflow/webhook",
            events: ["orderCreated", "orderUpdated"],
            status: "active",
            last_received_at: null,
            failure_count: 0,
            created_at: "2026-05-20T00:00:00Z",
            updated_at: "2026-05-20T00:00:00Z",
        } as never);
        mockedInflowApi.deleteWebhook.mockResolvedValue({ success: true } as never);
        mockedOrdersApi.signOrder.mockResolvedValue({ success: true, message: "signed" } as never);
        mockedOrdersApi.rollbackOrderStatus.mockResolvedValue({
            inflow_order_id: "TH3001",
            status: "picked",
        } as never);
        mockedOrdersApi.updateOrder.mockResolvedValue({
            inflow_order_id: "TH3001",
            assigned_deliverer: "New Driver",
        } as never);
    });

    it("renders the refined settings dashboard and major sections", async () => {
        renderWithProviders(<Settings />);

        expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
        expect(screen.getByText("System status")).toBeInTheDocument();
        expect(screen.getByText("Smoke tests")).toBeInTheDocument();
        expect(screen.getByText("Webhook management")).toBeInTheDocument();
        expect(screen.getByText("Print queue")).toBeInTheDocument();
        expect(screen.getByRole("tab", { name: /advanced recovery/i })).toBeInTheDocument();
        expect((await screen.findAllByText("TAMU SSO")).length).toBeGreaterThan(0);
        expect(screen.getByText("TH1001")).toBeInTheDocument();
    });

    it("supports the smoke test and webhook confirmation flows", async () => {
        renderWithProviders(<Settings />);

        const emailInput = await screen.findByPlaceholderText("recipient@tamu.edu");
        fireEvent.change(emailInput, { target: { value: "recipient@tamu.edu" } });
        fireEvent.click(screen.getByRole("button", { name: /test email/i }));

        await waitFor(() => {
            expect(mockedSettingsApi.testEmail).toHaveBeenCalledWith("recipient@tamu.edu");
        });

        fireEvent.click(screen.getByRole("button", { name: /delete/i }));
        const confirmInput = await screen.findByPlaceholderText("Type webhook-1 to confirm");
        fireEvent.change(confirmInput, { target: { value: "webhook-1" } });
        fireEvent.click(screen.getByRole("button", { name: /delete webhook/i }));

        await waitFor(() => {
            expect(mockedInflowApi.deleteWebhook).toHaveBeenCalledWith("webhook-1");
        });
    });

    it("requires confirmation for advanced bypass signing", async () => {
        renderWithProviders(<OperatorOverrideCard />);

        fireEvent.change(screen.getAllByPlaceholderText("order id")[0], { target: { value: "TH3001" } });
        fireEvent.click(screen.getByRole("button", { name: /bypass signing/i }));

        const confirmInput = await screen.findByPlaceholderText("Type TH3001 to confirm");
        fireEvent.change(confirmInput, { target: { value: "TH3001" } });
        const confirmButtons = await screen.findAllByRole("button", { name: /bypass signing/i });
        fireEvent.click(confirmButtons[confirmButtons.length - 1]);

        await waitFor(() => {
            expect(mockedOrdersApi.signOrder).toHaveBeenCalledWith(
                "TH3001",
                expect.objectContaining({
                    placements: expect.any(Array),
                    signature_image: expect.stringContaining("data:image/png;base64"),
                }),
            );
        });
    });
});
