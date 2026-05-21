import { useDeliveryRuns } from "../hooks/useDeliveryRuns";
import { Link } from "react-router-dom";
import { Badge } from "./ui/badge";
import { Skeleton } from "./Skeleton";

function getRunStatusVariant(status: string) {
    const normalized = status.toLowerCase().replace(/\s+/g, "_");

    if (/(cancel|canceled|cancelled|fail|failed|error)/.test(normalized)) return "destructive" as const;
    if (/(complete|completed|done|delivered)/.test(normalized)) return "secondary" as const;
    if (/(pending|queued|waiting|paused)/.test(normalized)) return "warning" as const;
    if (/(active|live|in_progress|inprogress|en_route|on_route|running)/.test(normalized)) return "success" as const;

    return "outline" as const;
}

export default function LiveDeliveryDashboard() {
    const { runs, loading, error } = useDeliveryRuns();
    const liveRegionMessage = loading
        ? "Loading active delivery runs"
        : runs.length === 0
            ? "No active delivery runs"
            : `Showing ${runs.length} active delivery runs`;

    return (
        <section className="p-4" aria-busy={loading} aria-describedby="live-delivery-summary">
            <p id="live-delivery-summary" className="sr-only" aria-live="polite">
                {liveRegionMessage}
            </p>
            {error && (
                <div className="mb-4 rounded-lg border border-destructive/20 bg-destructive/10 p-3 text-sm text-destructive" role="status" aria-live="polite">
                    {error}
                </div>
            )}

            {loading ? (
                <div className="space-y-3" role="status" aria-live="polite">
                    {Array.from({ length: 3 }).map((_, index) => (
                        <section key={index} className="rounded-2xl border border-border/70 bg-card/80 p-4 shadow-none">
                            <div className="space-y-3">
                                <div className="flex items-center justify-between gap-4">
                                    <div className="space-y-2">
                                        <Skeleton className="h-3 w-12" />
                                        <Skeleton className="h-4 w-28" />
                                    </div>
                                    <div className="space-y-2 text-right">
                                        <Skeleton className="ml-auto h-3 w-12" />
                                        <Skeleton className="ml-auto h-4 w-20" />
                                    </div>
                                </div>
                                <div className="grid grid-cols-3 gap-3">
                                    <Skeleton className="h-12 w-full" />
                                    <Skeleton className="h-12 w-full" />
                                    <Skeleton className="h-12 w-full" />
                                </div>
                            </div>
                        </section>
                    ))}
                </div>
            ) : runs.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border/70 bg-muted/20 p-6 text-sm text-muted-foreground" role="status" aria-live="polite">
                    No active delivery runs
                </div>
            ) : (
                <div className="grid grid-cols-1 gap-4">
                    {runs.map((r) => (
                        <Link
                            key={r.id}
                            to={`/delivery/runs/${r.id}`}
                            className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                        >
                            <section className="cursor-pointer rounded-2xl border border-border/70 bg-card/80 p-4 shadow-none transition-colors hover:bg-muted/30">
                                <div className="flex justify-between items-center">
                                    <div>
                                        <div className="text-sm text-muted-foreground">Run</div>
                                        <div className="font-medium">{r.name}</div>
                                    </div>
                                    <div className="text-right">
                                        <div className="text-sm text-muted-foreground">Vehicle</div>
                                        <div className="font-medium capitalize">{r.vehicle.replace('_', ' ')}</div>
                                    </div>
                                </div>

                                <div className="mt-3 grid grid-cols-3 gap-2 text-sm text-foreground">
                                    <div>
                                        <div className="text-xs text-muted-foreground">Runner</div>
                                        <div>{r.runner}</div>
                                    </div>
                                    <div>
                                        <div className="text-xs text-muted-foreground">Status</div>
                                        <div className="mt-1">
                                            <Badge variant={getRunStatusVariant(r.status)}>{r.status}</Badge>
                                        </div>
                                    </div>
                                    <div>
                                        <div className="text-xs text-muted-foreground">Orders</div>
                                        <div>{r.order_ids.length}</div>
                                    </div>
                                </div>
                            </section>
                        </Link>
                    ))}
                </div>
            )}
        </section>
    );
}
