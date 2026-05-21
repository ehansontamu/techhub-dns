import { useState, useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { useNavigate } from "react-router-dom";
import { OrderStatus, ShippingWorkflowStatus, ShippingWorkflowStatusDisplayNames } from "../types/order";
import { SkeletonTable } from "../components/Skeleton";
import { PackageSearch, Truck, CheckCircle } from "lucide-react";
import { useOrdersWebSocket } from "../hooks/useOrdersWebSocket";
import { ordersApi } from "../api/orders";
import { invalidateOrderQueries } from "../queries/orders";
import { toast } from "sonner";
import { isValidOrderId } from "../utils/orderIds";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Badge } from "../components/ui/badge";

export default function Shipping() {
    const [search, setSearch] = useState("");
    const [debouncedSearch, setDebouncedSearch] = useState("");
    const [pendingOrderIds, setPendingOrderIds] = useState<Set<string>>(new Set());
    const navigate = useNavigate();
    const queryClient = useQueryClient();

    const { orders: websocketOrders } = useOrdersWebSocket();
    const lastWebSocketUpdate = useRef<number>(0);

    const shipmentQuery = useQuery({
        queryKey: ["orders", "list", { status: OrderStatus.SHIPPING, search: debouncedSearch }],
        queryFn: () => ordersApi.getOrders({ status: OrderStatus.SHIPPING, search: debouncedSearch }).then((r) => r.items),
    });

    const shipmentOrders = shipmentQuery.data ?? [];
    const loading = shipmentQuery.isPending || shipmentQuery.isFetching;

    const updateShippingWorkflowMutation = useMutation({
        mutationFn: ({ orderId, status }: { orderId: string; status: ShippingWorkflowStatus }) =>
            ordersApi.updateShippingWorkflow(orderId, { status }),
        onSuccess: async (_data, variables) => {
            setPendingOrderIds((prev) => {
                const next = new Set(prev);
                next.delete(variables.orderId);
                return next;
            });
            await invalidateOrderQueries(queryClient, variables.orderId);
            toast.success("Shipping status updated");
        },
        onError: async (error: unknown, variables) => {
            setPendingOrderIds((prev) => {
                const next = new Set(prev);
                next.delete(variables.orderId);
                return next;
            });
            console.error("Failed to update shipping workflow:", error);
            if (isAxiosError(error) && error.response?.status === 409) {
                toast.error("Order changed by another user. Reloaded.");
                await invalidateOrderQueries(queryClient, variables.orderId);
                return;
            }
            toast.error("Failed to update shipping status");
        },
    });

    useEffect(() => {
        if (websocketOrders.length > 0) {
            const updateTime = Date.now();
            if (lastWebSocketUpdate.current > 0) {
                void invalidateOrderQueries(queryClient);
            }
            lastWebSocketUpdate.current = updateTime;
        }
    }, [queryClient, websocketOrders]);

    useEffect(() => {
        const timeoutId = window.setTimeout(() => setDebouncedSearch(search), 300);
        return () => window.clearTimeout(timeoutId);
    }, [search]);

    const handleAdvanceWorkflow = (orderId: string, currentStatus: ShippingWorkflowStatus | undefined) => {
        // Treat undefined/null as WORK_AREA (default state before any workflow action)
        const status = currentStatus ?? ShippingWorkflowStatus.WORK_AREA;

        let nextStatus: ShippingWorkflowStatus | null = null;
        if (status === ShippingWorkflowStatus.WORK_AREA) {
            nextStatus = ShippingWorkflowStatus.DOCK;
        } else if (status === ShippingWorkflowStatus.DOCK) {
            nextStatus = ShippingWorkflowStatus.SHIPPED;
        } else {
            toast.info("Already shipped");
            return;
        }

        setPendingOrderIds((prev) => new Set(prev).add(orderId));
        updateShippingWorkflowMutation.mutate({ orderId, status: nextStatus });
    };

    const handleViewDetail = (orderId?: string) => {
        if (!isValidOrderId(orderId)) {
            toast.error("Order details unavailable");
            return;
        }
        navigate(`/orders/${orderId}`, { state: { fromShipping: true } });
    };

    if (shipmentQuery.isError && shipmentOrders.length === 0) {
        return <div className="p-4">Failed to load shipment queue</div>;
    }

    return (
        <div className="h-full min-h-0 overflow-y-auto px-4 py-6 sm:px-6 lg:px-8">
            <div className="mx-auto max-w-[1600px] space-y-6">
                <div className="space-y-1">
                    <h1 className="text-2xl font-semibold tracking-tight text-foreground">
                        Shipment Queue
                        {shipmentOrders.length > 0 && (
                            <span className="ml-2 text-lg font-normal text-muted-foreground">
                                ({shipmentOrders.length})
                            </span>
                        )}
                    </h1>
                    <p className="text-sm text-muted-foreground">Orders outside Bryan/College Station, ready to ship.</p>
                </div>

                <section className="overflow-hidden rounded-2xl border border-border/70 bg-card/80 shadow-none">
                    <div className="px-5 pb-5 sm:px-6 sm:pb-6">
                        {shipmentOrders.length > 0 && (
                            <div className="mb-4">
                                <Input
                                    placeholder="Search by order ID..."
                                    value={search}
                                    onChange={(e) => setSearch(e.target.value)}
                                    className="max-w-sm"
                                />
                            </div>
                        )}
                        {loading && shipmentOrders.length === 0 ? (
                            <div className="transition-opacity duration-150 opacity-100">
                                <SkeletonTable rows={4} columns={5} />
                            </div>
                        ) : shipmentOrders.length === 0 ? (
                            <div className="flex flex-col items-center justify-center py-16 text-center">
                                <PackageSearch className="mb-3 h-8 w-8 text-muted-foreground/60" />
                                <p className="text-sm font-medium text-foreground">No shipments in the queue</p>
                                <p className="text-xs text-muted-foreground">Orders with &quot;Shipping&quot; status will appear here once QA is complete.</p>
                            </div>
                        ) : (
                            <div className={`transition-opacity duration-150 ${loading ? "opacity-90" : "opacity-100"}`}>
                                <div className="overflow-x-auto" style={{ scrollbarGutter: "stable" }}>
                                    <table className="min-w-[960px] w-full text-sm">
                                        <thead className="sticky top-0 z-20 bg-muted/40">
                                            <tr>
                                                <th className="px-4 py-3 text-left font-semibold text-muted-foreground whitespace-nowrap">Order ID</th>
                                                <th className="px-4 py-3 text-left font-semibold text-muted-foreground whitespace-nowrap">Recipient</th>
                                                <th className="px-4 py-3 text-left font-semibold text-muted-foreground whitespace-nowrap">Location</th>
                                                <th className="px-4 py-3 text-left font-semibold text-muted-foreground whitespace-nowrap">Shipping Workflow</th>
                                                <th className="px-4 py-3 text-left font-semibold text-muted-foreground whitespace-nowrap">Actions</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-border">
                                            {shipmentOrders.map((order) => (
                                                <tr
                                                    key={order.id}
                                                    onClick={() => handleViewDetail(order.id)}
                                                    className="cursor-pointer hover:bg-muted/30 transition-colors"
                                                >
                                                    <td className="px-4 py-3 break-words font-medium">{order.inflow_order_id}</td>
                                                    <td className="px-4 py-3 break-words">{order.recipient_name || "N/A"}</td>
                                                    <td className="px-4 py-3 break-words">{order.delivery_location || "N/A"}</td>
                                                    <td className="px-4 py-3 whitespace-nowrap">
                                                        <Badge variant="outline">
                                                            {order.shipping_workflow_status
                                                                ? ShippingWorkflowStatusDisplayNames[order.shipping_workflow_status]
                                                                : ShippingWorkflowStatusDisplayNames[ShippingWorkflowStatus.WORK_AREA]}
                                                        </Badge>
                                                    </td>
                                                    <td className="px-4 py-3 whitespace-nowrap">
                                                        {(() => {
                                                            const current = order.shipping_workflow_status as ShippingWorkflowStatus | undefined;
                                                            // Treat undefined as WORK_AREA
                                                            const normalized = current ?? ShippingWorkflowStatus.WORK_AREA;
                                                            const isPending = pendingOrderIds.has(order.id);

                                                            if (normalized === ShippingWorkflowStatus.WORK_AREA) {
                                                                return (
                                                                    <Button
                                                                        size="sm"
                                                                        variant="default"
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            handleAdvanceWorkflow(order.id, normalized);
                                                                        }}
                                                                        disabled={isPending}
                                                                    >
                                                                        <Truck className="h-3.5 w-3.5 mr-1" />
                                                                        Move to Dock
                                                                    </Button>
                                                                );
                                                            }
                                                            if (normalized === ShippingWorkflowStatus.DOCK) {
                                                                return (
                                                                    <Button
                                                                        size="sm"
                                                                        variant="default"
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            handleAdvanceWorkflow(order.id, normalized);
                                                                        }}
                                                                        disabled={isPending}
                                                                    >
                                                                        <CheckCircle className="h-3.5 w-3.5 mr-1" />
                                                                        Mark Shipped
                                                                    </Button>
                                                                );
                                                            }
                                                            return <span className="text-xs text-muted-foreground">Shipped</span>;
                                                        })()}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        )}
                    </div>
                </section>
            </div>
        </div>
    );
}
