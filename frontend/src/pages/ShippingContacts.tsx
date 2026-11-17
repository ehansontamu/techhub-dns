import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { shippingApi } from "../api/shipping";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { useAuth } from "../contexts/AuthContext";
import { extractApiErrorMessage } from "../utils/apiErrors";

export default function ShippingContacts() {
    const { isAdmin } = useAuth();
    const client = useQueryClient();
    const [search, setSearch] = useState("");
    const [file, setFile] = useState<File | null>(null);
    const query = useQuery({ queryKey: ["shipping-address-book", search], queryFn: () => shippingApi.addressBook(search) });
    const importing = useMutation({
        mutationFn: () => shippingApi.importContacts(file!),
        onSuccess: result => { toast.success(`Saved ${result.imported} FedEx contacts`); void client.invalidateQueries({ queryKey: ["shipping-address-book"] }); },
        onError: (error: unknown) => toast.error(extractApiErrorMessage(error, "Address-book import failed")),
    });
    return <div className="mx-auto max-w-5xl space-y-6">
        <Link to="/shipping" className="text-sm underline">← Shipping queue</Link>
        <h1 className="text-2xl font-semibold">Manage FedEx address book</h1>
        <p className="text-sm text-muted-foreground">Import once for the whole team. Contacts stay in the database between shipments and server restarts. Upload again only to add or refresh contacts. This is a saved copy of your FedEx address book, not a live sync.</p>
        {isAdmin && <section className="rounded-xl border border-border p-5 space-y-3">
            <label className="block text-sm">FedEx address-book CSV<Input type="file" accept=".csv,text/csv" onChange={e => setFile(e.target.files?.[0] ?? null)} /></label>
            <Button disabled={!file || importing.isPending} onClick={() => importing.mutate()}>{importing.isPending ? "Importing…" : "Import FedEx CSV"}</Button>
        </section>}
        <Input aria-label="Search saved FedEx contacts" placeholder="Search saved contacts…" value={search} onChange={e => setSearch(e.target.value)} />
        {query.isPending ? <p>Loading contacts…</p> : query.isError ? <p role="alert">Could not load the address book.</p> : <div className="space-y-3">
            {(query.data ?? []).map(contact => <div className="rounded-lg border border-border p-4 text-sm" key={contact.id}><strong>{contact.label}</strong><p>{contact.recipient.address1} {contact.recipient.address2}, {contact.recipient.city}, {contact.recipient.state} {contact.recipient.postal_code}</p></div>)}
            {query.data?.length === 0 && <p className="text-sm text-muted-foreground">No matching contacts. An admin can import the shared address book here once.</p>}
        </div>}
    </div>;
}
