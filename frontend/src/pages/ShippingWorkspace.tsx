import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { shippingApi } from "../api/shipping";
import type { PackingPlan, Recipient, ShippingBox, ShippingContact, ShippingWorkspace as Workspace } from "../api/shipping";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogFooter } from "../components/ui/dialog";
import { extractApiErrorMessage } from "../utils/apiErrors";
import { useAuth } from "../contexts/AuthContext";

type BoxSettings = Omit<ShippingBox, "items"> & { key: string };

const emptyBox = (key: string, name: string, own = false): BoxSettings => ({
    key, name, use_as_box: own,
});

function PackingEditor({ data, orderId, refresh }: { data: Workspace; orderId: string; refresh: () => void }) {
    const { isAdmin } = useAuth();
    const shipment = data.shipment;
    const [recipient, setRecipient] = useState<Recipient>(shipment?.draft.recipient ?? data.recipient);
    const units = data.items.flatMap(item => Array.from({ length: item.quantity }, (_, index) => ({ ...item, unit: index + 1, key: `${item.order_id}:${item.product_id}:${index}` })));
    const [boxes, setBoxes] = useState<BoxSettings[]>(() => shipment?.draft.boxes.map((box, index) => ({ ...box, key: `saved-${index}` })) ?? [emptyBox("box-1", "Box 1")]);
    const [assignments, setAssignments] = useState<Record<string, string>>(() => {
        const result: Record<string, string> = {};
        const consumed: Record<string, number> = {};
        shipment?.draft.boxes.forEach((box, index) => box.items.forEach(item => {
            const itemKey = `${item.order_id ?? data.order_ids[0]}:${item.product_id}`;
            const start = consumed[itemKey] ?? 0;
            for (let i = start; i < start + item.quantity; i++) result[`${itemKey}:${i}`] = `saved-${index}`;
            consumed[itemKey] = start + item.quantity;
        }));
        return result;
    });
    const [dirty, setDirty] = useState(false);
    const [confirm, setConfirm] = useState(false);
    const [contacts, setContacts] = useState<ShippingContact[]>([]);
    const [selectedContact, setSelectedContact] = useState(shipment?.draft.contact_id ?? data.contact_id ?? "");
    const [manualOverride, setManualOverride] = useState(shipment?.draft.manual_address_override ?? false);
    const [contactSearch, setContactSearch] = useState("");
    const editable = !shipment || ["draft", "rejected"].includes(shipment.status);
    const activeBoxes = boxes.filter(box => Object.values(assignments).includes(box.key));
    const missingWeights = data.items.some(item => item.weight_lb === null);
    const unassigned = units.filter(unit => !assignments[unit.key]).length;
    const buildPlan = (): PackingPlan => ({
        order_ids: data.order_ids, recipient, service_type: shipment?.draft.service_type ?? "FEDEX_GROUND",
        contact_id: selectedContact || null, manual_address_override: manualOverride,
        boxes: activeBoxes.map(box => {
            const counts = new Map<string, { order_id: string; product_id: string; quantity: number }>();
            units.filter(unit => assignments[unit.key] === box.key).forEach(unit => {
                const key = JSON.stringify([unit.order_id, unit.product_id]);
                const item = counts.get(key) ?? { order_id: unit.order_id, product_id: unit.product_id, quantity: 0 };
                item.quantity++; counts.set(key, item);
            });
            return { name: box.name, use_as_box: box.use_as_box, items: [...counts.values()] };
        }),
    });
    const save = useMutation({
        mutationFn: () => shippingApi.save(orderId, buildPlan(), shipment?.revision ?? 0),
        onSuccess: () => { toast.success("Packing saved. Get the cheapest FedEx price."); setDirty(false); refresh(); },
        onError: (error: unknown) => toast.error(extractApiErrorMessage(error, "Packing could not be saved")),
    });
    const create = useMutation({
        mutationFn: (confirmedHighCost: boolean) => shippingApi.create(orderId, shipment?.revision ?? 0, confirmedHighCost),
        onSuccess: result => { setConfirm(false); refresh(); if (result.status === "created") toast.success("FedEx sandbox labels saved and queued for the Brother printer"); else toast.info(result.last_error ?? "Shipment is processing. Refresh for its current status."); },
        onError: (error: unknown) => { setConfirm(false); refresh(); toast.error(extractApiErrorMessage(error, "Could not confirm shipment creation. Refresh its status before continuing.")); },
    });
    const reprint = useMutation({
        mutationFn: (index: number) => shippingApi.print(orderId, index),
        onSuccess: () => { toast.success("Saved label queued for printing"); refresh(); },
        onError: (error: unknown) => toast.error(extractApiErrorMessage(error, "Could not queue the label")),
    });
    const rates = useMutation({
        mutationFn: () => shippingApi.rates(orderId, shipment?.revision ?? 0),
        onSuccess: () => { toast.success("Prices loaded; the cheapest eligible service is selected"); refresh(); },
        onError: (error: unknown) => toast.error(extractApiErrorMessage(error, "Could not retrieve FedEx prices")),
    });
    const loadContacts = useMutation({
        mutationFn: () => shippingApi.contacts(orderId, contactSearch),
        onSuccess: loaded => {
            setContacts(loaded);
            const current = loaded.find(contact => contact.id === selectedContact);
            if (current && (Object.keys(recipient) as Array<keyof Recipient>).some(key => current.recipient[key] !== recipient[key])) {
                setSelectedContact(""); setDirty(true);
                toast.info("This saved contact changed. Select it again to use the updated details.");
            }
        },
        onError: (error: unknown) => toast.error(extractApiErrorMessage(error, "Could not load imported FedEx contacts")),
    });
    const busy = save.isPending || create.isPending || rates.isPending;
    const recipientAllowed = manualOverride ? isAdmin : !!selectedContact;
    const quote = shipment?.draft.rate_quote;
    const selectedRate = quote?.options.find(option => option.service_type === shipment?.draft.service_type);
    const price = (amount: string, currency: string) => new Intl.NumberFormat("en-US", { style: "currency", currency }).format(Number(amount));
    const highCost = !!selectedRate && Number(selectedRate.amount) > 100;
    const quoteExpired = !!quote && Date.parse(quote.expires_at) <= Date.now();
    const changeBox = (key: string, patch: Partial<BoxSettings>) => { setBoxes(current => current.map(box => box.key === key ? { ...box, ...patch } : box)); setDirty(true); };
    const assign = (unitKey: string, value: string) => {
        if (value === "own") {
            const key = `own-${unitKey}`;
            if (!boxes.some(box => box.key === key)) setBoxes(current => [...current, emptyBox(key, `Product box ${boxes.filter(box => box.use_as_box).length + 1}`, true)]);
            value = key;
        }
        setAssignments(current => ({ ...current, [unitKey]: value })); setDirty(true);
    };
    return <div className="space-y-6">
        <section className="rounded-xl border border-border p-4 text-sm">
            <strong>{data.orders.length} selected order(s)</strong>
            <p>{data.orders.map(order => order.order_number).join(" · ")}</p>
            <p className="mt-1 text-muted-foreground">Products can share boxes. Each unit remains linked to its original order.</p>
        </section>
        <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
            <strong>FedEx sandbox</strong> — test labels are not valid for real shipping. Creating labels does not mark an order delivered or schedule a pickup.
            {!data.config.configured && <p className="mt-2">Setup needed: {data.config.missing.join(", ")}. You can save packing while setup is completed.</p>}
        </div>
        {!data.eligible && <p role="alert" className="text-sm">This order must complete shipping QA and be in the Shipping queue before a shipment can be created.</p>}
        {shipment?.last_error && <div role="alert" className="rounded-xl border border-destructive/50 p-4 text-sm">{shipment.last_error}</div>}
        {shipment && !editable && <section className="rounded-xl border border-border p-5 space-y-3">
            <h2 className="text-lg font-semibold">Shipment: {shipment.status.replace(/_/g, " ")}</h2>
            {shipment.status === "needs_review" && <p className="break-all text-xs text-muted-foreground">Give an admin this shipment reference: {shipment.id}</p>}
            {shipment.master_tracking_number && <p>Master tracking: <strong>{shipment.master_tracking_number}</strong></p>}
            {shipment.status === "creating" && <p className="text-sm">FedEx creation was started. The app will not submit it again. If it stays here after a server interruption, ask an admin to reconcile transaction {shipment.id} with FedEx.</p>}
            {shipment.status === "processing_response" && <Button disabled={busy} onClick={() => create.mutate(false)}>Finish processing saved FedEx response</Button>}
            {shipment.packages.map(box => <div key={box.index} className="rounded-lg border border-border p-4 space-y-2">
                <p className="break-words font-medium">{box.name} · {box.shipping_weight_lb} lb · {box.tracking_number}</p>
                <p className="text-xs text-muted-foreground">{box.print_jobs.map(job => `Print: ${job.status}${job.last_error ? ` — ${job.last_error}` : ""}`).join("; ") || "Not queued"}</p>
                <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => void shippingApi.download(orderId, box.index).catch(error => toast.error(extractApiErrorMessage(error, "Download failed")))}>Download label PDF</Button>
                    <Button disabled={reprint.isPending || box.print_jobs.some(job => ["pending", "claimed"].includes(job.status))} onClick={() => reprint.mutate(box.index)}>Reprint saved label</Button></div>
            </div>)}
            <p className="text-xs text-muted-foreground">A completed print job means the workstation submitted the label to the printer. Check the physical output before applying it. Sandbox testing leaves dock and carrier-handoff records unchanged.</p>
        </section>}
        <fieldset disabled={!editable || busy || !data.eligible} className="min-w-0 space-y-6 disabled:opacity-70">
            <section className="rounded-xl border border-border p-5 space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">1. Assign products to packages</h2>
                    <Link to="/shipping/products" className="text-sm underline">Product weights</Link></div>
                <p className="text-sm text-muted-foreground">Choose a numbered box for each unit, or “Use as box” for a monitor or other product shipping in its own carton.</p>
                {missingWeights && <p role="alert" className="text-sm text-amber-600">Some products need saved weights before shipment creation.</p>}
                <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr><th className="p-2">Product / unit</th><th className="p-2">Weight (lb)</th><th className="p-2">Package</th></tr></thead><tbody>
                    {units.map(unit => <tr key={unit.key} className="border-t border-border"><td className="p-2"><span className="block text-xs text-muted-foreground">{unit.order_number}</span>{unit.name} <span className="text-muted-foreground">({unit.unit} of {unit.quantity})</span></td><td className="p-2">{unit.weight_lb ?? "Missing"}</td>
                        <td className="p-2"><select aria-label={`Package for ${unit.order_number} ${unit.name} unit ${unit.unit}`} className="rounded-md border border-input bg-background p-2" value={assignments[unit.key] ?? ""} onChange={e => assign(unit.key, e.target.value)}>
                            <option value="">Choose package…</option>{boxes.filter(box => !box.use_as_box || box.key === assignments[unit.key]).map(box => <option key={box.key} value={box.key}>{box.use_as_box ? "Use as box — " : ""}{box.name}</option>)}<option value="own">Use as box (one product unit)</option>
                        </select></td></tr>)}
                </tbody></table></div>
                <Button variant="outline" onClick={() => { const next = boxes.filter(box => !box.use_as_box).length + 1; setBoxes(current => [...current, emptyBox(`box-${Date.now()}`, `Box ${next}`)]); setDirty(true); }}>Add another box</Button>
                <p className="text-xs text-muted-foreground">{unassigned} unit(s) unassigned · {activeBoxes.length} package(s)</p>
            </section>
            <section className="rounded-xl border border-border p-5 space-y-4"><h2 className="text-lg font-semibold">2. Calculated package weights</h2>
                <p className="text-sm text-muted-foreground">Each package uses the sum of its products’ saved weights. No extra packing weight is added.</p>
                {activeBoxes.length === 0 && <p className="text-sm">Assign products above to configure their boxes.</p>}
                {activeBoxes.map(box => {
                    const productWeight = units.filter(unit => assignments[unit.key] === box.key).reduce((sum, unit) => sum + (unit.weight_lb ?? 0), 0);
                    return <div key={box.key} className="rounded-lg bg-muted/30 p-4 space-y-3">
                        <label className="block text-sm">Package name<Input maxLength={80} value={box.name} onChange={e => changeBox(box.key, { name: e.target.value })} className="mt-1 max-w-sm" /></label>
                        {box.use_as_box && <p className="text-xs">Uses the product’s existing carton.</p>}
                        <p className="text-sm"><strong>FedEx weight: {productWeight.toFixed(3)} lb</strong></p>
                    </div>;
                })}
            </section>
            <section className="rounded-xl border border-border p-5 space-y-4"><h2 className="text-lg font-semibold">3. Confirm recipient</h2>
                <div className="rounded-lg bg-muted/30 p-4 space-y-3">
                    <p className="text-sm">Select from your saved FedEx contacts. This shared address book stays in the database between shipments.</p>
                    <Link to="/shipping/contacts" className="text-sm underline">Manage address book</Link>
                    <Input aria-label="Search FedEx contacts" placeholder="Search name, company, or address…" value={contactSearch} onChange={e => setContactSearch(e.target.value)} />
                    <Button variant="outline" disabled={loadContacts.isPending} onClick={() => loadContacts.mutate()}>{loadContacts.isPending ? "Loading contacts…" : "Search FedEx address book"}</Button>
                    {loadContacts.isSuccess && contacts.length === 0 && <p className="text-sm">No matching contacts. Change the search or ask an admin to update the shared address book.</p>}
                    {contacts.length > 0 && <label className="block text-sm">Saved recipient<select aria-label="Saved recipient" className="mt-1 block w-full rounded-md border border-input bg-background p-2" value={selectedContact} onChange={e => {
                        const contact = contacts.find(item => item.id === e.target.value);
                        if (contact) { setRecipient({ ...contact.recipient }); setSelectedContact(contact.id); setManualOverride(false); setDirty(true); }
                    }}><option value="">Choose a saved address…</option>{contacts.map(contact => <option key={contact.id} value={contact.id}>{contact.label} · {contact.recipient.address1}, {contact.recipient.city}</option>)}</select></label>}
                </div>
                <p className="text-sm text-muted-foreground">Shipping must use a saved contact. Recipient details are read-only unless an admin enables the one-off override.</p>
                {isAdmin && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={manualOverride} onChange={e => { setManualOverride(e.target.checked); setSelectedContact(""); setDirty(true); }} />One-off address override (admin only)</label>}
                {manualOverride && <p className="text-sm text-amber-700">One-off address: this will not be added to the shared address book. An admin must complete this shipment.</p>}
                {recipient.country !== "US" && <p role="alert" className="text-sm text-amber-600">Destination country: {recipient.country}. International shipments require a separate workflow and cannot be created here.</p>}
                <div className="grid gap-3 sm:grid-cols-2">{([
                    ["name", "Recipient name"], ["company", "Company (optional)"], ["phone", "Phone"], ["address1", "Street address"],
                    ["address2", "Suite / room (optional)"], ["city", "City"], ["state", "State (two letters)"], ["postal_code", "ZIP code"],
                ] as const).map(([field, label]) => <label key={field} className="text-sm">{label}<Input readOnly={!isAdmin || !manualOverride} value={recipient[field]} onChange={e => { setRecipient(current => ({ ...current, [field]: field === "state" ? e.target.value.toUpperCase() : e.target.value })); setDirty(true); }} /></label>)}</div>
                <label className="flex gap-2 text-sm"><input type="checkbox" disabled={!isAdmin || !manualOverride} checked={recipient.residential} onChange={e => { setRecipient(current => ({ ...current, residential: e.target.checked })); setDirty(true); }} />Residential address</label>
            </section>
            <Button disabled={busy || !recipientAllowed || missingWeights || unassigned > 0 || activeBoxes.length === 0} onClick={() => save.mutate()}>{save.isPending ? "Saving…" : "Save packing & review totals"}</Button>
        </fieldset>
        {editable && shipment && !dirty && <section className="rounded-xl border border-border p-5 space-y-3">
            <h2 className="text-lg font-semibold">Saved shipment review</h2>
            {shipment.draft.computed_boxes?.map((box, index) => <p key={index} className="text-sm">{box.name}: {box.items.reduce((sum, item) => sum + item.quantity, 0)} unit(s), {box.shipping_weight_lb} lb</p>)}
            <p className="text-sm">{shipment.draft.recipient.name} · {shipment.draft.recipient.address1}, {shipment.draft.recipient.city}, {shipment.draft.recipient.state} {shipment.draft.recipient.postal_code}</p>
            <h2 className="pt-3 text-lg font-semibold">4. Lowest-price FedEx service</h2>
            <p className="text-sm text-muted-foreground">Only the cheapest eligible FedEx service and its whole-shipment estimate are shown. Estimates use weight only; final carrier charges may change.</p>
            <Button variant="outline" disabled={!data.config.configured || !data.eligible || !recipientAllowed || busy} onClick={() => rates.mutate()}>{rates.isPending ? "Getting FedEx prices…" : quote ? "Refresh FedEx prices" : "Get FedEx prices"}</Button>
            {selectedRate && !quoteExpired && <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border p-4 text-sm">
                <span>{selectedRate.name}<span className="ml-2 text-xs text-emerald-700">Cheapest eligible service</span></span>
                <strong>{price(selectedRate.amount, selectedRate.currency)}</strong>
            </div>}
            {quoteExpired && <p role="alert" className="text-sm text-amber-600">Prices expired. Refresh before creating the shipment.</p>}
            {selectedRate && !quoteExpired && <p className="text-sm font-medium">Selected: {selectedRate.name} · Estimated total {price(selectedRate.amount, selectedRate.currency)}</p>}
            <Button disabled={!data.config.configured || !data.eligible || !recipientAllowed || busy || !selectedRate || quoteExpired} onClick={() => setConfirm(true)}>Create FedEx sandbox shipment & print labels</Button>
        </section>}
        <Dialog open={confirm} onOpenChange={value => { if (!create.isPending) setConfirm(value); }}><DialogContent><DialogHeader><DialogTitle>{highCost ? "Hey, are you sure?" : "Create sandbox shipment?"}</DialogTitle></DialogHeader>
            <DialogDescription>{highCost && <>Shipping costs {price(selectedRate!.amount, selectedRate!.currency)}, which is over $100. </>}This will send the saved address and calculated weights to FedEx sandbox using {selectedRate?.name} (estimated {selectedRate ? price(selectedRate.amount, selectedRate.currency) : "price unavailable"}) and queue one test label per box for the Brother QL-1110NWB.</DialogDescription>
            <DialogFooter><Button variant="outline" disabled={create.isPending} onClick={() => setConfirm(false)}>Back</Button><Button disabled={create.isPending} onClick={() => create.mutate(highCost)}>{create.isPending ? "Creating…" : highCost ? "Yes, create shipment" : "Create test shipment"}</Button></DialogFooter>
        </DialogContent></Dialog>
    </div>;
}

export default function ShippingWorkspace() {
    const { orderId = "" } = useParams();
    const client = useQueryClient();
    const [params] = useSearchParams();
    const orders = params.get("orders") ?? "";
    const contact = params.get("contact") ?? "";
    const query = useQuery({ queryKey: ["shipping-workspace", orderId, orders, contact], queryFn: () => shippingApi.workspace(orderId, orders, contact), refetchInterval: 5000 });
    return <div className="mx-auto max-w-5xl space-y-6"><Link to="/shipping" className="text-sm underline">← Shipping queue</Link>
        <h1 className="text-2xl font-semibold">Pack & ship {query.data && query.data.orders.length > 1 ? `${query.data.orders.length} orders` : query.data?.order_number ?? "order"}</h1>
        {query.isPending ? <p>Loading shipment…</p> : query.isError ? <p role="alert">Could not load this shipment. <Button onClick={() => void query.refetch()}>Retry</Button></p> :
            <PackingEditor key={`${orders}-${contact}-${query.data.shipment?.revision ?? 0}-${query.data.shipment?.status ?? "new"}`} data={query.data} orderId={orderId} refresh={() => void client.invalidateQueries({ queryKey: ["shipping-workspace", orderId] })} />}
    </div>;
}
