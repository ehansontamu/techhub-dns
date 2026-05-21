import { useState } from "react";
import { OrderStatus, OrderStatusDisplayNames } from "../types/order";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";

interface StatusTransitionProps {
  currentStatus: OrderStatus;
  newStatus: OrderStatus;
  onConfirm: (reason?: string) => void;
  onCancel: () => void;
  requireReason?: boolean;
  submitting?: boolean;
  title?: string;
  confirmLabel?: string;
}

export default function StatusTransition({
  currentStatus,
  newStatus,
  onConfirm,
  onCancel,
  requireReason = false,
  submitting = false,
  title = "Change Status",
  confirmLabel = "Confirm",
}: StatusTransitionProps) {
  const [reason, setReason] = useState("");
  const [reasonTouched, setReasonTouched] = useState(false);

  const reasonInvalid = requireReason && !reason.trim();
  const showReasonError = reasonTouched && reasonInvalid;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setReasonTouched(true);
    if (reasonInvalid || submitting) return;
    onConfirm(reason || undefined);
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !submitting) onCancel();
      }}
    >
      <DialogContent className="sm:max-w-md">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>
              Change status from{" "}
              <span className="font-medium text-foreground">
                {OrderStatusDisplayNames[currentStatus]}
              </span>{" "}
              to{" "}
              <span className="font-medium text-foreground">
                {OrderStatusDisplayNames[newStatus]}
              </span>
              .
            </DialogDescription>
          </DialogHeader>

          {requireReason && (
            <div className="mt-4">
              <label htmlFor="status-transition-reason" className="block text-sm font-medium text-foreground mb-2">
                Reason <span className="text-destructive">*</span>
              </label>
              <textarea
                id="status-transition-reason"
                name="statusTransitionReason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                onBlur={() => setReasonTouched(true)}
                className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                rows={3}
                required
                disabled={submitting}
                placeholder="Enter reason for this status change..."
                aria-invalid={showReasonError}
              />
              {showReasonError && (
                <p className="mt-2 text-sm text-destructive">Reason is required.</p>
              )}
            </div>
          )}

          <DialogFooter className="mt-6 gap-2 sm:gap-0">
            <Button type="button" variant="outline" onClick={onCancel} disabled={submitting}>
              Cancel
            </Button>
            <Button type="submit" disabled={reasonInvalid || submitting}>
              {submitting ? "Updating..." : confirmLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
