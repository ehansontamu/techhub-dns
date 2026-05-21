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

  const cityUpper = city.toUpperCase();
  return cityUpper === "BRYAN" || cityUpper === "COLLEGE STATION";
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
