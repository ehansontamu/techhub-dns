import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { Loader2, PackageCheck, RefreshCw, Tag, UploadCloud } from "lucide-react";

import { ordersApi } from "../api/orders";
import { settingsApi } from "../api/settings";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Checkbox } from "../components/ui/checkbox";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "../components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { getOrdersListQueryOptions, getTagRequestCandidatesQueryOptions, ordersQueryKeys } from "../queries/orders";
import { canGeneratePicklist, isActiveRemainderLegWaitingOnPickup } from "../utils/orderPartial";
import { extractApiErrorMessage } from "../utils/apiErrors";
import { OrderDetail, OrderStatus } from "../types/order";

type TagRequestCandidate = {
    id: string;
    inflow_order_id: string;
    recipient_name?: string;
    delivery_location?: string;
    picklist_generated_at?: string;
};

const parseTagRequestCandidate = (value: unknown): TagRequestCandidate | null => {
    if (!value || typeof value !== "object") return null;
    const record = value as Record<string, unknown>;

    const id = typeof record.id === "string" ? record.id.trim() : "";
    const inflowOrderId = typeof record.inflow_order_id === "string" ? record.inflow_order_id.trim() : "";
    if (!id) return null;

    return {
        id,
        inflow_order_id: inflowOrderId,
        recipient_name: typeof record.recipient_name === "string" ? record.recipient_name : undefined,
        delivery_location: typeof record.delivery_location === "string" ? record.delivery_location : undefined,
        picklist_generated_at: typeof record.picklist_generated_at === "string" ? record.picklist_generated_at : undefined,
    };
};

const parseStringArray = (value: unknown): string[] | undefined => {
    if (!Array.isArray(value)) return undefined;
    const next = value
        .filter((item): item is string => typeof item === "string")
        .map((item) => item.trim())
        .filter(Boolean);
    return next.length > 0 ? next : [];
};

const parseIneligibleOrders = (value: unknown): Array<{ order: string; reason: string }> | undefined => {
    if (!Array.isArray(value)) return undefined;
    const next: Array<{ order: string; reason: string }> = [];
    for (const item of value) {
        if (!item || typeof item !== "object") continue;
        const record = item as Record<string, unknown>;
        const order = typeof record.order === "string" ? record.order.trim() : "";
        const reason = typeof record.reason === "string" ? record.reason.trim() : "";
        if (!order || !reason) continue;
        next.push({ order, reason });
    }
    return next.length > 0 ? next : [];
};

type UploadStatusState = {
    type: "success" | "error";
    message: string;
    uploadedUrl?: string | null;
    filename?: string | null;
    count?: number;
    teamsNotified?: boolean;
    updatedOrders?: number;
    missingOrders?: string[];
    eligibleOrders?: string[];
    ineligibleOrders?: Array<{ order: string; reason: string }>;
};

type BulkTagStatusState = {
    type: "success" | "error";
    message: string;
    updatedOrders?: string[];
    failedOrders?: Array<{ id: string; reason: string }>;
};

type BatchStatusState = {
    type: "success" | "error";
    message: string;
    generatedOrders?: string[];
    blockedOrders?: Array<{ order: string; reason: string }>;
    staleOrders?: string[];
    failedOrders?: Array<{ order: string; reason: string }>;
};

type PickerOverrideStatusState = {
    type: "success" | "error";
    message: string;
    updatedOrders?: string[];
    pickerLabel?: string;
};

function getBlockedReason(order: OrderDetail): string {
    if (order.picklist_generated_at) {
        return "picklist already generated";
    }

    if (order.asset_tag_required !== false && !order.tagged_at) {
        return "asset tagging pending";
    }

    if (isActiveRemainderLegWaitingOnPickup(order)) {
        return "remainder waiting on pickup";
    }

    return "not ready yet";
}

export default function Preparation() {
    const [uploadStatus, setUploadStatus] = useState<UploadStatusState | null>(null);
    const [batchStatus, setBatchStatus] = useState<BatchStatusState | null>(null);
    const [uploadConfirmOpen, setUploadConfirmOpen] = useState(false);
    const [bulkTagConfirmOpen, setBulkTagConfirmOpen] = useState(false);
    const [batchConfirmOpen, setBatchConfirmOpen] = useState(false);
    const uploadConfirmCancelRef = useRef<HTMLButtonElement | null>(null);
    const bulkTagConfirmCancelRef = useRef<HTMLButtonElement | null>(null);
    const batchConfirmCancelRef = useRef<HTMLButtonElement | null>(null);

    const [selectedTagCandidates, setSelectedTagCandidates] = useState<string[]>([]);
    const [bulkTagStatus, setBulkTagStatus] = useState<BulkTagStatusState | null>(null);
    const [selectedPrepOrders, setSelectedPrepOrders] = useState<string[]>([]);
    const [selectedOverrideOrders, setSelectedOverrideOrders] = useState<string[]>([]);
    const [selectedPickerEmail, setSelectedPickerEmail] = useState("");
    const [pickerOverrideStatus, setPickerOverrideStatus] = useState<PickerOverrideStatusState | null>(null);
    const queryClient = useQueryClient();

    const tagCandidatesQuery = useQuery({
        ...getTagRequestCandidatesQueryOptions({ limit: 1000 }),
        select: (result) => result
            .map(parseTagRequestCandidate)
            .filter((candidate): candidate is TagRequestCandidate => candidate !== null),
    });

    const prepOrdersQuery = useQuery({
        ...getOrdersListQueryOptions({ status: OrderStatus.PICKED, search: "", limit: 1000 }),
        select: (result) => result.items.filter((order) => canGeneratePicklist(order)),
    });

    const pickerOverrideOrdersQuery = useQuery({
        ...getOrdersListQueryOptions({ status: OrderStatus.QA, search: "", limit: 1000 }),
        select: (result) => result.items.filter((order) => Boolean(order.picklist_generated_at)),
    });

    const pickerOptionsQuery = useQuery({
        queryKey: ["picker-options"],
        queryFn: () => ordersApi.getPickerOptions(),
    });

    const tagCandidates = tagCandidatesQuery.data ?? [];
    const tagCandidatesLoading = tagCandidatesQuery.isPending || tagCandidatesQuery.isFetching;
    const tagCandidatesError = tagCandidatesQuery.isError ? "Failed to load picked orders. Please refresh." : null;

    const prepOrders = prepOrdersQuery.data ?? [];
    const prepOrdersLoading = prepOrdersQuery.isPending || prepOrdersQuery.isFetching;
    const prepOrdersError = prepOrdersQuery.isError ? "Failed to load preparation queue. Please refresh." : null;
    const pickerOverrideOrders = pickerOverrideOrdersQuery.data ?? [];
    const pickerOverrideOrdersLoading = pickerOverrideOrdersQuery.isPending || pickerOverrideOrdersQuery.isFetching;
    const pickerOverrideOrdersError = pickerOverrideOrdersQuery.isError ? "Failed to load QA orders. Please refresh." : null;
    const pickerOptions = pickerOptionsQuery.data ?? [];
    const pickerOptionsLoading = pickerOptionsQuery.isPending || pickerOptionsQuery.isFetching;
    const pickerOptionsError = pickerOptionsQuery.isError ? "Failed to load allowed picker options. Please refresh." : null;

    const uploadMutation = useMutation({
        mutationFn: (orders: string[]) => settingsApi.uploadCanopyOrders(orders),
        onSuccess: async (response) => {
            if (response.success) {
                setUploadStatus({
                    type: "success",
                    message: "Orders uploaded to Canopy.",
                    uploadedUrl: response.uploaded_url,
                    filename: response.filename,
                    count: response.count,
                    teamsNotified: response.teams_notified,
                    updatedOrders: response.updated_orders,
                    missingOrders: response.missing_orders,
                    eligibleOrders: response.eligible_orders,
                    ineligibleOrders: response.ineligible_orders,
                });
                setSelectedTagCandidates([]);
                await queryClient.invalidateQueries({ queryKey: ordersQueryKeys.all });
                return;
            }

            setUploadStatus({
                type: "error",
                message: response.error || "Upload failed.",
            });
        },
        onError: (err: unknown) => {
            const responseData = isAxiosError(err) ? (err.response?.data as unknown) : undefined;
            const record = responseData && typeof responseData === "object" ? (responseData as Record<string, unknown>) : null;

            const missingOrders = parseStringArray(record?.missing_orders);
            const ineligibleOrders = parseIneligibleOrders(record?.ineligible_orders);
            const backendError = typeof record?.error === "string" && record.error.trim() ? record.error.trim() : null;

            setUploadStatus({
                type: "error",
                message: backendError || "Upload failed.",
                missingOrders,
                ineligibleOrders,
                eligibleOrders: parseStringArray(record?.eligible_orders),
            });
        },
    });

    const bulkTagMutation = useMutation({
        mutationFn: (orderIds: string[]) => ordersApi.bulkMarkTagged({ order_ids: orderIds }),
        onSuccess: async (response) => {
            const updatedOrders = response.updated_orders.map((order) => order.inflow_order_id);
            setBulkTagStatus({
                type: response.failed_orders.length === 0 ? "success" : "error",
                message: response.updated_orders.length > 0
                    ? `Marked ${response.updated_orders.length} order${response.updated_orders.length === 1 ? "" : "s"} as tagged${response.failed_orders.length > 0 ? `; ${response.failed_orders.length} could not be updated` : ""}.`
                    : "No orders were marked as tagged.",
                updatedOrders,
                failedOrders: response.failed_orders,
            });
            setSelectedTagCandidates([]);
            await queryClient.invalidateQueries({ queryKey: ordersQueryKeys.all });
        },
        onError: (error: unknown) => {
            setBulkTagStatus({
                type: "error",
                message: extractApiErrorMessage(error, "Failed to mark selected orders as tagged."),
            });
        },
    });

    const batchMutation = useMutation({
        mutationFn: async (orderIds: string[]) => {
            const generatedOrders: string[] = [];
            const blockedOrders: Array<{ order: string; reason: string }> = [];
            const staleOrders: string[] = [];
            const failedOrders: Array<{ order: string; reason: string }> = [];

            for (const orderId of orderIds) {
                try {
                    const detail = await ordersApi.getOrder(orderId);
                    if (!canGeneratePicklist(detail)) {
                        blockedOrders.push({
                            order: detail.inflow_order_id,
                            reason: getBlockedReason(detail),
                        });
                        continue;
                    }

                    await ordersApi.generatePicklist(orderId, {
                        expected_updated_at: detail.updated_at,
                    });
                    generatedOrders.push(detail.inflow_order_id);
                } catch (error: unknown) {
                    if (isAxiosError(error) && error.response?.status === 409) {
                        staleOrders.push(orderId);
                        continue;
                    }

                    failedOrders.push({
                        order: orderId,
                        reason: extractApiErrorMessage(error, "Failed to generate picklist."),
                    });
                }
            }

            return { generatedOrders, blockedOrders, staleOrders, failedOrders };
        },
        onSuccess: async (result) => {
            const totalGenerated = result.generatedOrders.length;
            const totalBlocked = result.blockedOrders.length;
            const totalStale = result.staleOrders.length;
            const totalFailed = result.failedOrders.length;

            setBatchStatus({
                type: totalGenerated > 0 && totalFailed === 0 ? "success" : "error",
                message: totalGenerated > 0
                    ? `Prepared ${totalGenerated} order${totalGenerated === 1 ? "" : "s"}${totalBlocked > 0 ? `, skipped ${totalBlocked} blocked` : ""}${totalStale > 0 ? `, and marked ${totalStale} stale` : ""}.`
                    : "No orders were prepared.",
                generatedOrders: result.generatedOrders,
                blockedOrders: result.blockedOrders,
                staleOrders: result.staleOrders,
                failedOrders: result.failedOrders,
            });
            setSelectedPrepOrders([]);

            await queryClient.invalidateQueries({ queryKey: ordersQueryKeys.all });
        },
    });

    const pickerOverrideMutation = useMutation({
        mutationFn: (payload: { order_ids: string[]; picker_email: string }) => ordersApi.bulkOverridePicker(payload),
        onSuccess: async (response) => {
            setPickerOverrideStatus({
                type: "success",
                message: `Updated picker for ${response.updated_orders.length} order${response.updated_orders.length === 1 ? "" : "s"}.`,
                updatedOrders: response.updated_orders.map((order) => order.inflow_order_id),
                pickerLabel: response.picker_display_name,
            });
            setSelectedOverrideOrders([]);
            await queryClient.invalidateQueries({ queryKey: ordersQueryKeys.all });
        },
        onError: (error: unknown) => {
            setPickerOverrideStatus({
                type: "error",
                message: extractApiErrorMessage(error, "Failed to override picker."),
            });
        },
    });

    const selectedTagCandidateIds = useMemo(
        () => Array.from(new Set(selectedTagCandidates)).filter(Boolean).sort(),
        [selectedTagCandidates],
    );

    const selectedTagOrderIds = useMemo(
        () => tagCandidates
            .filter((candidate) => selectedTagCandidateIds.includes(candidate.id))
            .map((candidate) => candidate.inflow_order_id)
            .filter(Boolean)
            .sort(),
        [selectedTagCandidateIds, tagCandidates],
    );

    const selectedPrepOrderIds = useMemo(
        () => Array.from(new Set(selectedPrepOrders)).filter(Boolean).sort(),
        [selectedPrepOrders]
    );

    const selectedPrepOrderSet = useMemo(() => new Set(selectedPrepOrders), [selectedPrepOrders]);
    const selectedOverrideOrderSet = useMemo(() => new Set(selectedOverrideOrders), [selectedOverrideOrders]);

    const selectedPrepOrderDisplayIds = useMemo(
        () =>
            prepOrders
                .filter((order) => selectedPrepOrderSet.has(order.id))
                .map((order) => order.inflow_order_id || order.id)
                .filter(Boolean),
        [prepOrders, selectedPrepOrderSet],
    );

    const selectedTagCandidateSet = useMemo(() => new Set(selectedTagCandidates), [selectedTagCandidates]);
    const selectedOverrideOrderDisplayIds = useMemo(
        () =>
            pickerOverrideOrders
                .filter((order) => selectedOverrideOrderSet.has(order.id))
                .map((order) => order.inflow_order_id || order.id)
                .filter(Boolean),
        [pickerOverrideOrders, selectedOverrideOrderSet],
    );

    const selectableTagCount = useMemo(() => {
        let count = 0;
        for (const candidate of tagCandidates) {
            if (candidate.inflow_order_id) count += 1;
        }
        return count;
    }, [tagCandidates]);

    const selectablePrepCount = useMemo(() => {
        let count = 0;
        for (const order of prepOrders) {
            if (order.id) count += 1;
        }
        return count;
    }, [prepOrders]);

    const selectableOverrideCount = useMemo(() => {
        let count = 0;
        for (const order of pickerOverrideOrders) {
            if (order.id) count += 1;
        }
        return count;
    }, [pickerOverrideOrders]);

    const uploadStatusStyles = useMemo(() => {
        if (!uploadStatus) return null;
        return uploadStatus.type === "success"
            ? "border-emerald-200 bg-emerald-50 text-emerald-900"
            : "border-destructive/20 bg-destructive/5 text-destructive";
    }, [uploadStatus]);

    const bulkTagStatusStyles = useMemo(() => {
        if (!bulkTagStatus) return null;
        return bulkTagStatus.type === "success"
            ? "border-emerald-200 bg-emerald-50 text-emerald-900"
            : "border-destructive/20 bg-destructive/5 text-destructive";
    }, [bulkTagStatus]);

    const batchStatusStyles = useMemo(() => {
        if (!batchStatus) return null;
        return batchStatus.type === "success"
            ? "border-emerald-200 bg-emerald-50 text-emerald-900"
            : "border-destructive/20 bg-destructive/5 text-destructive";
    }, [batchStatus]);

    const pickerOverrideStatusStyles = useMemo(() => {
        if (!pickerOverrideStatus) return null;
        return pickerOverrideStatus.type === "success"
            ? "border-emerald-200 bg-emerald-50 text-emerald-900"
            : "border-destructive/20 bg-destructive/5 text-destructive";
    }, [pickerOverrideStatus]);

    const handleClearTagSelection = () => {
        setSelectedTagCandidates([]);
        setUploadStatus(null);
        setBulkTagStatus(null);
    };

    const handleClearPrepSelection = () => {
        setSelectedPrepOrders([]);
        setBatchStatus(null);
    };

    const handleClearOverrideSelection = () => {
        setSelectedOverrideOrders([]);
        setPickerOverrideStatus(null);
    };

    const loadTagCandidates = async () => {
        await tagCandidatesQuery.refetch();
    };

    const loadPrepOrders = async () => {
        await prepOrdersQuery.refetch();
    };

    const loadPickerOverrideOrders = async () => {
        await pickerOverrideOrdersQuery.refetch();
        await pickerOptionsQuery.refetch();
    };

    useEffect(() => {
        setSelectedTagCandidates((prev) => {
            if (prev.length === 0) return prev;
            const present = new Set(tagCandidates.map((candidate) => candidate.id).filter(Boolean));
            const next = prev.filter((id) => present.has(id));
            return next.length === prev.length ? prev : next;
        });
    }, [tagCandidates]);

    useEffect(() => {
        setSelectedPrepOrders((prev) => {
            if (prev.length === 0) return prev;
            const present = new Set(prepOrders.map((order) => order.id).filter(Boolean));
            const next = prev.filter((id) => present.has(id));
            return next.length === prev.length ? prev : next;
        });
    }, [prepOrders]);

    useEffect(() => {
        setSelectedOverrideOrders((prev) => {
            if (prev.length === 0) return prev;
            const present = new Set(pickerOverrideOrders.map((order) => order.id).filter(Boolean));
            const next = prev.filter((id) => present.has(id));
            return next.length === prev.length ? prev : next;
        });
    }, [pickerOverrideOrders]);

    const toggleTagCandidate = useCallback((candidateId: string, checked: boolean) => {
        setSelectedTagCandidates((prev) => {
            if (checked) {
                return prev.includes(candidateId) ? prev : [...prev, candidateId];
            }
            return prev.filter((id) => id !== candidateId);
        });
    }, []);

    const togglePrepOrder = useCallback((orderId: string, checked: boolean) => {
        setSelectedPrepOrders((prev) => {
            if (checked) {
                return prev.includes(orderId) ? prev : [...prev, orderId];
            }
            return prev.filter((id) => id !== orderId);
        });
    }, []);

    const toggleOverrideOrder = useCallback((orderId: string, checked: boolean) => {
        setSelectedOverrideOrders((prev) => {
            if (checked) {
                return prev.includes(orderId) ? prev : [...prev, orderId];
            }
            return prev.filter((id) => id !== orderId);
        });
    }, []);

    const handleUpload = async () => {
        if (selectedTagOrderIds.length === 0) return;

        setUploadStatus(null);
        try {
            await uploadMutation.mutateAsync(selectedTagOrderIds);
        } catch {
            // Handled by mutation callbacks.
        }
    };

    const handleBulkTag = async () => {
        if (selectedTagCandidateIds.length === 0) return;

        setBulkTagStatus(null);
        try {
            await bulkTagMutation.mutateAsync(selectedTagCandidateIds);
        } catch {
            // Handled by mutation callbacks.
        }
    };

    const handleBatchGenerate = async () => {
        if (selectedPrepOrderIds.length === 0) return;

        setBatchStatus(null);
        try {
            await batchMutation.mutateAsync(selectedPrepOrderIds);
        } catch {
            // Handled by mutation callbacks.
        }
    };

    const handlePickerOverride = async () => {
        if (selectedOverrideOrders.length === 0 || !selectedPickerEmail) return;

        setPickerOverrideStatus(null);
        try {
            await pickerOverrideMutation.mutateAsync({
                order_ids: selectedOverrideOrders,
                picker_email: selectedPickerEmail,
            });
        } catch {
            // Handled by mutation callbacks.
        }
    };

    return (
        <div className="container mx-auto px-4 py-4 sm:px-6 sm:py-6 space-y-6 overflow-hidden">
            <div className="space-y-1">
                <h1 className="text-xl font-semibold tracking-tight text-foreground sm:text-2xl">Preparation</h1>
            </div>

            <section className="overflow-hidden rounded-2xl border border-border/70 bg-card/80 shadow-none">
                <div className="p-5 pb-4 sm:p-6 sm:pb-4">
                    <div className="flex items-start justify-between gap-3">
                        <div>
                            <h2 className="text-base font-semibold tracking-tight">Tag Request Actions</h2>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                            <Button type="button" variant="outline" size="sm" onClick={() => void loadTagCandidates()}>
                                <RefreshCw className="mr-2 h-4 w-4" />
                                Refresh
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => {
                                    setSelectedTagCandidates((prev) => {
                                        const next = new Set(prev);
                                        for (const candidate of tagCandidates) {
                                            const id = candidate.id;
                                            if (id) next.add(id);
                                        }
                                        return Array.from(next);
                                    });
                                }}
                                disabled={selectableTagCount === 0}
                            >
                                Select all visible
                            </Button>
                            <Button type="button" variant="outline" size="sm" onClick={handleClearTagSelection} disabled={selectedTagOrderIds.length === 0}>
                                Clear selection
                            </Button>
                        </div>
                    </div>
                </div>

                <div className="px-5 pb-5 sm:px-6 sm:pb-6">
                    <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(18rem,2fr)]">
                        <div className="min-w-0 space-y-4">
                            {tagCandidatesLoading && tagCandidates.length === 0 ? (
                                <div className="rounded-lg border bg-muted/30 p-4 text-center text-sm text-muted-foreground">
                                    Loading picked orders...
                                </div>
                            ) : tagCandidatesError ? (
                                <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-4 text-sm text-destructive">
                                    {tagCandidatesError}
                                </div>
                            ) : tagCandidates.length === 0 ? (
                                <div className="rounded-lg border border-dashed bg-muted/30 p-4 text-center text-sm text-muted-foreground">
                                    No tag request candidates found.
                                </div>
                            ) : (
                                <div className="rounded-lg border bg-card overflow-hidden">
                                    <div className="max-h-[26rem] overflow-auto">
                                        <Table className="w-full">
                                            <TableHeader className="sticky top-0 z-10 bg-card">
                                                <TableRow>
                                                    <TableHead className="w-10" />
                                                    <TableHead className="whitespace-nowrap">Order</TableHead>
                                                    <TableHead>Recipient</TableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {tagCandidates.map((candidate) => {
                                                    const inflowOrderId = candidate.inflow_order_id;
                                                    const checked = candidate.id ? selectedTagCandidateSet.has(candidate.id) : false;
                                                    const disabled = !candidate.id;
                                                    const selectable = Boolean(candidate.id);

                                                    return (
                                                        <TableRow
                                                            key={candidate.id}
                                                            data-state={checked ? "selected" : undefined}
                                                            className={selectable ? "cursor-pointer hover:bg-muted/30" : undefined}
                                                            tabIndex={selectable ? 0 : undefined}
                                                            onClick={() => {
                                                                if (!candidate.id) return;
                                                                toggleTagCandidate(candidate.id, !checked);
                                                            }}
                                                            onKeyDown={(event) => {
                                                                if (!candidate.id) return;
                                                                if (event.key !== "Enter" && event.key !== " ") return;
                                                                event.preventDefault();
                                                                toggleTagCandidate(candidate.id, !checked);
                                                            }}
                                                        >
                                                            <TableCell className="w-10">
                                                                <Checkbox
                                                                    checked={checked}
                                                                    disabled={disabled}
                                                                    aria-label={inflowOrderId ? `Select ${inflowOrderId}` : "Select candidate"}
                                                                    onClick={(event) => event.stopPropagation()}
                                                                    onChange={(event) => {
                                                                        if (!candidate.id) return;
                                                                        toggleTagCandidate(candidate.id, event.target.checked);
                                                                    }}
                                                                />
                                                            </TableCell>
                                                            <TableCell>
                                                                <div className="flex items-center gap-2">
                                                                    <span className="font-medium text-foreground whitespace-nowrap">
                                                                        {candidate.inflow_order_id || "-"}
                                                                    </span>
                                                                    {checked ? (
                                                                        <Badge variant="secondary" className="whitespace-nowrap">
                                                                            Selected
                                                                        </Badge>
                                                                    ) : null}
                                                                </div>
                                                            </TableCell>
                                                            <TableCell>
                                                                <div className="min-w-0 max-w-[12rem] sm:max-w-none">
                                                                    <p className="truncate text-foreground">
                                                                        {candidate.recipient_name || "Unknown recipient"}
                                                                    </p>
                                                                    {candidate.delivery_location ? (
                                                                        <p className="truncate text-xs text-muted-foreground">
                                                                            {candidate.delivery_location}
                                                                        </p>
                                                                    ) : null}
                                                                </div>
                                                            </TableCell>
                                                        </TableRow>
                                                    );
                                                })}
                                            </TableBody>
                                        </Table>
                                    </div>
                                </div>
                            )}
                        </div>

                        <aside className="min-w-0 self-start rounded-2xl border border-border/70 bg-muted/20 p-4 shadow-none xl:sticky xl:top-6">
                            <div className="space-y-4">
                                <div className="rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground">
                                    <p className="text-foreground font-medium">{selectedTagOrderIds.length} selected</p>
                                    {selectedTagOrderIds.length > 0 ? (
                                        <p className="mt-1 text-xs text-muted-foreground break-words">
                                            {selectedTagOrderIds.join(", ")}
                                        </p>
                                    ) : null}
                                </div>

                                {uploadStatus ? (
                                    <div className={`rounded-lg border p-4 text-sm ${uploadStatusStyles}`}>
                                        <p className="font-medium">{uploadStatus.message}</p>
                                        {uploadStatus.uploadedUrl ? (
                                            <p className="mt-2 text-sm break-all">
                                                File URL:{" "}
                                                <a
                                                    href={uploadStatus.uploadedUrl}
                                                    target="_blank"
                                                    rel="noreferrer"
                                                    className="text-primary underline"
                                                >
                                                    {uploadStatus.uploadedUrl}
                                                </a>
                                            </p>
                                        ) : null}
                                        {uploadStatus.filename ? <p className="mt-1 text-xs">Filename: {uploadStatus.filename}</p> : null}
                                        {typeof uploadStatus.teamsNotified === "boolean" ? (
                                            <p className="mt-1 text-xs">
                                                Teams notification: {uploadStatus.teamsNotified ? "sent" : "not sent"}
                                            </p>
                                        ) : null}
                                        {typeof uploadStatus.updatedOrders === "number" ? (
                                            <p className="mt-1 text-xs">Updated local orders: {uploadStatus.updatedOrders}</p>
                                        ) : null}
                                        {uploadStatus.ineligibleOrders && uploadStatus.ineligibleOrders.length > 0 ? (
                                            <div className="mt-2 space-y-1">
                                                <p className="text-xs font-medium">Ineligible</p>
                                                <ul className="flex flex-wrap gap-1">
                                                    {uploadStatus.ineligibleOrders.map(({ order, reason }) => (
                                                        <li key={`${order}:${reason}`}>
                                                            <Badge
                                                                variant="outline"
                                                                className="whitespace-nowrap border-destructive/40 text-destructive"
                                                            >
                                                                {order} ({reason})
                                                            </Badge>
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>
                                        ) : null}
                                        {uploadStatus.missingOrders && uploadStatus.missingOrders.length > 0 ? (
                                            <div className="mt-2 space-y-1">
                                                <p className="text-xs font-medium">Missing locally</p>
                                                <ul className="flex flex-wrap gap-1">
                                                    {uploadStatus.missingOrders.map((order) => (
                                                        <li key={order}>
                                                            <Badge variant="outline" className="whitespace-nowrap">
                                                                {order}
                                                            </Badge>
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>
                                        ) : null}
                                    </div>
                                ) : null}

                                {bulkTagStatus ? (
                                    <div className={`rounded-lg border p-4 text-sm ${bulkTagStatusStyles}`}>
                                        <p className="font-medium">{bulkTagStatus.message}</p>
                                        {bulkTagStatus.updatedOrders && bulkTagStatus.updatedOrders.length > 0 ? (
                                            <p className="mt-1 text-xs">Updated: {bulkTagStatus.updatedOrders.join(", ")}</p>
                                        ) : null}
                                        {bulkTagStatus.failedOrders && bulkTagStatus.failedOrders.length > 0 ? (
                                            <ul className="mt-2 space-y-1 text-xs">
                                                {bulkTagStatus.failedOrders.map((order) => (
                                                    <li key={order.id}>{order.id}: {order.reason}</li>
                                                ))}
                                            </ul>
                                        ) : null}
                                    </div>
                                ) : null}

                                <div className="flex flex-col gap-2">
                                    <Button
                                        type="button"
                                        variant="outline"
                                        onClick={handleClearTagSelection}
                                        disabled={selectedTagOrderIds.length === 0}
                                    >
                                        Clear selection
                                    </Button>
                                    <Button
                                        type="button"
                                        onClick={() => setUploadConfirmOpen(true)}
                                        disabled={selectedTagCandidateIds.length === 0 || uploadMutation.isPending || bulkTagMutation.isPending}
                                        className="btn-lift"
                                    >
                                        <UploadCloud className="mr-2 h-4 w-4" />
                                        {uploadMutation.isPending ? "Uploading..." : "Upload orders"}
                                    </Button>
                                    <Button
                                        type="button"
                                        variant="secondary"
                                        onClick={() => setBulkTagConfirmOpen(true)}
                                        disabled={selectedTagCandidateIds.length === 0 || uploadMutation.isPending || bulkTagMutation.isPending}
                                    >
                                        <Tag className="mr-2 h-4 w-4" />
                                        {bulkTagMutation.isPending ? "Marking..." : "Mark selected as tagged"}
                                    </Button>
                                </div>
                            </div>
                        </aside>
                    </div>
                </div>
            </section>

            <Dialog open={uploadConfirmOpen} onOpenChange={setUploadConfirmOpen}>
                <DialogContent
                    onOpenAutoFocus={(event) => {
                        event.preventDefault();
                        uploadConfirmCancelRef.current?.focus();
                    }}
                >
                    <DialogHeader>
                        <DialogTitle>Upload orders to Canopy?</DialogTitle>
                    </DialogHeader>
                    <DialogFooter>
                        <Button
                            ref={uploadConfirmCancelRef}
                            type="button"
                            variant="outline"
                            onClick={() => setUploadConfirmOpen(false)}
                            disabled={uploadMutation.isPending}
                        >
                            Cancel
                        </Button>
                        <Button
                            type="button"
                            onClick={() => {
                                setUploadConfirmOpen(false);
                                void handleUpload();
                            }}
                            disabled={uploadMutation.isPending}
                        >
                            Upload now
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={bulkTagConfirmOpen} onOpenChange={setBulkTagConfirmOpen}>
                <DialogContent
                    onOpenAutoFocus={(event) => {
                        event.preventDefault();
                        bulkTagConfirmCancelRef.current?.focus();
                    }}
                >
                    <DialogHeader>
                        <DialogTitle>Mark selected orders as tagged?</DialogTitle>
                    </DialogHeader>
                    <p className="text-sm text-muted-foreground">
                        This records that tags have been applied to {selectedTagOrderIds.length} selected order{selectedTagOrderIds.length === 1 ? "" : "s"}.
                    </p>
                    <DialogFooter>
                        <Button
                            ref={bulkTagConfirmCancelRef}
                            type="button"
                            variant="outline"
                            onClick={() => setBulkTagConfirmOpen(false)}
                            disabled={bulkTagMutation.isPending}
                        >
                            Cancel
                        </Button>
                        <Button
                            type="button"
                            onClick={() => {
                                setBulkTagConfirmOpen(false);
                                void handleBulkTag();
                            }}
                            disabled={bulkTagMutation.isPending}
                        >
                            Mark as tagged
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <section className="overflow-hidden rounded-2xl border border-border/70 bg-card/80 shadow-none">
                <div className="p-5 pb-4 sm:p-6 sm:pb-4">
                    <div className="flex items-start justify-between gap-3">
                        <div>
                            <h2 className="text-base font-semibold tracking-tight">Generate Picklist &amp; Order Details</h2>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                            <Button type="button" variant="outline" size="sm" onClick={() => void loadPrepOrders()}>
                                <RefreshCw className="mr-2 h-4 w-4" />
                                Refresh
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => {
                                    setSelectedPrepOrders((prev) => {
                                        const next = new Set(prev);
                                        for (const order of prepOrders) {
                                            if (order.id) next.add(order.id);
                                        }
                                        return Array.from(next);
                                    });
                                }}
                                disabled={selectablePrepCount === 0}
                            >
                                Select all visible
                            </Button>
                            <Button type="button" variant="outline" size="sm" onClick={handleClearPrepSelection} disabled={selectedPrepOrderIds.length === 0}>
                                Clear selection
                            </Button>
                        </div>
                    </div>
                </div>

                <div className="px-5 pb-5 sm:px-6 sm:pb-6">
                    <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(18rem,2fr)]">
                        <div className="min-w-0 space-y-4">
                            {prepOrdersLoading && prepOrders.length === 0 ? (
                                <div className="rounded-lg border bg-muted/30 p-4 text-center text-sm text-muted-foreground">
                                    Loading preparation queue...
                                </div>
                            ) : prepOrdersError ? (
                                <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-4 text-sm text-destructive">
                                    {prepOrdersError}
                                </div>
                            ) : prepOrders.length === 0 ? (
                                <div className="rounded-lg border border-dashed bg-muted/30 p-4 text-center text-sm text-muted-foreground">
                                    No orders are ready to generate picklists and order details.
                                </div>
                            ) : (
                                <div className="rounded-lg border bg-card overflow-hidden">
                                    <div className="max-h-[26rem] overflow-auto">
                                        <Table className="w-full">
                                            <TableHeader className="sticky top-0 z-10 bg-card">
                                                <TableRow>
                                                    <TableHead className="w-10" />
                                                    <TableHead className="whitespace-nowrap">Order</TableHead>
                                                    <TableHead>Recipient</TableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {prepOrders.map((order) => {
                                                    const checked = selectedPrepOrderSet.has(order.id);

                                                    return (
                                                        <TableRow
                                                            key={order.id}
                                                            data-state={checked ? "selected" : undefined}
                                                            className="cursor-pointer hover:bg-muted/30"
                                                            tabIndex={0}
                                                            onClick={() => togglePrepOrder(order.id, !checked)}
                                                            onKeyDown={(event) => {
                                                                if (event.key !== "Enter" && event.key !== " ") return;
                                                                event.preventDefault();
                                                                togglePrepOrder(order.id, !checked);
                                                            }}
                                                        >
                                                            <TableCell className="w-10">
                                                                <Checkbox
                                                                    checked={checked}
                                                                    aria-label={`Select ${order.inflow_order_id || order.id}`}
                                                                    onClick={(event) => event.stopPropagation()}
                                                                    onChange={(event) => togglePrepOrder(order.id, event.target.checked)}
                                                                />
                                                            </TableCell>
                                                            <TableCell>
                                                                <div className="flex items-center gap-2">
                                                                    <span className="font-medium text-foreground whitespace-nowrap">
                                                                        {order.inflow_order_id || order.id}
                                                                    </span>
                                                                    {checked ? (
                                                                        <Badge variant="secondary" className="whitespace-nowrap">
                                                                            Selected
                                                                        </Badge>
                                                                    ) : null}
                                                                </div>
                                                            </TableCell>
                                                            <TableCell>
                                                                <div className="min-w-0 max-w-[12rem] sm:max-w-none">
                                                                    <p className="truncate text-foreground">
                                                                        {order.recipient_name || "Unknown recipient"}
                                                                    </p>
                                                                    {order.delivery_location ? (
                                                                        <p className="truncate text-xs text-muted-foreground">
                                                                            {order.delivery_location}
                                                                        </p>
                                                                    ) : null}
                                                                </div>
                                                            </TableCell>
                                                        </TableRow>
                                                    );
                                                })}
                                            </TableBody>
                                        </Table>
                                    </div>
                                </div>
                            )}
                        </div>

                        <aside className="min-w-0 self-start rounded-2xl border border-border/70 bg-muted/20 p-4 shadow-none xl:sticky xl:top-6">
                            <div className="space-y-4">
                                <div className="rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground">
                                    <p className="text-foreground font-medium">{selectedPrepOrderIds.length} selected</p>
                                    {selectedPrepOrderDisplayIds.length > 0 ? (
                                        <p className="mt-1 text-xs text-muted-foreground break-words">
                                            {selectedPrepOrderDisplayIds.join(", ")}
                                        </p>
                                    ) : null}
                                </div>

                                {batchStatus ? (
                                    <div className={`rounded-lg border p-4 text-sm ${batchStatusStyles}`}>
                                        <p className="font-medium">{batchStatus.message}</p>
                                        {batchStatus.generatedOrders && batchStatus.generatedOrders.length > 0 ? (
                                            <div className="mt-2 space-y-1">
                                                <p className="text-xs font-medium">Generated</p>
                                                <ul className="flex flex-wrap gap-1">
                                                    {batchStatus.generatedOrders.map((order) => (
                                                        <li key={order}>
                                                            <Badge variant="secondary" className="whitespace-nowrap">
                                                                {order}
                                                            </Badge>
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>
                                        ) : null}
                                        {batchStatus.blockedOrders && batchStatus.blockedOrders.length > 0 ? (
                                            <div className="mt-2 space-y-1">
                                                <p className="text-xs font-medium">Blocked</p>
                                                <ul className="flex flex-wrap gap-1">
                                                    {batchStatus.blockedOrders.map(({ order, reason }) => (
                                                        <li key={`${order}:${reason}`}>
                                                            <Badge variant="outline" className="whitespace-nowrap border-destructive/40 text-destructive">
                                                                {order} ({reason})
                                                            </Badge>
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>
                                        ) : null}
                                        {batchStatus.staleOrders && batchStatus.staleOrders.length > 0 ? (
                                            <div className="mt-2 space-y-1">
                                                <p className="text-xs font-medium">Stale</p>
                                                <ul className="flex flex-wrap gap-1">
                                                    {batchStatus.staleOrders.map((order) => (
                                                        <li key={order}>
                                                            <Badge variant="outline" className="whitespace-nowrap">
                                                                {order}
                                                            </Badge>
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>
                                        ) : null}
                                        {batchStatus.failedOrders && batchStatus.failedOrders.length > 0 ? (
                                            <div className="mt-2 space-y-1">
                                                <p className="text-xs font-medium">Failed</p>
                                                <ul className="space-y-1">
                                                    {batchStatus.failedOrders.map(({ order, reason }) => (
                                                        <li key={`${order}:${reason}`} className="text-xs">
                                                            {order}: {reason}
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>
                                        ) : null}
                                    </div>
                                ) : null}

                                <div className="flex flex-col gap-2">
                                    <Button
                                        type="button"
                                        variant="outline"
                                        onClick={handleClearPrepSelection}
                                        disabled={selectedPrepOrderIds.length === 0}
                                    >
                                        Clear selection
                                    </Button>
                                    <Button
                                        type="button"
                                        onClick={() => setBatchConfirmOpen(true)}
                                        disabled={selectedPrepOrderIds.length === 0 || batchMutation.isPending}
                                        className="btn-lift"
                                    >
                                        {batchMutation.isPending ? (
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        ) : (
                                            <PackageCheck className="mr-2 h-4 w-4" />
                                        )}
                                        {batchMutation.isPending ? "Preparing..." : "Generate"}
                                    </Button>
                                </div>
                            </div>
                        </aside>
                    </div>
                </div>
            </section>

            <Dialog open={batchConfirmOpen} onOpenChange={setBatchConfirmOpen}>
                <DialogContent
                    onOpenAutoFocus={(event) => {
                        event.preventDefault();
                        batchConfirmCancelRef.current?.focus();
                    }}
                >
                    <DialogHeader>
                        <DialogTitle>Generate picklists for selected orders?</DialogTitle>
                    </DialogHeader>
                    <DialogFooter>
                        <Button
                            ref={batchConfirmCancelRef}
                            type="button"
                            variant="outline"
                            onClick={() => setBatchConfirmOpen(false)}
                            disabled={batchMutation.isPending}
                        >
                            Cancel
                        </Button>
                        <Button
                            type="button"
                            onClick={() => {
                                setBatchConfirmOpen(false);
                                void handleBatchGenerate();
                            }}
                            disabled={batchMutation.isPending}
                        >
                            Generate now
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <section className="overflow-hidden rounded-2xl border border-border/70 bg-card/80 shadow-none">
                <div className="p-5 pb-4 sm:p-6 sm:pb-4">
                    <div className="flex items-start justify-between gap-3">
                        <div>
                            <h2 className="text-base font-semibold tracking-tight">Override Recorded Picker</h2>
                            <p className="mt-1 text-sm text-muted-foreground">
                                Use this for QA-stage orders when someone else printed the picklist and became the recorded picker.
                            </p>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                            <Button type="button" variant="outline" size="sm" onClick={() => void loadPickerOverrideOrders()}>
                                <RefreshCw className="mr-2 h-4 w-4" />
                                Refresh
                            </Button>
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => {
                                    setSelectedOverrideOrders((prev) => {
                                        const next = new Set(prev);
                                        for (const order of pickerOverrideOrders) {
                                            if (order.id) next.add(order.id);
                                        }
                                        return Array.from(next);
                                    });
                                }}
                                disabled={selectableOverrideCount === 0}
                            >
                                Select all visible
                            </Button>
                            <Button type="button" variant="outline" size="sm" onClick={handleClearOverrideSelection} disabled={selectedOverrideOrders.length === 0}>
                                Clear selection
                            </Button>
                        </div>
                    </div>
                </div>

                <div className="px-5 pb-5 sm:px-6 sm:pb-6">
                    <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(18rem,2fr)]">
                        <div className="min-w-0 space-y-4">
                            {pickerOverrideOrdersLoading && pickerOverrideOrders.length === 0 ? (
                                <div className="rounded-lg border bg-muted/30 p-4 text-center text-sm text-muted-foreground">
                                    Loading QA orders...
                                </div>
                            ) : pickerOverrideOrdersError ? (
                                <div className="rounded-lg border border-destructive/20 bg-destructive/5 p-4 text-sm text-destructive">
                                    {pickerOverrideOrdersError}
                                </div>
                            ) : pickerOverrideOrders.length === 0 ? (
                                <div className="rounded-lg border border-dashed bg-muted/30 p-4 text-center text-sm text-muted-foreground">
                                    No QA orders are waiting for picker overrides.
                                </div>
                            ) : (
                                <div className="rounded-lg border bg-card overflow-hidden">
                                    <div className="max-h-[26rem] overflow-auto">
                                        <Table className="w-full">
                                            <TableHeader className="sticky top-0 z-10 bg-card">
                                                <TableRow>
                                                    <TableHead className="w-10" />
                                                    <TableHead className="whitespace-nowrap">Order</TableHead>
                                                    <TableHead>Recorded picker</TableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {pickerOverrideOrders.map((order) => {
                                                    const checked = selectedOverrideOrderSet.has(order.id);

                                                    return (
                                                        <TableRow
                                                            key={order.id}
                                                            data-state={checked ? "selected" : undefined}
                                                            className="cursor-pointer hover:bg-muted/30"
                                                            tabIndex={0}
                                                            onClick={() => toggleOverrideOrder(order.id, !checked)}
                                                            onKeyDown={(event) => {
                                                                if (event.key !== "Enter" && event.key !== " ") return;
                                                                event.preventDefault();
                                                                toggleOverrideOrder(order.id, !checked);
                                                            }}
                                                        >
                                                            <TableCell className="w-10">
                                                                <Checkbox
                                                                    checked={checked}
                                                                    aria-label={`Select ${order.inflow_order_id || order.id}`}
                                                                    onClick={(event) => event.stopPropagation()}
                                                                    onChange={(event) => toggleOverrideOrder(order.id, event.target.checked)}
                                                                />
                                                            </TableCell>
                                                            <TableCell>
                                                                <div className="flex items-center gap-2">
                                                                    <span className="font-medium text-foreground whitespace-nowrap">
                                                                        {order.inflow_order_id || order.id}
                                                                    </span>
                                                                    {checked ? (
                                                                        <Badge variant="secondary" className="whitespace-nowrap">
                                                                            Selected
                                                                        </Badge>
                                                                    ) : null}
                                                                </div>
                                                                <div className="min-w-0 max-w-[12rem] sm:max-w-none">
                                                                    <p className="truncate text-xs text-muted-foreground">
                                                                        {order.recipient_name || "Unknown recipient"}
                                                                    </p>
                                                                </div>
                                                            </TableCell>
                                                            <TableCell>
                                                                <span className="text-sm text-foreground">
                                                                    {order.picklist_generated_by || "Not recorded"}
                                                                </span>
                                                            </TableCell>
                                                        </TableRow>
                                                    );
                                                })}
                                            </TableBody>
                                        </Table>
                                    </div>
                                </div>
                            )}
                        </div>

                        <aside className="min-w-0 self-start rounded-2xl border border-border/70 bg-muted/20 p-4 shadow-none xl:sticky xl:top-6">
                            <div className="space-y-4">
                                <div className="rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground">
                                    <p className="text-foreground font-medium">{selectedOverrideOrders.length} selected</p>
                                    {selectedOverrideOrderDisplayIds.length > 0 ? (
                                        <p className="mt-1 text-xs text-muted-foreground break-words">
                                            {selectedOverrideOrderDisplayIds.join(", ")}
                                        </p>
                                    ) : null}
                                </div>

                                <div className="space-y-2">
                                    <label htmlFor="picker-override-select" className="text-sm font-medium text-foreground">
                                        Set picker to
                                    </label>
                                    <select
                                        id="picker-override-select"
                                        value={selectedPickerEmail}
                                        onChange={(event) => setSelectedPickerEmail(event.target.value)}
                                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background"
                                        disabled={pickerOptionsLoading || pickerOverrideMutation.isPending}
                                    >
                                        <option value="">Select a student worker</option>
                                        {pickerOptions.map((option) => (
                                            <option key={option.email} value={option.email}>
                                                {option.label}
                                            </option>
                                        ))}
                                    </select>
                                    {pickerOptionsError ? (
                                        <p className="text-xs text-destructive">{pickerOptionsError}</p>
                                    ) : (
                                        <p className="text-xs text-muted-foreground">
                                            Picker options come from the allowed student worker list.
                                        </p>
                                    )}
                                </div>

                                {pickerOverrideStatus ? (
                                    <div className={`rounded-lg border p-4 text-sm ${pickerOverrideStatusStyles}`}>
                                        <p className="font-medium">{pickerOverrideStatus.message}</p>
                                        {pickerOverrideStatus.pickerLabel ? (
                                            <p className="mt-1 text-xs">Recorded picker: {pickerOverrideStatus.pickerLabel}</p>
                                        ) : null}
                                        {pickerOverrideStatus.updatedOrders && pickerOverrideStatus.updatedOrders.length > 0 ? (
                                            <div className="mt-2 space-y-1">
                                                <p className="text-xs font-medium">Updated</p>
                                                <ul className="flex flex-wrap gap-1">
                                                    {pickerOverrideStatus.updatedOrders.map((order) => (
                                                        <li key={order}>
                                                            <Badge variant="secondary" className="whitespace-nowrap">
                                                                {order}
                                                            </Badge>
                                                        </li>
                                                    ))}
                                                </ul>
                                            </div>
                                        ) : null}
                                    </div>
                                ) : null}

                                <div className="flex flex-col gap-2">
                                    <Button
                                        type="button"
                                        variant="outline"
                                        onClick={handleClearOverrideSelection}
                                        disabled={selectedOverrideOrders.length === 0}
                                    >
                                        Clear selection
                                    </Button>
                                    <Button
                                        type="button"
                                        onClick={() => void handlePickerOverride()}
                                        disabled={selectedOverrideOrders.length === 0 || !selectedPickerEmail || pickerOverrideMutation.isPending}
                                        className="btn-lift"
                                    >
                                        {pickerOverrideMutation.isPending ? (
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                        ) : null}
                                        {pickerOverrideMutation.isPending ? "Updating..." : "Override picker"}
                                    </Button>
                                </div>
                            </div>
                        </aside>
                    </div>
                </div>
            </section>

        </div>
    );
}
