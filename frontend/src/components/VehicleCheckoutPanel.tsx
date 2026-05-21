import { useMemo, useState } from "react";
import axios from "axios";
import { toast } from "sonner";

import {
  vehicleCheckoutsApi,
  type Vehicle,
  type VehicleStatusItem,
} from "../api/vehicleCheckouts";
import { Button } from "./ui/button";
import { Card, CardContent } from "./ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";
import { useAuth } from "../contexts/AuthContext";
import { getUserDisplayName } from "../utils/userDisplay";
import VehicleCommandCard from "./delivery/VehicleCommandCard";

type VehicleDescriptor = {
  vehicle: Vehicle;
  label: string;
};

const VEHICLES: VehicleDescriptor[] = [
  { vehicle: "van", label: "Van" },
  { vehicle: "golf_cart", label: "Golf Cart" },
];

function getApiErrorMessage(error: unknown): string {
  if (!axios.isAxiosError(error)) {
    return "Unexpected error";
  }

  const message = error.response?.data?.error?.message;
  if (typeof message === "string" && message.trim()) {
    return message;
  }

  return "Request failed";
}

export default function VehicleCheckoutPanel({
  statuses,
  isLoading,
  refresh,
  readonly,
}: {
  statuses: VehicleStatusItem[];
  isLoading: boolean;
  refresh: () => Promise<void>;
  readonly?: boolean;
}) {
  const { user } = useAuth();

  const [isSubmitting, setIsSubmitting] = useState(false);

  const [checkoutOpen, setCheckoutOpen] = useState(false);
  const [checkinOpen, setCheckinOpen] = useState(false);
  const [activeVehicle, setActiveVehicle] = useState<Vehicle | null>(null);

  const [purpose, setPurpose] = useState("");
  const [notes, setNotes] = useState("");

  const statusByVehicle = useMemo(() => {
    const map = new Map<Vehicle, VehicleStatusItem>();
    for (const item of statuses) {
      map.set(item.vehicle, item);
    }
    return map;
  }, [statuses]);

  const openCheckout = (vehicle: Vehicle) => {
    setActiveVehicle(vehicle);
    setPurpose("");
    setNotes("");
    setCheckoutOpen(true);
  };

  const openCheckin = (vehicle: Vehicle) => {
    setActiveVehicle(vehicle);
    setNotes("");
    setCheckinOpen(true);
  };

  const handleCheckout = async () => {
    const vehicle = activeVehicle;
    if (!vehicle) return;

    const trimmedPurpose = purpose.trim();
    if (!trimmedPurpose) {
      toast.error("Purpose is required");
      return;
    }

    try {
      setIsSubmitting(true);
      await vehicleCheckoutsApi.checkout({
        vehicle,
        checkout_type: "other",
        purpose: trimmedPurpose,
        notes: notes.trim() || undefined,
      });
      toast.success("Vehicle checked out");
      setCheckoutOpen(false);
      await refresh();
    } catch (error) {
      toast.error(getApiErrorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCheckin = async () => {
    const vehicle = activeVehicle;
    if (!vehicle) return;

    try {
      setIsSubmitting(true);
      await vehicleCheckoutsApi.checkin({
        vehicle,
        notes: notes.trim() || undefined,
      });
      toast.success("Vehicle checked in");
      setCheckinOpen(false);
      await refresh();
    } catch (error) {
      toast.error(getApiErrorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Card>
      <CardContent className="flex flex-col gap-3 p-4">
        <div className="flex items-center justify-between gap-2">
          <div>
            <div className="text-sm font-semibold">Vehicles</div>
            <div className="text-xs text-muted-foreground">Delivery-run checkout is controlled from Dispatch.</div>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {VEHICLES.map(({ vehicle, label }) => {
            const status = statusByVehicle.get(vehicle);
            const checkedOut = Boolean(status?.checked_out);
            const runActive = Boolean(status?.delivery_run_active);
            const disableActions = Boolean(readonly) || isLoading || isSubmitting || runActive;
            const resolvedStatus =
              status ?? {
                vehicle,
                checked_out: false,
                delivery_run_active: false,
              };

            return (
              <VehicleCommandCard key={vehicle} label={label} status={resolvedStatus} isLoading={isLoading}>
                <div className="flex items-center gap-2">
                  {!checkedOut ? (
                    <Button
                      size="sm"
                      onClick={() => openCheckout(vehicle)}
                      disabled={disableActions}
                      className="min-w-24"
                    >
                      Check Out (Other)
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => openCheckin(vehicle)}
                      disabled={disableActions}
                      className="min-w-24"
                    >
                      Check In
                    </Button>
                  )}

                  {runActive ? (
                    <span className="text-xs text-muted-foreground">Active delivery run</span>
                  ) : null}
                </div>
              </VehicleCommandCard>
            );
          })}
        </div>
      </CardContent>

      <Dialog open={checkoutOpen} onOpenChange={setCheckoutOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Check Out Vehicle (Other)</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3 py-2">
            <div className="text-xs text-muted-foreground">
              Checked out as: {getUserDisplayName(user, "your account")}
            </div>
            <div className="grid gap-1">
              <label htmlFor="vehicle-checkout-purpose" className="text-sm font-medium">Purpose (required)</label>
              <Input
                id="vehicle-checkout-purpose"
                name="vehicleCheckoutPurpose"
                value={purpose}
                onChange={(e) => setPurpose(e.target.value)}
                placeholder="Meeting / Receiving / Maintenance"
              />
            </div>
            <div className="grid gap-1">
              <label htmlFor="vehicle-checkout-notes" className="text-sm font-medium">Notes (optional)</label>
              <textarea
                id="vehicle-checkout-notes"
                name="vehicleCheckoutNotes"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                className="flex min-h-[72px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                placeholder="Optional notes"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCheckoutOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleCheckout}
              disabled={isLoading || isSubmitting || !purpose.trim()}
            >
              Check Out
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={checkinOpen} onOpenChange={setCheckinOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Check In Vehicle</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3 py-2">
            <div className="text-xs text-muted-foreground">
              Checking in as: {getUserDisplayName(user, "your account")}
            </div>
            <div className="grid gap-1">
              <label htmlFor="vehicle-checkin-notes" className="text-sm font-medium">Notes (optional)</label>
              <textarea
                id="vehicle-checkin-notes"
                name="vehicleCheckinNotes"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                className="flex min-h-[72px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                placeholder="Optional notes"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCheckinOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleCheckin} disabled={isLoading || isSubmitting}>
              Check In
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}
