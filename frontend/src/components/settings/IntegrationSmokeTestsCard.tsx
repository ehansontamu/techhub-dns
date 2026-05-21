import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, RefreshCw, Zap } from "lucide-react";
import { toast } from "sonner";

import { inflowApi } from "../../api/inflow";
import { settingsApi } from "../../api/settings";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Input } from "../ui/input";
import { extractApiErrorMessage } from "../../utils/apiErrors";

type ActionKind = "email" | "teams" | "inflow" | "sharepoint" | "webhook" | "sync";

interface ActionEntry {
    id: string;
    label: string;
    status: "success" | "error";
    message: string;
}

export function IntegrationSmokeTestsCard() {
    const [recipientEmail, setRecipientEmail] = useState("");
    const [recentActions, setRecentActions] = useState<ActionEntry[]>([]);
    const [runningAction, setRunningAction] = useState<ActionKind | null>(null);

    const testEmailMutation = useMutation({ mutationFn: async (email: string) => settingsApi.testEmail(email) });
    const testTeamsMutation = useMutation({ mutationFn: async (email: string) => settingsApi.testTeamsRecipient(email) });
    const testInflowMutation = useMutation({ mutationFn: async () => settingsApi.testInflow() });
    const testSharePointMutation = useMutation({ mutationFn: async () => settingsApi.testSharePoint() });
    const testWebhookMutation = useMutation({ mutationFn: async () => inflowApi.testWebhook() });
    const syncMutation = useMutation({ mutationFn: async () => inflowApi.sync() });

    const busy =
        testEmailMutation.isPending ||
        testTeamsMutation.isPending ||
        testInflowMutation.isPending ||
        testSharePointMutation.isPending ||
        testWebhookMutation.isPending ||
        syncMutation.isPending;

    const pushAction = (label: string, status: "success" | "error", message: string) => {
        setRecentActions((current) => [
            { id: `${Date.now()}-${Math.random()}`, label, status, message },
            ...current,
        ].slice(0, 4));
    };

    const handleRecipientActions = async (kind: "email" | "teams") => {
        if (!recipientEmail.trim()) {
            toast.error("Enter an email address to test");
            return;
        }

        try {
            setRunningAction(kind);
            if (kind === "email") {
                const result = await testEmailMutation.mutateAsync(recipientEmail.trim());
                if (result.success) {
                    toast.success("Test email queued", { description: result.message || undefined });
                    pushAction("Email", "success", result.message || `Queued for ${recipientEmail.trim()}`);
                } else {
                    toast.error("Test email failed", { description: result.error || result.message || undefined });
                    pushAction("Email", "error", result.error || result.message || "Failed");
                }
            } else {
                const result = await testTeamsMutation.mutateAsync(recipientEmail.trim());
                if (result.success) {
                    toast.success("Test Teams message queued", { description: result.message || undefined });
                    pushAction("Teams", "success", result.message || `Queued for ${recipientEmail.trim()}`);
                } else {
                    toast.error("Test Teams message failed", { description: result.error || result.message || undefined });
                    pushAction("Teams", "error", result.error || result.message || "Failed");
                }
            }
        } catch (error: unknown) {
            const label = kind === "email" ? "Email" : "Teams";
            const message = extractApiErrorMessage(error, "Please try again.");
            toast.error(`${label} failed`, { description: message });
            pushAction(label, "error", message);
        } finally {
            setRunningAction(null);
        }
    };

    const handleSimpleAction = async (kind: "inflow" | "sharepoint" | "webhook" | "sync") => {
        try {
            setRunningAction(kind);
            if (kind === "inflow") {
                const result = await testInflowMutation.mutateAsync();
                if (result.success) {
                    toast.success("Inflow connection OK", { description: result.message || undefined });
                    pushAction("Inflow", "success", result.message || "Connection succeeded");
                } else {
                    toast.error("Inflow connection failed", { description: result.error || result.message || undefined });
                    pushAction("Inflow", "error", result.error || result.message || "Connection failed");
                }
            } else if (kind === "sharepoint") {
                const result = await testSharePointMutation.mutateAsync();
                if (result.success) {
                    toast.success("SharePoint connection OK", { description: result.message || undefined });
                    pushAction("SharePoint", "success", result.message || "Connection succeeded");
                } else {
                    toast.error("SharePoint connection failed", { description: result.error || result.message || undefined });
                    pushAction("SharePoint", "error", result.error || result.message || "Connection failed");
                }
            } else if (kind === "webhook") {
                const result = await testWebhookMutation.mutateAsync();
                toast.success("Webhook test queued", { description: result?.message || undefined });
                pushAction("Webhook", "success", result?.message || "Webhook test queued");
            } else {
                const result = await syncMutation.mutateAsync();
                toast.success("Recovery sync completed", { description: result?.message || undefined });
                pushAction("Sync", "success", result?.message || "Recovery sync completed");
            }
        } catch (error: unknown) {
            const label = kind === "sharepoint" ? "SharePoint" : kind === "inflow" ? "Inflow" : kind === "webhook" ? "Webhook" : "Sync";
            const message = extractApiErrorMessage(error, "Please try again.");
            toast.error(`${label} failed`, { description: message });
            pushAction(label, "error", message);
        } finally {
            setRunningAction(null);
        }
    };

    return (
        <Card className="border-border/70 bg-card/80">
            <CardHeader>
                <CardTitle className="text-base">Smoke tests</CardTitle>
                <CardDescription>Quick checks for mail, Teams, Inflow, SharePoint, and webhook transport.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="space-y-2">
                    <label className="text-xs font-medium text-muted-foreground">Recipient email</label>
                    <Input
                        type="email"
                        placeholder="recipient@tamu.edu"
                        value={recipientEmail}
                        onChange={(event) => setRecipientEmail(event.target.value)}
                        disabled={busy}
                    />
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                        <Button onClick={() => void handleRecipientActions("email")} disabled={busy}>
                            {runningAction === "email" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Zap className="mr-2 h-4 w-4" />}
                            Test email
                        </Button>
                        <Button variant="secondary" onClick={() => void handleRecipientActions("teams")} disabled={busy}>
                            {runningAction === "teams" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Zap className="mr-2 h-4 w-4" />}
                            Test Teams
                        </Button>
                    </div>
                </div>

                <div className="rounded-xl border bg-muted/20 p-4 space-y-3">
                    <div className="text-sm font-medium">Integration probes</div>
                    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                        <Button variant="outline" onClick={() => void handleSimpleAction("inflow")} disabled={busy}>
                            {runningAction === "inflow" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                            Inflow
                        </Button>
                        <Button variant="outline" onClick={() => void handleSimpleAction("sharepoint")} disabled={busy}>
                            {runningAction === "sharepoint" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                            SharePoint
                        </Button>
                        <Button variant="outline" onClick={() => void handleSimpleAction("webhook")} disabled={busy}>
                            {runningAction === "webhook" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                            Webhook test
                        </Button>
                        <Button variant="outline" onClick={() => void handleSimpleAction("sync")} disabled={busy}>
                            {runningAction === "sync" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                            Recovery sync
                        </Button>
                    </div>
                </div>

                <div className="rounded-xl border bg-muted/10 p-4">
                    <div className="flex items-center justify-between gap-2">
                        <div>
                            <p className="text-sm font-medium">Recent actions</p>
                            <p className="text-xs text-muted-foreground">Last four smoke test runs.</p>
                        </div>
                    </div>
                    {recentActions.length ? (
                        <div className="mt-3 space-y-2">
                            {recentActions.map((action) => (
                                <div
                                    key={action.id}
                                    className="flex min-w-0 flex-col gap-1 rounded-lg border bg-background/70 p-3 sm:flex-row sm:items-start sm:justify-between"
                                >
                                    <div className="min-w-0">
                                        <div className="text-sm font-medium">{action.label}</div>
                                        <p className="break-words text-xs text-muted-foreground">{action.message}</p>
                                    </div>
                                    <span className={`text-xs font-semibold ${action.status === "success" ? "text-emerald-600" : "text-destructive"}`}>
                                        {action.status}
                                    </span>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <p className="mt-3 text-sm text-muted-foreground">No actions yet.</p>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}
