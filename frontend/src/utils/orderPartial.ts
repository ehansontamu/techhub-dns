import type { Order, PickStatus, PickStatusItem } from "../types/order";

type PartialOrderSource = Pick<
  Order,
  | "pick_status"
  | "inflow_data"
  | "has_remainder"
  | "remainder_order_id"
  | "remainder_inflow_order_id"
  | "parent_order_id"
  | "parent_inflow_order_id"
  | "tagged_at"
  | "picklist_generated_at"
> & {
  asset_tag_required?: boolean;
};

export interface PartialOrderInfo {
  isPartial: boolean;
  isPartialLeg: boolean;
  totalOrdered: number;
  totalPicked: number;
  missingItems: PickStatusItem[];
  hasRemainder: boolean;
  remainderOrderId?: string;
  remainderInflowOrderId?: string;
  parentOrderId?: string;
  parentInflowOrderId?: string;
  shortfall: number;
}

export interface OrderProductRow {
  productId: string;
  productName: string;
  quantity: number;
  serials: string[];
}

export interface OrderProductTableView {
  title: string;
  description: string;
  rows: OrderProductRow[];
  emptyState: string;
}

const isTruthyFlag = (value: unknown): boolean => {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    return ["y", "yes", "true", "1"].includes(normalized);
  }
  return Boolean(value);
};

const toQuantity = (value: unknown): number => {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
};

const getLineQuantity = (line: unknown): number => {
  if (!line || typeof line !== "object") return 0;

  const quantity = (line as Record<string, unknown>).quantity;
  if (quantity && typeof quantity === "object") {
    return toQuantity((quantity as Record<string, unknown>).standardQuantity);
  }

  return toQuantity(quantity);
};

const getLineProductId = (line: unknown): string | null => {
  if (!line || typeof line !== "object") return null;

  const productId = (line as Record<string, unknown>).productId;
  return productId == null ? null : String(productId);
};

const getLineProductName = (line: unknown, fallback: string): string => {
  if (!line || typeof line !== "object") return fallback;

  const record = line as Record<string, unknown>;
  const product = record.product;
  if (product && typeof product === "object") {
    const name = (product as Record<string, unknown>).name;
    if (typeof name === "string" && name.trim()) {
      return name;
    }
  }

  const description = record.description;
  if (typeof description === "string" && description.trim()) {
    return description;
  }

  const productName = record.productName;
  if (typeof productName === "string" && productName.trim()) {
    return productName;
  }

  return fallback;
};

const normalizePickStatus = (value: unknown): PickStatus | null => {
  if (!value || typeof value !== "object") return null;

  const candidate = value as Record<string, unknown>;
  const isFullyPicked = Boolean(candidate.is_fully_picked);
  const totalOrdered = toQuantity(candidate.total_ordered);
  const totalPicked = toQuantity(candidate.total_picked);
  const missingItems = Array.isArray(candidate.missing_items)
    ? (candidate.missing_items as PickStatusItem[])
    : [];

  return {
    is_fully_picked: isFullyPicked,
    total_ordered: totalOrdered,
    total_picked: totalPicked,
    missing_items: missingItems,
  };
};

const derivePickStatusFromInflow = (inflowData: unknown): PickStatus | null => {
  if (!inflowData || typeof inflowData !== "object") return null;

  const inflow = inflowData as Record<string, unknown>;
  const lines = Array.isArray(inflow.lines) ? inflow.lines : [];
  const pickLines = Array.isArray(inflow.pickLines) ? inflow.pickLines : [];

  const required = new Map<string, number>();
  const picked = new Map<string, number>();
  const productNames = new Map<string, string>();

  for (const rawLine of lines) {
    const productId = getLineProductId(rawLine);
    const quantity = getLineQuantity(rawLine);
    if (!productId || quantity <= 0) continue;

    required.set(productId, (required.get(productId) ?? 0) + quantity);
    if (!productNames.has(productId)) {
      productNames.set(productId, getLineProductName(rawLine, productId));
    }
  }

  for (const rawLine of pickLines) {
    const productId = getLineProductId(rawLine);
    const quantity = getLineQuantity(rawLine);
    if (!productId || quantity <= 0) continue;

    picked.set(productId, (picked.get(productId) ?? 0) + quantity);
    if (!productNames.has(productId)) {
      productNames.set(productId, getLineProductName(rawLine, productId));
    }
  }

  const totalOrdered = Array.from(required.values()).reduce((sum, quantity) => sum + quantity, 0);
  const missingItems: PickStatusItem[] = [];
  let totalPicked = 0;

  for (const [productId, ordered] of required.entries()) {
    const pickedQuantity = Math.min(picked.get(productId) ?? 0, ordered);
    totalPicked += pickedQuantity;

    if (pickedQuantity < ordered) {
      missingItems.push({
        product_id: productId,
        product_name: productNames.get(productId) ?? productId,
        ordered,
        picked: pickedQuantity,
      });
    }
  }

  return {
    is_fully_picked: totalOrdered > 0 && totalPicked >= totalOrdered && missingItems.length === 0,
    total_ordered: totalOrdered,
    total_picked: totalPicked,
    missing_items: missingItems,
  };
};

const buildOrderProductRows = (lines: unknown[]): OrderProductRow[] => {
  const rows: OrderProductRow[] = [];

  for (const rawLine of lines) {
    if (!rawLine || typeof rawLine !== "object") continue;
    const line = rawLine as Record<string, unknown>;
    const productId = getLineProductId(line);
    const quantity = getLineQuantity(line);
    if (!productId || quantity <= 0) continue;

    const quantityValue = line.quantity;
    const serials =
      quantityValue && typeof quantityValue === "object"
        ? Array.isArray((quantityValue as Record<string, unknown>).serialNumbers)
          ? ((quantityValue as Record<string, unknown>).serialNumbers as Array<string | number>).map((serial) => String(serial))
          : []
        : [];

    rows.push({
      productId,
      productName: getLineProductName(line, productId),
      quantity,
      serials,
    });
  }

  return rows;
};

export const getPartialOrderInfo = (order: PartialOrderSource): PartialOrderInfo => {
  const pickStatus = normalizePickStatus(order.pick_status) ?? derivePickStatusFromInflow(order.inflow_data);
  const totalOrdered = pickStatus?.total_ordered ?? 0;
  const totalPicked = pickStatus?.total_picked ?? 0;
  const missingItems = pickStatus?.missing_items ?? [];
  const isPartial = Boolean(pickStatus && totalOrdered > 0 && totalPicked > 0 && totalPicked < totalOrdered);
  const isPartialLeg = Boolean(order.parent_order_id || order.parent_inflow_order_id);
  const hasRemainder = isTruthyFlag(order.has_remainder) || Boolean(order.remainder_order_id);

  return {
    isPartial,
    isPartialLeg,
    totalOrdered,
    totalPicked,
    missingItems,
    hasRemainder,
    remainderOrderId: order.remainder_order_id ?? undefined,
    remainderInflowOrderId: order.remainder_inflow_order_id ?? undefined,
    parentOrderId: order.parent_order_id ?? undefined,
    parentInflowOrderId: order.parent_inflow_order_id ?? undefined,
    shortfall: Math.max(totalOrdered - totalPicked, 0),
  };
};

export const getOrderProductTableView = (order: PartialOrderSource): OrderProductTableView => {
  const partialInfo = getPartialOrderInfo(order);
  const inflowData = order.inflow_data && typeof order.inflow_data === "object" ? (order.inflow_data as Record<string, unknown>) : {};
  const lines = Array.isArray(inflowData.lines) ? inflowData.lines : [];

  if (partialInfo.isPartialLeg) {
    const pickLines = Array.isArray(inflowData.pickLines) ? inflowData.pickLines : [];
    const childRows = pickLines.length > 0 ? buildOrderProductRows(pickLines) : buildOrderProductRows(lines);

    return {
      title: "Picked leg items",
      description: "Items included in this picked leg only.",
      rows: childRows,
      emptyState: "No items found on this picked leg.",
    };
  }

  if (partialInfo.hasRemainder) {
    return {
      title: "Remainder leg items",
      description: "Items still left on the remainder leg after the split.",
      rows: buildOrderProductRows(lines),
      emptyState: "No items remain on the remainder leg.",
    };
  }

  return {
    title: "Product table",
    description: "All items on this order.",
    rows: buildOrderProductRows(lines),
    emptyState: "No items found on this order.",
  };
};

export const isPartialOrder = (order: PartialOrderSource): boolean => getPartialOrderInfo(order).isPartial;

export const isActiveRemainderLegWaitingOnPickup = (order: PartialOrderSource): boolean => {
  const partialInfo = getPartialOrderInfo(order);
  return partialInfo.hasRemainder && !partialInfo.isPartialLeg && partialInfo.missingItems.length > 0;
};

export const isRemainderLegWaitingOnPickup = isActiveRemainderLegWaitingOnPickup;

export const canGeneratePicklist = (order: PartialOrderSource): boolean => {
  if (order.picklist_generated_at) {
    return false;
  }

  const assetTagRequired = order.asset_tag_required !== false;
  if (assetTagRequired && !order.tagged_at) {
    return false;
  }

  const partialInfo = getPartialOrderInfo(order);
  if (partialInfo.hasRemainder && partialInfo.totalPicked === 0 && partialInfo.missingItems.length > 0) {
    return false;
  }

  return true;
};
