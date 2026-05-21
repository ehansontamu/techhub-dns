import { ChevronDown, PackageSearch } from "lucide-react";
import { useCallback, useState } from "react";
import type { Order } from "../types/order";
import { OrderStatus, OrderStatusDisplayNames } from "../types/order";
import StatusBadge from "./StatusBadge";
import { Skeleton } from "./Skeleton";
import type { StatusFilter } from "./Filters";

interface OrdersRailProps {
    orders: Order[];
    selectedOrderId?: string | null;
    loading?: boolean;
    status?: StatusFilter;
    onStatusChange?: (status: StatusFilter) => void;
    onSelectOrder: (orderId: string) => void;
}

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
    { value: [OrderStatus.PICKED, OrderStatus.QA], label: "Picked + QA" },
    { value: null, label: "All" },
    { value: OrderStatus.PICKED, label: OrderStatusDisplayNames[OrderStatus.PICKED] },
    { value: OrderStatus.QA, label: OrderStatusDisplayNames[OrderStatus.QA] },
    { value: OrderStatus.PRE_DELIVERY, label: OrderStatusDisplayNames[OrderStatus.PRE_DELIVERY] },
    { value: OrderStatus.IN_DELIVERY, label: OrderStatusDisplayNames[OrderStatus.IN_DELIVERY] },
    { value: OrderStatus.SHIPPING, label: OrderStatusDisplayNames[OrderStatus.SHIPPING] },
    { value: OrderStatus.DELIVERED, label: OrderStatusDisplayNames[OrderStatus.DELIVERED] },
    { value: OrderStatus.ISSUE, label: OrderStatusDisplayNames[OrderStatus.ISSUE] },
];

export default function OrdersRail({
    orders,
    selectedOrderId = null,
    loading = false,
    status,
    onStatusChange,
    onSelectOrder,
}: OrdersRailProps) {
    const [focusedIndex, setFocusedIndex] = useState(0);

    const handleKeyDown = useCallback(
        (e: React.KeyboardEvent) => {
            if (orders.length === 0) return;

            let nextIndex = focusedIndex;
            switch (e.key) {
                case "ArrowDown":
                    e.preventDefault();
                    nextIndex = Math.min(focusedIndex + 1, orders.length - 1);
                    break;
                case "ArrowUp":
                    e.preventDefault();
                    nextIndex = Math.max(focusedIndex - 1, 0);
                    break;
                case "Home":
                    e.preventDefault();
                    nextIndex = 0;
                    break;
                case "End":
                    e.preventDefault();
                    nextIndex = orders.length - 1;
                    break;
                default:
                    return;
            }

            setFocusedIndex(nextIndex);
            // Focus the button after state update
            requestAnimationFrame(() => {
                const container = e.currentTarget as HTMLElement;
                const buttons = container.querySelectorAll<HTMLElement>("[data-order-button]");
                buttons[nextIndex]?.focus();
            });
        },
        [focusedIndex, orders.length],
    );

    if (loading && orders.length === 0) {
        return (
            <div className="rounded-lg border border-border bg-card shadow-sm">
                <div className="border-b border-border px-4 py-3">
                    <div className="h-5 w-28 rounded bg-muted" />
                </div>
                <div role="status" aria-label="Loading orders" className="space-y-3 p-4">
                    {Array.from({ length: 5 }).map((_, index) => (
                        <div key={index} className="space-y-2 rounded-md border border-border/60 p-3">
                            <Skeleton className="h-4 w-32" />
                            <Skeleton className="h-4 w-24" />
                            <Skeleton className="h-3 w-full" />
                        </div>
                    ))}
                </div>
            </div>
        );
    }

    if (orders.length === 0) {
        return (
            <div className="bg-card lg:flex lg:h-full lg:min-h-0 lg:flex-col">
                <div className="border-b border-border px-4 py-2.5">
                    {onStatusChange ? (
                        <div className="relative">
                            <select
                                value={status !== undefined ? JSON.stringify(status) : JSON.stringify([OrderStatus.PICKED, OrderStatus.QA])}
                                onChange={(e) => {
                                    const parsed = JSON.parse(e.target.value) as StatusFilter;
                                    onStatusChange(parsed);
                                }}
                                className="w-full appearance-none rounded-md border border-border bg-background py-1.5 pl-3 pr-8 text-sm font-medium text-foreground"
                            >
                                {STATUS_OPTIONS.map((opt) => (
                                    <option key={opt.label} value={JSON.stringify(opt.value)}>
                                        {opt.label}
                                    </option>
                                ))}
                            </select>
                            <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                        </div>
                    ) : (
                        <div className="flex items-center justify-between gap-3">
                            <h2 className="text-sm font-semibold text-foreground">Orders</h2>
                            <span className="text-xs text-muted-foreground">0</span>
                        </div>
                    )}
                </div>
                <div className="flex flex-1 flex-col items-center justify-center px-4 py-10 text-center text-muted-foreground">
                    <PackageSearch className="mb-3 h-8 w-8 text-muted-foreground/60" />
                    <p className="text-sm font-medium text-foreground">No orders found</p>
                    <p className="text-xs text-muted-foreground">Try adjusting your filters or search.</p>
                </div>
            </div>
        );
    }

    return (
        <div className="bg-card lg:flex lg:h-full lg:min-h-0 lg:flex-col">
            <div className="border-b border-border px-4 py-2.5">
                {onStatusChange ? (
                    <div className="relative">
                        <select
                            value={status !== undefined ? JSON.stringify(status) : JSON.stringify([OrderStatus.PICKED, OrderStatus.QA])}
                            onChange={(e) => {
                                const parsed = JSON.parse(e.target.value) as StatusFilter;
                                onStatusChange(parsed);
                            }}
                            className="w-full appearance-none rounded-md border border-border bg-background py-1.5 pl-3 pr-8 text-sm font-medium text-foreground"
                        >
                            {STATUS_OPTIONS.map((opt) => (
                                <option key={opt.label} value={JSON.stringify(opt.value)}>
                                    {opt.label}
                                </option>
                            ))}
                        </select>
                        <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    </div>
                ) : (
                    <div className="flex items-center justify-between gap-3">
                        <h2 className="text-sm font-semibold text-foreground">Orders</h2>
                        <span className="text-xs text-muted-foreground">{orders.length}</span>
                    </div>
                )}
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto" onKeyDown={handleKeyDown}>
                <div className="divide-y divide-border">
                    {orders.map((order, index) => {
                        const orderId = order.inflow_order_id || order.id || `${order.created_at || "order"}-${index}`;
                        const isSelected = selectedOrderId === orderId || selectedOrderId === order.id;
                        const isFocused = focusedIndex === index;
                        const orderLabel = `Order ${order.inflow_order_id || order.id}, ${order.status ?? "Unknown"}`;

                        return (
                            <button
                                key={orderId}
                                type="button"
                                data-order-button
                                tabIndex={isFocused ? 0 : -1}
                                onClick={() => onSelectOrder(orderId)}
                                onFocus={() => setFocusedIndex(index)}
                                aria-current={isSelected ? "page" : undefined}
                                aria-label={orderLabel}
                                className={`flex w-full items-center justify-between gap-2 px-4 py-3 text-left transition-colors duration-150 hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${isSelected ? "bg-primary/15" : ""}`}
                            >
                                <span className="truncate text-sm text-foreground">
                                    {order.inflow_order_id || order.id}
                                </span>
                                <StatusBadge status={order.status} />
                            </button>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}
