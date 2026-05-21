import { useState, useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { isAxiosError } from "axios";
import type { StatusFilter } from "../components/Filters";
import { AlertCircle, ArrowLeft, ChevronDown, FileSearch } from "lucide-react";
import { toast } from "sonner";

import { ordersApi } from "../api/orders";
import { settingsApi } from "../api/settings";
import OrdersRail from "../components/OrdersRail";
import { useAuth } from "../contexts/AuthContext";
import { getUserDisplayName } from "../utils/userDisplay";
import OrderDetailComponent from "../components/OrderDetail";
import StatusTransition from "../components/StatusTransition";
import { Button } from "../components/ui/button";
import { useOrdersWebSocket } from "../hooks/useOrdersWebSocket";

import { getOrderAuditQueryOptions, getOrderDetailQueryOptions, getOrdersListQueryOptions, invalidateOrderQueries } from "../queries/orders";
import { OrderStatus } from "../types/order";
import { extractApiErrorMessage, shouldThrowToBoundary } from "../utils/apiErrors";
import { isValidOrderId } from "../utils/orderIds";
import { SkeletonCard } from "../components/Skeleton";

export default function OrderDetailPage() {
    const { orderId: rawOrderId } = useParams<{ orderId: string }>();
    const orderId = isValidOrderId(rawOrderId) ? rawOrderId : null;
    const invalidOrderId = Boolean(rawOrderId) && !orderId;
    const navigate = useNavigate();
    const location = useLocation();
    const locationState = (location.state as {
        statusFilter?: StatusFilter;
        search?: string;
    } | null);
    const [sidebarStatusFilter, setSidebarStatusFilter] = useState<StatusFilter>(
        locationState?.statusFilter ?? [OrderStatus.PICKED, OrderStatus.QA],
    );
    const sidebarSearch = locationState?.search ?? "";
    const { user } = useAuth();
    const [transitioningStatus, setTransitioningStatus] = useState<{
        newStatus: OrderStatus;
        requireReason: boolean;
        title?: string;
        confirmLabel?: string;
        action: "status" | "rollback";
    } | null>(null);
    const queryClient = useQueryClient();
    const [mobileShowOrders, setMobileShowOrders] = useState(false);

    const { orders: websocketOrders } = useOrdersWebSocket();
    const lastWebSocketUpdate = useRef<number>(0);

    const orderQuery = useQuery({
        ...getOrderDetailQueryOptions(orderId ?? ""),
        enabled: Boolean(orderId),
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        throwOnError: shouldThrowToBoundary,
    });

    const auditQuery = useQuery({
        ...getOrderAuditQueryOptions(orderId ?? ""),
        enabled: Boolean(orderId),
        refetchOnWindowFocus: false,
        throwOnError: shouldThrowToBoundary,
    });

    const listQuery = useQuery({
        ...getOrdersListQueryOptions({ status: sidebarStatusFilter, search: sidebarSearch }),
        staleTime: 30_000,
        refetchOnWindowFocus: false,
    });

    const order = orderQuery.data ?? null;
    const auditLogs = auditQuery.data ?? [];
    const notifications = order?.teams_notifications ?? [];
    const sidebarOrders = listQuery.data?.items ?? [];
    const detailLoading = orderQuery.isPending || auditQuery.isPending;
    const sidebarLoading = listQuery.isPending && sidebarOrders.length === 0;

    const handleBack = () => {
        navigate("/orders", {
            state: locationState ?? undefined,
        });
    };

    const renderState = (title: string, description: string, icon: "error" | "missing") => (
        <div className="mx-auto flex min-h-[50vh] max-w-xl items-center justify-center px-4">
            <div className="w-full rounded-lg border border-border bg-card p-6 text-center shadow-sm">
                {icon === "error" ? (
                    <AlertCircle className="mx-auto mb-3 h-8 w-8 text-destructive" />
                ) : (
                    <FileSearch className="mx-auto mb-3 h-8 w-8 text-muted-foreground" />
                )}
                <h1 className="text-lg font-semibold text-foreground">{title}</h1>
                <p className="mt-2 text-sm text-muted-foreground">{description}</p>
                <Button type="button" variant="outline" className="mt-4 min-h-11 gap-2 px-4" onClick={handleBack} disabled={detailLoading}>
                    <ArrowLeft className="h-4 w-4" />
                    Back
                </Button>
            </div>
        </div>
    );

    const refreshOrder = async (): Promise<void> => {
        if (!orderId) {
            return;
        }

        await invalidateOrderQueries(queryClient, orderId);
    };

    const getUserName = () => getUserDisplayName(user, "Unknown User");

    const updateStatusMutation = useMutation({
        mutationFn: ({ newStatus, reason, expectedUpdatedAt }: {
            newStatus: OrderStatus;
            reason?: string;
            expectedUpdatedAt?: string;
        }) => {
            if (!orderId) {
                throw new Error("Order id is required");
            }

            return ordersApi.updateOrderStatus(orderId, {
                status: newStatus,
                reason,
                expected_updated_at: expectedUpdatedAt,
            });
        },
        onSuccess: async () => {
            setTransitioningStatus(null);
            await refreshOrder();
        },
        onError: async (error: unknown) => {
            console.error("Failed to update status:", error);
            if (isAxiosError(error) && error.response?.status === 409) {
                toast.error("Order changed by another user. Reloaded the latest details.");
                await refreshOrder();
                return;
            }

            toast.error("Failed to update order status");
        },
    });

    const rollbackStatusMutation = useMutation({
        mutationFn: ({ newStatus, reason, expectedUpdatedAt }: {
            newStatus: OrderStatus;
            reason?: string;
            expectedUpdatedAt?: string;
        }) => {
            if (!orderId) {
                throw new Error("Order id is required");
            }

            return ordersApi.rollbackOrderStatus(orderId, {
                status: newStatus,
                reason,
                expected_updated_at: expectedUpdatedAt,
            });
        },
        onSuccess: async () => {
            setTransitioningStatus(null);
            await refreshOrder();
        },
        onError: async (error: unknown) => {
            console.error("Failed to rollback status:", error);
            if (isAxiosError(error) && error.response?.status === 409) {
                toast.error("Order changed by another user. Reloaded the latest details.");
                await refreshOrder();
                return;
            }

            toast.error("Failed to rollback order status");
        },
    });

    const tagOrderMutation = useMutation({
        mutationFn: (tagIds: string[]) => {
            if (!orderId || !order) {
                throw new Error("Order is unavailable");
            }

            return ordersApi.tagOrder(orderId, {
                tag_ids: tagIds,
                technician: getUserName(),
                expected_updated_at: order.updated_at,
            });
        },
        onSuccess: async () => {
            await refreshOrder();
        },
        onError: async (error: unknown) => {
            console.error("Failed to tag order:", error);
            if (isAxiosError(error) && error.response?.status === 409) {
                toast.error("Order changed by another user. Reloaded the latest details.");
                await refreshOrder();
                return;
            }

            toast.error("Failed to tag order");
        },
    });

    const generatePicklistMutation = useMutation({
        mutationFn: ({ createPartialLeg }: { createPartialLeg?: boolean } = {}) => {
            if (!orderId || !order) {
                throw new Error("Order is unavailable");
            }

            return ordersApi.generatePicklist(orderId, {
                generated_by: getUserName(),
                expected_updated_at: order.updated_at,
                create_partial_leg: createPartialLeg,
                confirm_create_partial_leg: createPartialLeg,
            });
        },
        onSuccess: async () => {
            await refreshOrder();
        },
        onError: async (error: unknown) => {
            console.error("Failed to generate picklist:", error);
            if (isAxiosError(error) && error.response?.status === 409) {
                toast.error("Order changed by another user. Reloaded the latest details.");
                await refreshOrder();
                return;
            }

            const message = extractApiErrorMessage(error, "Failed to generate picklist");
            toast.error(message);
        },
    });

    const handleRequestTags = async () => {
        if (!order) return;
        const inflowOrderId = order.inflow_order_id;
        if (!inflowOrderId) {
            toast.error("Order is missing an inflow order id");
            return;
        }

        try {
            const result = await settingsApi.uploadCanopyOrders([inflowOrderId]);
            if (!result.success) {
                toast.error(result.error || "Failed to request tags");
                return;
            }
            await refreshOrder();
        } catch (error: unknown) {
            const message = extractApiErrorMessage(error, "Failed to request tags");
            toast.error(message);
        }
    };

    useEffect(() => {
        if (websocketOrders.length > 0 && orderId) {
            const updateTime = Date.now();
            if (lastWebSocketUpdate.current > 0) {
                const orderUpdated = websocketOrders.some((wo) => wo.id === orderId);
                if (orderUpdated) {
                    void refreshOrder();
                }
            }
            lastWebSocketUpdate.current = updateTime;
        }
    }, [orderId, websocketOrders]);

    // Auto-adjust sidebar filter so the opened order is visible
    useEffect(() => {
        if (!order) return;

        const orderStatus = order.status;
        const currentFilter = sidebarStatusFilter;

        let wouldBeIncluded = false;
        if (currentFilter === null) {
            wouldBeIncluded = true; // "All" includes everything
        } else if (Array.isArray(currentFilter)) {
            wouldBeIncluded = currentFilter.includes(orderStatus);
        } else {
            wouldBeIncluded = currentFilter === orderStatus;
        }

        if (!wouldBeIncluded) {
            setSidebarStatusFilter(orderStatus);
        }
    }, [order?.status]);

    const handleStatusChange = (newStatus: OrderStatus, reason?: string) => {
        if (!order) return;
        const requireReason = newStatus === OrderStatus.ISSUE;
        if (requireReason && reason === undefined) {
            setTransitioningStatus({ newStatus, requireReason: true, title: "Change Status", confirmLabel: "Confirm", action: "status" });
        } else {
            void performTransition("status", newStatus, reason);
        }
    };

    const handleRollbackStatus = (newStatus: OrderStatus) => {
        if (!order) return;
        setTransitioningStatus({ newStatus, requireReason: true, title: "Rollback Order", confirmLabel: "Rollback", action: "rollback" });
    };

    const performTransition = async (action: "status" | "rollback", newStatus: OrderStatus, reason?: string) => {
        if (!order) return;
        try {
            const payload = {
                newStatus,
                reason,
                expectedUpdatedAt: order.updated_at,
            };
            if (action === "rollback") {
                await rollbackStatusMutation.mutateAsync(payload);
                return;
            }
            await updateStatusMutation.mutateAsync(payload);
        } catch {
            // Handled by mutation callbacks.
        }
    };

    const handleTagOrder = async (tagIds: string[]) => {
        if (!order) return;
        try {
            await tagOrderMutation.mutateAsync(tagIds);
        } catch {
            // Handled by mutation callbacks.
        }
    };

    const handleGeneratePicklist = async (options?: { createPartialLeg?: boolean }) => {
        if (!order) return;
        try {
            await generatePicklistMutation.mutateAsync(options ?? {});
        } catch {
            // Handled by mutation callbacks.
        }
    };

    const handleSelectOrder = (nextOrderId: string) => {
        navigate(`/orders/${nextOrderId}`, {
            state: locationState ?? undefined,
        });
    };

    if (invalidOrderId) {
        return renderState("Invalid order link", "This order link is malformed or missing a valid order id.", "missing");
    }

    if (!detailLoading && orderQuery.isError) {
        return renderState("Failed to load order details", "Try refreshing the page or return to the orders list and try again.", "error");
    }

    if (!detailLoading && !order) {
        return renderState("Order not found", "The order may have been removed or you may not have access to it.", "missing");
    }

    return (
        <div className="h-full">
            <div className="lg:flex lg:h-full lg:items-stretch lg:overflow-hidden">
                <aside className="hidden lg:block lg:h-full lg:w-80 lg:shrink-0 lg:border-r lg:border-border/60">
                    <OrdersRail
                            orders={sidebarOrders}
                            selectedOrderId={orderId}
                            loading={sidebarLoading}
                            status={sidebarStatusFilter}
                            onStatusChange={setSidebarStatusFilter}
                            onSelectOrder={handleSelectOrder}
                        />
                </aside>

                <div className="min-w-0 flex-1 px-4 pt-4 sm:px-6 sm:pt-6 lg:px-8 lg:pt-6 lg:h-full lg:min-h-0 lg:overflow-y-auto">
                    <div className="lg:hidden space-y-3">
                        <Button
                            type="button"
                            variant="outline"
                            className="w-full justify-between min-h-11 gap-2 px-4"
                            onClick={() => setMobileShowOrders((prev) => !prev)}
                        >
                            Orders
                            <ChevronDown className={`h-4 w-4 transition-transform ${mobileShowOrders ? "rotate-180" : ""}`} />
                        </Button>
                        {mobileShowOrders && (
                            <div className="mt-3">
                                <OrdersRail
                                    orders={sidebarOrders}
                                    selectedOrderId={orderId}
                                    loading={sidebarLoading}
                                    status={sidebarStatusFilter}
                                    onStatusChange={setSidebarStatusFilter}
                                    onSelectOrder={handleSelectOrder}
                                />
                            </div>
                        )}
                    </div>

                    <div className="hidden lg:flex items-center pb-4">
                        <Button type="button" variant="outline" className="min-h-11 gap-2 px-4" onClick={handleBack} disabled={detailLoading}>
                            <ArrowLeft className="h-4 w-4" />
                            Back to orders
                        </Button>
                    </div>

                <div className="lg:hidden">
                    <Button type="button" variant="outline" className="min-h-11 gap-2 px-4" onClick={handleBack} disabled={detailLoading}>
                        <ArrowLeft className="h-4 w-4" />
                        Back
                    </Button>
                </div>
                {order ? (
                    <OrderDetailComponent
                        order={order}
                        auditLogs={auditLogs}
                        notifications={notifications}
                        onStatusChange={handleStatusChange}
                        onRollbackStatus={handleRollbackStatus}
                        onTagOrder={handleTagOrder}
                        onRequestTags={handleRequestTags}
                        onGeneratePicklist={handleGeneratePicklist}
                        generatingPicklist={generatePicklistMutation.isPending}
                    />
                ) : (
                    <SkeletonCard lines={4} />
                )}
                {order && transitioningStatus && (
                    <StatusTransition
                        currentStatus={order.status}
                        newStatus={transitioningStatus.newStatus}
                        requireReason={transitioningStatus.requireReason}
                        title={transitioningStatus.title}
                        confirmLabel={transitioningStatus.confirmLabel}
                        onConfirm={(reason) => performTransition(transitioningStatus.action, transitioningStatus.newStatus, reason)}
                        onCancel={() => setTransitioningStatus(null)}
                        submitting={updateStatusMutation.isPending || rollbackStatusMutation.isPending}
                    />
                )}
                </div>
            </div>
        </div>
    );
}
