import { describe, expect, it } from "vitest";
import { canGeneratePicklist, getOrderProductTableView, getPartialLegLabel, getPartialOrderInfo } from "./orderPartial";

describe("getPartialOrderInfo", () => {
  it("uses the persisted picked-leg suffix for the operator-facing part number", () => {
    expect(
      getPartialLegLabel({
        inflow_order_id: "TH000140-P2",
        parent_order_id: "parent-uuid",
      } as any),
    ).toBe("Part 2 leg");
  });

  it("marks a child partial leg as a partial leg even when it is fully picked itself", () => {
    const info = getPartialOrderInfo({
      inflow_data: {
        lines: [{ productId: "1", quantity: { standardQuantity: 2 } }],
        pickLines: [{ productId: "1", quantity: { standardQuantity: 2 } }],
      },
      parent_order_id: "parent-uuid",
    } as any);

    expect(info.isPartialLeg).toBe(true);
    expect(info.isPartial).toBe(false);
    expect(info.hasRemainder).toBe(false);
  });

  it("marks a parent order with missing items as having a remainder", () => {
    const info = getPartialOrderInfo({
      inflow_data: {
        lines: [{ productId: "1", quantity: { standardQuantity: 4 } }],
        pickLines: [{ productId: "1", quantity: { standardQuantity: 2 } }],
      },
      has_remainder: "Y",
      remainder_order_id: "remainder-uuid",
    } as any);

    expect(info.isPartial).toBe(true);
    expect(info.hasRemainder).toBe(true);
    expect(info.isPartialLeg).toBe(false);
    expect(info.totalOrdered).toBe(4);
    expect(info.totalPicked).toBe(2);
  });

  it("does not mark computer imaging as a partial shortfall", () => {
    const info = getPartialOrderInfo({
      inflow_data: {
        lines: [
          { productId: "1", product: { name: "Laptop" }, quantity: { standardQuantity: 1 } },
          {
            productId: "computer-imaging",
            description: "Computer Imaging",
            product: { itemType: "service", name: "Computer Imaging" },
            quantity: { standardQuantity: 1 },
          },
        ],
        pickLines: [{ productId: "1", product: { name: "Laptop" }, quantity: { standardQuantity: 1 } }],
      },
    } as any);

    expect(info.isPartial).toBe(false);
    expect(info.missingItems).toEqual([]);
    expect(info.totalOrdered).toBe(2);
    expect(info.totalPicked).toBe(2);
  });

  it("prefers server pick_status for remainder legs over raw inflow fallback data", () => {
    const info = getPartialOrderInfo({
      inflow_data: {
        lines: [{ productId: "1", quantity: { standardQuantity: 10 } }],
        pickLines: [{ productId: "1", quantity: { standardQuantity: 2 } }],
      },
      has_remainder: "Y",
      remainder_order_id: "remainder-uuid",
      pick_status: {
        total_ordered: 8,
        total_picked: 0,
        is_fully_picked: false,
        missing_items: [{ product_id: "1", product_name: "Widget", ordered: 8, picked: 0 }],
      },
    } as any);

    expect(info.hasRemainder).toBe(true);
    expect(info.totalOrdered).toBe(8);
    expect(info.totalPicked).toBe(0);
    expect(info.shortfall).toBe(8);
  });
});

describe("getOrderProductTableView", () => {
  it("shows remaining items on the parent leg", () => {
    const view = getOrderProductTableView({
      inflow_data: {
        lines: [{ productId: "A", product: { name: "Widget" }, quantity: { standardQuantity: 3 } }],
        pickLines: [],
      },
      has_remainder: true,
    } as any);

    expect(view.title).toBe("Final leg items");
    expect(view.rows).toEqual([
      {
        productId: "A",
        productName: "Widget",
        quantity: 3,
        serials: [],
      },
    ]);
  });

  it("shows only child leg items for a partial child leg", () => {
    const view = getOrderProductTableView({
      inflow_data: {
        lines: [
          { productId: "B", product: { name: "Bolt" }, quantity: { standardQuantity: 1, serialNumbers: ["S1"] } },
          { productId: "C", description: "Nut", quantity: 2 },
        ],
        pickLines: [{ productId: "B", product: { name: "Bolt" }, quantity: { standardQuantity: 1 } }],
      },
      parent_order_id: "parent-uuid",
    } as any);

    expect(view.title).toBe("Part 1 leg items");
    expect(view.rows).toEqual([
      {
        productId: "B",
        productName: "Bolt",
        quantity: 1,
        serials: [],
      },
    ]);
  });

  it("shows only picked items for a partially picked order before the split", () => {
    const view = getOrderProductTableView({
      inflow_data: {
        lines: [
          { productId: "D", product: { name: "Dock" }, quantity: { standardQuantity: 4 } },
          { productId: "E", product: { name: "Monitor" }, quantity: { standardQuantity: 2 } },
        ],
        pickLines: [
          { productId: "D", product: { name: "Dock" }, quantity: { standardQuantity: 1, serialNumbers: ["SN1"] } },
        ],
      },
    } as any);

    expect(view.title).toBe("Picked items (partial order)");
    expect(view.rows).toEqual([
      {
        productId: "D",
        productName: "Dock",
        quantity: 1,
        serials: ["SN1"],
      },
    ]);
  });

  it("still shows all items for a fully picked order", () => {
    const view = getOrderProductTableView({
      inflow_data: {
        lines: [{ productId: "F", product: { name: "Cable" }, quantity: { standardQuantity: 2 } }],
        pickLines: [{ productId: "F", product: { name: "Cable" }, quantity: { standardQuantity: 2 } }],
      },
    } as any);

    expect(view.title).toBe("Product table");
    expect(view.rows).toEqual([
      {
        productId: "F",
        productName: "Cable",
        quantity: 2,
        serials: [],
      },
    ]);
  });
});

describe("canGeneratePicklist", () => {
  it("allows a normal picked order to generate a picklist", () => {
    expect(
      canGeneratePicklist({
        inflow_data: {
          lines: [{ productId: "1", quantity: { standardQuantity: 1 } }],
          pickLines: [{ productId: "1", quantity: { standardQuantity: 1 } }],
        },
        asset_tag_required: false,
      } as any),
    ).toBe(true);
  });

  it("blocks a remainder leg that has not picked anything yet", () => {
    expect(
      canGeneratePicklist({
        inflow_data: {
          lines: [{ productId: "1", quantity: { standardQuantity: 2 } }],
          pickLines: [],
        },
        has_remainder: "Y",
        remainder_order_id: "remainder-uuid",
        asset_tag_required: false,
      } as any),
    ).toBe(false);
  });

  it("allows a partially picked remainder leg to generate the next split", () => {
    expect(
      canGeneratePicklist({
        inflow_data: {
          lines: [{ productId: "1", quantity: { standardQuantity: 2 } }],
          pickLines: [{ productId: "1", quantity: { standardQuantity: 1 } }],
        },
        has_remainder: "Y",
        remainder_order_id: "remainder-uuid",
        asset_tag_required: false,
      } as any),
    ).toBe(true);
  });

  it("allows a partially picked remainder leg to generate the next split before tagging", () => {
    expect(
      canGeneratePicklist({
        inflow_data: {
          lines: [{ productId: "1", quantity: { standardQuantity: 66 } }],
          pickLines: [{ productId: "1", quantity: { standardQuantity: 61 } }],
        },
        has_remainder: "Y",
        remainder_order_id: "remainder-uuid",
        asset_tag_required: true,
        tagged_at: null,
      } as any),
    ).toBe(true);
  });
});
