import type { SyncHealthResponse, SystemStatusResponse } from "../../api/settings";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { formatStatusLabel, formatTimestamp, getStatusBadgeVariant } from "./utils";

interface SystemHealthCardProps {
    syncHealth?: SyncHealthResponse | null;
    systemStatus?: SystemStatusResponse | null;
    isLoading?: boolean;
    isError?: boolean;
    errorMessage?: string;
    onRetry?: () => void;
}

export function SystemHealthCard({
    syncHealth,
    systemStatus,
    isLoading = false,
    isError = false,
    errorMessage,
    onRetry,
}: SystemHealthCardProps) {
    const statusCards = systemStatus
        ? [
              systemStatus.saml_auth,
              systemStatus.graph_api,
              systemStatus.sharepoint,
              systemStatus.inflow_sync,
          ]
        : [];

    return (
        <Card className="border-border/70 bg-card/80">
            <CardHeader>
                <CardTitle className="text-base">System status</CardTitle>
                <CardDescription>Safe feature visibility without exposing credentials or secrets.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                <div className="grid gap-3 md:grid-cols-3">
                    <div className="rounded-xl border bg-muted/20 p-3">
                        <div className="text-xs font-medium uppercase text-muted-foreground">Server time</div>
                        <p className="mt-1 text-sm">{formatTimestamp(syncHealth?.server_time)}</p>
                    </div>
                    <div className="rounded-xl border bg-muted/20 p-3">
                        <div className="text-xs font-medium uppercase text-muted-foreground">Webhook received</div>
                        <p className="mt-1 text-sm">{formatTimestamp(syncHealth?.inflow.last_webhook_received_at)}</p>
                    </div>
                    <div className="rounded-xl border bg-muted/20 p-3">
                        <div className="text-xs font-medium uppercase text-muted-foreground">Webhook health</div>
                        <p className="mt-1 text-sm">
                            {syncHealth?.inflow.webhook_enabled ? "Enabled" : "Disabled"}
                            {syncHealth?.inflow.webhook_failed ? " · last attempt failed" : ""}
                        </p>
                    </div>
                </div>

                {isError ? (
                    <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-4">
                        <p className="text-sm font-medium text-destructive">Unable to load system status</p>
                        <p className="mt-1 text-sm text-muted-foreground">{errorMessage ?? "Try refreshing this panel."}</p>
                        {onRetry ? (
                            <Button variant="outline" size="sm" className="mt-3" onClick={onRetry}>
                                Retry
                            </Button>
                        ) : null}
                    </div>
                ) : null}

                <div className="grid gap-3 lg:grid-cols-2">
                    {isLoading ? (
                        <div className="rounded-xl border bg-muted/20 p-4 text-sm text-muted-foreground">Loading status...</div>
                    ) : (
                        statusCards.map((item) => (
                            <div key={item.name} className="min-w-0 rounded-xl border bg-muted/20 p-4">
                                <div className="flex min-w-0 items-start justify-between gap-3">
                                    <div className="min-w-0">
                                        <h3 className="text-sm font-semibold">{item.name}</h3>
                                        <p className="mt-1 break-words text-sm text-muted-foreground">{item.details}</p>
                                    </div>
                                    <Badge variant={getStatusBadgeVariant(item.status)}>{formatStatusLabel(item.status)}</Badge>
                                </div>
                                <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
                                    <span>Enabled: {item.enabled ? "Yes" : "No"}</span>
                                    <span>Configured: {item.configured ? "Yes" : "No"}</span>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </CardContent>
        </Card>
    );
}
