import React from "react";
import { createRoot } from "react-dom/client";
import BigCommerceChat from "./pages/BigCommerceChat";
import "./index.css";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "./lib/queryClient";
import { AuthProvider } from "./contexts/AuthContext";

const root = createRoot(document.getElementById("root")!);

root.render(
  <React.StrictMode>
    <AuthProvider>
      <QueryClientProvider client={queryClient}>
        <div className="w-screen h-screen bg-background flex items-center justify-center">
          <div className="max-w-xl w-full">
            <BigCommerceChat />
          </div>
        </div>
      </QueryClientProvider>
    </AuthProvider>
  </React.StrictMode>
);
