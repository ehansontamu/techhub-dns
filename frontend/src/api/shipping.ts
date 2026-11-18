import apiClient from "./client";

export interface ShippingProduct {
    product_id: string;
    name: string;
    sku: string;
    weight_lb: number | null;
}

export interface Recipient {
    name: string;
    company: string;
    phone: string;
    address1: string;
    address2: string;
    city: string;
    state: string;
    postal_code: string;
    country: string;
    residential: boolean;
}

export interface ShippingContact {
    id: string;
    label: string;
    recipient: Recipient;
}

export interface ShippingBox {
    name: string;
    use_as_box: boolean;
    items: Array<{ order_id?: string; product_id: string; quantity: number }>;
}

export interface PackingPlan {
    order_ids?: string[];
    recipient: Recipient;
    contact_id?: string | null;
    manual_address_override?: boolean;
    service_type: string;
    boxes: ShippingBox[];
    computed_boxes?: Array<ShippingBox & { product_weight_lb: number; shipping_weight_lb: number }>;
    rate_quote?: { options: RateOption[]; quoted_at: string; expires_at: string; ship_date: string; environment: string };
}

export interface RateOption {
    service_type: string;
    name: string;
    amount: string;
    currency: string;
}

export interface Shipment {
    order_ids: string[];
    id: string;
    status: "draft" | "creating" | "processing_response" | "created" | "rejected" | "needs_review";
    revision: number;
    environment: string | null;
    draft: PackingPlan;
    master_tracking_number: string | null;
    packages: Array<ShippingBox & { index: number; shipping_weight_lb: number; tracking_number: string; print_jobs: Array<{ id: string; status: string; last_error: string | null }> }>;
    last_error: string | null;
}

export interface ShippingQueueOrder {
    id: string;
    order_number: string;
    recipient_name: string | null;
    products: Array<{ product_id: string; name: string; quantity: number }>;
    shipment_id: string | null;
    shipment_order_id: string | null;
    shipment_status: string | null;
    shipping_workflow_status: string | null;
}
export interface ShippingQueue {
    cities: Array<{ key: string; name: string; locations: Array<{
        contact_id: string; name: string; recipient: Recipient; orders: ShippingQueueOrder[];
    }> }>;
    unmatched: Array<ShippingQueueOrder & { city: string }>;
}

export interface ShippingWorkspace {
    order_ids: string[];
    orders: Array<{ id: string; order_number: string }>;
    contact_id: string | null;
    order_number: string;
    order_status: string;
    shipping_workflow_status: string | null;
    eligible: boolean;
    items: Array<ShippingProduct & { order_id: string; order_number: string; quantity: number }>;
    shipment: Shipment | null;
    recipient: Recipient;
    config: { configured: boolean; environment: string; missing: string[] };
}

export const shippingApi = {
    addressBook: async (search = ""): Promise<ShippingContact[]> => (await apiClient.get<{ contacts: ShippingContact[] }>("/shipping/contacts", { params: { search } })).data.contacts,
    products: async (): Promise<ShippingProduct[]> => (await apiClient.get<{ products: ShippingProduct[] }>("/shipping/products")).data.products,
    saveProduct: async (product: ShippingProduct) => apiClient.put(`/shipping/products/${encodeURIComponent(product.product_id)}`, {
        name: product.name, sku: product.sku || "", weight_lb: product.weight_lb,
    }),
    importProducts: async (): Promise<{ imported: number }> => (await apiClient.post("/shipping/products/import", {}, { timeout: 180000 })).data,
    queue: async (): Promise<ShippingQueue> => (await apiClient.get("/shipping/queue")).data,
    workspace: async (orderId: string, orders = "", contact = ""): Promise<ShippingWorkspace> =>
        (await apiClient.get(`/shipping/orders/${orderId}`, { params: { orders, contact } })).data,
    contacts: async (orderId: string, search = ""): Promise<ShippingContact[]> => (await apiClient.get<{ contacts: ShippingContact[] }>(`/shipping/orders/${orderId}/contacts`, { params: { search } })).data.contacts,
    importContacts: async (file: File): Promise<{ imported: number }> => {
        const body = new FormData(); body.append("file", file);
        return (await apiClient.post("/shipping/contacts/import", body, { headers: { "Content-Type": "multipart/form-data" } })).data;
    },
    save: async (orderId: string, plan: PackingPlan, revision: number): Promise<Shipment> =>
        (await apiClient.put(`/shipping/orders/${orderId}/packing`, { ...plan, expected_revision: revision })).data,
    create: async (orderId: string, revision: number, confirmHighShippingCost = false): Promise<Shipment> =>
        (await apiClient.post(`/shipping/orders/${orderId}/create`, { expected_revision: revision, confirm_high_shipping_cost: confirmHighShippingCost }, { timeout: 100000 })).data,
    rates: async (orderId: string, revision: number): Promise<Shipment> =>
        (await apiClient.post(`/shipping/orders/${orderId}/rates`, { expected_revision: revision }, { timeout: 80000 })).data,
    print: async (orderId: string, index: number) => apiClient.post(`/shipping/orders/${orderId}/labels/${index}/print`),
    download: async (orderId: string, index: number) => {
        const response = await apiClient.get<Blob>(`/shipping/orders/${orderId}/labels/${index}`, { responseType: "blob" });
        const url = URL.createObjectURL(response.data);
        const link = document.createElement("a");
        link.href = url;
        link.download = `fedex-${orderId}-box-${index + 1}.pdf`;
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
};
