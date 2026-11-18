import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { shippingApi } from "../api/shipping";
import type { ShippingProduct } from "../api/shipping";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { useAuth } from "../contexts/AuthContext";
import { extractApiErrorMessage } from "../utils/apiErrors";

function WeightRow({ product, canEdit, saved }: { product: ShippingProduct; canEdit: boolean; saved: () => void }) {
    const [weight, setWeight] = useState(product.weight_lb?.toString() ?? "");
    const mutation = useMutation({
        mutationFn: () => shippingApi.saveProduct({ ...product, weight_lb: Number(weight) }),
        onSuccess: () => { toast.success("Product weight saved"); saved(); },
        onError: (error: unknown) => toast.error(extractApiErrorMessage(error, "Could not save weight")),
    });
    return <tr className="border-t border-border">
        <td className="p-3"><div className="font-medium break-words">{product.name}</div><div className="text-xs text-muted-foreground">{product.sku || product.product_id}</div></td>
        <td className="p-3"><Input aria-label={`Weight in pounds for ${product.name}`} type="number" min="0.001" max="150" step="0.001" value={weight} onChange={e => setWeight(e.target.value)} disabled={!canEdit} className="w-32" /></td>
        <td className="p-3">{product.weight_lb === null ? <span className="text-amber-600">Weight needed</span> : `${product.weight_lb} lb`}</td>
        <td className="p-3">{canEdit && <Button size="sm" disabled={mutation.isPending || !weight || Number(weight) <= 0 || Number(weight) > 150} onClick={() => mutation.mutate()}>Save weight</Button>}</td>
    </tr>;
}

export default function ShippingProducts() {
    const { isAdmin } = useAuth();
    const client = useQueryClient();
    const [search, setSearch] = useState("");
    const [missingOnly, setMissingOnly] = useState(false);
    const query = useQuery({ queryKey: ["shipping-products"], queryFn: shippingApi.products });
    const refresh = () => { void client.invalidateQueries({ queryKey: ["shipping-products"] }); void client.invalidateQueries({ queryKey: ["shipping-workspace"] }); };
    const importing = useMutation({
        mutationFn: shippingApi.importProducts,
        onSuccess: result => { toast.success(`Imported ${result.imported} products; existing weights kept`); refresh(); },
        onError: (error: unknown) => toast.error(extractApiErrorMessage(error, "Catalog import failed")),
    });
    const products = (query.data ?? []).filter(product =>
        (!missingOnly || product.weight_lb === null) && `${product.name} ${product.sku} ${product.product_id}`.toLowerCase().includes(search.toLowerCase()));
    return <div className="mx-auto max-w-5xl space-y-6">
        <Link to="/shipping" className="text-sm underline">← Shipping queue</Link>
        <div><h1 className="text-2xl font-semibold">Product shipping weights</h1>
            <p className="mt-2 text-sm text-muted-foreground">Save the weight of one product unit in pounds, including manufacturer packaging that stays on it. Shipment weights use only these saved product weights.</p></div>
        <div className="flex flex-wrap items-center gap-4">
            <Input aria-label="Search shipping products" placeholder="Search products or SKU…" value={search} onChange={e => setSearch(e.target.value)} className="max-w-sm" />
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={missingOnly} onChange={e => setMissingOnly(e.target.checked)} />Missing weights only</label>
            {isAdmin && <Button variant="outline" disabled={importing.isPending} onClick={() => importing.mutate()}>{importing.isPending ? "Importing…" : "Import full Inflow catalog"}</Button>}
        </div>
        <p className="text-xs text-muted-foreground">Products from synced orders appear automatically. Import fills in Inflow product names and SKUs while keeping saved weights. A product shown by SKU is waiting for its name; weights are never guessed. {isAdmin ? "" : "Ask an admin to maintain weights."}</p>
        {query.isPending ? <p>Loading products…</p> : query.isError ? <p role="alert">Could not load products. <Button onClick={() => void query.refetch()}>Retry</Button></p> :
            <div className="overflow-x-auto rounded-xl border border-border"><table className="w-full text-left text-sm">
                <thead className="bg-muted/40"><tr><th className="p-3">Product</th><th className="p-3">Unit weight (lb)</th><th className="p-3">Saved weight</th><th className="p-3">Action</th></tr></thead>
                <tbody>{products.map(product => <WeightRow key={`${product.product_id}-${product.weight_lb}`} product={product} canEdit={isAdmin} saved={refresh} />)}</tbody>
            </table>{products.length === 0 && <p className="p-6 text-sm">No matching products.</p>}</div>}
    </div>;
}
