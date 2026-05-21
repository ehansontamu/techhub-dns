export type OrderReorderPlacement = "before" | "after" | "end";

export function reorderOrderIds(
  orderIds: string[],
  draggedOrderId: string,
  targetOrderId: string | null,
  placement: OrderReorderPlacement
): string[] {
  const draggedIndex = orderIds.indexOf(draggedOrderId);
  if (draggedIndex < 0) return orderIds;

  const next = orderIds.filter((orderId) => orderId !== draggedOrderId);

  if (placement === "end" || !targetOrderId) {
    next.push(draggedOrderId);
    return next;
  }

  if (targetOrderId === draggedOrderId) return orderIds;

  const targetIndex = next.indexOf(targetOrderId);
  if (targetIndex < 0) return orderIds;

  const insertIndex = placement === "before" ? targetIndex : targetIndex + 1;
  next.splice(insertIndex, 0, draggedOrderId);
  return next;
}
