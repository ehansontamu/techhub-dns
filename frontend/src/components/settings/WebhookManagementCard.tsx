import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardCopy, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { inflowApi } from "../../api/inflow";
import type { WebhookResponse } from "../../api/inflow";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Input } from "../ui/input";
import { Badge } from "../ui/badge";
import { ConfirmActionDialog } from "./ConfirmActionDialog";
import { copyToClipboard, formatTimestamp, getStatusBadgeVariant } from "./utils";
import { extractApiErrorMessage } from "../../utils/apiErrors";

interface RecentWebhookAction {
    id: string;
    label: string;
    message: string;
    status: "success" | "error";
}

type RecentWebhookActionInput = Omit<RecentWebhookAction, "id">;

function parseEvents(raw: string) {
    return raw
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
}

function formatWebhookSummary(webhooks: WebhookResponse[]) {
    const active = webhooks.find((webhook) => webhook.status === "active");
    return {
        active,
        lastReceivedAt: active?.last_received_at ?? webhooks[0]?.last_received_at ?? null,
    };
}

export function WebhookManagementCard() {
    const queryClient = useQueryClient();
    const [webhookUrl, setWebhookUrl] = useState("");
    const [eventDraft, setEventDraft] = useState("");
    const [eventInput, setEventInput] = useState("");
    const [recentActions, setRecentActions] = useState<RecentWebhookAction[]>([]);
    const [deleteTarget, setDeleteTarget] = useState<WebhookResponse | null>(null);

    const recordRecentAction = (action: RecentWebhookActionInput) => {
        setRecentActions((current) => [{ id: `${Date.now()}`, ...action }, ...current].slice(0, 4));
    };

    const webhookDefaultsQuery = useQuery({
        queryKey: ["settings", "webhook-defaults"],
        queryFn: async () => inflowApi.getWebhookDefaults(),
        refetchOnWindowFocus: false,
    });
    const webhooksQuery = useQuery({
        queryKey: ["settings", "webhooks"],
        queryFn: async () => inflowApi.listWebhooks(),
        refetchInterval: 60_000,
    });

    const registerWebhookMutation = useMutation({
        mutationFn: async ({ url, events }: { url: string; events: string[] }) => inflowApi.registerWebhook({ url, events }),
        onSuccess: async () => {
            await Promise.all([
                queryClient.invalidateQueries({ queryKey: ["settings", "webhooks"] }),
                queryClient.invalidateQueries({ queryKey: ["settings", "webhook-defaults"] }),
            ]);
            toast.success("Webhook registered", {
                description: "The Inflow webhook subscription was recreated from the settings page.",
            });
            recordRecentAction({ label: "Register", message: "Webhook registration updated", status: "success" });
        },
        onError: (error: unknown) => {
            const message = extractApiErrorMessage(error, "Please try again.");
            toast.error("Failed to register webhook", { description: message });
            recordRecentAction({ label: "Register", message, status: "error" });
        },
    });
    const deleteWebhookMutation = useMutation({
        mutationFn: async (webhookId: string) => inflowApi.deleteWebhook(webhookId),
        onSuccess: async (_result, webhookId) => {
            await queryClient.invalidateQueries({ queryKey: ["settings", "webhooks"] });
            toast.success("Webhook deleted", { description: `${webhookId} removed` });
            recordRecentAction({ label: "Delete", message: `${webhookId} removed`, status: "success" });
        },
        onError: (error: unknown) => {
            const message = extractApiErrorMessage(error, "Please try again.");
            toast.error("Failed to delete webhook", { description: message });
            recordRecentAction({ label: "Delete", message, status: "error" });
        },
    });

    const defaults = webhookDefaultsQuery.data;
    const webhooks = webhooksQuery.data?.webhooks ?? [];
    const summary = formatWebhookSummary(webhooks);
    const busy = webhookDefaultsQuery.isFetching || webhooksQuery.isFetching || registerWebhookMutation.isPending || deleteWebhookMutation.isPending;
    const eventList = useMemo(() => parseEvents(eventDraft), [eventDraft]);

    useEffect(() => {
        if (!defaults) return;
        setWebhookUrl((current) => current || defaults.url || "");
        setEventDraft((current) => current || defaults.events.join(", "));
    }, [defaults]);

    const addEvent = () => {
        const normalized = eventInput.trim();
        if (!normalized) return;
        setEventDraft((current) => {
            const items = parseEvents(current);
            if (!items.includes(normalized)) {
                items.push(normalized);
            }
            return items.join(", ");
        });
        setEventInput("");
    };

    const removeEvent = (value: string) => {
        setEventDraft((current) => parseEvents(current).filter((item) => item !== value).join(", "));
    };

    const handleRegister = async () => {
        const url = webhookUrl.trim();
        if (!url) {
            toast.error("Enter a webhook URL");
            return;
        }
        if (!eventList.length) {
            toast.error("Add at least one webhook event");
            return;
        }

        await registerWebhookMutation.mutateAsync({ url, events: eventList });
    };

    return (
        <>
            <Card className="border-border/70 bg-card/80">
                <CardHeader>
                    <CardTitle className="text-base">Webhook management</CardTitle>
                    <CardDescription>Register, inspect, or remove the Inflow webhook subscription.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid gap-3 sm:grid-cols-3">
                        <div className="min-w-0 rounded-xl border bg-muted/20 p-3">
                            <div className="text-xs font-medium uppercase text-muted-foreground">Active subscription</div>
                            <p className="mt-1 break-words text-sm">{summary.active ? summary.active.webhook_id : "None"}</p>
                        </div>
                        <div className="min-w-0 rounded-xl border bg-muted/20 p-3">
                            <div className="text-xs font-medium uppercase text-muted-foreground">Last received</div>
                            <p className="mt-1 text-sm">{formatTimestamp(summary.lastReceivedAt)}</p>
                        </div>
                        <div className="min-w-0 rounded-xl border bg-muted/20 p-3">
                            <div className="text-xs font-medium uppercase text-muted-foreground">Default URL</div>
                            <p className="mt-1 break-words text-sm">{defaults?.url || "Not configured"}</p>
                        </div>
                    </div>

                    <div className="space-y-2">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div>
                                <p className="text-sm font-medium">Webhook URL</p>
                                <p className="text-xs text-muted-foreground">Use the defaults or copy the current value.</p>
                            </div>
                            <div className="flex gap-2">
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    onClick={() => void copyToClipboard(webhookUrl || defaults?.url || "", "Webhook URL copied")}
                                    disabled={!webhookUrl && !defaults?.url}
                                >
                                    <ClipboardCopy className="mr-2 h-4 w-4" />
                                    Copy
                                </Button>
                                <Button type="button" variant="outline" size="sm" onClick={() => defaults && setWebhookUrl(defaults.url || "")} disabled={!defaults || busy}>
                                    Use defaults
                                </Button>
                            </div>
                        </div>
                        <Input value={webhookUrl} onChange={(event) => setWebhookUrl(event.target.value)} placeholder={defaults?.url || "https://..."} disabled={busy} />
                    </div>

                    <div className="space-y-2">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div>
                                <p className="text-sm font-medium">Webhook events</p>
                                <p className="text-xs text-muted-foreground">Manage subscriptions as chips instead of a comma string.</p>
                            </div>
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => void copyToClipboard((defaults?.events || []).join(", "), "Default events copied")}
                                disabled={!defaults?.events?.length}
                            >
                                <ClipboardCopy className="mr-2 h-4 w-4" />
                                Copy defaults
                            </Button>
                        </div>
                        <div className="flex min-w-0 flex-wrap gap-2">
                            {eventList.map((event) => (
                                <Badge key={event} variant="secondary" className="gap-2 pr-1">
                                    {event}
                                    <button
                                        type="button"
                                        className="rounded-full px-1 text-xs text-muted-foreground hover:text-foreground"
                                        onClick={() => removeEvent(event)}
                                        aria-label={`Remove ${event}`}
                                        disabled={busy}
                                    >
                                        ×
                                    </button>
                                </Badge>
                            ))}
                        </div>
                        <div className="flex flex-col gap-2 sm:flex-row">
                            <Input
                                value={eventInput}
                                onChange={(event) => setEventInput(event.target.value)}
                                placeholder="orderCreated"
                                onKeyDown={(event) => {
                                    if (event.key === "Enter") {
                                        event.preventDefault();
                                        addEvent();
                                    }
                                }}
                                disabled={busy}
                            />
                            <Button type="button" variant="outline" onClick={addEvent} disabled={busy}>
                                Add event
                            </Button>
                        </div>
                        <div className="rounded-xl border bg-background/60 p-3 break-words text-sm text-muted-foreground">
                            {eventList.length ? eventList.join(", ") : "No events selected"}
                        </div>
                    </div>

                    <div className="flex flex-wrap gap-2">
                        <Button onClick={() => void handleRegister()} disabled={busy}>
                            {registerWebhookMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                            Register / re-register
                        </Button>
                        <Button variant="secondary" onClick={() => void webhooksQuery.refetch()} disabled={busy}>
                            Refresh list
                        </Button>
                    </div>

                    <div className="space-y-3">
                        <div className="text-xs font-medium uppercase text-muted-foreground">Current webhooks</div>
                        {webhooks.length ? (
                            <div className="space-y-2">
                                {webhooks.map((webhook) => (
                                    <div key={webhook.id} className="rounded-xl border bg-background/60 p-3">
                                        <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                                            <div className="min-w-0 space-y-1">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <span className="min-w-0 break-words text-sm font-medium">{webhook.webhook_id}</span>
                                                    <Badge variant={getStatusBadgeVariant(webhook.status === "active" ? "active" : "warning")}>
                                                        {webhook.status}
                                                    </Badge>
                                                </div>
                                                <p className="break-words text-xs text-muted-foreground">{webhook.url}</p>
                                                <p className="break-words text-xs text-muted-foreground">
                                                    {webhook.events.join(", ") || "No events recorded"}
                                                </p>
                                                <p className="break-words text-xs text-muted-foreground">Last received: {formatTimestamp(webhook.last_received_at)}</p>
                                            </div>
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => setDeleteTarget(webhook)}
                                                disabled={busy}
                                            >
                                                {deleteWebhookMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Trash2 className="mr-2 h-4 w-4" />}
                                                Delete
                                            </Button>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <p className="text-sm text-muted-foreground">No active webhook registrations found.</p>
                        )}
                    </div>

                    <div className="rounded-xl border bg-muted/10 p-4">
                        <p className="text-sm font-medium">Recent webhook actions</p>
                        {recentActions.length ? (
                            <div className="mt-3 space-y-2">
                                {recentActions.map((action) => (
                                    <div key={action.id} className="rounded-lg border bg-background/70 p-3">
                                        <div className="flex min-w-0 items-center justify-between gap-2">
                                            <div className="min-w-0">
                                                <div className="text-sm font-medium">{action.label}</div>
                                                <p className="break-words text-xs text-muted-foreground">{action.message}</p>
                                            </div>
                                            <span className={`text-xs font-semibold ${action.status === "success" ? "text-emerald-600" : "text-destructive"}`}>
                                                {action.status}
                                            </span>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <p className="mt-3 text-sm text-muted-foreground">No recent changes.</p>
                        )}
                    </div>
                </CardContent>
            </Card>

            <ConfirmActionDialog
                open={Boolean(deleteTarget)}
                onOpenChange={(open) => {
                    if (!open) {
                        setDeleteTarget(null);
                    }
                }}
                title="Delete webhook"
                description={`Type ${deleteTarget?.webhook_id ?? "the webhook ID"} to permanently delete this subscription.`}
                confirmLabel="Delete webhook"
                confirmValue={deleteTarget?.webhook_id}
                confirmHint="This removes the webhook from both Inflow and the local registry."
                isPending={deleteWebhookMutation.isPending}
                onConfirm={async () => {
                    if (!deleteTarget) return;
                    await deleteWebhookMutation.mutateAsync(deleteTarget.webhook_id);
                    setDeleteTarget(null);
                }}
            />
        </>
    );
}
