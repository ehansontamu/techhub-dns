import { useState } from "react";
import { LogOut } from "lucide-react";

import { useAuth } from "../contexts/AuthContext";
import { getUserFirstName } from "../utils/userDisplay";
import { Button } from "./ui/button";

export function AccountControls() {
  const { user, logout } = useAuth();
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const firstName = getUserFirstName(user, "there");

  const handleLogout = async () => {
    setIsLoggingOut(true);
    await logout();
  };

  return (
    <div className="ml-auto flex min-w-0 items-center gap-2 sm:gap-3">
      <p className="truncate text-sm font-medium text-foreground">Hello {firstName}</p>

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
