import { describe, it, expect } from "vitest";
import { extractApiErrorMessage } from "./apiErrors";

describe("extractApiErrorMessage", () => {
  it("extracts message from Axios-like error response", () => {
    const error = {
      response: {
        data: {
          error: { message: "Order not found", code: "NOT_FOUND" },
        },
      },
    };
    expect(extractApiErrorMessage(error, "default")).toBe("Order not found");
  });

  it("extracts message from flat error string", () => {
    const error = { response: { data: { error: "Something broke" } } };
    expect(extractApiErrorMessage(error, "default")).toBe("Something broke");
  });

  it("returns fallback for non-Error objects", () => {
    expect(extractApiErrorMessage({ something: "weird" }, "fallback")).toBe("fallback");
  });

  it("extracts message from Error objects", () => {
    expect(extractApiErrorMessage(new Error("fail"), "fallback")).toBe("fail");
  });

  it("handles null/undefined gracefully", () => {
    expect(extractApiErrorMessage(null, "fallback")).toBe("fallback");
  });

  it("extracts nested error message", () => {
    const error = {
      response: {
        data: { error: { code: "VALIDATION_ERROR", message: "Validation failed" } },
      },
    };
    expect(extractApiErrorMessage(error, "default")).toBe("Validation failed");
  });
});
