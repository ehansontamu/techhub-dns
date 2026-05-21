import { useEffect, useState } from "react";

import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { Input } from "../ui/input";

interface ConfirmActionDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    title: string;
    description: string;
    confirmLabel: string;
    confirmValue?: string;
    confirmPlaceholder?: string;
    confirmHint?: string;
    onConfirm: () => Promise<void> | void;
    isPending?: boolean;
}

export function ConfirmActionDialog({
    open,
    onOpenChange,
    title,
    description,
    confirmLabel,
    confirmValue,
    confirmPlaceholder,
    confirmHint,
    onConfirm,
    isPending = false,
}: ConfirmActionDialogProps) {
    const [confirmation, setConfirmation] = useState("");

    useEffect(() => {
        if (!open) {
            setConfirmation("");
        }
    }, [open]);

    const requiresConfirmation = Boolean(confirmValue);
    const isAllowed = !requiresConfirmation || confirmation.trim() === confirmValue;

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-lg">
                <DialogHeader>
                    <DialogTitle>{title}</DialogTitle>
                    <DialogDescription>{description}</DialogDescription>
                </DialogHeader>

                {requiresConfirmation ? (
                    <div className="space-y-2">
                        <Input
                            value={confirmation}
                            onChange={(event) => setConfirmation(event.target.value)}
                            placeholder={confirmPlaceholder ?? `Type ${confirmValue} to confirm`}
                            disabled={isPending}
                        />
                        {confirmHint ? <p className="text-xs text-muted-foreground">{confirmHint}</p> : null}
                    </div>
                ) : null}

                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isPending}>
                        Cancel
                    </Button>
                    <Button variant="destructive" onClick={() => void onConfirm()} disabled={isPending || !isAllowed}>
                        {confirmLabel}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
