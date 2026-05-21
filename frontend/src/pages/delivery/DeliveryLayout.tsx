import { Outlet } from "react-router-dom";

export default function DeliveryLayout() {
  return (
    <div className="container mx-auto px-4 py-6 sm:px-6 space-y-4">
      <div className="flex flex-col gap-1">
        <h1 className="text-lg font-semibold leading-none tracking-tight">Delivery</h1>
        <p className="text-xs text-muted-foreground">Manage dispatch workflow, run history, and vehicle availability.</p>
      </div>

      <Outlet />
    </div>
  );
}
