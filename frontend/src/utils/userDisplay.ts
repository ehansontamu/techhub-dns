import type { User } from "../contexts/AuthContext";

export function getUserDisplayName(user: User | null | undefined, fallback = "Unknown User"): string {
  const displayName = user?.display_name?.trim();
  if (displayName) return displayName;

  const email = user?.email?.trim();
  if (email) return email;

  return fallback;
}

export function getUserFirstAndLastName(user: User | null | undefined, fallback = "there"): string {
  const displayName = user?.display_name?.trim();
  if (displayName) {
    const normalised = displayName.includes(",")
      ? (() => {
          const [lastName, ...rest] = displayName.split(",");
          const firstPart = rest.join(",").trim().split(/\s+/)[0] ?? "";
          const lastPart = lastName.trim().split(/\s+/)[0] ?? "";
          return [firstPart, lastPart].filter(Boolean).join(" ").trim();
        })()
      : (() => {
          const parts = displayName.split(/\s+/).filter(Boolean);
          if (parts.length >= 2) {
            return `${parts[0]} ${parts[parts.length - 1]}`.trim();
          }
          return parts[0] ?? "";
        })();

    if (normalised) return normalised;
  }

  return fallback;
}
