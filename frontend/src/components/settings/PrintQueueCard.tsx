import { useQuery } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";

import { settingsApi } from "../../api/settings";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { formatTimestamp, getStatusBadgeVariant } from "./utils";

function formatJobStatus(status: string) {
    return status.replace(/_/g, " ");
}

export function PrintQueueCard() {
    const printJobsQuery = useQuery({
        queryKey: ["settings", "print-jobs"],
        queryFn: async () => settingsApi.getPrintJobs(undefined, 5),
        refetchInterval: 60_000,
    });

    const jobs = printJobsQuery.data?.jobs ?? [];

    return (
        <Card className="border-border/70 bg-card/80">
            <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                <div>
                    <CardTitle className="text-base">Print queue</CardTitle>
                    <CardDescription>Recent picklist job diagnostics without opening the print agent.</CardDescription>
                </div>
                <Button variant="outline" size="sm" onClick={() => void printJobsQuery.refetch()} disabled={printJobsQuery.isFetching}>
                    {printJobsQuery.isFetching ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                    Refresh
                </Button>
            </CardHeader>
            <CardContent className="space-y-3">
                {jobs.length ? (
                    jobs.map((job) => (
                        <div key={job.id} className="rounded-xl border bg-muted/20 p-3">
                            <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                                <div className="min-w-0">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <p className="break-words text-sm font-medium">{job.order_id}</p>
                                        <Badge variant={getStatusBadgeVariant(job.status === "completed" ? "active" : job.status === "failed" ? "error" : "warning")}>
                                            {formatJobStatus(job.status)}
                                        </Badge>
                                    </div>
                                    <p className="break-words text-xs text-muted-foreground">
                                        {job.document_type} · {job.trigger_source} · attempts {job.attempt_count}
                                    </p>
                                    <p className="break-words text-xs text-muted-foreground">Updated {formatTimestamp(job.updated_at)}</p>
                                    {job.last_error ? <p className="mt-1 break-words text-xs text-destructive">{job.last_error}</p> : null}
                                </div>
                                <p className="break-words text-xs text-muted-foreground">{formatTimestamp(job.completed_at || job.claimed_at || job.created_at)}</p>
                            </div>
                        </div>
                    ))
                ) : (
                    <p className="text-sm text-muted-foreground">No recent print jobs found.</p>
                )}
            </CardContent>
        </Card>
    );
}
