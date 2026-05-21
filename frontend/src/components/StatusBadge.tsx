import { OrderStatus, OrderStatusDisplayNames } from "../types/order";
import { Badge } from "./ui/badge";
import { cn } from "../lib/utils";

interface StatusBadgeProps {
    status: OrderStatus;
    className?: string;
}

const statusVariants: Record<OrderStatus, "default" | "secondary" | "destructive" | "success" | "warning" | "outline"> = {
    [OrderStatus.PICKED]: "secondary",
    [OrderStatus.QA]: "default",
    [OrderStatus.PRE_DELIVERY]: "default",
    [OrderStatus.IN_DELIVERY]: "warning",
    [OrderStatus.SHIPPING]: "outline",
    [OrderStatus.DELIVERED]: "success",
    [OrderStatus.ISSUE]: "destructive",
};

export default function StatusBadge({ status, className }: StatusBadgeProps) {
    return (
        <Badge variant={statusVariants[status]} className={cn("whitespace-nowrap", className)}>
            {OrderStatusDisplayNames[status]}
        </Badge>
    );
}
