import { useState } from "react";
import { LogOut } from "lucide-react";

import { useAuth } from "../contexts/AuthContext";
import { getUserDisplayName } from "../utils/userDisplay";
import { Button } from "./ui/button";

function getNetId(email: string | null | undefined): string {
  const trimmed = email?.trim();
  if (!trimmed) return "";

  return trimmed.split("@")[0] ?? "";
}

export function AccountControls() {
  const { user, logout } = useAuth();
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const displayName = getUserDisplayName(user, "there");
  const netId = getNetId(user?.email);

  const handleLogout = async () => {
    setIsLoggingOut(true);
    await logout();
  };

  return (
    <div className="ml-auto flex min-w-0 items-center gap-2 sm:gap-3">
      <div className="hidden min-w-0 text-right leading-tight sm:block">
        <p className="truncate text-sm font-medium text-foreground">Hello {displayName}</p>
        {netId && <p className="truncate text-xs text-muted-foreground">{netId}</p>}
      </div>

      {netId && (
        <span className="max-w-[8rem] truncate text-sm font-medium text-foreground sm:hidden">
          {netId}
        </span>
      )}

      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => void handleLogout()}
        disabled={isLoggingOut}
        aria-label="Log out"
        title="Log out"
        className="shrink-0 gap-2 px-3"
      >
        <LogOut className="h-4 w-4" />
        <span className="hidden md:inline">{isLoggingOut ? "Logging out" : "Log out"}</span>
      </Button>
    </div>
  );
}
