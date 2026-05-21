import { useRef, useState, useLayoutEffect, useCallback } from "react";
import { OrderStatus, OrderStatusDisplayNames } from "../types/order";

// Special filter type that can be a single status, array of statuses, or null (all)
export type StatusFilter = OrderStatus | OrderStatus[] | null;

interface FiltersProps {
    status: StatusFilter;
    onStatusChange: (status: StatusFilter) => void;
    search: string;
    onSearchChange: (search: string) => void;
    loading?: boolean;
}

const STATUS_TABS: { label: string; value: StatusFilter }[] = [
    { label: "All", value: null },
    { label: "Picked + QA", value: [OrderStatus.PICKED, OrderStatus.QA] },
    { label: OrderStatusDisplayNames[OrderStatus.PRE_DELIVERY], value: OrderStatus.PRE_DELIVERY },
    { label: OrderStatusDisplayNames[OrderStatus.IN_DELIVERY], value: OrderStatus.IN_DELIVERY },
    { label: OrderStatusDisplayNames[OrderStatus.SHIPPING], value: OrderStatus.SHIPPING },
    { label: OrderStatusDisplayNames[OrderStatus.DELIVERED], value: OrderStatus.DELIVERED },
    { label: OrderStatusDisplayNames[OrderStatus.ISSUE], value: OrderStatus.ISSUE },
];

export default function Filters({
    status,
    onStatusChange,
    search,
    onSearchChange,
    loading = false,
}: FiltersProps) {
    const tabRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
    const [indicatorStyle, setIndicatorStyle] = useState({ left: 0, width: 0 });
    const [indicatorReady, setIndicatorReady] = useState(false);

    // Helper to check if current filter matches a tab
    const isActiveTab = (tabValue: StatusFilter) => {
        if (tabValue === null && status === null) return true;
        if (Array.isArray(tabValue) && Array.isArray(status)) {
            return tabValue.length === status.length && tabValue.every(v => status.includes(v));
        }
        return tabValue === status;
    };

    const updateIndicatorPosition = useCallback(() => {
        const activeTab = STATUS_TABS.find((tab) => isActiveTab(tab.value));
        if (!activeTab) {
            setIndicatorReady(false);
            return;
        }

        const activeElement = tabRefs.current.get(activeTab.label);
        if (!activeElement) {
            setIndicatorReady(false);
            return;
        }
        setIndicatorStyle({
            left: activeElement.offsetLeft,
            width: activeElement.offsetWidth,
        });
        setIndicatorReady(true);
    }, [status]);

    useLayoutEffect(() => {
        updateIndicatorPosition();

        const handleResize = () => {
            updateIndicatorPosition();
        };

        window.addEventListener("resize", handleResize);
        return () => {
            window.removeEventListener("resize", handleResize);
        };
    }, [updateIndicatorPosition]);

    return (
        <div className="flex flex-col sm:flex-row gap-4 sm:items-end sm:justify-between">
            <div
                className="relative flex gap-1 border-b overflow-x-auto whitespace-nowrap ios-scroll no-scrollbar -mx-2 px-2"
            >
                {/* Animated indicator */}
                <div
                    className={`absolute bottom-0 h-0.5 bg-primary ${indicatorReady ? "transition-[left,width,opacity] duration-200 ease-out opacity-100" : "opacity-0"}`}
                    style={{
                        left: indicatorStyle.left,
                        width: indicatorStyle.width,
                    }}
                />
                {STATUS_TABS.map((tab) => (
                    <button
                        key={tab.label}
                        ref={(el) => {
                            if (el) tabRefs.current.set(tab.label, el);
                        }}
                        type="button"
                        onClick={() => onStatusChange(tab.value)}
                        disabled={loading}
                        className={`px-3 py-2 text-sm -mb-px transition-colors duration-150 flex-shrink-0 ${loading ? "opacity-75 cursor-not-allowed" : ""} ${isActiveTab(tab.value)
                            ? "text-primary font-medium"
                            : "text-muted-foreground hover:text-foreground"
                            }`}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            <div className="w-full sm:w-auto sm:min-w-[260px] lg:min-w-[300px]">
                <input
                    id="orders-search"
                    name="ordersSearch"
                    type="text"
                    placeholder="Search orders"
                    value={search}
                    onChange={(e) => onSearchChange(e.target.value)}
                    aria-label="Search"
                    aria-busy={loading}
                    className={`w-full px-3 py-2 text-sm border rounded-md focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent ${loading ? "opacity-90" : ""}`}
                />
            </div>
        </div>
    );
}
