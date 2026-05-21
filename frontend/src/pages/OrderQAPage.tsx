import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { useAuth } from "../contexts/AuthContext";
import { getUserDisplayName } from "../utils/userDisplay";
import { isLocalDelivery } from "../utils/location";
import { ordersApi } from "../api/orders";
import { getOrderDetailQueryOptions, invalidateOrderQueries } from "../queries/orders";
import type { OrderDetail } from "../types/order";
import { formatToCentralTime } from "../utils/timezone";
import { getOrderPickerLabel, isOrderPickedByUser } from "../utils/qaEligibility";

type QAFormState = {
    orderNumber: string;
    technician: string;
    verifyAssetTagSerialMatch: boolean;
    verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved: boolean;
    verifyPackagedProperly: boolean;
    verifyPackingSlipSerialsMatch: boolean;
    verifyBoxesLabeledCorrectly: boolean;
    qaSignature: string;
};

type LegacyQAFormState = QAFormState & {
    method?: string;
};

type SavedQAChecklist = {
    orderId: string;
    inflowOrderId: string;
    submittedAt: string;
    form: LegacyQAFormState;
};

const defaultForm = (orderNumber: string): QAFormState => ({
    orderNumber,
    technician: "",
    verifyAssetTagSerialMatch: false,
    verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved: false,
    verifyPackagedProperly: false,
    verifyPackingSlipSerialsMatch: false,
    verifyBoxesLabeledCorrectly: false,
    qaSignature: "",
});

const normalizeSavedForm = (form: LegacyQAFormState, orderNumber: string): QAFormState => {
    const { method: _method, ...rest } = form;
    return {
        ...defaultForm(orderNumber),
        ...rest,
    };
};

const storageKey = (orderId: string) => `order-qa-checklist-v3:${orderId}`;

const verificationSteps = [
    {
        id: "verifyAssetTagSerialMatch",
        label:
            "Confirm the asset tag is applied and the device serial number, sticker, and pick list all match.",
    },
    {
        id: "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved",
        label:
            "Confirm the order details template was sent to the customer and the electronic packing slip was saved.",
    },
    {
        id: "verifyPackagedProperly",
        label: "Confirm the system and all included materials are packaged properly.",
    },
    {
        id: "verifyPackingSlipSerialsMatch",
        label: "Confirm the packing slip, picked items, and serial numbers all match.",
    },
    {
        id: "verifyBoxesLabeledCorrectly",
        label: "Confirm boxes are labeled with the correct order details and shipping labels are marked out.",
    },
];

function isFormComplete(form: QAFormState) {
    return (
        form.orderNumber.trim().length > 0 &&
        form.verifyAssetTagSerialMatch &&
        form.verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved &&
        form.verifyPackagedProperly &&
        form.verifyPackingSlipSerialsMatch &&
        form.verifyBoxesLabeledCorrectly
    );
}

export default function OrderQAPage() {
    const navigate = useNavigate();
    const { orderId } = useParams<{ orderId: string }>();
    const { user } = useAuth();
    const currentUserName = getUserDisplayName(user, "Current User");

    const [form, setForm] = useState<QAFormState>(() => defaultForm(""));
    const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
    const queryClient = useQueryClient();

    const orderQuery = useQuery({
        ...getOrderDetailQueryOptions(orderId ?? ""),
        enabled: Boolean(orderId),
        retry: false,
    });

    const order = orderQuery.data ?? null;
    const loading = orderQuery.isPending;
    const isParentPartialLeg = Boolean(order?.remainder_order_id && !order?.parent_order_id);
    const pickedByCurrentUser = order ? isOrderPickedByUser(order, user) : false;
    const pickerLabel = order ? getOrderPickerLabel(order) : "Not recorded";

    useEffect(() => {
        if (order) {
            initializeForm(order);
        }
    }, [order]);

    useEffect(() => {
        if (orderQuery.isError) {
            console.error("Failed to load order:", orderQuery.error);
            toast.error("Failed to load order details.");
            navigate("/order-qa");
        }
    }, [navigate, orderQuery.error, orderQuery.isError]);

    const initializeForm = (order: OrderDetail) => {
        const orderNumber = (order.inflow_order_id || order.id || "").toString();
        const savedRaw = localStorage.getItem(storageKey(order.id));

        if (savedRaw) {
            try {
                const parsed = JSON.parse(savedRaw) as SavedQAChecklist;
                setForm(normalizeSavedForm(parsed.form, orderNumber));
                setLastSavedAt(parsed.submittedAt);
            } catch {
                setupDefaults(orderNumber);
            }
        } else {
            setupDefaults(orderNumber);
        }
    };

    const setupDefaults = (orderNumber: string) => {
        const defaults = defaultForm(orderNumber);
        defaults.qaSignature = getUserDisplayName(user, defaults.qaSignature);
        defaults.technician = currentUserName;
        setForm(defaults);
        setLastSavedAt(null);
    };

    const submitQaMutation = useMutation({
        mutationFn: (payload: { responses: Record<string, unknown>; technician: string; expected_updated_at?: string }) => {
            if (!order) {
                throw new Error("Order is unavailable");
            }

            return ordersApi.submitQa(order.id, payload);
        },
        onSuccess: async () => {
            if (orderId) {
                await invalidateOrderQueries(queryClient, orderId);
            }
        },
        onError: async (error: unknown) => {
            console.error("Failed to submit QA:", error);
            if (isAxiosError(error) && error.response?.status === 409 && orderId) {
                toast.error("Order changed by another user. Reloaded the latest details.");
                await invalidateOrderQueries(queryClient, orderId);
                return;
            }

            toast.error("Failed to submit QA checklist. Please try again.");
        },
    });

    const submitQA = async () => {
        if (!order) return;

        if (pickedByCurrentUser) {
            toast.error("You cannot QA an order you picked.");
            return;
        }

        if (!isFormComplete(form)) {
            toast.error("Please complete all required QA fields before submitting.");
            return;
        }

        try {
            await submitQaMutation.mutateAsync({
                responses: {
                    ...form,
                    qaSignature: currentUserName,
                },
                technician: currentUserName,
                expected_updated_at: order.updated_at,
            });

            const payload: SavedQAChecklist = {
                orderId: order.id,
                inflowOrderId: order.inflow_order_id || order.id,
                submittedAt: new Date().toISOString(),
                form,
            };
            localStorage.setItem(storageKey(order.id), JSON.stringify(payload));

            toast.success("QA checklist submitted successfully!");
            navigate("/order-qa");
        } catch {
            // Handled by mutation callbacks.
        }
    };

    if (loading) {
        return (
            <div className="flex h-full min-h-0 items-center justify-center bg-background px-4 py-8">
                <div className="text-lg text-foreground">Loading order...</div>
            </div>
        );
    }

    if (!order) return null;

    const hasIncompleteSteps = verificationSteps.some((step) => !form[step.id as keyof QAFormState]);
    const qaMethod = order.qa_method?.trim().toLowerCase();
    const routingMethod =
        qaMethod === "delivery" || qaMethod === "shipping"
            ? qaMethod.charAt(0).toUpperCase() + qaMethod.slice(1)
            : isLocalDelivery(order)
                ? "Delivery"
                : "Shipping";

    return (
        <div className="h-full min-h-0 overflow-y-auto bg-background px-4 py-8">
            <div className="mx-auto max-w-4xl space-y-6">
                <div className="space-y-1 text-foreground">
                    <h1 className="text-3xl font-semibold">QA Checklist</h1>
                    <p className="text-sm text-muted-foreground">
                        Use this checklist to verify tagging, packaging, and documentation before you submit <span className="font-semibold text-foreground">{order.inflow_order_id}</span>.
                    </p>
                    {lastSavedAt && (
                        <p className="text-xs text-muted-foreground">Previously submitted: {formatToCentralTime(lastSavedAt)}</p>
                    )}
                    {isParentPartialLeg && (
                        <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                            This is the remainder parent leg. QA can be completed here as a separate delivery order.
                        </div>
                    )}
                    <div className="rounded-2xl border border-border bg-card px-4 py-3 text-sm text-foreground">
                        Picked by <span className="font-semibold">{pickerLabel}</span>.
                    </div>
                    {pickedByCurrentUser && (
                        <div className="rounded-2xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                            You picked this order, so QA must be completed by another logged-in user.
                        </div>
                    )}
                </div>

                <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-lg">
                    <div className="space-y-6 p-6">
                        <div className="space-y-2">
                            <label className="text-sm font-medium text-foreground">
                                Order Number <span className="text-red-600">*</span>
                            </label>
                            <input
                                id="qa-order-number"
                                name="orderNumber"
                                value={form.orderNumber}
                                readOnly
                                className="w-full rounded-xl border border-border bg-muted px-3 py-2 text-sm text-muted-foreground shadow-sm focus:outline-none focus:ring-2 focus:ring-accent/70"
                            />
                        </div>

                        <div className="rounded-2xl border border-border/60 bg-muted/40 p-4 text-sm text-foreground">
                            Technician recorded as <span className="font-semibold text-foreground">{currentUserName}</span> on submission.
                        </div>

                        <div className="space-y-4">
                            <div className="border-b border-border pb-2">
                                <p className="text-sm font-medium text-foreground">Verification Steps <span className="text-destructive">*</span></p>
                                <p className="text-xs text-muted-foreground">All steps must be checked before submission.</p>
                            </div>
                            <div className="space-y-3">
                                {verificationSteps.map((step) => {
                                    const checked = form[step.id as keyof QAFormState] as boolean;
                                    return (
                                        <label
                                            key={step.id}
                                            className={`flex gap-3 rounded-2xl border px-4 py-3 text-sm transition-colors ${
                                                checked
                                                    ? "border-green-200 bg-green-50 text-green-900"
                                                    : "border-border bg-card hover:border-accent"
                                            }`}
                                        >
                                            <input
                                                type="checkbox"
                                                id={step.id}
                                                checked={checked}
                                                disabled={pickedByCurrentUser}
                                                onChange={() => setForm((prev) => ({ ...prev, [step.id]: !checked }))}
                                                className="mt-1 h-5 w-5 rounded border-border/70 text-accent focus:ring-accent"
                                            />
                                            <span>{step.label}</span>
                                        </label>
                                    );
                                })}
                            </div>
                        </div>

                        <div className="space-y-2">
                            <label className="text-sm font-medium text-muted-foreground">
                                QA Signature <span className="text-red-600">*</span>
                            </label>
                            <div className="rounded-xl border border-border bg-muted px-3 py-2 text-sm text-muted-foreground">
                                {currentUserName}
                            </div>
                            <p className="text-xs text-muted-foreground">Automatic signature recorded on submit.</p>
                        </div>

                        <div className="space-y-2">
                            <label className="text-sm font-medium text-muted-foreground">
                                Routing
                            </label>
                            <div className="rounded-xl border border-border bg-muted px-3 py-2 text-sm text-foreground">
                                <span className="font-semibold">{routingMethod}</span>
                            </div>
                        </div>
                    </div>

                    <div className="flex flex-col gap-3 border-t border-border bg-background p-6 sm:flex-row sm:items-center sm:justify-between">
                        <div className="text-sm text-muted-foreground">
                            {hasIncompleteSteps ? "Some required steps remain unchecked." : "Ready to submit."}
                        </div>
                        <div className="flex gap-3">
                            <button
                                type="button"
                                onClick={() => navigate(-1)}
                                disabled={submitQaMutation.isPending}
                                className="rounded-2xl border border-transparent bg-card px-5 py-2 text-sm font-medium text-foreground shadow-sm hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
                            >
                                Cancel
                            </button>
                            <button
                                type="button"
                                onClick={submitQA}
                                disabled={submitQaMutation.isPending || pickedByCurrentUser || !isFormComplete(form)}
                                className={`rounded-2xl px-5 py-2 text-sm font-semibold transition-colors ${
                                    submitQaMutation.isPending || pickedByCurrentUser || !isFormComplete(form)
                                        ? "bg-muted text-muted-foreground/70 cursor-not-allowed"
                                        : "bg-primary text-primary-foreground hover:bg-maroon-800 hover:text-white"
                                }`}
                            >
                                {pickedByCurrentUser ? "QA unavailable" : submitQaMutation.isPending ? "Submitting..." : "Submit QA Checklist"}
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
