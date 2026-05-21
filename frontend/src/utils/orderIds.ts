const ORDER_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ORDER_NUMBER_RE = /^TH\d+(?:-(?:P(?:\d+)?|R))?$/i;

export function isValidOrderId(value: unknown): value is string {
  return typeof value === "string" && (ORDER_ID_RE.test(value.trim()) || ORDER_NUMBER_RE.test(value.trim()));
}
