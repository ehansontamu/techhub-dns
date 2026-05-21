import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ChevronDown, Eye } from "lucide-react";
import { toast } from "sonner";

import StatusBadge from "./StatusBadge";
import StatusPathViz from "./audit/StatusPathViz";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "./ui/table";
import { formatToCentralTime } from "../utils/timezone";
import {
  canGeneratePicklist as canGeneratePicklistForOrder,
  getOrderProductTableView,
  getPartialOrderInfo,
  isActiveRemainderLegWaitingOnPickup,
} from "../utils/orderPartial";
import {
  AuditLog,
  OrderDetail as OrderDetailType,
  OrderStatus,
  OrderStatusDisplayNames,
  TeamsNotification,
} from "../types/order";

interface ShippingAddress {
  address1?: string;
  address2?: string;
  city?: string;
  state?: string;
  postalCode?: string;
}

function getShippingAddress(order: OrderDetailType): ShippingAddress | null {
  if (!order.inflow_data || typeof order.inflow_data !== "object") return null;
  const addr = (order.inflow_data as Record<string, unknown>).shippingAddress;
  return typeof addr === "object" && addr !== null ? (addr as ShippingAddress) : null;
}

interface OrderDetailProps {
  order: OrderDetailType;
  auditLogs: AuditLog[];
  notifications: TeamsNotification[];
  onStatusChange: (newStatus: OrderStatus, reason?: string) => void;
  onRollbackStatus: (newStatus: OrderStatus) => void;
  onTagOrder: (tagIds: string[]) => Promise<void>;
  onRequestTags: () => Promise<void>;
  onGeneratePicklist: (options?: { createPartialLeg?: boolean }) => Promise<void>;
  generatingPicklist: boolean;
}

export default function OrderDetail({
  order,
  auditLogs,
  notifications,
  onTagOrder,
  onRequestTags,
  onGeneratePicklist,
  onStatusChange,
  onRollbackStatus,
  generatingPicklist,
}: OrderDetailProps) {
  const latestNotification = notifications[0];
  const [tagPrintedDialogOpen, setTagPrintedDialogOpen] = useState(false);
  const [tagConfirming, setTagConfirming] = useState(false);
  const [requestTagsConfirmOpen, setRequestTagsConfirmOpen] = useState(false);
  const [requestingTags, setRequestingTags] = useState(false);
  const [statusDropdownOpen, setStatusDropdownOpen] = useState(false);

  const [issueDialogOpen, setIssueDialogOpen] = useState(false);
  const [partialConfirmOpen, setPartialConfirmOpen] = useState(false);
  const [partialConfirmSubmitting, setPartialConfirmSubmitting] = useState(false);

  const partialOrderInfo = getPartialOrderInfo(order);
  const productTableView = getOrderProductTableView(order);
  const isPartialLeg = partialOrderInfo.isPartialLeg;
  const remainderLegWaitingOnPickup = isActiveRemainderLegWaitingOnPickup(order);
  const recursiveSplitEligible =
    partialOrderInfo.hasRemainder &&
    partialOrderInfo.totalPicked > 0 &&
    partialOrderInfo.missingItems.length > 0;
  const shouldConfirmPartialPicklist = partialOrderInfo.isPartial && !isPartialLeg;
  const assetTagRequired = order.asset_tag_required !== false;
  const canGeneratePicklist = canGeneratePicklistForOrder(order);

  const requestSentAt =
    order.tag_data?.canopyorders_request_sent_at || order.tag_data?.tag_request_sent_at;
  const requestSentBy = order.tag_data?.canopyorders_request_sent_by;
  const requestSent = Boolean(requestSentAt || order.tag_data?.tag_request_status === "sent");

  const canRequestTags =
    assetTagRequired &&
    order.status === OrderStatus.PICKED &&
    !order.tagged_at &&
    Boolean(order.inflow_order_id) &&
    !remainderLegWaitingOnPickup;







  const getRollbackTargets = (status: OrderStatus): OrderStatus[] => {
    // Rollback is only allowed from ISSUE status (quarantine-first workflow)
    if (status === OrderStatus.ISSUE) {
      return [OrderStatus.PICKED, OrderStatus.QA, OrderStatus.PRE_DELIVERY];
    }
    return [];
  };

  const handleRequestTags = async (): Promise<boolean> => {
    if (!canRequestTags) return false;

    setRequestingTags(true);
    try {
      await onRequestTags();
      return true;
    } catch (error) {
      toast.error("Failed to request tags");
      return false;
    } finally {
      setRequestingTags(false);
    }
  };

  const handleRequestTagsConfirm = async () => {
    const ok = await handleRequestTags();
    if (ok) setRequestTagsConfirmOpen(false);
  };

  const handleTagPrintedConfirm = async () => {
    setTagConfirming(true);
    try {
      await onTagOrder([]);
      setTagPrintedDialogOpen(false);
    } catch (error) {
      toast.error("Failed to confirm tags printed");
    } finally {
      setTagConfirming(false);
    }
  };

  const handleGeneratePicklist = async (options?: { createPartialLeg?: boolean }) => {
    setPartialConfirmSubmitting(true);
    try {
      await onGeneratePicklist(options);
      setPartialConfirmOpen(false);
    } catch (error) {
      toast.error("Failed to generate picklist");
    } finally {
      setPartialConfirmSubmitting(false);
    }
  };

  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-transparent bg-card p-6 shadow-none">
        <div className="flex flex-col gap-5">
          <h2 className="text-2xl font-semibold tracking-tight">Order {order.inflow_order_id}</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <p className="text-sm font-medium text-muted-foreground">Status</p>
<div className="mt-1 flex items-center gap-1">
                <StatusBadge status={order.status} />

                {/* Context-aware actions dropdown */}
                <div className="relative">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setStatusDropdownOpen(!statusDropdownOpen)}
                    className="flex h-7 w-7 items-center justify-center rounded-full p-0"
                  >
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  </Button>
                  {statusDropdownOpen && (
                    <div className="absolute left-0 top-full mt-1 z-10 min-w-[180px] rounded-md border bg-popover p-1 shadow-md">
                      {/* For non-ISSUE orders: raise issue */}
                      {order.status !== OrderStatus.ISSUE && (
                        <button
                          onClick={() => {
                            setIssueDialogOpen(true);
                            setStatusDropdownOpen(false);
                          }}
                          className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-accent"
                        >
                          <AlertTriangle className="h-4 w-4 text-destructive" />
                          <span>Raise Issue</span>
                        </button>
                      )}
                      {/* For ISSUE orders: recovery targets */}
                      {order.status === OrderStatus.ISSUE &&
                        getRollbackTargets(order.status).map((status) => (
                          <button
                            key={status}
                            onClick={() => {
                              onRollbackStatus(status);
                              setStatusDropdownOpen(false);
                            }}
                            className="block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-accent"
                          >
                            {OrderStatusDisplayNames[status]}
                          </button>
                        ))}
                    </div>
                  )}
                </div>

                {/* Issue Reason Dialog */}
                <Dialog open={issueDialogOpen} onOpenChange={setIssueDialogOpen}>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>Raise Issue</DialogTitle>
                      <DialogDescription>
                        Provide a reason for flagging this order as an issue. This will pause the workflow until resolved.
                      </DialogDescription>
                    </DialogHeader>
                    <div className="py-4">
                      <label htmlFor="issue-reason" className="text-sm font-medium text-foreground">
                        Reason
                      </label>
                      <textarea
                        id="issue-reason"
                        placeholder="Describe the issue..."
                        className="mt-2 w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                        rows={3}
                      />
                    </div>
                    <DialogFooter>
                      <Button variant="outline" onClick={() => setIssueDialogOpen(false)}>
                        Cancel
                      </Button>
                      <Button
                        variant="destructive"
                        onClick={() => {
                          const reason = (document.getElementById("issue-reason") as HTMLTextAreaElement)?.value;
                          onStatusChange(OrderStatus.ISSUE, reason || "");
                          setIssueDialogOpen(false);
                        }}
                      >
                        Confirm Issue
                      </Button>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>
              </div>
            </div>
            <div>
              <p className="text-sm font-medium text-muted-foreground">Recipient</p>
              <p className="text-sm text-foreground">
                {order.recipient_name || <span className="text-muted-foreground italic">N/A</span>}
              </p>
            </div>
            <div>
              <p className="text-sm font-medium text-muted-foreground">Contact</p>
              <p className="text-sm text-foreground">
                {order.recipient_contact || "N/A"}
              </p>
            </div>
            <div>
              <p className="text-sm font-medium text-muted-foreground">Location</p>
              <p className="text-sm text-foreground">
                {order.delivery_location || "N/A"}
              </p>
              {getShippingAddress(order) && (
                <p className="mt-1 text-sm text-muted-foreground">
                  {[
                    getShippingAddress(order)!.address1,
                    getShippingAddress(order)!.address2,
                    getShippingAddress(order)!.city,
                    getShippingAddress(order)!.state,
                    getShippingAddress(order)!.postalCode,
                  ]
                    .filter(Boolean)
                    .join(", ")}
                </p>
              )}
            </div>
            <div>
              <p className="text-sm font-medium text-muted-foreground">PO Number</p>
              <p className="text-sm text-foreground">{order.po_number || "N/A"}</p>
            </div>
            <div>
              <p className="text-sm font-medium text-muted-foreground">Deliverer</p>
              <p className="text-sm text-foreground">
                {order.assigned_deliverer || "Unassigned"}
              </p>
            </div>
            {partialOrderInfo.isPartial || partialOrderInfo.hasRemainder || isPartialLeg ? (
              <div className="sm:col-span-2 rounded-xl border border-amber-500/20 bg-amber-500/5 p-4">
                <div className="flex gap-3">
                  <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" />
                  <div className="min-w-0 space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-medium text-foreground">
                        {isPartialLeg ? "Picked leg" : partialOrderInfo.hasRemainder ? "Remainder leg" : "Partial order"}
                      </p>
                      <Badge variant="warning">
                        {partialOrderInfo.totalPicked}/{partialOrderInfo.totalOrdered} picked
                      </Badge>
                      {isPartialLeg ? <Badge variant="secondary">Picked leg</Badge> : null}
                      {partialOrderInfo.hasRemainder ? (
                        <Badge variant="secondary">Remainder leg</Badge>
                      ) : null}
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {isPartialLeg
                        ? "This is the picked leg. It only contains the items already selected for this split."
                        : "This order is the remainder leg. Generating the picklist will keep the original order as the remainder and create the picked leg if needed."}
                    </p>
                    <div className="grid gap-2 text-sm sm:grid-cols-2">
                      {partialOrderInfo.parentOrderId ? (
                        <div>
                          <span className="font-medium text-foreground">Parent order:</span>{" "}
                          <Link
                            to={`/orders/${partialOrderInfo.parentOrderId}`}
                            className="text-primary underline-offset-4 hover:underline"
                          >
                            {partialOrderInfo.parentInflowOrderId || partialOrderInfo.parentOrderId}
                          </Link>
                        </div>
                      ) : null}
                      {partialOrderInfo.remainderOrderId ? (
                        <div>
                          <span className="font-medium text-foreground">Remainder order:</span>{" "}
                          <Link
                            to={`/orders/${partialOrderInfo.remainderOrderId}`}
                            className="text-primary underline-offset-4 hover:underline"
                          >
                            {partialOrderInfo.remainderInflowOrderId || partialOrderInfo.remainderOrderId}
                          </Link>
                        </div>
                      ) : null}
                    </div>
                    {remainderLegWaitingOnPickup ? (
                      <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                        {recursiveSplitEligible
                          ? "This remainder leg still has picked items. Asset tagging and order details stay blocked until the remainder is fully picked, but you can generate another picklist to split off the picked subset."
                          : "This remainder leg is waiting on the remaining items to be picked. Asset tagging, picklist generation, and order details are blocked until that happens."}
                      </div>
                    ) : null}
                    {partialOrderInfo.missingItems.length > 0 ? (
                      <details className="group">
                        <summary className="cursor-pointer select-none text-sm font-medium text-foreground">
                          View missing items
                        </summary>
                        <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
                          {partialOrderInfo.missingItems.map((item) => (
                            <li key={item.product_id}>
                              {item.product_name}: {item.picked}/{item.ordered}
                            </li>
                          ))}
                        </ul>
                      </details>
                    ) : null}
                  </div>
                </div>
              </div>
            ) : null}
            {order.issue_reason && (
              <div className="sm:col-span-2">
                <p className="text-sm font-medium text-muted-foreground">Issue Reason</p>
                <p className="text-sm text-destructive">{order.issue_reason}</p>
              </div>
            )}
          </div>
          {order.status === OrderStatus.IN_DELIVERY && (
            <div>
              <Button asChild variant="destructive">
                <Link to={`/document-signing?orderId=${order.id}`}>
                  Open Document Signing
                </Link>
              </Button>
            </div>
          )}
        </div>
      </section>

      <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
        <div className="space-y-4">

          <h3 className="text-lg font-semibold tracking-tight">Preparation Checklist</h3>
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="font-medium text-foreground">Asset Tagging</p>
                <p className="text-sm text-muted-foreground">
                  {!assetTagRequired
                    ? "Not required"
                    : order.tagged_at
                      ? `Completed ${formatToCentralTime(order.tagged_at)}`
                      : requestSentAt
                        ? `Requested ${formatToCentralTime(requestSentAt)}`
                        : "Pending"}
                </p>
                {assetTagRequired && requestSent && !order.tagged_at && requestSentBy && (
                  <p className="text-xs text-muted-foreground">
                    Requested by {requestSentBy}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2">
                {assetTagRequired && !order.tagged_at && order.status === OrderStatus.PICKED && !remainderLegWaitingOnPickup && (
                  <Button
                    onClick={() => setRequestTagsConfirmOpen(true)}
                    disabled={!canRequestTags || requestingTags}
                    size="sm"
                  >
                    {requestingTags ? "Requesting..." : "Request Tags"}
                  </Button>
                )}
                {assetTagRequired && !order.tagged_at && requestSent && !remainderLegWaitingOnPickup && (
                  <Button
                    onClick={() => setTagPrintedDialogOpen(true)}
                    variant="secondary"
                    size="sm"
                  >
                    Mark Tagged
                  </Button>
                )}
              </div>
            </div>

            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="font-medium text-foreground">
                  Generate Picklist & Email Order Details
                </p>
                <p className="text-sm text-muted-foreground">
                  {order.picklist_generated_at
                    ? `Generated ${formatToCentralTime(order.picklist_generated_at)}`
                    : "Pending"}
                </p>
                {order.picklist_generated_at && (
                  <p className="text-xs text-muted-foreground">
                    ✓ Order Details emailed to recipient
                  </p>
                )}
                {order.picklist_path && (
                  <div className="flex flex-wrap items-center gap-2 mt-1">
                    <a
                      className="inline-flex items-center gap-1.5 text-sm text-primary underline-offset-4 hover:underline"
                      href={`/api/orders/${order.id}/picklist`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <Eye className="h-3.5 w-3.5" />
                      Preview picklist
                    </a>
                  </div>
                )}
              </div>
                <Button
                  onClick={() => {
                    if (shouldConfirmPartialPicklist) {
                      setPartialConfirmOpen(true);
                      return;
                    }
                    void handleGeneratePicklist();
                  }}
                  disabled={
                    generatingPicklist ||
                    partialConfirmSubmitting ||
                    !canGeneratePicklist ||
                    (remainderLegWaitingOnPickup && !recursiveSplitEligible)
                  }
                  size="sm"
                >
                {order.picklist_generated_at
                  ? "Generated"
                  : generatingPicklist || partialConfirmSubmitting
                    ? "Generating..."
                    : shouldConfirmPartialPicklist
                      ? "Generate & Review"
                      : "Generate & Email"}
              </Button>
            </div>

            {order.signed_picklist_path && (
              <div className="flex items-center justify-between gap-4 border-t border-border/40 pt-4">
                <div>
                  <p className="font-medium text-foreground">
                    Signed Picklist
                  </p>
                  <p className="text-sm text-muted-foreground">
                    Signed PDF with customer signature
                  </p>
                  <div className="flex flex-wrap items-center gap-2 mt-1">
                    <a
                      className="inline-flex items-center gap-1.5 text-sm text-primary underline-offset-4 hover:underline"
                      href={`/api/orders/${order.id}/signed-picklist`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <Eye className="h-3.5 w-3.5" />
                      Preview signed picklist
                    </a>
                  </div>
                </div>
              </div>
            )}


            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="font-medium text-foreground">QA Checklist</p>
                <p className="text-sm text-muted-foreground">
                  {order.qa_completed_at
                    ? `Completed ${formatToCentralTime(order.qa_completed_at)}${order.qa_completed_by ? ` by ${order.qa_completed_by}` : ""}`
                    : "Pending"}
                </p>
                {order.qa_method && (
                  <p className="text-xs text-muted-foreground">
                    Method: {order.qa_method}
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>

      {order.inflow_data && (
        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
          <div className="space-y-1">
            <h3 className="text-lg font-semibold tracking-tight">{productTableView.title}</h3>
            <p className="text-sm text-muted-foreground">{productTableView.description}</p>
          </div>
          <div className="mt-4 rounded-lg border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[60px]">#</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead>Serials</TableHead>
                  <TableHead className="text-right">Quantity</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {productTableView.rows.length > 0 ? (
                  productTableView.rows.map((row: { productId: string; productName: string; quantity: number; serials: string[] }, index: number) => (
                    <TableRow key={row.productId}>
                      <TableCell className="text-muted-foreground">{index + 1}</TableCell>
                      <TableCell className="font-medium">{row.productName}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {row.serials.length > 0 ? row.serials.join(", ") : "-"}
                      </TableCell>
                      <TableCell className="text-right">{row.quantity}</TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell className="text-muted-foreground" colSpan={4}>
                      {productTableView.emptyState}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </section>
      )}

      {latestNotification && (
        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
          <h3 className="text-lg font-semibold tracking-tight">Teams Notification</h3>
          <div className="mt-4 space-y-2">
              <p className="text-sm text-foreground">
                <span className="font-medium">Status:</span>{" "}
                <Badge
                  variant={
                    latestNotification.status === "sent"
                      ? "success"
                      : latestNotification.status === "failed"
                        ? "destructive"
                        : "warning"
                  }
                >
                  {latestNotification.status}
                </Badge>
              </p>
              {latestNotification.sent_at && (
                <p className="text-sm text-foreground">
                  <span className="font-medium">Sent at:</span>{" "}
                  {formatToCentralTime(latestNotification.sent_at)}
                </p>
              )}
              {latestNotification.error_message && (
                <div>
                  <p className="text-sm text-destructive">
                    <span className="font-medium">Error:</span>{" "}
                    {latestNotification.error_message}
                  </p>
                </div>
              )}
            </div>
        </section>
      )}

      <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
        <h3 className="text-lg font-semibold tracking-tight">Status Path</h3>
          {auditLogs.length > 0 ? (
            <>
              <StatusPathViz auditLogs={auditLogs} title="Workflow path" />
              <div className="max-h-[320px] space-y-2 overflow-auto pr-2">
                {auditLogs
                  .slice()
                  .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
                  .map((log) => (
                    <div key={log.id} className="rounded-lg border border-maroon-900/10 bg-card p-3">
                      <p className="text-sm font-medium text-foreground">
                        {log.from_status || "Created"} -&gt; {log.to_status}
                      </p>
                      <p className="text-sm text-muted-foreground">
                        {formatToCentralTime(log.timestamp)}
                        {log.changed_by && ` by ${log.changed_by}`}
                      </p>
                      {log.reason ? <p className="mt-1 text-sm text-muted-foreground">{log.reason}</p> : null}
                    </div>
                  ))}
              </div>
            </>
          ) : (
            <div className="rounded-lg border border-dashed border-border bg-card p-4 text-center text-sm text-muted-foreground">
              No workflow audit history available.
            </div>
          )}
      </section>

      {order.inflow_data && (
        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
          <details className="group">
            <summary className="cursor-pointer select-none list-none [&::-webkit-details-marker]:hidden">
              <div className="relative pr-10">
                <h3 className="text-lg font-semibold tracking-tight">Inflow Data</h3>
                <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground transition-transform group-open:rotate-180" />
              </div>
            </summary>
            <pre className="mt-4 rounded-md bg-muted/50 p-4 overflow-auto text-sm">
                {JSON.stringify(order.inflow_data, null, 2)}
              </pre>
          </details>
        </section>
      )}

      <Dialog
        open={requestTagsConfirmOpen}
        onOpenChange={(open) => {
          if (requestingTags) return;
          setRequestTagsConfirmOpen(open);
        }}
      >
        <DialogContent aria-describedby={undefined} className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Request tags for {order.inflow_order_id}?</DialogTitle>
          </DialogHeader>

          <div className="space-y-1 text-sm">
            <p className="text-foreground">
              <span className="font-medium">Recipient:</span> {order.recipient_name || <span className="text-muted-foreground italic">N/A</span>}
            </p>
            <p className="text-foreground">
              <span className="font-medium">Location:</span> {order.delivery_location || "N/A"}
            </p>
          </div>

          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              variant="outline"
              onClick={() => setRequestTagsConfirmOpen(false)}
              disabled={requestingTags}
            >
              Cancel
            </Button>
            <Button onClick={() => void handleRequestTagsConfirm()} disabled={requestingTags}>
              {requestingTags ? "Requesting..." : "Request Tags"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={tagPrintedDialogOpen}
        onOpenChange={(open) => {
          if (tagConfirming) return;
          setTagPrintedDialogOpen(open);
        }}
      >
        <DialogContent aria-describedby={undefined} className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Confirm tags printed?</DialogTitle>
            <DialogDescription>
              Marking tags as printed will update this order so it can continue through the picklist and QA workflow.
            </DialogDescription>
          </DialogHeader>

          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              variant="outline"
              onClick={() => setTagPrintedDialogOpen(false)}
              disabled={tagConfirming}
            >
              Cancel
            </Button>
            <Button onClick={() => void handleTagPrintedConfirm()} disabled={tagConfirming}>
              {tagConfirming ? "Updating..." : "Mark Tagged"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={partialConfirmOpen}
        onOpenChange={(open) => {
          if (partialConfirmSubmitting) return;
          setPartialConfirmOpen(open);
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
              Create picked leg?
            </DialogTitle>
            <DialogDescription>
              {recursiveSplitEligible
                ? `This remainder leg already has picked items (${partialOrderInfo.totalPicked}/${partialOrderInfo.totalOrdered}). Generating the picklist will create another picked leg and keep the original as the remainder leg.`
                : `This order is partially picked (${partialOrderInfo.totalPicked}/${partialOrderInfo.totalOrdered} items). Generating the picklist will create the picked leg and keep the original as the remainder leg.`}
            </DialogDescription>
          </DialogHeader>

          {partialOrderInfo.missingItems.length > 0 ? (
            <div className="rounded-lg border border-border/70 bg-muted/30 p-3">
              <p className="text-sm font-medium text-foreground">Missing items</p>
              <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
                {partialOrderInfo.missingItems.map((item) => (
                  <li key={item.product_id}>
                    {item.product_name}: {item.picked}/{item.ordered}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" onClick={() => setPartialConfirmOpen(false)} disabled={partialConfirmSubmitting}>
              Cancel
            </Button>
            <Button onClick={() => void handleGeneratePicklist({ createPartialLeg: true })} disabled={partialConfirmSubmitting} className="bg-amber-500 hover:bg-amber-600">
              {partialConfirmSubmitting ? "Creating..." : "Create Partial Leg"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
