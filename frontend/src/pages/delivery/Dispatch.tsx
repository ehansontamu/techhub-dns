import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type PointerEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import axios from "axios";
import { AlertTriangle, ChevronDown, ChevronUp, Truck } from "lucide-react";
import { toast } from "sonner";

import { deliveryRunsApi } from "../../api/deliveryRuns";
import { ordersApi } from "../../api/orders";
import {
  type Vehicle,
  type VehicleStatusItem,
  vehicleCheckoutsApi,
} from "../../api/vehicleCheckouts";
import DispatchOrderLane from "../../components/delivery/DispatchOrderLane";
import {
  DELIVERY_RUN_PRIORITY_OPTIONS,
  formatTimeSince,
  getPriorityActionSelection,
  type DeliveryRunPriorityPurpose,
} from "../../components/delivery/vehiclePriority";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent } from "../../components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Input } from "../../components/ui/input";
import { useAuth } from "../../contexts/AuthContext";
import { useDeliveryRuns } from "../../hooks/useDeliveryRuns";
import { isValidOrderId } from "../../utils/orderIds";
import { getUserDisplayName } from "../../utils/userDisplay";
import { useOrdersWebSocket } from "../../hooks/useOrdersWebSocket";
import { useVehicleStatuses } from "../../hooks/useVehicleStatuses";
import type { User } from "../../contexts/AuthContext";
import type { Order } from "../../types/order";
import { OrderStatus } from "../../types/order";
import { formatDeliveryLocation } from "../../utils/location";

type VehicleDescriptor = {
  id: Vehicle;
  label: string;
  icon: string;
};

type DispatchTarget = Vehicle | "pickup";

type DispatchTargetDescriptor = {
  id: DispatchTarget;
  label: string;
};

const VEHICLES: VehicleDescriptor[] = [
  { id: "van", label: "Van", icon: "" },
  { id: "golf_cart", label: "Golf Cart", icon: "" },
];

const DISPATCH_TARGETS: DispatchTargetDescriptor[] = [
  { id: "van", label: "Van" },
  { id: "golf_cart", label: "Golf Cart" },
  { id: "pickup", label: "Pickup" },
];

function getApiErrorMessage(error: unknown): string {
  if (!axios.isAxiosError(error)) return "Request failed";
  const message = error.response?.data?.error?.message;
  if (typeof message === "string" && message.trim()) return message;
  return "Request failed";
}

function checkedOutByCurrentUser(status: VehicleStatusItem, user: User | null): boolean {
  if (!status.checked_out) return false;

  const checkedOutByUserId = status.checked_out_by_user_id;
  if (checkedOutByUserId && user?.id) {
    return checkedOutByUserId === user.id;
  }

  const checkedOutBy = status.checked_out_by;
  if (!checkedOutBy) return false;

  const candidates = [getUserDisplayName(user, ""), user?.email].filter(
    (value): value is string => typeof value === "string" && Boolean(value.trim())
  );
  return candidates.some((candidate) => candidate === checkedOutBy);
}

function formatRunLabel(deliveryRunId: string | undefined): string {
  if (!deliveryRunId) return "No run";
  if (deliveryRunId.length <= 8) return `Run ${deliveryRunId}`;
  return `Run ${deliveryRunId.slice(0, 8)}`;
}

function formatDispatchTargetLabel(target: string): string {
  return target
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function formatVehicleStatus(status: VehicleStatusItem): string {
  if (status.delivery_run_active) return "Active run";
  if (!status.checked_out) return "Available";
  if (status.checkout_type === "delivery_run") return "On delivery";
  const purpose = status.purpose?.trim();
  return purpose ? purpose : "Checked out";
}

function getVehicleStatusVariant(
  status: VehicleStatusItem
): "success" | "warning" | "secondary" {
  if (!status.checked_out && !status.delivery_run_active) return "success";
  if (status.delivery_run_active) return "warning";
  return "secondary";
}

function getRunStatusVariant(status: string) {
  const normalized = status.toLowerCase().replace(/\s+/g, "_");

  if (/(cancel|canceled|cancelled|fail|failed|error)/.test(normalized)) return "destructive" as const;
  if (/(complete|completed|done|delivered)/.test(normalized)) return "secondary" as const;
  if (/(pending|queued|waiting|paused)/.test(normalized)) return "warning" as const;
  if (/(active|live|in_progress|inprogress|en_route|on_route|running)/.test(normalized)) return "success" as const;

  return "outline" as const;
}

export default function Dispatch() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user } = useAuth();
  const { orders: websocketOrders } = useOrdersWebSocket();
  const { statusByVehicle, isLoading: statusesLoading, refresh: refreshStatuses } = useVehicleStatuses();
  const { runs: activeDeliveryRuns, loading: activeRunsLoading, refetch: refreshDeliveryRuns } = useDeliveryRuns();

  const [preDeliveryOrders, setPreDeliveryOrders] = useState<Order[]>([]);
  const [inDeliveryOrders, setInDeliveryOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedOrderIds, setSelectedOrderIds] = useState<string[]>([]);
  const [draggingOrderId, setDraggingOrderId] = useState<string | null>(null);
  const [dragOverOrderId, setDragOverOrderId] = useState<string | null>(null);
  const touchDragStateRef = useRef<
    { orderId: string; pointerId: number; startClientX: number; startClientY: number; hasMoved: boolean } | null
  >(null);
  const [activeVehicleAction, setActiveVehicleAction] = useState<DispatchTarget | null>(null);
  const [selectedDispatchTarget, setSelectedDispatchTarget] = useState<DispatchTarget | null>(null);
  const [selectedPurpose, setSelectedPurpose] = useState<DeliveryRunPriorityPurpose | null>(null);

  const [partialPickDialogOpen, setPartialPickDialogOpen] = useState(false);
  const [partialPickOrders, setPartialPickOrders] = useState<Order[]>([]);
  const [pendingStartTarget, setPendingStartTarget] = useState<DispatchTarget | null>(null);
  const [pendingStartPriority, setPendingStartPriority] = useState<DeliveryRunPriorityPurpose | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [activeRunsExpanded, setActiveRunsExpanded] = useState(true);

  const loadOrders = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [pre, inDelivery] = await Promise.all([
        ordersApi.getOrders({ status: OrderStatus.PRE_DELIVERY }).then((r) => r.items),
        ordersApi.getOrders({ status: OrderStatus.IN_DELIVERY }).then((r) => r.items),
      ]);
      setPreDeliveryOrders(pre);
      setInDeliveryOrders(inDelivery);
    } catch (error) {
      const message = getApiErrorMessage(error);
      setLoadError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadOrders();
    void refreshStatuses();
  }, [loadOrders, refreshStatuses, websocketOrders]);

  // Auto-select available vehicle
  useEffect(() => {
    if (selectedDispatchTarget) return;
    const available = VEHICLES.find(
      (v) => !statusByVehicle[v.id].checked_out && !statusByVehicle[v.id].delivery_run_active
    );
    if (available) setSelectedDispatchTarget(available.id);
  }, [selectedDispatchTarget, statusByVehicle]);

  const selectedOrdersById = useMemo(
    () => new Map(preDeliveryOrders.map((order) => [order.id, order])),
    [preDeliveryOrders]
  );

  const selectedOrderIdSet = useMemo(() => new Set(selectedOrderIds), [selectedOrderIds]);

  const selectedOrdersList = useMemo(
    () => selectedOrderIds.map((orderId) => selectedOrdersById.get(orderId)).filter((order): order is Order => Boolean(order)),
    [preDeliveryOrders, selectedOrderIds, selectedOrdersById]
  );

  useEffect(() => {
    setSelectedOrderIds((previous) => {
      if (previous.length === 0) return previous;
      const availableIds = new Set(preDeliveryOrders.map((order) => order.id));
      const next = previous.filter((orderId) => availableIds.has(orderId));
      return next.length === previous.length ? previous : next;
    });
  }, [preDeliveryOrders]);

  useEffect(() => {
    if (selectedDispatchTarget === "pickup") return;
    if (selectedOrderIds.length > 0 && !selectedPurpose) {
      setSelectedPurpose("Delivery");
    }
  }, [selectedDispatchTarget, selectedOrderIds.length, selectedPurpose]);

  const preDeliveryFilteredOrders = useMemo(() => {
    const search = searchTerm.trim().toLowerCase();
    if (!search) return preDeliveryOrders;

    return preDeliveryOrders.filter((order) => {
      const haystack = [
        order.inflow_order_id,
        order.recipient_name,
        order.assigned_deliverer,
        formatDeliveryLocation(order),
      ]
        .filter((value): value is string => typeof value === "string" && value.length > 0)
        .join(" ")
        .toLowerCase();

      return haystack.includes(search);
    });
  }, [preDeliveryOrders, searchTerm]);

  const needsAttentionOrders = useMemo(
    () =>
      preDeliveryFilteredOrders.filter(
        (order) => Boolean(order.pick_status && !order.pick_status.is_fully_picked) && !selectedOrderIdSet.has(order.id)
      ),
    [preDeliveryFilteredOrders, selectedOrderIdSet]
  );

  const readyOrders = useMemo(
    () =>
      preDeliveryFilteredOrders.filter(
        (order) => (!order.pick_status || order.pick_status.is_fully_picked) && !selectedOrderIdSet.has(order.id)
      ),
    [preDeliveryFilteredOrders, selectedOrderIdSet]
  );

  const activeDeliveryRunGroups = useMemo(() => {
    const ordersById = new Map(inDeliveryOrders.map((order) => [order.id, order]));
    return [...activeDeliveryRuns]
      .sort((left, right) => {
        const leftTime = Date.parse(left.updated_at ?? left.start_time ?? "");
        const rightTime = Date.parse(right.updated_at ?? right.start_time ?? "");
        const safeLeftTime = Number.isNaN(leftTime) ? 0 : leftTime;
        const safeRightTime = Number.isNaN(rightTime) ? 0 : rightTime;
        return safeRightTime - safeLeftTime;
      })
      .map((run) => ({
        run,
        orders: run.order_ids
          .map((orderId) => ordersById.get(orderId))
          .filter((order): order is Order => Boolean(order))
          .sort((a, b) => (a.delivery_sequence ?? 0) - (b.delivery_sequence ?? 0)),
      }));
  }, [activeDeliveryRuns, inDeliveryOrders]);

  const activeDeliveryOrderCount = activeDeliveryRunGroups.reduce((total, group) => total + group.orders.length, 0);

  const allVisibleSelected =
    preDeliveryFilteredOrders.length > 0 && preDeliveryFilteredOrders.every((order) => selectedOrderIdSet.has(order.id));

  const selectedPartialPickCount = useMemo(
    () => selectedOrdersList.filter((order) => order.pick_status && !order.pick_status.is_fully_picked).length,
    [selectedOrdersList]
  );

  // Determine vehicle availability for action bar

  const getStartDisabledReason = useCallback(
    (target: DispatchTarget): string | null => {
      if (target === "pickup") {
        return null;
      }

      const vehicle = target;
      const status = statusByVehicle[vehicle];
      if (statusesLoading) return "Vehicle status is loading";

      if (status.delivery_run_active) {
        return "Vehicle already has an active run";
      }

      if (status.checked_out) {
        if (status.checkout_type === "other") {
          const purpose = status.purpose?.trim();
          const suffix = purpose ? ` (purpose: ${purpose})` : "";
          return `Checked out for Other${suffix}. Check in, then check out again for a Delivery run.`;
        }

        if (!checkedOutByCurrentUser(status, user)) {
          const who = status.checked_out_by;
          return who ? `Checked out by ${who}` : "Checked out by another user";
        }
      }

      return null;
    },
    [statusByVehicle, statusesLoading, user]
  );

  const handleToggleVisible = () => {
    if (allVisibleSelected) {
      setSelectedOrderIds((previous) => {
        const visibleIds = new Set(preDeliveryFilteredOrders.map((order) => order.id));
        return previous.filter((orderId) => !visibleIds.has(orderId));
      });
      return;
    }

    setSelectedOrderIds((previous) => {
      const next = [...previous];
      const existing = new Set(previous);
      for (const order of preDeliveryFilteredOrders) {
        if (!existing.has(order.id)) next.push(order.id);
      }
      return next;
    });
  };

  const handleSelectOrder = (orderId: string) => {
    setSelectedOrderIds((previous) => {
      if (previous.includes(orderId)) {
        return previous.filter((currentId) => currentId !== orderId);
      }
      return [...previous, orderId];
    });
  };

  const handleDragStartOrder = (orderId: string, event: DragEvent<HTMLElement>) => {
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", orderId);
    setDraggingOrderId(orderId);
    setDragOverOrderId(orderId);
  };

  const resolveDropTargetOrderId = useCallback((clientX: number, clientY: number): string | null => {
    const element = document.elementFromPoint(clientX, clientY);
    const orderElement = element?.closest<HTMLElement>("[data-order-id]");
    return orderElement?.dataset.orderId ?? null;
  }, []);

  const clearDragState = useCallback(() => {
    touchDragStateRef.current = null;
    setDraggingOrderId(null);
    setDragOverOrderId(null);
  }, []);

  const handleTouchReorderStart = (orderId: string, event: PointerEvent<HTMLButtonElement>) => {
    if (event.pointerType !== "touch") return;

    event.preventDefault();
    touchDragStateRef.current = {
      orderId,
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      hasMoved: false,
    };
    setDraggingOrderId(orderId);
    setDragOverOrderId(orderId);

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const handleTouchReorderMove = (event: PointerEvent<HTMLButtonElement>) => {
    const touchDragState = touchDragStateRef.current;
    if (!touchDragState || touchDragState.pointerId !== event.pointerId) return;

    const deltaX = event.clientX - touchDragState.startClientX;
    const deltaY = event.clientY - touchDragState.startClientY;
    if (!touchDragState.hasMoved) {
      const distance = Math.hypot(deltaX, deltaY);
      if (distance < 8) {
        return;
      }
      touchDragStateRef.current = { ...touchDragState, hasMoved: true };
    }

    const targetOrderId = resolveDropTargetOrderId(event.clientX, event.clientY);
    setDragOverOrderId(targetOrderId);
  };

  const handleTouchReorderEnd = (event: PointerEvent<HTMLButtonElement>) => {
    const touchDragState = touchDragStateRef.current;
    if (!touchDragState || touchDragState.pointerId !== event.pointerId) return;

    if (!touchDragState.hasMoved) {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
      clearDragState();
      return;
    }

    const targetOrderId = resolveDropTargetOrderId(event.clientX, event.clientY);
    if (targetOrderId === touchDragState.orderId) {
      clearDragState();
    } else if (targetOrderId) {
      handleDropOrderBefore(targetOrderId);
    } else {
      handleDropOrderToEnd();
    }

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    clearDragState();
  };

  const handleTouchReorderCancel = (event: PointerEvent<HTMLButtonElement>) => {
    const touchDragState = touchDragStateRef.current;
    if (!touchDragState || touchDragState.pointerId !== event.pointerId) return;

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    clearDragState();
  };

  const handleDropOrderBefore = (targetOrderId: string) => {
    setSelectedOrderIds((previous) => {
      const fromIndex = previous.indexOf(draggingOrderId ?? "");
      const toIndex = previous.indexOf(targetOrderId);
      if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return previous;

      const next = [...previous];
      const [moved] = next.splice(fromIndex, 1);
      const adjustedToIndex = fromIndex < toIndex ? toIndex - 1 : toIndex;
      next.splice(adjustedToIndex, 0, moved);
      return next;
    });
    setDraggingOrderId(null);
    setDragOverOrderId(null);
  };

  const handleDropOrderToEnd = () => {
    setSelectedOrderIds((previous) => {
      if (!draggingOrderId) return previous;
      const fromIndex = previous.indexOf(draggingOrderId);
      if (fromIndex < 0 || fromIndex === previous.length - 1) return previous;
      const next = [...previous];
      const [moved] = next.splice(fromIndex, 1);
      next.push(moved);
      return next;
    });
    setDraggingOrderId(null);
    setDragOverOrderId(null);
  };

  const doStartRun = async (
    target: DispatchTarget,
    checkoutPurpose?: DeliveryRunPriorityPurpose,
    options?: {
      skipPartialPickConfirm?: boolean;
    }
  ) => {
    if (selectedOrderIds.length === 0) {
      toast.error("Select at least one order to start a run");
      return;
    }

    if (!options?.skipPartialPickConfirm) {
      const partialPicks = selectedOrdersList.filter(
        (order) => order.pick_status && !order.pick_status.is_fully_picked
      );
      if (partialPicks.length > 0) {
        setPartialPickOrders(partialPicks);
        setPendingStartTarget(target);
        setPendingStartPriority(checkoutPurpose ?? null);
        setPartialPickDialogOpen(true);
        return;
      }
    }

    try {
      if (target !== "pickup") {
        const vehicle = target;
        const status = statusByVehicle[vehicle];
        if (status.delivery_run_active) {
          toast.error("Vehicle already has an active run");
          await refreshStatuses();
          return;
        }

        if (status.checked_out) {
          if (status.checkout_type === "other") {
            const purpose = status.purpose?.trim();
            const suffix = purpose ? ` (purpose: ${purpose})` : "";
            toast.error(`Checked out for Other${suffix}. Check in, then check out again for a Delivery run.`);
            await refreshStatuses();
            return;
          }

          const checkedOutByUserId = status.checked_out_by_user_id;
          const checkedOutByName = status.checked_out_by;
          const currentUserId = user?.id ?? null;
          const currentUserDisplayName = getUserDisplayName(user, "");
          const currentUserCandidates = [currentUserDisplayName, user?.email].filter(
            (value): value is string => typeof value === "string" && Boolean(value.trim())
          );
          const isCheckedOutByCurrentUser =
            (checkedOutByUserId && currentUserId && checkedOutByUserId === currentUserId) ||
            (checkedOutByName ? currentUserCandidates.includes(checkedOutByName) : false);

          if (!isCheckedOutByCurrentUser) {
            toast.error(checkedOutByName ? `Checked out by ${checkedOutByName}` : "Checked out by another user");
            await refreshStatuses();
            return;
          }
        } else {
          await vehicleCheckoutsApi.checkout({
            vehicle,
            checkout_type: "delivery_run",
            purpose: checkoutPurpose ?? "Delivery",
          });
        }
      }

      await deliveryRunsApi.createRun({
        order_ids: selectedOrderIds,
        vehicle: target,
      });
      toast.success(target === "pickup" ? "Pickup started" : "Delivery run started");
      setSelectedOrderIds([]);
      await Promise.all([loadOrders(), refreshStatuses(), refreshDeliveryRuns()]);
    } catch (error) {
      toast.error(getApiErrorMessage(error));
      await refreshStatuses();
    }
  };

  const handleStartRun = useCallback(async (): Promise<void> => {
    const target = selectedDispatchTarget;
    if (!target) {
      toast.error("Select van, golf cart, or pickup");
      return;
    }

    if (target !== "pickup" && !selectedPurpose) {
      toast.error("Select a purpose");
      return;
    }

    if (target === "pickup") {
      const disabledReason = getStartDisabledReason(target);
      if (disabledReason) return;

      setActiveVehicleAction(target);
      try {
        await doStartRun(target);
      } finally {
        setActiveVehicleAction((current) => (current === target ? null : current));
      }
      return;
    }

    const purpose = selectedPurpose;
    if (!purpose) return;
    const action = getPriorityActionSelection(purpose);

    if (!action.createsRun) {
      setActiveVehicleAction(target);
      try {
        const status = statusByVehicle[target];
        if (status.delivery_run_active) {
          toast.error("Vehicle already has an active run");
          await refreshStatuses();
          return;
        }
        if (status.checked_out) {
          const checkedOutBy = status.checked_out_by?.trim();
          toast.error(checkedOutBy ? `Checked out by ${checkedOutBy}` : "Vehicle is already checked out");
          await refreshStatuses();
          return;
        }
        await vehicleCheckoutsApi.checkout({
          vehicle: target,
          checkout_type: action.checkoutType,
          purpose: action.purpose,
        });
        toast.success(`Vehicle checked out for ${action.purpose}`);
        setSelectedPurpose(null);
        await refreshStatuses();
      } catch (error) {
        toast.error(getApiErrorMessage(error));
        await refreshStatuses();
      } finally {
        setActiveVehicleAction((current) => (current === target ? null : current));
      }
      return;
    }

    const disabledReason = getStartDisabledReason(target);
    if (disabledReason) return;

    setActiveVehicleAction(target);
    try {
      await doStartRun(target, purpose);
    } finally {
      setActiveVehicleAction((current) => (current === target ? null : current));
    }
  }, [selectedDispatchTarget, selectedPurpose, doStartRun, getStartDisabledReason, refreshStatuses, statusByVehicle]);

  const handleCheckin = useCallback(async (vehicle: Vehicle): Promise<void> => {
    setActiveVehicleAction(vehicle);
    try {
      await vehicleCheckoutsApi.checkin({ vehicle });
      toast.success("Vehicle checked in");
      await refreshStatuses();
    } catch (error) {
      toast.error(getApiErrorMessage(error));
      await refreshStatuses();
    } finally {
      setActiveVehicleAction((current) => (current === vehicle ? null : current));
    }
  }, [refreshStatuses]);

  const handlePartialPickConfirm = async () => {
    setPartialPickDialogOpen(false);
    const target = pendingStartTarget;
    const checkoutPurpose = pendingStartPriority;
    setPendingStartTarget(null);
    setPendingStartPriority(null);
    if (!target) return;
    await doStartRun(target, checkoutPurpose || undefined, { skipPartialPickConfirm: true });
  };

  const handlePartialPickDialogOpenChange = (open: boolean) => {
    setPartialPickDialogOpen(open);
    if (open) return;
    setPendingStartTarget(null);
    setPendingStartPriority(null);
  };

  const handleViewDetail = (orderId?: string) => {
    if (!isValidOrderId(orderId)) {
      toast.error("Order details are unavailable for this row.");
      return;
    }
    navigate(`/orders/${orderId}`, { state: { fromPath: location.pathname } });
  };

  // Find vehicle checked out by current user (you can only have one)
  const userCheckedOutVehicle = useMemo(() => {
    const hit = VEHICLES.find((v) => checkedOutByCurrentUser(statusByVehicle[v.id], user));
    return hit ? hit.id : null;
  }, [statusByVehicle, user]);

  // Sticky action bar validation
  const actionBarBlocker = useMemo(() => {
    if (userCheckedOutVehicle) return null; // check-in mode — always enabled
    if (!selectedDispatchTarget) return "Pick van, golf cart, or pickup";
    if (selectedDispatchTarget !== "pickup" && !selectedPurpose) return "Pick a purpose";
    if (selectedDispatchTarget === "pickup") {
      if (selectedOrderIds.length === 0) return "Select orders";
      return null;
    }
    const action = selectedPurpose ? getPriorityActionSelection(selectedPurpose) : null;
    if (action?.createsRun && selectedOrderIds.length === 0) return "Select orders";
    const reason = getStartDisabledReason(selectedDispatchTarget);
    if (reason) return reason;
    return null;
  }, [selectedOrderIds.length, selectedDispatchTarget, selectedPurpose, getStartDisabledReason, userCheckedOutVehicle]);


  const canStartRun = actionBarBlocker === null;
  const isActionLoading = activeVehicleAction !== null;
  const selectedAction =
    selectedDispatchTarget === "pickup" || !selectedPurpose
      ? null
      : getPriorityActionSelection(selectedPurpose);
  const actionButtonLabel =
    selectedDispatchTarget === "pickup"
      ? "Start Pickup"
      : selectedAction?.buttonLabel ?? "Start Run";

  if (loading) {
    return <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">Loading...</div>;
  }

  return (
    <div className="space-y-5 pb-60 sm:pb-28">
      {/* ── Active Delivery Runs (above the fold) ── */}
      <Card>
        <button
          type="button"
          className="flex w-full items-center justify-between px-4 py-3 text-left"
          onClick={() => setActiveRunsExpanded((prev) => !prev)}
        >
          <div className="flex items-center gap-2">
            <Truck className="h-4 w-4 text-muted-foreground" />
            <div>
              <h2 className="text-sm font-semibold">Active Delivery</h2>
              <p className="text-xs text-muted-foreground">
                {activeDeliveryRunGroups.length} run{activeDeliveryRunGroups.length === 1 ? "" : "s"}
                {activeDeliveryOrderCount ? ` · ${activeDeliveryOrderCount} orders` : ""}
              </p>
            </div>
          </div>
          {activeRunsExpanded ? (
            <ChevronUp className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          )}
        </button>

        {activeRunsExpanded && (
          <CardContent className="space-y-3 border-t px-4 py-3">
            {activeRunsLoading ? (
              <div className="py-4 text-center text-xs text-muted-foreground">Loading active delivery runs...</div>
            ) : activeDeliveryRunGroups.length === 0 ? (
              <div className="py-4 text-center text-xs text-muted-foreground">
                No orders are currently in delivery
              </div>
            ) : (
              <div className="space-y-3">
                {activeDeliveryRunGroups.map(({ run, orders }) => {
                  const runLabel = run.name?.trim() || formatRunLabel(run.id);
                  const orderCount = run.order_ids.length;
                  return (
                    <section key={run.id} className="rounded-lg border border-border/70 bg-background/60 p-4 shadow-none">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0 space-y-1">
                          <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                            {run.vehicle === "pickup" ? "Pickup" : "Delivery Run"}
                          </div>
                          <div className="flex flex-wrap items-center gap-2">
                            <button
                              type="button"
                              className="truncate text-left text-sm font-semibold text-foreground hover:underline"
                              onClick={() => void navigate(`/delivery/runs/${run.id}`)}
                            >
                              {runLabel}
                            </button>
                            <Badge variant={getRunStatusVariant(run.status)}>{run.status}</Badge>
                            <Badge variant="secondary">{orderCount} order{orderCount === 1 ? "" : "s"}</Badge>
                          </div>
                          <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                            <span>{run.vehicle === "pickup" ? "Mode: Pickup" : `Vehicle: ${formatDispatchTargetLabel(run.vehicle)}`}</span>
                            <span>Runner: {run.runner}</span>
                          </div>
                        </div>

                        <Link
                          to={`/delivery/runs/${run.id}`}
                          className="inline-flex h-9 items-center rounded-md border border-border px-3 text-sm font-medium text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                        >
                          View run
                        </Link>
                      </div>

                      <div className="mt-3 space-y-2 border-t border-border/60 pt-3">
                        {orders.length === 0 ? (
                          <div className="text-xs text-muted-foreground">Order details are still syncing.</div>
                        ) : (
                          orders.map((order, index) => (
                            <div
                              key={order.id}
                              className="flex items-start justify-between gap-3 rounded-md border border-border/60 bg-muted/20 px-3 py-2"
                            >
                              <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                  <Badge variant="outline">Stop {index + 1}</Badge>
                                  <button
                                    type="button"
                                    className="truncate text-left text-sm font-medium text-foreground hover:underline"
                                    onClick={() => handleViewDetail(order.inflow_order_id || order.id)}
                                  >
                                    {order.inflow_order_id || order.id}
                                  </button>
                                </div>
                                <div className="truncate text-xs text-muted-foreground">
                                  {order.recipient_name || "Unknown recipient"}
                                  {formatDeliveryLocation(order) ? ` — ${formatDeliveryLocation(order)}` : ""}
                                </div>
                              </div>
                              <Badge variant="secondary" className="shrink-0">
                                {order.status.toLowerCase().replace(/_/g, " ")}
                              </Badge>
                            </div>
                          ))
                        )}
                      </div>
                    </section>
                  );
                })}
              </div>
            )}
          </CardContent>
        )}
      </Card>

      {/* ── Vehicle Status (read-only display) ── */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">Vehicles:</span>
        {VEHICLES.map((vehicle) => {
          const status = statusByVehicle[vehicle.id];
          const since = formatTimeSince(status.checked_out_at);
          const isOwnedByMe = checkedOutByCurrentUser(status, user);

          return (
            <div
              key={vehicle.id}
              className="inline-flex min-h-[40px] items-center gap-2 rounded-md border border-border bg-background px-3 py-1.5 text-xs text-muted-foreground"
            >
              <span className="font-medium">{vehicle.label}</span>
              <Badge variant={getVehicleStatusVariant(status)} className="text-[10px]">
                {formatVehicleStatus(status)}
              </Badge>
              {status.checked_out && since && (
                <span>{since}</span>
              )}
              {isOwnedByMe && status.checked_out && (
                <span className="text-[10px] text-accent font-medium">your vehicle</span>
              )}
            </div>
          );
        })}
      </div>

      {/* ── Pre-Delivery Orders ── */}
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">Pre-Delivery Orders</h2>
            <p className="text-xs text-muted-foreground">
              {readyOrders.length} ready · {needsAttentionOrders.length} need attention
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Input
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="Search orders..."
              className="h-9 w-[220px] text-sm"
            />
            <Button
              variant="outline"
              size="sm"
              onClick={handleToggleVisible}
              disabled={preDeliveryFilteredOrders.length === 0}
              className="h-9"
            >
              {allVisibleSelected ? "Clear" : "Select all"}
            </Button>
          </div>
        </div>

        {loadError && (
          <div className="flex items-center justify-between gap-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
            <span>Failed to refresh. {loadError}</span>
            <Button variant="outline" size="sm" onClick={() => void loadOrders()}>
              Retry
            </Button>
          </div>
        )}

        <div className="space-y-3">
          <DispatchOrderLane
            title="Needs Attention"
            description="Partial picks or exceptions to review."
            orders={needsAttentionOrders}
            selectedOrderIds={selectedOrderIdSet}
            emptyText="No exception orders"
            onToggleOrder={handleSelectOrder}
            onViewOrder={handleViewDetail}
          />
          <DispatchOrderLane
            title="Ready to Dispatch"
            description="Fully picked and ready for dispatch or pickup."
            orders={readyOrders}
            selectedOrderIds={selectedOrderIdSet}
            emptyText="No ready orders"
            onToggleOrder={handleSelectOrder}
            onViewOrder={handleViewDetail}
          />
        </div>
      </section>

      {selectedOrderIds.length > 0 && (
        <section className="space-y-3 rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold">Dispatch Order</h2>
              <p className="text-xs text-muted-foreground">Drag selected orders to set the handoff order before starting the run or pickup.</p>
            </div>
            <Badge variant="secondary">{selectedOrderIds.length} stops</Badge>
          </div>

          <div
            className="space-y-2"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              handleDropOrderToEnd();
            }}
          >
            {selectedOrdersList.map((order, index) => {
              const isDragging = draggingOrderId === order.id;
              const isDropTarget = dragOverOrderId === order.id;
              return (
                <div
                  key={order.id}
                  data-order-id={order.id}
                  draggable
                  onDragStart={(event) => handleDragStartOrder(order.id, event)}
                  onDragEnd={() => {
                    clearDragState();
                  }}
                  onDragOver={(event) => {
                    event.preventDefault();
                    setDragOverOrderId(order.id);
                  }}
                  onDragLeave={() => {
                    setDragOverOrderId((current) => (current === order.id ? null : current));
                  }}
                  onDrop={(event) => {
                    event.preventDefault();
                    handleDropOrderBefore(order.id);
                  }}
                  className={`flex items-center justify-between gap-3 rounded-lg border px-3 py-3 transition-colors ${
                    isDragging
                      ? "border-accent/50 bg-accent/10 opacity-70"
                      : isDropTarget
                        ? "border-accent/50 bg-accent/5"
                        : "border-border/60 bg-background"
                  }`}
                >
                  <div className="min-w-0 space-y-1">
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        aria-label={`Reorder stop ${index + 1}`}
                        className="inline-flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground active:cursor-grabbing"
                        draggable
                        onDragStart={(event) => handleDragStartOrder(order.id, event)}
                        onDragEnd={clearDragState}
                        onPointerDown={(event) => handleTouchReorderStart(order.id, event)}
                        onPointerMove={handleTouchReorderMove}
                        onPointerUp={handleTouchReorderEnd}
                        onPointerCancel={handleTouchReorderCancel}
                        style={{ touchAction: "none" }}
                      >
                        <span aria-hidden="true" className="cursor-grab select-none text-lg leading-none">
                          ⋮⋮
                        </span>
                      </button>
                      <Badge variant="secondary">Stop {index + 1}</Badge>
                      <button
                        type="button"
                        className="truncate text-left text-sm font-medium text-foreground hover:underline"
                        onClick={() => handleViewDetail(order.inflow_order_id || order.id)}
                      >
                        {order.inflow_order_id || order.id}
                      </button>
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {order.recipient_name || "Unknown recipient"}
                      {formatDeliveryLocation(order) ? ` — ${formatDeliveryLocation(order)}` : ""}
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    <Badge variant="outline" className="shrink-0">
                      {order.status.toLowerCase().replace(/_/g, " ")}
                    </Badge>
                    <Button variant="ghost" size="sm" onClick={() => handleSelectOrder(order.id)}>
                      Remove
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* ── Sticky Action Bar ── */}
      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80 lg:left-[var(--sidebar-width)]">
        <div className="mx-auto flex max-w-5xl flex-col items-stretch gap-3 px-4 py-3 sm:flex-row sm:items-center">
          {userCheckedOutVehicle ? (
            /* ── Check-in mode: user has a vehicle out ── */
            <>
              <div className="flex items-center gap-2 text-sm">
                <span className="font-medium">
                  {VEHICLES.find((v) => v.id === userCheckedOutVehicle)?.label} checked out
                </span>
              </div>
              <Button
                onClick={() => void handleCheckin(userCheckedOutVehicle)}
                disabled={isActionLoading}
                className="h-9 w-full min-w-[120px] sm:ml-auto sm:w-auto"
                variant="destructive"
              >
                {isActionLoading ? "Checking in..." : "Check In"}
              </Button>
            </>
          ) : (
            /* ── Checkout mode: normal flow ── */
            <>
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <div className="flex items-center gap-2 text-sm">
                  {selectedOrderIds.length > 0 ? (
                    <Badge variant="default" className="text-xs">
                      {selectedOrderIds.length} selected
                    </Badge>
                  ) : (
                    <span className="text-xs text-muted-foreground">No orders selected</span>
                  )}
                  {selectedPartialPickCount > 0 && (
                    <Badge variant="warning" className="text-[10px]">
                      {selectedPartialPickCount} partial
                    </Badge>
                  )}
                </div>

                <div className="hidden h-5 w-px bg-border sm:block" />

                {/* Vehicle selector */}
                <div className="flex flex-wrap items-center gap-1.5">
                  {DISPATCH_TARGETS.map((target) => {
                    const isActive = target.id === selectedDispatchTarget;
                    const status = target.id === "pickup" ? null : statusByVehicle[target.id];
                    const canUse = target.id === "pickup"
                      ? true
                      : Boolean(status && !status.checked_out && !status.delivery_run_active);
                    return (
                      <button
                        key={target.id}
                        type="button"
                        className={`inline-flex h-8 items-center gap-1 rounded-md border px-2 text-xs transition-colors ${
                          isActive
                            ? "border-accent bg-accent/10 text-foreground font-medium"
                            : "border-border text-muted-foreground hover:text-foreground"
                        } ${!canUse ? "opacity-50" : ""}`}
                        onClick={() => setSelectedDispatchTarget(target.id)}
                      >
                        {target.label}
                      </button>
                    );
                  })}
                </div>

                {selectedDispatchTarget !== "pickup" && (
                  <>
                    <div className="hidden h-5 w-px bg-border sm:block" />

                    <div className="flex flex-wrap items-center gap-1.5">
                      {DELIVERY_RUN_PRIORITY_OPTIONS.map((option) => {
                        const isSelected = selectedPurpose === option.purpose;
                        return (
                          <button
                            key={option.purpose}
                            type="button"
                            className={`inline-flex h-8 items-center rounded-md border px-2 text-xs transition-colors ${
                              isSelected
                                ? "border-accent bg-accent text-accent-foreground font-medium"
                                : "border-border text-muted-foreground hover:text-foreground"
                            }`}
                            onClick={() => setSelectedPurpose(option.purpose)}
                          >
                            {option.label}
                          </button>
                        );
                      })}
                    </div>
                  </>
                )}
              </div>

              <div className="flex min-w-0 flex-col gap-2 sm:ml-auto sm:flex-row sm:items-center">
                {actionBarBlocker ? (
                  <span className="text-xs text-muted-foreground">{actionBarBlocker}</span>
                ) : null}
                <Button
                  onClick={() => void handleStartRun()}
                  disabled={!canStartRun || isActionLoading}
                  className="h-9 w-full min-w-[120px] sm:w-auto"
                >
                  {isActionLoading ? "Starting..." : actionButtonLabel}
                </Button>
              </div>
            </>
          )}
        </div>
      </div>

      {/* ── Partial Pick Dialog ── */}
      <Dialog open={partialPickDialogOpen} onOpenChange={handlePartialPickDialogOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              Partial Pick Warning
            </DialogTitle>
            <DialogDescription>
              {partialPickOrders.length} order{partialPickOrders.length > 1 ? "s are" : " is"} only partially picked.
              Only the picked items will be delivered. Any remaining items stay on the remainder leg and can be
              handled through their own asset tagging, picklist, and order details flow.
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-48 overflow-y-auto py-2">
            <ul className="space-y-1 text-sm">
              {partialPickOrders.map((order) => (
                <li key={order.id} className="flex items-center justify-between rounded bg-muted px-2 py-1">
                  <button
                    type="button"
                    className="font-medium hover:underline"
                    onClick={() => {
                      handlePartialPickDialogOpenChange(false);
                      handleViewDetail(order.inflow_order_id || order.id);
                    }}
                  >
                    {order.inflow_order_id}
                  </button>
                  <span className="text-muted-foreground">
                    {order.pick_status?.total_picked}/{order.pick_status?.total_ordered} items
                  </span>
                </li>
              ))}
            </ul>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => handlePartialPickDialogOpenChange(false)}>
              Cancel
            </Button>
            <Button onClick={handlePartialPickConfirm} className="bg-amber-500 hover:bg-amber-600">
              Continue Anyway
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
