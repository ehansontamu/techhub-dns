import type { Order } from "../types/order";

export function getCompletedTodayOrders(orders: Order[], today: Date = new Date()): Order[] {
  const todayStart = new Date(today);
  todayStart.setHours(0, 0, 0, 0);

  return orders
    .filter((order) => {
      if (!order.signature_captured_at) return false;
      const signatureDate = new Date(order.signature_captured_at);
      signatureDate.setHours(0, 0, 0, 0);
      return signatureDate.getTime() === todayStart.getTime();
    })
    .sort((a, b) => {
      const aTime = a.signature_captured_at ? new Date(a.signature_captured_at).getTime() : 0;
      const bTime = b.signature_captured_at ? new Date(b.signature_captured_at).getTime() : 0;
      return bTime - aTime;
    });
}
