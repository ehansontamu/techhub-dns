import { describe, expect, it } from "vitest";

import type { User } from "../contexts/AuthContext";
import { getOrderPickerLabel, isOrderPickedByUser } from "./qaEligibility";

const user: User = {
  id: "user-1",
  email: "picker@example.com",
  display_name: "Picker Person",
  department: null,
  created_at: "2026-01-01T00:00:00Z",
  last_login_at: "2026-01-01T00:00:00Z",
};

describe("qaEligibility", () => {
  it("matches a picker stored as email to the logged-in user", () => {
    expect(isOrderPickedByUser({ picklist_generated_by: "picker@example.com" }, user)).toBe(true);
  });

  it("matches a picker stored as display name for legacy rows", () => {
    expect(isOrderPickedByUser({ picklist_generated_by: "Picker Person" }, user)).toBe(true);
  });

  it("does not match a different picker", () => {
    expect(isOrderPickedByUser({ picklist_generated_by: "other@example.com" }, user)).toBe(false);
  });

  it("falls back when the picker is missing", () => {
    expect(getOrderPickerLabel({ picklist_generated_by: undefined })).toBe("Not recorded");
  });
});
