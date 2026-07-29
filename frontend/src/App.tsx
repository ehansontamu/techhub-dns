import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { toast, Toaster } from "sonner";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import { AppShellErrorBoundary, RouteContentErrorBoundary } from "./components/error-boundaries/AppErrorBoundaries";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { Sidebar } from "./components/Sidebar";
import { Skeleton } from "./components/Skeleton";
import { Breadcrumbs } from "./components/Breadcrumbs";
import { AccountControls } from "./components/AccountControls";
import { SyncHealthBanner } from "./components/SyncHealthBanner";
import { OfflineBanner } from "./components/OfflineBanner";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const Orders = lazy(() => import("./pages/Orders"));
const OrderDetailPage = lazy(() => import("./pages/OrderDetailPage"));
const DeliveryLayout = lazy(() => import("./pages/delivery/DeliveryLayout"));
const DeliveryDispatchPage = lazy(() => import("./pages/delivery/Dispatch"));
const DeliveryHistoryPage = lazy(() => import("./pages/delivery/History"));
const Shipping = lazy(() => import("./pages/Shipping"));
const Admin = lazy(() => import("./pages/Admin"));
const Settings = lazy(() => import("./pages/Settings"));
const Audit = lazy(() => import("./pages/audit/Audit"));
const DocumentSigningPage = lazy(() => import("./pages/DocumentSigningPage"));
const OrderQAChecklist = lazy(() => import("./pages/OrderQAChecklist"));
const OrderQAPage = lazy(() => import("./pages/OrderQAPage"));
const DeliveryRunDetailPage = lazy(() => import("./pages/DeliveryRunDetailPage"));
const Login = lazy(() => import("./pages/Login"));
const Sessions = lazy(() => import("./pages/Sessions"));
const Preparation = lazy(() => import("./pages/Preparation"));
const VettingEditor = lazy(() => import("./pages/VettingEditor"));
const CompatibilityEditor = lazy(() => import("./pages/CompatibilityEditor"));
const InventoryReorder = lazy(() => import("./pages/InventoryReorder"));

const prefetchRoutes = () => {
    void import("./pages/Dashboard");
    void import("./pages/Orders");
};

function AppRoutes() {
    return (
        <Routes>
            <Route path="/" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
            <Route path="/orders" element={<ProtectedRoute><Orders /></ProtectedRoute>} />
            <Route path="/orders/:orderId" element={<ProtectedRoute><OrderDetailPage /></ProtectedRoute>} />
            <Route path="/orders/:orderId/qa" element={<ProtectedRoute><OrderQAPage /></ProtectedRoute>} />
            <Route path="/preparation" element={<ProtectedRoute><Preparation /></ProtectedRoute>} />
            <Route path="/tag-request" element={<Navigate to="/preparation" replace />} />
            <Route path="/vetting-editor" element={<ProtectedRoute><VettingEditor /></ProtectedRoute>} />
            <Route path="/compatibility-editor" element={<ProtectedRoute><CompatibilityEditor /></ProtectedRoute>} />
            <Route path="/inventory-reorder" element={<ProtectedRoute><InventoryReorder /></ProtectedRoute>} />
            <Route path="/order-qa" element={<ProtectedRoute><OrderQAChecklist /></ProtectedRoute>} />
            <Route path="/delivery" element={<ProtectedRoute><DeliveryLayout /></ProtectedRoute>}>
                <Route index element={<Navigate to="dispatch" replace />} />
                <Route path="dispatch" element={<DeliveryDispatchPage />} />
                <Route path="history" element={<DeliveryHistoryPage />} />
                <Route path="runs/:runId" element={<DeliveryRunDetailPage />} />
            </Route>
            <Route path="/shipping" element={<ProtectedRoute><Shipping /></ProtectedRoute>} />
            <Route path="/document-signing" element={<ProtectedRoute><DocumentSigningPage /></ProtectedRoute>} />
            <Route path="/settings" element={<ProtectedRoute><Settings /></ProtectedRoute>} />
            <Route path="/admin" element={<ProtectedRoute><Admin /></ProtectedRoute>} />
            <Route path="/audit" element={<ProtectedRoute><Audit /></ProtectedRoute>} />

            <Route path="/sessions" element={<ProtectedRoute><Sessions /></ProtectedRoute>} />
            <Route path="/login" element={<Navigate to="/" replace />} />
        </Routes>
    );
}

function AppContent() {
    const { isAuthenticated, isLoading } = useAuth();
    const location = useLocation();

    useEffect(() => {
        if (typeof window === "undefined") return;
        const idleCallback = window.requestIdleCallback || ((cb: IdleRequestCallback) => window.setTimeout(cb, 250));
        const idleCancel = window.cancelIdleCallback || window.clearTimeout;
        const id = idleCallback(() => {
            prefetchRoutes();
        });
        return () => idleCancel(id as number);
    }, []);

    // Listen for rate-limit events from the API client
    useEffect(() => {
        const handler = (e: Event) => {
            const detail = (e as CustomEvent).detail;
            toast.warning(detail?.message ?? "Too many requests. Please wait a moment.");
        };
        window.addEventListener("app-rate-limit", handler);
        return () => window.removeEventListener("app-rate-limit", handler);
    }, []);

    if (isLoading) {
        return (
            <div className="min-h-screen bg-background flex items-center justify-center">
                <Skeleton className="w-96 h-96 rounded-lg" />
            </div>
        );
    }

    if (!isAuthenticated) {
        return (
            <div className="min-h-screen bg-background flex items-center justify-center">
                <Suspense fallback={<Skeleton className="w-96 h-96 rounded-lg" />}>
                    <RouteContentErrorBoundary>
                        <Routes>
                            <Route path="/login" element={<Login />} />
                            <Route path="*" element={<Navigate to="/login" state={{ from: location }} replace />} />
                        </Routes>
                    </RouteContentErrorBoundary>
                </Suspense>
            </div>
        );
    }

    const isOrdersRoute = location.pathname === "/orders" || location.pathname.startsWith("/orders/");

    return (
        <div className="min-h-screen bg-background overflow-x-hidden">
            <Sidebar />

            <main className={`transition-[margin] duration-300 lg:ml-[var(--sidebar-width)] ${isOrdersRoute ? "flex h-screen flex-col overflow-hidden" : "min-h-screen"}`}>
                <div className="sticky top-0 z-30 h-12 border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
                    <div className="flex h-full min-w-0 items-center gap-4 px-4 pl-16 sm:px-6 lg:px-8 lg:pl-8">
                        <div className="min-w-0 flex-1">
                            <Breadcrumbs />
                        </div>
                        <AccountControls />
                    </div>
                </div>

                <SyncHealthBanner />

                <div className={`${isOrdersRoute ? "flex-1 min-h-0 overflow-hidden" : "p-4 sm:p-6 lg:p-8"}`}>
                    <RouteContentErrorBoundary>
                        <Suspense fallback={
                            <div className="space-y-4">
                                <Skeleton className="h-8 w-64" />
                                <Skeleton className="h-64 w-full rounded-lg" />
                            </div>
                        }>
                            {isOrdersRoute ? (
                                <AppRoutes />
                            ) : (
                                <AnimatePresence mode="wait">
                                    <motion.div
                                        key={location.pathname}
                                        initial={{ opacity: 0, y: 10 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        exit={{ opacity: 0, y: -8 }}
                                        transition={{ duration: 0.2 }}
                                    >
                                        <AppRoutes />
                                    </motion.div>
                                </AnimatePresence>
                            )}
                        </Suspense>
                    </RouteContentErrorBoundary>
                </div>
                <OfflineBanner />
            </main>

            <Toaster
                position="top-right"
                toastOptions={{
                    style: {
                        background: "hsl(var(--popover))",
                        color: "hsl(var(--popover-foreground))",
                        border: "1px solid hsl(var(--border))",
                        borderRadius: "var(--radius)",
                    },
                }}
            />
        </div>
    );
}

function App() {
    return (
        <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
            <AppShellErrorBoundary>
                <AuthProvider>
                    <AppContent />
                </AuthProvider>
            </AppShellErrorBoundary>
        </BrowserRouter>
    );
}

export default App;
