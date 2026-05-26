import type { User } from "../contexts/AuthContext";
import type { Order } from "../types/order";

function identityCandidates(...values: Array<string | null | undefined>): Set<string> {
  const candidates = new Set<string>();

  for (const value of values) {
    const normalized = value?.trim().toLowerCase();
    if (!normalized) continue;

    candidates.add(normalized);
    const compact = normalized.replace(/[^a-z0-9]+/g, "");
    if (compact) {
      candidates.add(compact);
    }
    if (normalized.includes("@")) {
      const localPart = normalized.split("@", 1)[0]?.trim();
      if (localPart) {
        candidates.add(localPart);
        const compactLocalPart = localPart.replace(/[^a-z0-9]+/g, "");
        if (compactLocalPart) {
          candidates.add(compactLocalPart);
        }
      }
    }
  }

  return candidates;
}

export function getOrderPickerLabel(order: Pick<Order, "picklist_generated_by">): string {
  return order.picklist_generated_by?.trim() || "Not recorded";
}

export function isOrderPickedByUser(
  order: Pick<Order, "picklist_generated_by">,
  user: User | null | undefined,
): boolean {
  const pickerCandidates = identityCandidates(order.picklist_generated_by);
  const userCandidates = identityCandidates(user?.email, user?.display_name);

  if (pickerCandidates.size === 0 || userCandidates.size === 0) {
    return false;
  }

  for (const candidate of pickerCandidates) {
    if (userCandidates.has(candidate)) {
      return true;
    }
  }

  return false;
}
