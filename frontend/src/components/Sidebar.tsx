import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { motion } from "framer-motion";
import {
  LayoutDashboard,
  Package,
  Tag,
  ClipboardCheck,
  Truck,
  Settings,
  ChevronLeft,
  ChevronRight,
  Send,
  Users,
  FilePenLine,
  type LucideIcon,
} from "lucide-react";
import { cn } from "../lib/utils";
import boxTAM from "../../assets/boxTAM.svg";
import { useAuth } from "../contexts/AuthContext";

type LeafNavItem = {
  path: string;
  to?: string;
  label: string;
  icon: LucideIcon;
};

const navItems: LeafNavItem[] = [
  { path: "/", label: "Dashboard", icon: LayoutDashboard },
  { path: "/orders", label: "Orders", icon: Package },
  { path: "/preparation", label: "Preparation", icon: Tag },
  { path: "/order-qa", label: "QA Checklist", icon: ClipboardCheck },
  { path: "/delivery", to: "/delivery/dispatch", label: "Delivery", icon: Truck },
  { path: "/shipping", label: "Shipping", icon: Send },
];

const adminItems = [
  { path: "/settings", label: "Settings", icon: Settings },
  { path: "/admin", label: "Admin Tools", icon: Settings },
  { path: "/sessions", label: "Sessions", icon: Users },
];

export function Sidebar({ className }: { className?: string }) {
  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.matchMedia("(max-width: 1023px)").matches;
  });
  const [isMobileOpen, setIsMobileOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.matchMedia("(max-width: 1023px)").matches;
  });
  const location = useLocation();
  const { isAdmin } = useAuth();
  const touchStartXRef = useRef<number | null>(null);
  const touchStartYRef = useRef<number | null>(null);
  const visibleAdminItems = isAdmin
    ? adminItems
    : adminItems.filter((item) => item.path !== "/admin" && item.path !== "/vetting-editor");

  useEffect(() => {
    if (typeof document === "undefined") return;
    document.documentElement.style.setProperty("--sidebar-width", isMobile ? "0px" : collapsed ? "72px" : "256px");
  }, [collapsed, isMobile]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const mediaQuery = window.matchMedia("(max-width: 1023px)");
    const syncResponsiveState = () => {
      const matches = mediaQuery.matches;
      setIsMobile(matches);
      setCollapsed(matches);
      setIsMobileOpen(false);
    };

    syncResponsiveState();
    mediaQuery.addEventListener("change", syncResponsiveState);
    window.addEventListener("resize", syncResponsiveState);
    window.addEventListener("orientationchange", syncResponsiveState);
    return () => {
      mediaQuery.removeEventListener("change", syncResponsiveState);
      window.removeEventListener("resize", syncResponsiveState);
      window.removeEventListener("orientationchange", syncResponsiveState);
    };
  }, []);

  useEffect(() => {
    if (!isMobile) {
      return;
    }

    const onTouchStart = (event: TouchEvent) => {
      touchStartXRef.current = event.touches[0]?.clientX ?? null;
      touchStartYRef.current = event.touches[0]?.clientY ?? null;
    };

    const onTouchEnd = (event: TouchEvent) => {
      const startX = touchStartXRef.current;
      const startY = touchStartYRef.current;
      const endX = event.changedTouches[0]?.clientX;
      const endY = event.changedTouches[0]?.clientY;

      touchStartXRef.current = null;
      touchStartYRef.current = null;

      if (startX === null || startY === null || typeof endX !== "number" || typeof endY !== "number") {
        return;
      }

      const deltaX = endX - startX;
      const deltaY = Math.abs(endY - startY);
      if (deltaY > 80) {
        return;
      }

      if (!isMobileOpen && startX < 24 && deltaX > 48) {
        setIsMobileOpen(true);
      }

      if (isMobileOpen && deltaX < -48) {
        setIsMobileOpen(false);
      }
    };

    window.addEventListener("touchstart", onTouchStart, { passive: true });
    window.addEventListener("touchend", onTouchEnd, { passive: true });
    return () => {
      window.removeEventListener("touchstart", onTouchStart);
      window.removeEventListener("touchend", onTouchEnd);
    };
  }, [isMobile, isMobileOpen]);

  useEffect(() => {
    if (!isMobile) {
      return;
    }

    setIsMobileOpen(false);
  }, [isMobile, location.pathname]);

  const isActive = (path: string) =>
    path === "/" ? location.pathname === "/" : location.pathname.startsWith(path);
  const showExpandedContent = isMobile || !collapsed;

  return (
    <>
      {isMobile && isMobileOpen && (
        <button
          type="button"
          aria-label="Close sidebar overlay"
          onClick={() => setIsMobileOpen(false)}
          className="fixed inset-0 z-40 bg-foreground/50 backdrop-blur-[1px] touch-manipulation"
        />
      )}
      {isMobile && !isMobileOpen && (
        <button
          type="button"
          aria-label="Open sidebar"
          onClick={() => setIsMobileOpen(true)}
          className="fixed left-3 top-1 z-40 flex h-10 w-10 items-center justify-center rounded-full border border-border bg-card text-muted-foreground shadow-lg touch-manipulation"
        >
          <ChevronRight className="h-5 w-5" />
        </button>
      )}
      <motion.aside
        initial={false}
        animate={isMobile ? { x: isMobileOpen ? 0 : -288 } : { x: 0, width: collapsed ? 72 : 256 }}
        transition={{ duration: 0.25, ease: [0.4, 0, 0.2, 1] }}
        className={cn(
          "fixed left-0 top-0 z-50 flex h-screen flex-col border-r border-border bg-background text-foreground will-change-transform",
          isMobile ? "w-[288px] max-w-[calc(100vw-3rem)] shadow-2xl" : "",
          className
        )}
      >
        <div className={cn("relative flex h-12 items-center border-b border-border", showExpandedContent ? "justify-between px-4" : "justify-center px-3")}>
          {showExpandedContent && (
            <div className="flex items-center gap-3">
              <motion.img
                initial={false}
                src={boxTAM}
                alt="Texas A&M University"
                className="h-8 w-auto"
              />
              <motion.span
                initial={false}
                className="font-semibold leading-tight tracking-tight text-foreground"
              >
                TechHub
                <br />
                Super App
              </motion.span>
            </div>
          )}
          <button
            type="button"
            onClick={() => {
              if (isMobile) {
                setIsMobileOpen((current) => !current);
                return;
              }
              setCollapsed(!collapsed);
            }}
            aria-label={isMobile ? (isMobileOpen ? "Close sidebar" : "Open sidebar") : collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-expanded={isMobile ? isMobileOpen : !collapsed}
            className={cn(
              "flex h-10 w-10 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground touch-manipulation",
              showExpandedContent ? "" : "static"
            )}
          >
            {isMobile ? (
              isMobileOpen ? <ChevronLeft className="h-5 w-5" /> : <ChevronRight className="h-5 w-5" />
            ) : collapsed ? (
              <ChevronRight className="h-5 w-5" />
            ) : (
              <ChevronLeft className="h-5 w-5" />
            )}
          </button>
        </div>

        <nav className="custom-scrollbar flex-1 touch-pan-y space-y-1 overflow-y-auto px-3 py-4">
          {navItems.map((item) => {
            const active = isActive(item.path);
            const Icon = item.icon;

            return (
              <NavLink
                key={item.path}
                to={item.to ?? item.path}
                aria-label={item.label}
                title={item.label}
                className={cn(
                  "flex min-h-[44px] items-center rounded-lg py-2.5 text-sm font-medium transition-colors",
                  showExpandedContent ? "gap-3 px-3" : "justify-center px-0",
                  active
                    ? "bg-secondary text-foreground"
                    : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                )}
              >
                <Icon className="h-5 w-5 flex-shrink-0" />
                {showExpandedContent && <span className="overflow-hidden whitespace-nowrap">{item.label}</span>}
              </NavLink>
            );
          })}

          <div className={cn("my-4 border-t border-border", showExpandedContent ? "" : "mx-2")} />

          {isAdmin && (
            <NavLink
              key="/vetting-editor"
              to="/vetting-editor"
              aria-label="Vetting Editor"
              title="Vetting Editor"
              className={({ isActive: isCurrentVettingEditor }) =>
                cn(
                  "flex min-h-[44px] items-center rounded-lg py-2.5 text-sm font-medium transition-colors",
                  showExpandedContent ? "gap-3 px-3" : "justify-center px-0",
                  isCurrentVettingEditor
                    ? "bg-accent text-accent-foreground shadow-lg shadow-accent/25"
                    : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                )
              }
            >
              <FilePenLine className="h-5 w-5 flex-shrink-0" />
              {showExpandedContent && <span className="overflow-hidden whitespace-nowrap">Vetting Editor</span>}
            </NavLink>
          )}

          {visibleAdminItems.map((item) => {
            const active = isActive(item.path);
            const Icon = item.icon;

            return (
              <NavLink
                key={item.path}
                to={item.path}
                aria-label={item.label}
                title={item.label}
                className={cn(
                  "flex min-h-[44px] items-center rounded-lg py-2.5 text-sm font-medium transition-colors",
                  showExpandedContent ? "gap-3 px-3" : "justify-center px-0",
                  active
                    ? "bg-secondary text-foreground"
                    : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                )}
              >
                <Icon className="h-5 w-5 flex-shrink-0" />
                {showExpandedContent && <span className="overflow-hidden whitespace-nowrap">{item.label}</span>}
              </NavLink>
            );
          })}
        </nav>
      </motion.aside>
    </>
  );
}
