import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { ordersApi } from "../api/orders";
import { shippingApi } from "../api/shipping";
import type { ShippingQueueOrder } from "../api/shipping";
import { Button } from "../components/ui/button";
import { invalidateOrderQueries } from "../queries/orders";
import { ShippingWorkflowStatus } from "../types/order";
import { extractApiErrorMessage } from "../utils/apiErrors";

export default function Shipping() {
    const navigate = useNavigate();
    const client = useQueryClient();
    const [cityKey, setCityKey] = useState("");
    const [locationId, setLocationId] = useState("");
    const [selected, setSelected] = useState<string[]>([]);
    const query = useQuery({ queryKey: ["shipping-queue"], queryFn: shippingApi.queue, refetchInterval: 10000 });
    const city = query.data?.cities.find(item => item.key === cityKey);
    const location = city?.locations.length === 1 ? city.locations[0] : city?.locations.find(item => item.contact_id === locationId);
    const available = location?.orders.filter(order => !order.shipment_id) ?? [];
    const selectedIds = available.filter(order => selected.includes(order.id)).map(order => order.id);
    const handoff = useMutation({
        mutationFn: (orderId: string) => ordersApi.updateShippingWorkflow(orderId, { status: ShippingWorkflowStatus.SHIPPED }),
        onSuccess: async (_data, orderId) => {
            await Promise.all([client.invalidateQueries({ queryKey: ["shipping-queue"] }), invalidateOrderQueries(client, orderId)]);
            toast.success("Carrier handoff recorded");
        },
        onError: (error: unknown) => toast.error(extractApiErrorMessage(error, "Could not update shipping status")),
    });
    const actions = (order: ShippingQueueOrder) => <div className="flex flex-wrap gap-2">
        <Link className="text-sm underline" to={`/orders/${order.id}`}>Order details</Link>
        {order.shipment_id && <Button size="sm" variant="outline" onClick={() => navigate(`/shipping/orders/${order.shipment_order_id ?? order.id}`)}>Open shipment · {order.shipment_status?.replace(/_/g, " ")}</Button>}
        <Button size="sm" variant="outline" disabled={handoff.isPending} onClick={() => handoff.mutate(order.id)}>Mark Shipped</Button>
    </div>;
    const pack = () => {
        if (!location || selectedIds.length === 0) return;
        const params = new URLSearchParams({ orders: selectedIds.join(","), contact: location.contact_id });
        navigate(`/shipping/orders/${selectedIds[0]}?${params}`);
    };
    return <div className="mx-auto max-w-6xl space-y-6 p-4 sm:p-6">
        <div className="space-y-2"><h1 className="text-2xl font-semibold">Shipment Queue</h1>
            <p className="text-sm text-muted-foreground">Orders that completed QA and were marked ready for shipping. Choose a city, then a saved location, and combine the orders you select.</p>
            <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => navigate("/shipping/products")}>Product shipping weights</Button><Button variant="outline" onClick={() => navigate("/shipping/contacts")}>Manage address book</Button></div>
        </div>
        {query.isPending && <p>Loading shipping queue…</p>}
        {query.isError && <p role="alert">Could not load the shipping queue. <Button onClick={() => void query.refetch()}>Retry</Button></p>}
        {query.data && <>
            <section className="rounded-xl border border-border bg-card p-5 space-y-4">
                <h2 className="text-lg font-semibold">1. Choose destination</h2>
                <label className="block text-sm">City<select aria-label="Destination city" className="mt-1 block w-full rounded-md border border-input bg-background p-3 sm:max-w-sm" value={cityKey} onChange={e => { setCityKey(e.target.value); setLocationId(""); setSelected([]); }}>
                    <option value="">Choose a city…</option>{query.data.cities.map(item => <option key={item.key} value={item.key}>{item.name} ({item.locations.reduce((sum, place) => sum + place.orders.length, 0)} orders)</option>)}
                </select></label>
                {city && city.locations.length > 1 && <label className="block text-sm">Location<select aria-label="Destination location" className="mt-1 block w-full rounded-md border border-input bg-background p-3 sm:max-w-sm" value={locationId} onChange={e => { setLocationId(e.target.value); setSelected([]); }}>
                    <option value="">Choose a location…</option>{city.locations.map(place => <option key={place.contact_id} value={place.contact_id}>{place.name} ({place.orders.length} orders)</option>)}
                </select></label>}
                {location && <div className="rounded-lg bg-muted/40 p-4"><strong>{location.name}</strong><p className="text-sm">{location.recipient.address1}{location.recipient.address2 ? `, ${location.recipient.address2}` : ""} · {location.recipient.city}, {location.recipient.state} {location.recipient.postal_code}</p></div>}
                {!query.data.cities.length && <p className="text-sm text-muted-foreground">No matched destinations are waiting. Review unmatched orders below, or import your shared FedEx address book.</p>}
            </section>
            {location && <section className="rounded-xl border border-border bg-card p-5 space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">2. Select orders for {location.name}</h2>
                    <Button variant="outline" disabled={!available.length} onClick={() => setSelected(selectedIds.length === available.length ? [] : available.slice(0, 50).map(order => order.id))}>{selectedIds.length === available.length && available.length ? "Clear selection" : "Select available orders"}</Button></div>
                <p className="text-sm text-muted-foreground">Select up to 50 orders for one shipment. Orders at other locations stay separate, even in the same city.</p>
                {location.orders.map(order => <div key={order.id} className="rounded-lg border border-border p-4 space-y-3">
                    <label className="flex items-start gap-3"><input className="mt-1" type="checkbox" aria-label={`Select ${order.order_number}`} checked={selectedIds.includes(order.id)} disabled={!!order.shipment_id || (selectedIds.length >= 50 && !selectedIds.includes(order.id))} onChange={e => setSelected(current => e.target.checked ? [...current, order.id] : current.filter(id => id !== order.id))} />
                        <span><strong>{order.order_number}</strong><span className="ml-2 text-sm text-muted-foreground">{order.recipient_name}</span>
                            <span className="mt-1 block text-sm">{order.products.map(product => `${product.quantity} × ${product.name}`).join(" · ") || "No unshipped physical products"}</span></span></label>
                    {actions(order)}
                </div>)}
                <div className="flex flex-wrap items-center gap-3"><Button disabled={!selectedIds.length} onClick={pack}>Pack {selectedIds.length || "selected"} order{selectedIds.length === 1 ? "" : "s"} together</Button><span className="text-sm text-muted-foreground">One saved destination · combined products and boxes</span></div>
            </section>}
            {!!query.data.unmatched.length && <section className="rounded-xl border border-amber-500/40 p-5 space-y-4"><h2 className="text-lg font-semibold">Address needs review ({query.data.unmatched.length})</h2>
                <p className="text-sm text-muted-foreground">These addresses do not identify one saved destination clearly. Review each order and the shared address book before combining. An individual order can use a saved contact or an admin’s one-off override.</p>
                {query.data.unmatched.map(order => <div key={order.id} className="rounded-lg border border-border p-4 space-y-2"><p><strong>{order.order_number}</strong> · {order.city} · {order.recipient_name}</p>
                    <Button variant="outline" size="sm" onClick={() => navigate(`/shipping/orders/${order.shipment_order_id ?? order.id}`)}>Review individual shipment</Button>{actions(order)}</div>)}
            </section>}
            <p className="text-xs text-muted-foreground">Mark Shipped records the existing carrier-handoff workflow. Creating a sandbox label leaves that status unchanged.</p>
        </>}
    </div>;
}
