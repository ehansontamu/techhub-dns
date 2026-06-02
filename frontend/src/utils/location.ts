import { Order } from "../types/order";

interface ShippingAddress {
  city?: string;
  address1?: string;
  address2?: string;
  state?: string;
  postalCode?: string;
}

function getShippingAddress(order: Order): ShippingAddress | null {
  if (!order.inflow_data || typeof order.inflow_data !== "object") return null;
  const addr = (order.inflow_data as Record<string, unknown>).shippingAddress;
  return typeof addr === "object" && addr !== null ? addr as ShippingAddress : null;
}

const LOCAL_CITIES = ["BRYAN", "COLLEGE STATION"];
const LOCAL_CITY_MATCH_THRESHOLD = 0.9;

function normalizeCityName(city: string): string {
  return city.toUpperCase().replace(/[^A-Z]/g, "");
}

function levenshteinDistance(a: string, b: string): number {
  if (a === b) return 0;
  if (!a.length) return b.length;
  if (!b.length) return a.length;

  const previous = Array.from({ length: b.length + 1 }, (_, index) => index);
  const current = new Array<number>(b.length + 1);

  for (let i = 1; i <= a.length; i += 1) {
    current[0] = i;
    for (let j = 1; j <= b.length; j += 1) {
      const substitutionCost = a[i - 1] === b[j - 1] ? 0 : 1;
      current[j] = Math.min(
        current[j - 1] + 1,
        previous[j] + 1,
        previous[j - 1] + substitutionCost,
      );
    }

    for (let j = 0; j <= b.length; j += 1) {
      previous[j] = current[j];
    }
  }

  return previous[b.length];
}

export function isLocalDeliveryCity(city: string): boolean {
  const normalizedCity = normalizeCityName(city);
  if (!normalizedCity) {
    return true;
  }

  return LOCAL_CITIES.some((localCity) => {
    const normalizedLocalCity = normalizeCityName(localCity);
    if (normalizedCity === normalizedLocalCity) {
      return true;
    }

    const longestLength = Math.max(normalizedCity.length, normalizedLocalCity.length);
    const similarity = 1 - levenshteinDistance(normalizedCity, normalizedLocalCity) / longestLength;
    return similarity >= LOCAL_CITY_MATCH_THRESHOLD;
  });
}

/**
 * Determines if an order is a local delivery (Bryan/College Station) or shipping
 * @param order The order to check
 * @returns true if the order is in Bryan or College Station, false otherwise
 */
export function isLocalDelivery(order: Order): boolean {
  if (!order.inflow_data) {
    return true; // Assume local if no inflow data
  }

  const shippingAddress = getShippingAddress(order);
  if (!shippingAddress) {
    return true; // Assume local if no shipping address
  }

  const city = shippingAddress.city?.trim();
  if (!city) {
    return true; // Assume local if no city specified
  }

  return isLocalDeliveryCity(city);
}

/**
 * Formats the delivery location for display
 * For local deliveries (Bryan/College Station): shows the delivery_location as-is (building codes, etc.)
 * For shipping orders: shows just the city name
 * @param order The order to format location for
 * @returns The formatted location string
 */
export function formatDeliveryLocation(order: Order): string {
  if (!order.delivery_location) {
    return "N/A";
  }

  if (isLocalDelivery(order)) {
    return order.delivery_location;
  }

  // For non-local orders, extract city from inflow data
  const addr = getShippingAddress(order);
  if (addr?.city) {
    return addr.city.trim();
  }

  // Fallback: if no city in inflow data, return the delivery location as-is
  return order.delivery_location;
}
