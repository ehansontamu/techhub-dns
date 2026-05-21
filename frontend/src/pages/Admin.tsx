import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Settings2 } from "lucide-react";
import { toast } from "sonner";

import { settingsApi, type SystemSettings } from "../api/settings";
import { useAuth } from "../contexts/AuthContext";
import { SectionErrorBoundary } from "../components/error-boundaries/AppErrorBoundaries";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { extractApiErrorMessage } from "../utils/apiErrors";

const AdminsTab = lazy(() => import("../components/admin/AdminsTab"));

type RuleKey =
    | "email_notifications_enabled"
    | "teams_recipient_notifications_enabled"
    | "document_signing_enabled"
    | "picklist_auto_print_enabled"
    | "require_asset_tags_before_picklist"
    | "require_same_user_for_tagging_and_picklist"
    | "require_partial_picklist_confirmation"
    | "picklist_print_claim_timeout_seconds";

type RuleKind = "boolean" | "integer";

interface RuleMeta {
    key: RuleKey;
    title: string;
    description: string;
    kind: RuleKind;
}

const adminQueryKeys = {
    all: ["admin"] as const,
    settings: () => [...adminQueryKeys.all, "settings"] as const,
};

const RULES: RuleMeta[] = [
    { key: "document_signing_enabled", title: "Document signing", description: "Require signing to complete delivery.", kind: "boolean" },
    { key: "email_notifications_enabled", title: "Email notifications", description: "Send email on workflow events.", kind: "boolean" },
    { key: "teams_recipient_notifications_enabled", title: "Teams notifications", description: "Send Teams messages to recipients.", kind: "boolean" },
    { key: "picklist_auto_print_enabled", title: "Picklist auto-print", description: "Push generated picklists to the print queue.", kind: "boolean" },
    { key: "require_asset_tags_before_picklist", title: "Asset tags before picklist", description: "Gate picklist generation on completed tagging.", kind: "boolean" },
    { key: "require_same_user_for_tagging_and_picklist", title: "Same user tag + picklist", description: "Require the same operator for both steps.", kind: "boolean" },
    { key: "require_partial_picklist_confirmation", title: "Confirm partial picklists", description: "Prompt before creating a partial-leg picklist.", kind: "boolean" },
    { key: "picklist_print_claim_timeout_seconds", title: "Picklist claim timeout", description: "Seconds before a claimed print job can be reclaimed.", kind: "integer" },
];

const parseBoolean = (value: string | undefined | null) => (value || "false").toLowerCase() === "true";

const formatRuleValue = (rule: RuleMeta, settings: SystemSettings | null) => {
    const current = settings?.[rule.key];
    if (!current) return "-";
    if (rule.kind === "boolean") return parseBoolean(current.value) ? "Enabled" : "Disabled";
    return current.value || "-";
};

export default function Admin() {
    const { user, isAdmin, isLoading: authLoading } = useAuth();
    const queryClient = useQueryClient();
    const [timeoutDraft, setTimeoutDraft] = useState("");
    const [savingTimeout, setSavingTimeout] = useState(false);

    const adminQueriesEnabled = isAdmin && !authLoading;

    const settingsQuery = useQuery({
        queryKey: adminQueryKeys.settings(),
        enabled: adminQueriesEnabled,
        queryFn: async (): Promise<SystemSettings> => settingsApi.getSettings(),
    });

    const settings = settingsQuery.data ?? null;

    useEffect(() => {
        if (!settings) return;
        const current = settings.picklist_print_claim_timeout_seconds?.value ?? "";
        setTimeoutDraft((prev) => (prev || current));
    }, [settings]);

    const ruleMutation = useMutation({
        mutationFn: async ({ key, value }: { key: RuleKey; value: string }) => settingsApi.updateSetting(key, value, user?.email),
        onSuccess: (_result, variables) => {
            queryClient.setQueryData<SystemSettings | undefined>(adminQueryKeys.settings(), (current) => {
                if (!current) return current;
                return {
                    ...current,
                    [variables.key]: {
                        ...current[variables.key],
                        value: variables.value,
                        updated_at: new Date().toISOString(),
                        updated_by: user?.email ?? null,
                    },
                };
            });
            toast.success("Rule updated", { description: `${variables.key} = ${variables.value}` });
        },
        onError: (error: unknown) => {
            toast.error("Failed to update rule", { description: extractApiErrorMessage(error, "Please try again.") });
        },
    });

    const ruleCards = useMemo(() => RULES, []);

    const handleToggle = async (key: RuleKey, currentValue: string) => {
        const nextValue = parseBoolean(currentValue) ? "false" : "true";
        await ruleMutation.mutateAsync({ key, value: nextValue });
    };

    const handleSaveTimeout = async () => {
        const normalized = timeoutDraft.trim();
        if (!normalized) {
            toast.error("Enter a timeout in seconds");
            return;
        }
        const timeout = Number(normalized);
        if (!Number.isFinite(timeout) || timeout <= 0 || !Number.isInteger(timeout)) {
            toast.error("Timeout must be a positive whole number");
            return;
        }
        try {
            setSavingTimeout(true);
            await ruleMutation.mutateAsync({ key: "picklist_print_claim_timeout_seconds", value: String(timeout) });
        } finally {
            setSavingTimeout(false);
        }
    };

    if (authLoading) {
        return (
            <div className="container mx-auto py-6">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Loading...
                </div>
            </div>
        );
    }

    return (
        <div className="container mx-auto py-6 space-y-6">
            <div className="space-y-1">
                <h1 className="text-2xl font-semibold tracking-tight">Admin Tools</h1>
                <p className="text-sm text-muted-foreground">Durable workflow defaults. Operator actions live on /settings.</p>
            </div>

            <section className="grid grid-cols-1 gap-6 xl:grid-cols-2">
                <Card className="border-border/70 bg-card/80 shadow-none">
                    <CardHeader>
                        <CardTitle className="text-base">Workflow rules</CardTitle>
                        <CardDescription>Toggle defaults that shape normal order flow.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {ruleCards.map((rule) => {
                            const current = settings?.[rule.key];
                            const enabled = rule.kind === "boolean" ? parseBoolean(current?.value ?? "false") : false;
                            const value = formatRuleValue(rule, settings);
                            const isBusy = ruleMutation.isPending;

                            if (rule.kind === "integer") {
                                return (
                                    <div key={rule.key} className="rounded-xl border bg-muted/30 p-4 space-y-3">
                                        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                                            <div>
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <h3 className="text-sm font-semibold">{rule.title}</h3>
                                                    <Badge variant="secondary">{value}</Badge>
                                                </div>
                                                <p className="mt-1 text-sm text-muted-foreground">{rule.description}</p>
                                            </div>
                                            <Settings2 className="h-4 w-4 text-muted-foreground" />
                                        </div>
                                        <div className="flex flex-col gap-2 sm:flex-row">
                                            <Input
                                                type="number"
                                                min={1}
                                                step={1}
                                                value={timeoutDraft}
                                                onChange={(event) => setTimeoutDraft(event.target.value)}
                                                disabled={isBusy || savingTimeout}
                                                className="sm:max-w-[14rem]"
                                            />
                                            <Button
                                                type="button"
                                                variant="outline"
                                                onClick={() => void handleSaveTimeout()}
                                                disabled={isBusy || savingTimeout}
                                            >
                                                {isBusy || savingTimeout ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                                Save timeout
                                            </Button>
                                        </div>
                                    </div>
                                );
                            }

                            return (
                                <div key={rule.key} className="rounded-xl border bg-muted/30 p-4">
                                    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                                        <div>
                                            <div className="flex flex-wrap items-center gap-2">
                                                <h3 className="text-sm font-semibold">{rule.title}</h3>
                                                <Badge variant={enabled ? "success" : "secondary"}>{enabled ? "Enabled" : "Disabled"}</Badge>
                                            </div>
                                            <p className="mt-1 text-sm text-muted-foreground">{rule.description}</p>
                                        </div>
                                        <Button
                                            type="button"
                                            size="sm"
                                            variant={enabled ? "outline" : "default"}
                                            disabled={isBusy}
                                            onClick={() => void handleToggle(rule.key, current?.value ?? "false")}
                                        >
                                            {isBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                                            {enabled ? "Disable" : "Enable"}
                                        </Button>
                                    </div>
                                </div>
                            );
                        })}
                    </CardContent>
                </Card>

                <Card className="border-border/70 bg-card/80 shadow-none">
                    <CardHeader>
                        <CardTitle className="text-base">Admin allowlist</CardTitle>
                        <CardDescription>Users with admin access.</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <SectionErrorBoundary title="Admin allowlist failed" message="Try reloading the panel.">
                            <Suspense
                                fallback={
                                    <Card>
                                        <CardContent className="p-6">
                                            <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                                <Loader2 className="h-4 w-4 animate-spin" />
                                                Loading...
                                            </div>
                                        </CardContent>
                                    </Card>
                                }
                            >
                                <AdminsTab />
                            </Suspense>
                        </SectionErrorBoundary>
                    </CardContent>
                </Card>
            </section>
        </div>
    );
}
