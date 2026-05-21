import type { User } from "../contexts/AuthContext";

export function getUserDisplayName(user: User | null | undefined, fallback = "Unknown User"): string {
  const displayName = user?.display_name?.trim();
  if (displayName) return displayName;

  const email = user?.email?.trim();
  if (email) return email;

  return fallback;
}

export function getUserFirstName(user: User | null | undefined, fallback = "there"): string {
  const displayName = user?.display_name?.trim();
  if (displayName) {
    const namePart = displayName.includes(",")
      ? displayName.split(",").slice(1).join(",")
      : displayName;
    const firstName = namePart.trim().split(/\s+/)[0]?.replace(/[^\p{L}\p{M}'-]+$/u, "");
    if (firstName) return firstName;
  }

  return fallback;
}
