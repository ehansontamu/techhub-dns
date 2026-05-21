import { describe, expect, it } from "vitest";

import { reorderOrderIds } from "./orderReorder";

describe("reorderOrderIds", () => {
  it("moves an order before a target", () => {
    expect(reorderOrderIds(["a", "b", "c"], "c", "a", "before")).toEqual(["c", "a", "b"]);
  });

  it("moves an order after a target", () => {
    expect(reorderOrderIds(["a", "b", "c"], "a", "c", "after")).toEqual(["b", "c", "a"]);
  });

  it("moves an order to the end when there is no target", () => {
    expect(reorderOrderIds(["a", "b", "c"], "a", null, "end")).toEqual(["b", "c", "a"]);
  });

  it("keeps the original order when dragging onto itself", () => {
    expect(reorderOrderIds(["a", "b", "c"], "b", "b", "before")).toEqual(["a", "b", "c"]);
  });
});
