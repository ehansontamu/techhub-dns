import React from "react";
import { createRoot } from "react-dom/client";
import BigCommerceChat from "./pages/BigCommerceChat";

const root = createRoot(document.getElementById("root")!);
root.render(
  <React.StrictMode>
    <div className="w-screen h-screen bg-background flex items-center justify-center">
      <div className="max-w-xl w-full">
        <BigCommerceChat />
      </div>
    </div>
  </React.StrictMode>
);
