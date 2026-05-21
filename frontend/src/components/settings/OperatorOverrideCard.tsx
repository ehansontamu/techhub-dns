import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, RefreshCw, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { ordersApi } from "../../api/orders";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Input } from "../ui/input";
import { ConfirmActionDialog } from "./ConfirmActionDialog";
import { extractApiErrorMessage } from "../../utils/apiErrors";
import { useAuth } from "../../contexts/AuthContext";
import { OrderStatus } from "../../types/order";

const REOPEN_TARGETS = [OrderStatus.PICKED, OrderStatus.QA, OrderStatus.PRE_DELIVERY] as const;

const makeBypassSignatureData = () => ({
    signature_image:
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Yl8X7kAAAAASUVORK5CYII=",
    placements: [{ page_number: 1, x: 48, y: 48, width: 1, height: 1 }],
});

function formatStatusLabel(status: OrderStatus) {
    return status.replace("-", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function OperatorOverrideCard() {
    const { user } = useAuth();
    const currentUserLabel = user?.email ?? "system";

    const [signingOrderId, setSigningOrderId] = useState("");
    const [reopenOrderId, setReopenOrderId] = useState("");
    const [reopenTarget, setReopenTarget] = useState<OrderStatus>(OrderStatus.PICKED);
    const [reopenReason, setReopenReason] = useState("");
    const [ownershipOrderId, setOwnershipOrderId] = useState("");
    const [ownershipDeliverer, setOwnershipDeliverer] = useState("");

    const [pendingAction, setPendingAction] = useState<"sign" | "reopen" | "ownership" | null>(null);

    const bypassSigningMutation = useMutation({
        mutationFn: async ({ orderId }: { orderId: string }) => ordersApi.signOrder(orderId, makeBypassSignatureData()),
    });
    const reopenOrderMutation = useMutation({
        mutationFn: async ({ orderId, target, reason }: { orderId: string; target: OrderStatus; reason?: string }) =>
            ordersApi.rollbackOrderStatus(orderId, { status: target, reason }, currentUserLabel),
    });
    const overrideOwnershipMutation = useMutation({
        mutationFn: async ({ orderId, owner }: { orderId: string; owner: string }) =>
            ordersApi.updateOrder(orderId, { assigned_deliverer: owner }),
    });

    const busy = bypassSigningMutation.isPending || reopenOrderMutation.isPending || overrideOwnershipMutation.isPending;

    return (
        <>
            <Card className="border-border/70 bg-card/80">
                <CardHeader>
                    <CardTitle className="text-base">Advanced recovery</CardTitle>
                    <CardDescription>High-risk actions are isolated here and require explicit confirmation.</CardDescription>
                </CardHeader>
                <CardContent className="grid gap-4 xl:grid-cols-3">
                    <div className="rounded-xl border bg-muted/20 p-4">
                        <div className="space-y-1 pb-3">
                            <h3 className="text-sm font-semibold">Bypass signing</h3>
                            <p className="break-words text-sm text-muted-foreground">Force delivery by signing with a transparent placeholder.</p>
                        </div>
                        <div className="space-y-3">
                            <Input
                                placeholder="order id"
                                value={signingOrderId}
                                onChange={(event) => setSigningOrderId(event.target.value)}
                                disabled={busy}
                            />
                            <Button variant="outline" className="w-full" onClick={() => setPendingAction("sign")} disabled={busy || !signingOrderId.trim()}>
                                {bypassSigningMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ShieldAlert className="mr-2 h-4 w-4" />}
                                Bypass signing
                            </Button>
                        </div>
                    </div>

                    <div className="rounded-xl border bg-muted/20 p-4">
                        <div className="space-y-1 pb-3">
                            <h3 className="text-sm font-semibold">Force reopen</h3>
                            <p className="break-words text-sm text-muted-foreground">Rollback an order to an earlier workflow step.</p>
                        </div>
                        <div className="space-y-3">
                            <Input
                                placeholder="order id"
                                value={reopenOrderId}
                                onChange={(event) => setReopenOrderId(event.target.value)}
                                disabled={busy}
                            />
                            <select
                                value={reopenTarget}
                                onChange={(event) => setReopenTarget(event.target.value as OrderStatus)}
                                disabled={busy}
                                className="h-11 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                                {REOPEN_TARGETS.map((status) => (
                                    <option key={status} value={status}>
                                        {formatStatusLabel(status)}
                                    </option>
                                ))}
                            </select>
                            <Input
                                placeholder="reason (optional)"
                                value={reopenReason}
                                onChange={(event) => setReopenReason(event.target.value)}
                                disabled={busy}
                            />
                            <Button variant="outline" className="w-full" onClick={() => setPendingAction("reopen")} disabled={busy || !reopenOrderId.trim()}>
                                {reopenOrderMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                                Reopen order
                            </Button>
                        </div>
                    </div>

                    <div className="rounded-xl border bg-muted/20 p-4">
                        <div className="space-y-1 pb-3">
                            <h3 className="text-sm font-semibold">Override ownership</h3>
                            <p className="break-words text-sm text-muted-foreground">Reassign the deliverer for an order.</p>
                        </div>
                        <div className="space-y-3">
                            <Input
                                placeholder="order id"
                                value={ownershipOrderId}
                                onChange={(event) => setOwnershipOrderId(event.target.value)}
                                disabled={busy}
                            />
                            <Input
                                placeholder="new deliverer"
                                value={ownershipDeliverer}
                                onChange={(event) => setOwnershipDeliverer(event.target.value)}
                                disabled={busy}
                            />
                            <Button variant="outline" className="w-full" onClick={() => setPendingAction("ownership")} disabled={busy || !ownershipOrderId.trim() || !ownershipDeliverer.trim()}>
                                {overrideOwnershipMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                Update ownership
                            </Button>
                        </div>
                    </div>
                </CardContent>
            </Card>

            <ConfirmActionDialog
                open={pendingAction === "sign"}
                onOpenChange={(open) => {
                    if (!open) setPendingAction(null);
                }}
                title="Confirm signing bypass"
                description="This will sign the order with a transparent placeholder and move it forward."
                confirmLabel="Bypass signing"
                confirmValue={signingOrderId.trim()}
                confirmHint="Type the order id to continue."
                isPending={bypassSigningMutation.isPending}
                onConfirm={async () => {
                    const orderId = signingOrderId.trim();
                    if (!orderId) return;
                    try {
                        const result = await bypassSigningMutation.mutateAsync({ orderId });
                        toast.success("Signing bypass completed", { description: result.message || `Order ${orderId} moved to delivered` });
                        setSigningOrderId("");
                        setPendingAction(null);
                    } catch (error: unknown) {
                        toast.error("Failed to bypass signing", { description: extractApiErrorMessage(error, "Please try again.") });
                    }
                }}
            />

            <ConfirmActionDialog
                open={pendingAction === "reopen"}
                onOpenChange={(open) => {
                    if (!open) setPendingAction(null);
                }}
                title="Confirm order reopen"
                description={`Rollback the order to ${formatStatusLabel(reopenTarget)}.`}
                confirmLabel="Reopen order"
                confirmValue={reopenOrderId.trim()}
                confirmHint="Type the order id to continue."
                isPending={reopenOrderMutation.isPending}
                onConfirm={async () => {
                    const orderId = reopenOrderId.trim();
                    if (!orderId) return;
                    try {
                        const result = await reopenOrderMutation.mutateAsync({
                            orderId,
                            target: reopenTarget,
                            reason: reopenReason.trim() || undefined,
                        });
                        toast.success("Order reopened", { description: `${result.inflow_order_id} → ${result.status}` });
                        setReopenOrderId("");
                        setReopenReason("");
                        setPendingAction(null);
                    } catch (error: unknown) {
                        toast.error("Failed to reopen order", { description: extractApiErrorMessage(error, "Please try again.") });
                    }
                }}
            />

            <ConfirmActionDialog
                open={pendingAction === "ownership"}
                onOpenChange={(open) => {
                    if (!open) setPendingAction(null);
                }}
                title="Confirm ownership override"
                description="This changes the assigned deliverer on the order."
                confirmLabel="Update ownership"
                confirmValue={ownershipOrderId.trim()}
                confirmHint="Type the order id to continue."
                isPending={overrideOwnershipMutation.isPending}
                onConfirm={async () => {
                    const orderId = ownershipOrderId.trim();
                    const owner = ownershipDeliverer.trim();
                    if (!orderId || !owner) return;
                    try {
                        const result = await overrideOwnershipMutation.mutateAsync({ orderId, owner });
                        toast.success("Queue ownership updated", {
                            description: `${result.inflow_order_id} → ${result.assigned_deliverer || owner}`,
                        });
                        setOwnershipOrderId("");
                        setOwnershipDeliverer("");
                        setPendingAction(null);
                    } catch (error: unknown) {
                        toast.error("Failed to override queue ownership", { description: extractApiErrorMessage(error, "Please try again.") });
                    }
                }}
            />
        </>
    );
}
