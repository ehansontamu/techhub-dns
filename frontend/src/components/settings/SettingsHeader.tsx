import { Link } from "react-router-dom";
import { ArrowRight, ChevronRight, ShieldCheck } from "lucide-react";

import type { SyncHealthResponse, SystemStatusResponse } from "../../api/settings";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "../ui/card";
import { formatStatusLabel, getStatusBadgeVariant } from "./utils";

interface SettingsHeaderProps {
    syncHealth?: SyncHealthResponse | null;
    systemStatus?: SystemStatusResponse | null;
}

export function SettingsHeader({ syncHealth, systemStatus }: SettingsHeaderProps) {
    const statusItems = systemStatus
        ? [
              systemStatus.saml_auth,
              systemStatus.graph_api,
              systemStatus.sharepoint,
              systemStatus.inflow_sync,
          ]
        : [];

    const activeCount = statusItems.filter((item) => item.status === "active").length;
    const warningCount = statusItems.filter((item) => item.status === "warning").length;
    const failedWebhook = Boolean(syncHealth?.inflow.webhook_failed);
    const statusSummary = systemStatus ? `${activeCount}/${statusItems.length} systems active` : "Loading system status";

    return (
        <Card className="relative overflow-hidden border-border/70 bg-card/80">
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/40 to-transparent" />
            <CardHeader className="space-y-4">
                <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                    <div className="min-w-0 space-y-2">
                        <div className="flex flex-wrap items-center gap-2">
                            <Badge variant={failedWebhook ? "warning" : "success"}>
                                {failedWebhook ? "Webhook attention needed" : "Live operations"}
                            </Badge>
                            <Badge variant="outline">{statusSummary}</Badge>
                            {systemStatus && warningCount ? <Badge variant="warning">{warningCount} warning{warningCount === 1 ? "" : "s"}</Badge> : null}
                        </div>
                        <h1 className="text-[clamp(1.75rem,4vw,2.5rem)] leading-none tracking-tight">Settings</h1>
                        <CardDescription className="max-w-2xl text-sm md:text-base">
                            Operator diagnostics, recovery, and high-risk overrides. Durable policy belongs on /admin.
                        </CardDescription>
                    </div>

                    <div className="flex flex-wrap gap-2 md:justify-end">
                        <Button asChild variant="outline" size="sm">
                            <Link to="/admin">
                                Admin tools
                                <ArrowRight className="ml-2 h-4 w-4" />
                            </Link>
                        </Button>
                        <Button asChild variant="outline" size="sm">
                            <Link to="/audit">Audit trail</Link>
                        </Button>
                        <Button asChild variant="outline" size="sm">
                            <Link to="/sessions">Sessions</Link>
                        </Button>
                    </div>
                </div>

                <div className="grid gap-3 xl:grid-cols-[1.2fr_1fr]">
                    <div className="grid gap-3 sm:grid-cols-2">
                        {statusItems.map((item) => (
                            <div key={item.name} className="min-w-0 rounded-xl border bg-muted/20 p-3">
                                <div className="flex min-w-0 items-start justify-between gap-2">
                                    <div className="min-w-0">
                                        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{item.name}</p>
                                        <p className="mt-1 break-words text-sm font-medium">{item.details}</p>
                                    </div>
                                    <Badge variant={getStatusBadgeVariant(item.status)}>{formatStatusLabel(item.status)}</Badge>
                                </div>
                            </div>
                        ))}
                    </div>
                    <div className="rounded-xl border border-dashed bg-background/60 p-4">
                        <div className="flex min-w-0 items-start gap-3">
                            <div className="mt-1 flex h-8 w-8 items-center justify-center rounded-full border border-border/70 bg-muted/40">
                                <ChevronRight className="h-4 w-4 text-muted-foreground" />
                            </div>
                            <div className="min-w-0 space-y-1">
                                <p className="text-sm font-medium">Operational focus</p>
                                <p className="break-words text-sm text-muted-foreground">
                                    Use this page for live checks, recovery actions, and temporary overrides. Keep long-lived policy in admin tools.
                                </p>
                            </div>
                        </div>
                    </div>
                </div>

                <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                    <ShieldCheck className="h-4 w-4" />
                <span className="break-words">Configuration visibility is redacted to status, health, and last-seen information only.</span>
                </div>
            </CardHeader>

            <CardContent className="grid gap-3 border-t border-border/60 pt-5 sm:grid-cols-3">
                <div className="rounded-xl border bg-muted/20 p-3">
                    <div className="text-xs font-medium uppercase text-muted-foreground">Server time</div>
                    <p className="mt-1 text-sm">{syncHealth?.server_time ?? "-"}</p>
                </div>
                <div className="rounded-xl border bg-muted/20 p-3">
                    <div className="text-xs font-medium uppercase text-muted-foreground">Last webhook</div>
                    <p className="mt-1 text-sm">{syncHealth?.inflow.last_webhook_received_at ?? "Never"}</p>
                </div>
                <div className="rounded-xl border bg-muted/20 p-3">
                    <div className="text-xs font-medium uppercase text-muted-foreground">Webhook state</div>
                    <p className="mt-1 text-sm">
                        {syncHealth?.inflow.webhook_enabled ? "Enabled" : "Disabled"}
                        {failedWebhook ? " · last attempt failed" : ""}
                    </p>
                </div>
            </CardContent>
        </Card>
    );
}
