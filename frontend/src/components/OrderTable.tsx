import { useMemo, useState } from "react";
import { ArrowUpDown, PackageSearch } from "lucide-react";
import { Order } from "../types/order";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "./ui/table";
import StatusBadge from "./StatusBadge";
import { formatDeliveryLocation } from "../utils/location";
import { getPartialOrderInfo } from "../utils/orderPartial";
import { formatToCentralTime } from "../utils/timezone";

interface OrderTableProps {
    orders: Order[];
    onViewDetail: (orderId: string) => void;
    showEmptyState?: boolean;
    loading?: boolean;
}

export default function OrderTable({
    orders,
    onViewDetail,
    showEmptyState = true,
    loading = false,
}: OrderTableProps) {
    const [sortKey, setSortKey] = useState<"id" | "recipient" | "location" | "date">("date");
    const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
    const [expandedOrderId, setExpandedOrderId] = useState<string | null>(null);

    const compareText = (left: unknown, right: unknown): number => {
        const leftText = typeof left === "string" ? left : "";
        const rightText = typeof right === "string" ? right : "";
        return leftText.localeCompare(rightText);
    };

    const compareDate = (left: unknown, right: unknown): number => {
        const leftTime = typeof left === "string" ? Date.parse(left) : Number.NaN;
        const rightTime = typeof right === "string" ? Date.parse(right) : Number.NaN;
        const safeLeft = Number.isNaN(leftTime) ? 0 : leftTime;
        const safeRight = Number.isNaN(rightTime) ? 0 : rightTime;
        return safeLeft - safeRight;
    };

    const compareStableOrderKey = (left: Order, right: Order): number => {
        return compareText(left.inflow_order_id || left.id, right.inflow_order_id || right.id);
    };

    const compareWithFallback = (primary: number, left: Order, right: Order): number => {
        if (primary !== 0) {
            return primary;
        }

        const updatedAtComparison = compareDate(left.updated_at, right.updated_at);
        if (updatedAtComparison !== 0) {
            return updatedAtComparison;
        }

        const createdAtComparison = compareDate(left.created_at, right.created_at);
        if (createdAtComparison !== 0) {
            return createdAtComparison;
        }

        return compareStableOrderKey(left, right);
    };

    const sortedOrders = useMemo(() => {
        const copy = [...orders];
        copy.sort((a, b) => {
            const direction = sortDir === "asc" ? 1 : -1;
            let comparison = 0;

            switch (sortKey) {
                case "id":
                    comparison = compareStableOrderKey(a, b);
                    break;
                case "recipient":
                    comparison = compareText(a.recipient_name, b.recipient_name);
                    break;
                case "location":
                    comparison = compareText(formatDeliveryLocation(a), formatDeliveryLocation(b));
                    break;
                case "date":
                default:
                    comparison = compareDate(a.created_at, b.created_at);
                    break;
            }

            return direction * compareWithFallback(comparison, a, b);
        });
        return copy;
    }, [orders, sortKey, sortDir]);

    const toggleSort = (key: typeof sortKey) => {
        if (sortKey === key) {
            setSortDir(sortDir === "asc" ? "desc" : "asc");
            return;
        }
        setSortKey(key);
        setSortDir("desc");
    };

    const navigateToOrder = (orderId: string) => onViewDetail(orderId);

    const getOrderNumber = (order: Order) => order.inflow_order_id || order.id;
    const getOrderLegLabel = (order: Order) => {
        const partialInfo = getPartialOrderInfo(order);
        if (partialInfo.isPartialLeg) return "Picked leg";
        if (partialInfo.hasRemainder) return "Remainder leg";
        if (partialInfo.isPartial) return "Partial order";
        return null;
    };

    if (orders.length === 0) {
        if (!showEmptyState) {
            return null;
        }
        return (
            <div className="py-12 text-center text-muted-foreground">
                <PackageSearch className="mx-auto mb-3 h-8 w-8 text-muted-foreground/40" />
                <p className="text-sm font-medium">No orders found</p>
                <p className="text-xs text-muted-foreground/80">Try adjusting your filters or search.</p>
            </div>
        );
    }

    return (
        <div className="overflow-hidden rounded-2xl border border-border/70 bg-card/80 shadow-none" style={{ scrollbarGutter: "stable" }}>
            <div className="md:hidden divide-y divide-border">
                {sortedOrders.map((order, index) => {
                    const orderId = order.id || order.inflow_order_id || `${order.created_at || "order"}-${index}`;
                    const isExpanded = expandedOrderId === orderId;
                    const legLabel = getOrderLegLabel(order);

                    return (
                        <div
                            key={orderId}
                            role="button"
                            tabIndex={0}
                            onClick={() => setExpandedOrderId((current) => (current === orderId ? null : orderId))}
                            onKeyDown={(event) => {
                                if (event.key === "Enter" || event.key === " ") {
                                    event.preventDefault();
                                    setExpandedOrderId((current) => (current === orderId ? null : orderId));
                                }
                            }}
                            className="touch-manipulation block w-full p-4 text-left hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                        >
                            <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0 space-y-1">
                                    <div className="flex min-h-[44px] items-center gap-2">
                                        <span className="truncate text-sm font-semibold text-foreground">
                                            {order.inflow_order_id || order.id}
                                        </span>
                                        <StatusBadge
                                            status={order.status}
                                            className="h-6 px-2 py-0 text-[10px] font-medium uppercase tracking-wide"
                                        />
                                        {legLabel ? (
                                            <Badge variant={legLabel === "Remainder leg" ? "warning" : "secondary"} className="text-[10px] uppercase tracking-wide">
                                                {legLabel}
                                            </Badge>
                                        ) : null}
                                    </div>
                                    <p className="break-words text-sm text-muted-foreground">
                                        {order.recipient_name || "N/A"}
                                    </p>
                                </div>
                            </div>
                            <div className={`mt-3 grid gap-2 text-xs text-muted-foreground ${isExpanded ? "grid-cols-1" : "grid-cols-2"}`}>
                                <div>
                                    <p className="uppercase tracking-wide">Location</p>
                                    <p className="mt-1 break-words text-foreground">
                                        {formatDeliveryLocation(order)}
                                    </p>
                                </div>
                                <div>
                                    <p className="uppercase tracking-wide">Date</p>
                                    <p className="mt-1 whitespace-nowrap text-foreground">{formatToCentralTime(order.created_at, "MMM d, yyyy")}</p>
                                </div>
                                {isExpanded && (
                                    <div className="col-span-2 mt-1 flex items-center justify-between gap-3">
                                        <p className="text-foreground">Tap to collapse or open the order detail page.</p>
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            onClick={(event) => {
                                                event.stopPropagation();
                                                navigateToOrder(getOrderNumber(order));
                                            }}
                                        >
                                            Open details
                                        </Button>
                                    </div>
                                )}
                            </div>
                        </div>
                    );
                })}
            </div>

            <div className="hidden md:block overflow-x-auto ios-scroll">
                <Table className="min-w-[900px]">
                    <TableHeader className="sticky top-0 z-20 bg-muted/40">
                        <TableRow>
                            <TableHead className="w-[220px] lg:w-[260px]" aria-sort={sortKey === "id" ? sortDir === "asc" ? "ascending" : "descending" : "none"}>
                                <button type="button" onClick={() => toggleSort("id")} className="flex items-center gap-2 text-xs font-semibold text-muted-foreground hover:text-foreground">
                                    Order ID
                                    <ArrowUpDown className="h-3.5 w-3.5" />
                                </button>
                            </TableHead>
                            <TableHead aria-sort={sortKey === "recipient" ? sortDir === "asc" ? "ascending" : "descending" : "none"}>
                                <button type="button" onClick={() => toggleSort("recipient")} className="flex items-center gap-2 text-xs font-semibold text-muted-foreground hover:text-foreground">
                                    Recipient
                                    <ArrowUpDown className="h-3.5 w-3.5" />
                                </button>
                            </TableHead>
                            <TableHead aria-sort={sortKey === "location" ? sortDir === "asc" ? "ascending" : "descending" : "none"}>
                                <button type="button" onClick={() => toggleSort("location")} className="flex items-center gap-2 text-xs font-semibold text-muted-foreground hover:text-foreground">
                                    Location
                                    <ArrowUpDown className="h-3.5 w-3.5" />
                                </button>
                            </TableHead>
                            <TableHead className="whitespace-nowrap" aria-sort={sortKey === "date" ? sortDir === "asc" ? "ascending" : "descending" : "none"}>
                                <button type="button" onClick={() => toggleSort("date")} className="flex items-center gap-2 whitespace-nowrap text-xs font-semibold text-muted-foreground hover:text-foreground">
                                    Order Date
                                    <ArrowUpDown className="h-3.5 w-3.5" />
                                </button>
                            </TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {sortedOrders.map((order, index) => (
                            <TableRow key={order.id || order.inflow_order_id || `${order.created_at || "order"}-${index}`} className="hover:bg-muted/30 transition-colors">
                                <TableCell className="min-w-0 break-words">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <Button
                                            variant="link"
                                            disabled={loading}
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                navigateToOrder(getOrderNumber(order));
                                            }}
                                            className={`h-auto min-h-0 p-0 font-normal text-foreground/90 hover:text-foreground ${loading ? "opacity-75 cursor-not-allowed" : ""}`}
                                        >
                                            {order.inflow_order_id || order.id}
                                        </Button>
                                        <StatusBadge
                                            status={order.status}
                                            className="h-6 px-2 py-0 text-[10px] font-medium uppercase tracking-wide"
                                        />
                                        {getOrderLegLabel(order) ? (
                                            <div className="basis-full">
                                                <Badge
                                                    variant={getOrderLegLabel(order) === "Remainder leg" ? "warning" : "secondary"}
                                                    className="text-[10px] uppercase tracking-wide"
                                                >
                                                    {getOrderLegLabel(order)}
                                                </Badge>
                                            </div>
                                        ) : null}
                                    </div>
                                </TableCell>
                                <TableCell className="break-words">{order.recipient_name || "N/A"}</TableCell>
                                <TableCell className="break-words">{formatDeliveryLocation(order)}</TableCell>
                                <TableCell className="whitespace-nowrap">{formatToCentralTime(order.created_at, "MMM d, yyyy")}</TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </div>
        </div>
    );
}
