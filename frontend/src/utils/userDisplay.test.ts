import { describe, expect, it } from "vitest";

import { getUserDisplayName, getUserFirstName } from "./userDisplay";

describe("getUserDisplayName", () => {
  it("prefers display_name", () => {
    expect(
      getUserDisplayName({
        id: "1",
        email: "tech@example.com",
        display_name: "Tech One",
        department: null,
        created_at: "2026-01-01T00:00:00Z",
        last_login_at: "2026-01-01T00:00:00Z",
      }),
    ).toBe("Tech One");
  });

  it("falls back to email", () => {
    expect(
      getUserDisplayName({
        id: "1",
        email: "tech@example.com",
        display_name: null,
        department: null,
        created_at: "2026-01-01T00:00:00Z",
        last_login_at: "2026-01-01T00:00:00Z",
      }),
    ).toBe("tech@example.com");
  });

  it("falls back to provided default", () => {
    expect(getUserDisplayName(null, "Unknown")).toBe("Unknown");
  });
});

describe("getUserFirstName", () => {
  it("extracts first name from comma-separated display names", () => {
    expect(
      getUserFirstName({
        id: "1",
        email: "tech@example.com",
        display_name: "Cao, Kyler Anh-Khoa",
        department: null,
        created_at: "2026-01-01T00:00:00Z",
        last_login_at: "2026-01-01T00:00:00Z",
      }),
    ).toBe("Kyler");
  });

  it("falls back to the first token for simple display names", () => {
    expect(
      getUserFirstName({
        id: "1",
        email: "tech@example.com",
        display_name: "Kyler Cao",
        department: null,
        created_at: "2026-01-01T00:00:00Z",
        last_login_at: "2026-01-01T00:00:00Z",
      }),
    ).toBe("Kyler");
  });

  it("falls back to the provided default when there is no display name", () => {
    expect(getUserFirstName(null, "there")).toBe("there");
  });
});
