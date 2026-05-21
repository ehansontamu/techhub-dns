import { describe, it, expect } from "vitest";
import { isValidOrderId } from "./orderIds";

describe("isValidOrderId", () => {
  it("accepts valid UUID", () => {
    expect(isValidOrderId("550e8400-e29b-41d4-a716-446655440000")).toBe(true);
  });

  it("rejects empty string", () => {
    expect(isValidOrderId("")).toBe(false);
  });

  it("rejects null/undefined", () => {
    expect(isValidOrderId(null)).toBe(false);
    expect(isValidOrderId(undefined)).toBe(false);
  });

  it("accepts TH order numbers", () => {
    expect(isValidOrderId("TH3270")).toBe(true);
  });

  it("accepts partial leg order numbers", () => {
    expect(isValidOrderId("TH000140-P")).toBe(true);
    expect(isValidOrderId("TH000140-P2")).toBe(true);
    expect(isValidOrderId("TH000140-P10")).toBe(true);
    expect(isValidOrderId("TH000140-R")).toBe(true);
  });
});
