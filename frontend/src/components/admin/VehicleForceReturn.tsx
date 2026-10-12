import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { vehicleCheckoutsApi } from "../../api/vehicleCheckouts";
import type { Vehicle, VehicleCheckoutResponse } from "../../api/vehicleCheckouts";
import { extractApiErrorMessage } from "../../utils/apiErrors";
import { formatToCentralTime } from "../../utils/timezone";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import {
    Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "../ui/dialog";
import { Input } from "../ui/input";

const VEHICLES: Array<{ vehicle: Vehicle; label: string }> = [
    { vehicle: "van", label: "Van" },
    { vehicle: "golf_cart", label: "Golf Cart" },
];
const QUERY_KEY = ["admin", "vehicle-force-return"] as const;

export default function VehicleForceReturn() {
    const queryClient = useQueryClient();
    const [selected, setSelected] = useState<VehicleCheckoutResponse | null>(null);
    const [reason, setReason] = useState("");
    const [returnError, setReturnError] = useState<string | null>(null);
    const fleetQuery = useQuery({
        queryKey: QUERY_KEY,
        queryFn: async () => {
            const [checkouts, statuses] = await Promise.all([
                vehicleCheckoutsApi.getActive(),
                vehicleCheckoutsApi.getStatuses(),
            ]);
            return { checkouts, statuses: statuses.vehicles };
        },
        refetchInterval: 15000,
    });

    const returnMutation = useMutation({
        mutationFn: vehicleCheckoutsApi.forceReturn,
        onSuccess: () => {
            setSelected(null);
            setReason("");
            setReturnError(null);
            toast.success("Vehicle force returned");
            void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
            void queryClient.invalidateQueries({ queryKey: ["vehicle-checkouts"] });
        },
        onError: (error: unknown) => {
            setReturnError(extractApiErrorMessage(error, "Unable to return vehicle. Refresh and try again."));
            void queryClient.invalidateQueries({ queryKey: QUERY_KEY });
        },
    });

    const selectedStatus = fleetQuery.data?.statuses.find((item) => item.vehicle === selected?.vehicle);
    const selectedStillActive = fleetQuery.data?.checkouts.some((item) => item.id === selected?.id);
    const busy = fleetQuery.isFetching || returnMutation.isPending;
    const selectedBlocked = fleetQuery.isError || !selectedStillActive ||
        !selectedStatus?.checked_out || selectedStatus.delivery_run_active;
    const selectedLabel = VEHICLES.find((item) => item.vehicle === selected?.vehicle)?.label;

    return (
        <Card className="border-border/70 bg-card/80 shadow-none">
            <CardHeader>
                <CardTitle className="text-base">Vehicle Force Return</CardTitle>
                <CardDescription>
                    Check in a vehicle on someone else's behalf after confirming it has physically returned.
                    Active delivery runs must be resolved from Delivery before returning the vehicle.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
                <Button variant="outline" size="sm" disabled={busy} onClick={() => void fleetQuery.refetch()}>
                    Refresh vehicles
                </Button>
                {fleetQuery.isPending && <p className="text-sm text-muted-foreground">Loading vehicles...</p>}
                {fleetQuery.isError && (
                    <p role="alert" className="text-sm text-destructive">
                        {extractApiErrorMessage(fleetQuery.error, "Unable to load vehicles. Refresh to try again.")}
                    </p>
                )}
                {fleetQuery.data && VEHICLES.map(({ vehicle, label }) => {
                    const checkout = fleetQuery.data.checkouts.find((item) => item.vehicle === vehicle);
                    const status = fleetQuery.data.statuses.find((item) => item.vehicle === vehicle);
                    const activeRun = Boolean(status?.delivery_run_active);
                    const blocked = busy || fleetQuery.isError || !checkout || !status?.checked_out || activeRun;
                    return (
                        <div key={vehicle} className="space-y-2 rounded-xl border bg-muted/30 p-4">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <h3 className="text-sm font-semibold">{label}</h3>
                                <Badge variant={activeRun ? "warning" : checkout ? "secondary" : "success"}>
                                    {activeRun ? "Active delivery run" : checkout ? "Checked out" : "Available"}
                                </Badge>
                            </div>
                            {checkout && (
                                <div className="text-sm text-muted-foreground">
                                    <p>Checked out by: {checkout.checked_out_by}</p>
                                    <p>Purpose: {checkout.purpose || (checkout.checkout_type === "delivery_run" ? "Delivery" : "Other")}</p>
                                    <p>Since: {formatToCentralTime(checkout.checked_out_at)}</p>
                                </div>
                            )}
                            {activeRun && (
                                <p className="text-sm text-muted-foreground">
                                    Force Return is blocked. Finish the run or recall undelivered orders in{" "}
                                    <Link className="underline" to="/delivery/dispatch">Delivery</Link>, then finish the run.
                                </p>
                            )}
                            {checkout && (
                                <Button
                                    size="sm"
                                    variant="destructive"
                                    disabled={blocked}
                                    onClick={() => {
                                        setSelected(checkout);
                                        setReason("");
                                        setReturnError(null);
                                    }}
                                >
                                    Force Return {label}
                                </Button>
                            )}
                        </div>
                    );
                })}
            </CardContent>
            <Dialog open={Boolean(selected)} onOpenChange={(open) => {
                if (!open && !returnMutation.isPending) setSelected(null);
            }}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Force Return {selectedLabel}</DialogTitle>
                        <DialogDescription>
                            Confirm the vehicle has physically returned. This closes {selected?.checked_out_by}'s
                            checkout and records your account and reason. It does not complete runs or change orders.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-2">
                        <label htmlFor="force-return-reason" className="text-sm font-medium">Reason (required)</label>
                        <Input
                            id="force-return-reason"
                            value={reason}
                            onChange={(event) => setReason(event.target.value)}
                            maxLength={2000}
                            disabled={returnMutation.isPending}
                            placeholder="Student returned from tech duty but forgot to check in"
                        />
                        {selectedBlocked && (
                            <p role="alert" className="text-sm text-destructive">
                                Vehicle status changed or could not be verified. Refresh and review the checkout.
                            </p>
                        )}
                        {returnError && <p role="alert" className="text-sm text-destructive">{returnError}</p>}
                    </div>
                    <DialogFooter>
                        <Button variant="outline" disabled={returnMutation.isPending} onClick={() => setSelected(null)}>
                            Cancel
                        </Button>
                        <Button
                            variant="destructive"
                            disabled={busy || selectedBlocked || !reason.trim()}
                            onClick={() => {
                                if (!selected || selectedBlocked || !reason.trim()) return;
                                setReturnError(null);
                                returnMutation.mutate({ vehicle: selected.vehicle, checkout_id: selected.id, reason: reason.trim() });
                            }}
                        >
                            {returnMutation.isPending ? "Returning..." : "Confirm Force Return"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </Card>
    );
}
