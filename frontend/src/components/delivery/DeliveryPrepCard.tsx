import { useMemo, useState } from "react";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import type { User } from "../../contexts/AuthContext";
import type { Vehicle, VehicleStatusItem } from "../../api/vehicleCheckouts";
import { getStatusBadge, VehicleStatusMeta } from "../vehicles/VehicleStatusStrip";
import { getUserDisplayName } from "../../utils/userDisplay";

type Props = {
  selectedOrdersCount: number;
  user: User | null;
  statusByVehicle: Record<Vehicle, VehicleStatusItem>;
  statusesLoading: boolean;
  onStartRun: (vehicle: Vehicle) => Promise<void>;
};

function runnerDisplay(user: User | null): string {
  return getUserDisplayName(user, "you");
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

export default function DeliveryPrepCard({
  selectedOrdersCount,
  user,
  statusByVehicle,
  statusesLoading,
  onStartRun,
}: Props) {
  const [vehicle, setVehicle] = useState<Vehicle>("van");
  const [isStarting, setIsStarting] = useState(false);

  const status = statusByVehicle[vehicle];
  const badge = getStatusBadge(status);
  const runner = useMemo(() => runnerDisplay(user), [user?.display_name, user?.email]);

  const startDisabledReason = useMemo((): string | null => {
    if (selectedOrdersCount === 0) return "Select orders to start a run";
    if (statusesLoading) return "Loading vehicle status";

    if (status.delivery_run_active) return "Vehicle already has an active run";

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
  }, [selectedOrdersCount, status, statusesLoading, user]);

  const canStart = startDisabledReason === null;

  const handleStart = async () => {
    if (!canStart) return;
    setIsStarting(true);
    try {
      await onStartRun(vehicle);
    } finally {
      setIsStarting(false);
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle className="text-base">Delivery Prep</CardTitle>
        <div className="text-xs text-muted-foreground">Runner: {runner}</div>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-3 sm:grid-cols-3 sm:items-end">
          <div className="grid gap-1">
            <label htmlFor="delivery-prep-vehicle" className="text-sm font-medium">
              Vehicle
            </label>
            <select
              id="delivery-prep-vehicle"
              value={vehicle}
              onChange={(e) => setVehicle(e.target.value as Vehicle)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              disabled={isStarting}
            >
              <option value="van">Van</option>
              <option value="golf_cart">Golf Cart</option>
            </select>
          </div>

          <div className="grid gap-1">
            <div className="text-sm font-medium">Checkout</div>
            <div className="flex items-center gap-2">
              <Badge variant={badge.variant}>{badge.label}</Badge>
              <VehicleStatusMeta
                status={status}
                isLoading={statusesLoading}
                checkedOutByFormat="short"
                loadingText="Loading..."
                className="text-xs text-muted-foreground"
              />
            </div>
          </div>

          <div className="flex flex-col items-start gap-2 sm:items-end">
            <Button
              onClick={handleStart}
              disabled={!canStart || isStarting}
              className="bg-accent text-accent-foreground hover:bg-accent/90"
            >
              {isStarting ? "Starting..." : `Start Run (${selectedOrdersCount})`}
            </Button>
            {startDisabledReason ? (
              <div className="text-xs text-muted-foreground">{startDisabledReason}</div>
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
