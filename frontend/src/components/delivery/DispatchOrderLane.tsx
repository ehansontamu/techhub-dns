import { AlertTriangle } from "lucide-react";

import type { Order } from "../../types/order";
import { formatDeliveryLocation } from "../../utils/location";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";

type DispatchOrderLaneProps = {
  title: string;
  description: string;
  orders: Order[];
  selectedOrderIds: Set<string>;
  emptyText: string;
  onToggleOrder: (orderId: string) => void;
  onViewOrder: (orderId: string) => void;
};

export default function DispatchOrderLane({
  title,
  description,
  orders,
  selectedOrderIds,
  emptyText,
  onToggleOrder,
  onViewOrder,
}: DispatchOrderLaneProps) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-foreground">{title}</h3>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
        <Badge variant="secondary">{orders.length}</Badge>
      </div>

      {orders.length === 0 ? (
        <div className="rounded-md border border-dashed border-border/70 px-3 py-6 text-center text-xs text-muted-foreground">
          {emptyText}
        </div>
      ) : (
        <div className="divide-y divide-border/60 rounded-md border border-border/60 bg-background">
          {orders.map((order) => {
            const isSelected = selectedOrderIds.has(order.id);
            const hasPartialPick = Boolean(order.pick_status && !order.pick_status.is_fully_picked);

            return (
              <div key={order.id} className="px-3 py-3 first:pt-3 last:pb-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1">
                      <button
                        type="button"
                        className="inline-flex min-h-[44px] items-center rounded-md px-1.5 text-left text-sm font-medium text-foreground hover:underline"
                        onClick={() => onViewOrder(order.id)}
                      >
                        {order.inflow_order_id}
                      </button>
                      <div className="text-xs text-muted-foreground">{order.recipient_name || "N/A"}</div>
                      <div className="text-xs text-muted-foreground">{formatDeliveryLocation(order)}</div>
                    </div>
                    <Button
                      size="sm"
                      variant={isSelected ? "secondary" : "outline"}
                      onClick={() => onToggleOrder(order.id)}
                      className="h-8"
                    >
                      {isSelected ? "Added" : "Add to Delivery"}
                    </Button>
                  </div>

                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-muted-foreground">Deliverer: {order.assigned_deliverer || "Unassigned"}</span>
                  {hasPartialPick ? (
                    <Badge
                      variant="warning"
                      className="gap-1"
                      title={`Partial pick: ${order.pick_status?.total_picked}/${order.pick_status?.total_ordered} items picked`}
                    >
                      <AlertTriangle className="h-3 w-3" />
                      Partial Pick
                    </Badge>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
