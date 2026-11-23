import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { ordersApi } from "../api/orders";
import { shippingApi } from "../api/shipping";
import type { ShippingQueueOrder } from "../api/shipping";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { invalidateOrderQueries } from "../queries/orders";
import { ShippingWorkflowStatus } from "../types/order";
import { extractApiErrorMessage } from "../utils/apiErrors";

const selectable = (order: ShippingQueueOrder) => !order.shipment_id && order.products.some(product => product.quantity > 0);

export default function Shipping() {
    const navigate = useNavigate();
    const client = useQueryClient();
    const [search, setSearch] = useState("");
    const [selected, setSelected] = useState<string[]>([]);
    const query = useQuery({ queryKey: ["shipping-queue"], queryFn: shippingApi.queue, refetchInterval: 10000 });
    const orders = query.data?.orders ?? [];
    const available = orders.filter(selectable);
    const selectedOrders = available.filter(order => selected.includes(order.id));
    const selectedIds = selectedOrders.map(order => order.id);
    const visible = orders.filter(order => order.order_number.toLowerCase().includes(search.trim().toLowerCase()));
    const visibleAvailable = visible.filter(selectable);
    const allShownSelected = visibleAvailable.length > 0 && visibleAvailable.every(order => selectedIds.includes(order.id));
    useEffect(() => {
        if (!query.data) return;
        const eligibleIds = new Set(query.data.orders.filter(selectable).map(order => order.id));
        setSelected(current => {
            const next = current.filter(id => eligibleIds.has(id));
            return next.length === current.length ? current : next;
        });
    }, [query.data]);
    const handoff = useMutation({
        mutationFn: (orderId: string) => ordersApi.updateShippingWorkflow(orderId, { status: ShippingWorkflowStatus.SHIPPED }),
        onSuccess: async (_data, orderId) => {
            await Promise.all([client.invalidateQueries({ queryKey: ["shipping-queue"] }), invalidateOrderQueries(client, orderId)]);
            toast.success("Carrier handoff recorded");
        },
        onError: (error: unknown) => toast.error(extractApiErrorMessage(error, "Could not update shipping status")),
    });
    const toggleShown = () => {
        const shown = new Set(visibleAvailable.map(order => order.id));
        setSelected(current => allShownSelected ? current.filter(id => !shown.has(id)) : [...new Set([...selectedIds, ...shown])].slice(0, 50));
    };
    const pack = () => {
        if (!selectedIds.length) return;
        const params = new URLSearchParams({ orders: selectedIds.join(",") });
        navigate(`/shipping/orders/${selectedIds[0]}?${params}`);
    };
    return <div className="mx-auto max-w-6xl space-y-6 p-4 sm:p-6">
        <div className="space-y-2"><h1 className="text-2xl font-semibold">Shipment Queue</h1>
            <p className="text-sm text-muted-foreground">Select QA-ready orders first. Pack their products together, then choose the saved FedEx contact for that shipment.</p>
            <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => navigate("/shipping/products")}>Product shipping weights</Button><Button variant="outline" onClick={() => navigate("/shipping/contacts")}>Manage address book</Button></div>
        </div>
        {query.isPending && <p>Loading shipping queue…</p>}
        {query.isError && <p role="alert">Could not load the shipping queue. <Button onClick={() => void query.refetch()}>Retry</Button></p>}
        {query.data && <>
            <section className="rounded-xl border border-border bg-card p-5 space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">1. Select orders ({orders.length})</h2>
                    <span className="text-sm text-muted-foreground">{selectedIds.length} selected · up to 50 per shipment</span></div>
                <p className="text-sm text-muted-foreground">All ready orders are shown here. You will choose one shared shipping recipient after selecting the orders.</p>
                <div className="flex flex-wrap items-center gap-3"><Input aria-label="Search order number" placeholder="Find an order number…" className="max-w-sm" value={search} onChange={e => setSearch(e.target.value)} />
                    <Button variant="outline" disabled={!visibleAvailable.length} onClick={toggleShown}>{allShownSelected ? "Clear shown orders" : "Select shown orders"}</Button>
                    <Button variant="outline" disabled={!selectedIds.length} onClick={() => setSelected([])}>Clear selection</Button></div>
                {!orders.length && <p className="text-sm text-muted-foreground">No QA-ready orders are waiting for shipping.</p>}
                {!!orders.length && !visible.length && <p className="text-sm text-muted-foreground">No order numbers match your search.</p>}
                {visible.map(order => <div key={order.id} className="rounded-lg border border-border p-4 space-y-3">
                    <label className="flex items-start gap-3"><input className="mt-1" type="checkbox" aria-label={`Select ${order.order_number}`} checked={selectedIds.includes(order.id)} disabled={!selectable(order) || (selectedIds.length >= 50 && !selectedIds.includes(order.id))} onChange={e => setSelected(current => e.target.checked ? [...new Set([...current, order.id])] : current.filter(id => id !== order.id))} />
                        <span><strong>{order.order_number}</strong><span className="ml-2 text-sm text-muted-foreground">{order.recipient_name || "Recipient not listed"}{order.city ? ` · Order city: ${order.city}` : ""}</span>
                            <span className="mt-1 block text-sm">{order.products.map(product => `${product.quantity} × ${product.name}`).join(" · ") || "No unshipped physical products"}</span>
                            {order.shipment_id && <span className="mt-1 block text-xs text-muted-foreground">Already included in a saved shipment. Open it to continue.</span>}</span></label>
                    <div className="flex flex-wrap items-center gap-2"><Link className="text-sm underline" to={`/orders/${order.id}`}>Order details</Link>
                        {order.shipment_id && <Button size="sm" variant="outline" onClick={() => navigate(`/shipping/orders/${order.shipment_order_id ?? order.id}`)}>Open shipment · {order.shipment_status?.replace(/_/g, " ")}</Button>}
                        <Button size="sm" variant="outline" disabled={handoff.isPending} onClick={() => handoff.mutate(order.id)}>Mark Shipped</Button>
                    </div>
                </div>)}
            </section>
            <section className="sticky bottom-0 rounded-xl border border-border bg-background p-5 shadow-sm space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3"><p className="font-medium">{selectedIds.length} order{selectedIds.length === 1 ? "" : "s"} selected</p>
                    <Button disabled={!selectedIds.length} onClick={pack}>{selectedIds.length ? `Pack ${selectedIds.length} order${selectedIds.length === 1 ? "" : "s together"}` : "Pack selected orders"}</Button></div>
                {!!selectedIds.length && <p className="break-words text-sm text-muted-foreground">{selectedOrders.map(order => order.order_number).join(" · ")}</p>}
                <p className="text-sm text-muted-foreground">The selected orders will share one shipment and the recipient you choose next.</p>
            </section>
            <p className="text-xs text-muted-foreground">Mark Shipped records the existing carrier-handoff workflow. Creating a sandbox label leaves that status unchanged.</p>
        </>}
    </div>;
}
