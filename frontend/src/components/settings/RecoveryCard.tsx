import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, RefreshCw, ClipboardCopy } from "lucide-react";
import { toast } from "sonner";

import { settingsApi } from "../../api/settings";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Input } from "../ui/input";
import { ConfirmActionDialog } from "./ConfirmActionDialog";
import { copyToClipboard } from "./utils";
import { extractApiErrorMessage } from "../../utils/apiErrors";

interface RecentRecoveryAction {
    id: string;
    label: string;
    message: string;
    status: "success" | "error";
}

export function RecoveryCard() {
    const [picklistOrderId, setPicklistOrderId] = useState("");
    const [confirmOrderId, setConfirmOrderId] = useState<string | null>(null);
    const [recentActions, setRecentActions] = useState<RecentRecoveryAction[]>([]);

    const retryPicklistMutation = useMutation({
        mutationFn: async (orderId: string) => settingsApi.retryPicklistPrint(orderId),
    });

    const pushAction = (label: string, status: "success" | "error", message: string) => {
        setRecentActions((current) => [
            { id: `${Date.now()}-${Math.random()}`, label, status, message },
            ...current,
        ].slice(0, 4));
    };

    const handleRetryPicklist = async () => {
        const normalized = picklistOrderId.trim();
        if (!normalized) {
            toast.error("Enter an order id to requeue");
            return;
        }
        setConfirmOrderId(normalized);
    };

    return (
        <>
            <Card className="border-border/70 bg-card/80">
                <CardHeader>
                    <CardTitle className="text-base">Recovery</CardTitle>
                    <CardDescription>Queue a picklist reprint without leaving the operations page.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="space-y-2">
                        <label className="text-xs font-medium text-muted-foreground">Order id</label>
                        <Input
                            placeholder="order id or inflow order id"
                            value={picklistOrderId}
                            onChange={(event) => setPicklistOrderId(event.target.value)}
                            disabled={retryPicklistMutation.isPending}
                        />
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <Button onClick={() => void handleRetryPicklist()} disabled={retryPicklistMutation.isPending}>
                            {retryPicklistMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                            Requeue picklist
                        </Button>
                        <Button
                            variant="outline"
                            onClick={() => void copyToClipboard(picklistOrderId.trim(), "Order id copied")}
                            disabled={!picklistOrderId.trim()}
                        >
                            <ClipboardCopy className="mr-2 h-4 w-4" />
                            Copy order id
                        </Button>
                    </div>

                    <div className="rounded-xl border bg-muted/10 p-4">
                        <p className="text-sm font-medium">Recent recovery actions</p>
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
                            <p className="mt-3 text-sm text-muted-foreground">No recovery actions yet.</p>
                        )}
                    </div>
                </CardContent>
            </Card>

            <ConfirmActionDialog
                open={Boolean(confirmOrderId)}
                onOpenChange={(open) => {
                    if (!open) setConfirmOrderId(null);
                }}
                title="Confirm picklist requeue"
                description="This will enqueue a picklist reprint for the selected order."
                confirmLabel="Requeue picklist"
                confirmValue={confirmOrderId ?? undefined}
                confirmHint="Type the order id to continue."
                isPending={retryPicklistMutation.isPending}
                onConfirm={async () => {
                    if (!confirmOrderId) return;
                    try {
                        const result = await retryPicklistMutation.mutateAsync(confirmOrderId);
                        toast.success("Picklist reprint queued", {
                            description: result.job?.order_id ? `Order ${result.job.order_id}` : undefined,
                        });
                        pushAction("Requeue", "success", result.job?.order_id ? `Order ${result.job.order_id}` : "Queued");
                        setPicklistOrderId("");
                        setConfirmOrderId(null);
                    } catch (error: unknown) {
                        const message = extractApiErrorMessage(error, "Please try again.");
                        toast.error("Failed to queue picklist reprint", { description: message });
                        pushAction("Requeue", "error", message);
                    }
                }}
            />
        </>
    );
}
