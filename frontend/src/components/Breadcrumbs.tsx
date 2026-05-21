import { Link, useLocation } from "react-router-dom";
import { ChevronRight } from "lucide-react";

const LABELS: Record<string, string> = {
  "": "Dashboard",
  orders: "Orders",
  preparation: "Preparation",
  "order-qa": "QA Checklist",
  delivery: "Delivery",
  dispatch: "Dispatch",
  history: "History",
  van: "Van",
  golf_cart: "Golf Cart",
  shipping: "Shipping",
  "vetting-editor": "Vetting Editor",
  settings: "Settings",
  admin: "Admin Tools",
  sessions: "Sessions",
  runs: "Runs",
  qa: "QA",
};

function formatSegment(segment: string) {
  return LABELS[segment] ?? segment.replace(/-/g, " ");
}

export function Breadcrumbs({ loading = false }: { loading?: boolean }) {
  const location = useLocation();
  const segments = location.pathname.split("/").filter(Boolean);

  const crumbs = segments.map((segment, index) => {
    const path = "/" + segments.slice(0, index + 1).join("/");
    const isId = /^[0-9a-f-]{8,}$/.test(segment);
    const label = isId ? "Details" : formatSegment(segment);
    return { path, label, isId };
  });

  return (
    <nav aria-label="Breadcrumb" className="min-w-0 flex-1 overflow-x-auto">
      <div className="flex min-w-max items-center text-xs text-muted-foreground">
        {loading ? (
          <span className="cursor-not-allowed opacity-75">Dashboard</span>
        ) : (
          <Link to="/" className="shrink-0 transition-colors hover:text-foreground">
            Dashboard
          </Link>
        )}
      {crumbs.map((crumb) => (
        <div key={crumb.path} className="flex min-w-0 items-center">
          <ChevronRight className="mx-1.5 h-3.5 w-3.5 text-muted-foreground/70" />
          {crumb.isId ? (
            <span className="max-w-[12rem] truncate text-foreground sm:max-w-[16rem]">{crumb.label}</span>
          ) : loading ? (
            <span className="truncate cursor-not-allowed opacity-75">{crumb.label}</span>
          ) : (
            <Link
              to={crumb.path}
              className="truncate transition-colors hover:text-foreground"
            >
              {crumb.label}
            </Link>
          )}
        </div>
      ))}
      </div>
    </nav>
  );
}
