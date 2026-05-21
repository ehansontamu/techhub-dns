import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { AlertCircle, ArrowDown, ArrowLeft, ArrowUp, CheckCircle, Clock, Package, Truck, User } from "lucide-react";

import { deliveryRunsApi } from "../api/deliveryRuns";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../components/ui/dialog";
import { Input } from "../components/ui/input";
import { useDeliveryRun } from "../hooks/useDeliveryRun";
import { OrderStatus } from "../types/order";
import { isValidOrderId } from "../utils/orderIds";
import { extractApiErrorMessage } from "../utils/apiErrors";
import { formatToCentralTime } from "../utils/timezone";

type DeliveryDetailLocationState = {
  from?: string;
};

function formatVehicleName(vehicle: string): string {
  return vehicle
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function formatDateTime(dateString: string | null | undefined): string {
  if (!dateString) return "Not started";
  return formatToCentralTime(dateString);
}

function getRunStatusVariant(status: string) {
  switch (status.toLowerCase()) {
    case "active":
      return "success" as const;
    case "completed":
      return "secondary" as const;
    case "cancelled":
      return "destructive" as const;
    default:
      return "outline" as const;
  }
}

function getOrderStatusVariant(status: string) {
  switch (status.toLowerCase()) {
    case "pre_delivery":
      return "warning" as const;
    case "in_delivery":
      return "secondary" as const;
    case "delivered":
      return "success" as const;
    case "issue":
      return "destructive" as const;
    default:
      return "outline" as const;
  }
}



export default function DeliveryRunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const { run, loading, error, refetch } = useDeliveryRun(runId);
  const [finishing, setFinishing] = useState(false);
  const [recalling, setRecalling] = useState(false);
  const [errorDialogOpen, setErrorDialogOpen] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [recallDialogOpen, setRecallDialogOpen] = useState(false);
  const [recallReason, setRecallReason] = useState("");
  const [recallOrderId, setRecallOrderId] = useState<string | null>(null);
  const [reorderMode, setReorderMode] = useState(false);
  const [orderedOrderIds, setOrderedOrderIds] = useState<string[]>([]);
  const [savingOrder, setSavingOrder] = useState(false);

  const locationState = location.state as DeliveryDetailLocationState | null;
  const backTo = locationState?.from ?? "/delivery/dispatch";

  const allOrdersDelivered =
    run?.orders.every((order) => order.status.toLowerCase() === "delivered") ?? false;
  const runIsActive = run?.status.toLowerCase() === "active";

  const blockingOrders = useMemo(
    () => run?.orders.filter((order) => order.status.toLowerCase() !== "delivered") ?? [],
    [run?.orders]
  );

  const pendingSignatureOrders = useMemo(
    () => blockingOrders.filter((order) => order.status === OrderStatus.IN_DELIVERY),
    [blockingOrders]
  );

  const nonSignableBlockingOrders = useMemo(
    () => blockingOrders.filter((order) => order.status !== OrderStatus.IN_DELIVERY),
    [blockingOrders]
  );

  useEffect(() => {
    if (!run) {
      return;
    }

    const nextIds = run.orders
      .slice()
      .sort((a, b) => {
        const aSeq = a.delivery_sequence ?? Number.MAX_SAFE_INTEGER;
        const bSeq = b.delivery_sequence ?? Number.MAX_SAFE_INTEGER;
        if (aSeq === bSeq) {
          return (a.inflow_order_id || a.id).localeCompare(b.inflow_order_id || b.id);
        }
        return aSeq - bSeq;
      })
      .map((order) => order.id);

    setOrderedOrderIds(nextIds);
  }, [run]);

  const orderedOrders = useMemo(() => {
    if (!run) {
      return [];
    }

    const orderMap = new Map(run.orders.map((order) => [order.id, order]));
    const ordered = orderedOrderIds.map((id) => orderMap.get(id)).filter((order): order is NonNullable<typeof order> => Boolean(order));

    if (ordered.length !== run.orders.length) {
      return run.orders;
    }
    return ordered;
  }, [run, orderedOrderIds]);

  const handleCompleteRun = async () => {
    if (!run || !allOrdersDelivered) return;

    setFinishing(true);
    try {
      await deliveryRunsApi.finishRun(run.id, true, run.updated_at ?? undefined);
      toast.success("Delivery run completed");
      await refetch();
    } catch (error: unknown) {
      setErrorMessage(extractApiErrorMessage(error, "Failed to complete delivery. Ensure all orders are delivered first."));
      setErrorDialogOpen(true);
      await refetch();
    } finally {
      setFinishing(false);
    }
  };

  const openRecallDialog = (orderId: string) => {
    setRecallOrderId(orderId);
    setRecallReason("");
    setRecallDialogOpen(true);
  };

  const handleRecallOrder = async () => {
    if (!run || !recallOrderId) {
      return;
    }

    const reason = recallReason.trim();
    if (!reason) {
      toast.error("Recall reason is required");
      return;
    }

    setRecalling(true);
    try {
      await deliveryRunsApi.recallOrder(run.id, recallOrderId, reason, run.updated_at ?? undefined);
      toast.success("Order recalled from run");
      setRecallDialogOpen(false);
      setRecallOrderId(null);
      setRecallReason("");
      await refetch();
    } catch (error: unknown) {
      setErrorMessage(extractApiErrorMessage(error, "Action failed. Refresh and try again."));
      setErrorDialogOpen(true);
      await refetch();
    } finally {
      setRecalling(false);
    }
  };

  const moveOrder = (orderId: string, direction: "up" | "down") => {
    setOrderedOrderIds((previous) => {
      const index = previous.indexOf(orderId);
      if (index < 0) return previous;
      const targetIndex = direction === "up" ? index - 1 : index + 1;
      if (targetIndex < 0 || targetIndex >= previous.length) return previous;

      const next = previous.slice();
      const [moved] = next.splice(index, 1);
      next.splice(targetIndex, 0, moved);
      return next;
    });
  };

  const hasReorderChanges = useMemo(() => {
    if (!run || orderedOrderIds.length !== run.orders.length) {
      return false;
    }

    const currentIds = run.orders
      .slice()
      .sort((a, b) => {
        const aSeq = a.delivery_sequence ?? Number.MAX_SAFE_INTEGER;
        const bSeq = b.delivery_sequence ?? Number.MAX_SAFE_INTEGER;
        if (aSeq === bSeq) {
          return (a.inflow_order_id || a.id).localeCompare(b.inflow_order_id || b.id);
        }
        return aSeq - bSeq;
      })
      .map((order) => order.id);

    return currentIds.some((id, index) => id !== orderedOrderIds[index]);
  }, [run, orderedOrderIds]);

  const handleSaveOrder = async () => {
    if (!run || !hasReorderChanges) {
      setReorderMode(false);
      return;
    }

    setSavingOrder(true);
    try {
      await deliveryRunsApi.reorderOrders(run.id, orderedOrderIds, run.updated_at ?? undefined);
      toast.success("Run order updated");
      setReorderMode(false);
      await refetch();
    } catch (error: unknown) {
      setErrorMessage(extractApiErrorMessage(error, "Action failed. Refresh and try again."));
      setErrorDialogOpen(true);
      await refetch();
    } finally {
      setSavingOrder(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="text-sm text-muted-foreground">Loading delivery run details...</div>
      </div>
    );
  }

  if (error || !run) {
    return (
      <div className="py-12 text-center">
        <div className="mb-4 text-destructive">{error || "Delivery run not found"}</div>
        <Button variant="outline" onClick={() => navigate(backTo)}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Delivery
        </Button>
      </div>
    );
  }

  return (
    <div className="container mx-auto p-4 sm:p-6 space-y-6">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex items-start gap-3">
          <Button variant="outline" size="sm" onClick={() => navigate(backTo)}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back
          </Button>
          <div>
            <h2 className="text-xl font-semibold">{run.name}</h2>
            <p className="text-sm text-muted-foreground">Run ID: {run.id}</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => void refetch()}>
            Refresh Status
          </Button>
          {runIsActive && run.orders.length > 1 ? (
            reorderMode ? (
              <>
                <Button
                  variant="outline"
                  onClick={() => {
                    setReorderMode(false);
                    setOrderedOrderIds(
                      run.orders
                        .slice()
                        .sort((a, b) => {
                          const aSeq = a.delivery_sequence ?? Number.MAX_SAFE_INTEGER;
                          const bSeq = b.delivery_sequence ?? Number.MAX_SAFE_INTEGER;
                          if (aSeq === bSeq) {
                            return (a.inflow_order_id || a.id).localeCompare(b.inflow_order_id || b.id);
                          }
                          return aSeq - bSeq;
                        })
                        .map((order) => order.id)
                    );
                  }}
                  disabled={savingOrder}
                >
                  Cancel Reorder
                </Button>
                <Button onClick={() => void handleSaveOrder()} disabled={savingOrder || !hasReorderChanges}>
                  {savingOrder ? "Saving..." : "Save Order"}
                </Button>
              </>
            ) : (
              <Button variant="outline" onClick={() => setReorderMode(true)}>
                Reorder Stops
              </Button>
            )
          ) : null}
          {runIsActive ? (
            <Button
              onClick={handleCompleteRun}
              disabled={!allOrdersDelivered || finishing || savingOrder}
              className="bg-emerald-600 hover:bg-emerald-700 disabled:bg-muted disabled:text-muted-foreground"
            >
              <CheckCircle className="mr-2 h-4 w-4" />
              {finishing ? "Completing..." : "Complete Run"}
            </Button>
          ) : null}
        </div>
      </div>

            <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
        <div className="space-y-4">
          <h3 className="text-xl font-semibold tracking-tight">Completion Readiness</h3>
          <p className="text-sm text-muted-foreground">
            Resolve blockers below before completing this run.
          </p>
        </div>
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <div className="rounded-lg border border-border/70 p-3">
              <div className="text-xs text-muted-foreground">Blocking orders</div>
              <div className="text-lg font-semibold text-foreground">{blockingOrders.length}</div>
            </div>
            <div className="rounded-lg border border-border/70 p-3">
              <div className="text-xs text-muted-foreground">Pending signature</div>
              <div className="text-lg font-semibold text-foreground">{pendingSignatureOrders.length}</div>
            </div>
            <div className="rounded-lg border border-border/70 p-3">
              <div className="text-xs text-muted-foreground">Non-signable blockers</div>
              <div className="text-lg font-semibold text-foreground">{nonSignableBlockingOrders.length}</div>
            </div>
          </div>

          {blockingOrders.length === 0 ? (
            <div className="rounded-lg border border-emerald-300/40 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
              All orders are delivered. This run is ready for completion.
            </div>
          ) : nonSignableBlockingOrders.length > 0 ? (
            <div className="space-y-2">
              {nonSignableBlockingOrders.map((order) => {
                const orderLabel = order.inflow_order_id || order.id.slice(0, 8);

                return (
                  <div
                    key={`blocker-${order.id}`}
                    className="flex flex-col gap-2 rounded-lg border border-amber-300/40 bg-amber-50/60 p-3 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div>
                      <div className="text-sm font-medium text-foreground">Order {orderLabel}</div>
                      <div className="text-xs text-muted-foreground">
                        Status: {order.status.toLowerCase().replace("_", " ")}
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      {isValidOrderId(order.id) ? (
                        <Link to={`/orders/${order.inflow_order_id || order.id}`} state={{ fromPath: location.pathname }}>
                          <Button variant="outline" size="sm">
                            View Order
                          </Button>
                        </Link>
                      ) : (
                        <Button variant="outline" size="sm" disabled>
                          View Order
                        </Button>
                      )}
                      <Badge variant="warning">Move to In Delivery first</Badge>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="rounded-lg border border-amber-300/40 bg-amber-50/60 px-3 py-2 text-sm text-amber-900">
              {pendingSignatureOrders.length} order{pendingSignatureOrders.length !== 1 ? "s" : ""} still need
              signature or proof. Use the run list below to sign them.
            </div>
          )}
        </div>
      </section>

      <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
        <div className="flex flex-col gap-4">
          <h3 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
            <Truck className="h-5 w-5" />
            Run Summary
          </h3>
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-4">
            <div className="flex items-center gap-3">
              <User className="h-5 w-5 text-muted-foreground" />
              <div>
                <div className="text-sm text-muted-foreground">Runner</div>
                <div className="font-medium">{run.runner}</div>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <Truck className="h-5 w-5 text-muted-foreground" />
              <div>
                <div className="text-sm text-muted-foreground">Vehicle</div>
                <div className="font-medium">{formatVehicleName(run.vehicle)}</div>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <div className="flex h-5 w-5 items-center justify-center rounded-full bg-current text-xs text-muted-foreground">
                *
              </div>
              <div>
                <div className="text-sm text-muted-foreground">Status</div>
                <Badge variant={getRunStatusVariant(run.status)}>{run.status.toLowerCase()}</Badge>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <Package className="h-5 w-5 text-muted-foreground" />
              <div>
                <div className="text-sm text-muted-foreground">Orders</div>
                <div className="font-medium">{run.orders.length}</div>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-6 border-t pt-6 md:grid-cols-2">
            <div className="flex items-center gap-3">
              <Clock className="h-5 w-5 text-muted-foreground" />
              <div>
                <div className="text-sm text-muted-foreground">Started</div>
                <div className="font-medium">{formatDateTime(run.start_time)}</div>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <Clock className="h-5 w-5 text-muted-foreground" />
              <div>
                <div className="text-sm text-muted-foreground">Completed</div>
                <div className="font-medium">{formatDateTime(run.end_time)}</div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
        <div className="space-y-4">
          <h3 className="text-xl font-semibold tracking-tight">Orders in This Run</h3>
          <p className="text-sm text-muted-foreground">
            {run.orders.length} order{run.orders.length !== 1 ? "s" : ""} assigned to this delivery run
            {reorderMode ? " - reorder mode enabled" : ""}
          </p>
        </div>
        <div className="mt-4">
          {run.orders.length === 0 ? (
            <div className="py-8 text-center text-muted-foreground">No orders assigned to this run</div>
          ) : (
            <div className="space-y-3">
              {orderedOrders.map((order, index) => (
                <div
                  key={order.id}
                  className={`flex flex-col gap-3 rounded-lg border p-4 transition-colors sm:flex-row sm:items-center sm:justify-between ${order.status.toLowerCase() !== "delivered" ? "border-accent/20 bg-accent/5" : "hover:bg-muted/50"}`}
                >
                  <div>
                    <div className="flex items-center gap-2">
                      <Badge variant="outline">Stop {index + 1}</Badge>
                      <div className="font-medium">Order {order.inflow_order_id || order.id.slice(0, 8)}</div>
                    </div>
                    {order.recipient_name ? (
                      <div className="text-sm text-muted-foreground">{order.recipient_name}</div>
                    ) : null}
                    {order.delivery_location ? (
                      <div className="mt-0.5 flex items-center gap-1 text-sm text-muted-foreground">
                        <span className="font-medium">Location:</span> {order.delivery_location}
                      </div>
                    ) : null}
                  </div>

                  <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                    <Badge variant={getOrderStatusVariant(order.status)}>
                      {order.status.toLowerCase().replace("_", " ")}
                    </Badge>

                    {order.status === OrderStatus.IN_DELIVERY ? (
                      <span className="text-xs font-medium text-accent">Pending proof/signature</span>
                    ) : order.status.toLowerCase() !== "delivered" ? (
                      <span className="text-xs font-medium text-accent">Not yet in delivery</span>
                    ) : null}

                    {isValidOrderId(order.id) ? (
                      <Link to={`/orders/${order.inflow_order_id || order.id}`} state={{ fromPath: location.pathname }}>
                        <Button variant="outline" size="sm">
                          View Details
                        </Button>
                      </Link>
                    ) : (
                      <Button variant="outline" size="sm" disabled>
                        View Details
                      </Button>
                    )}

                    {reorderMode ? (
                      <>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => moveOrder(order.id, "up")}
                          disabled={index === 0 || savingOrder}
                        >
                          <ArrowUp className="mr-1 h-4 w-4" />
                          Up
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => moveOrder(order.id, "down")}
                          disabled={index === orderedOrders.length - 1 || savingOrder}
                        >
                          <ArrowDown className="mr-1 h-4 w-4" />
                          Down
                        </Button>
                      </>
                    ) : null}

                    {!reorderMode && order.status === OrderStatus.IN_DELIVERY ? (
                      <>
                        <Link to={`/document-signing?orderId=${order.id}&returnTo=/delivery/runs/${run.id}`}>
                          <Button variant="default" size="sm" className="bg-emerald-600 hover:bg-emerald-700">
                            Sign Document
                          </Button>
                        </Link>
                        <Button variant="outline" size="sm" onClick={() => openRecallDialog(order.id)}>
                          Recall
                        </Button>
                      </>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <Dialog open={errorDialogOpen} onOpenChange={setErrorDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-destructive">
              <AlertCircle className="h-5 w-5" />
              Cannot Complete Delivery
            </DialogTitle>
            <DialogDescription className="pt-2">{errorMessage}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button onClick={() => setErrorDialogOpen(false)}>OK</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={recallDialogOpen} onOpenChange={setRecallDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Recall Order From Run</DialogTitle>
            <DialogDescription>
              This marks the order as Issue and removes it from the active run so you can finish remaining deliveries.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2">
            <label htmlFor="recall-reason" className="text-sm font-medium text-foreground">
              Reason
            </label>
            <Input
              id="recall-reason"
              value={recallReason}
              onChange={(event) => setRecallReason(event.target.value)}
              placeholder="Undeliverable details (recipient unavailable, address issue, etc.)"
              maxLength={500}
              disabled={recalling}
            />
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setRecallDialogOpen(false)} disabled={recalling}>
              Cancel
            </Button>
            <Button onClick={() => void handleRecallOrder()} disabled={recalling || !recallReason.trim()}>
              {recalling ? "Recalling..." : "Confirm Recall"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
