import { describe, expect, it } from "vitest";

import { dedupeByName } from "./utils";

describe("dedupeByName", () => {
    it("keeps the first unique name and removes later duplicates", () => {
        const items = dedupeByName([
            { name: "System Operations", status: "active" },
            { name: "SharePoint Storage", status: "warning" },
            { name: "system operations", status: "error" },
            { name: "Inflow Sync", status: "active" },
        ] as Array<{ name: string; status: string }>);

        expect(items).toEqual([
            { name: "System Operations", status: "active" },
            { name: "SharePoint Storage", status: "warning" },
            { name: "Inflow Sync", status: "active" },
        ]);
    });
});
