import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { isAxiosError } from "axios";
import type { PDFPageProxy } from "pdfjs-dist";
// pdf-lib is used server-side for PDF bundling
import { ordersApi } from "../api/orders";
import { OrderDetail } from "../types/order";
import { signatureCache, type LastSignature } from "../lib/signatureCache";
import { Button } from "../components/ui/button";
import { SignaturePlacementLayer } from "../components/document-signing/SignaturePlacementLayer";

import { ArrowLeft, PenTool } from "lucide-react";

const SignatureModal = lazy(() => import("../components/SignatureModal").then((module) => ({ default: module.SignatureModal })));
const PdfPane = lazy(() => import("../components/document-signing/PdfPane"));

const SIGNATURE_CACHE_TTL_MS = 4 * 60 * 60 * 1000;

function normalizeText(value: string | undefined): string {
    return (value || "").trim().toLowerCase().replace(/\s+/g, " ");
}

type ReusableSignatureState =
    | { canUse: true; entry: LastSignature }
    | { canUse: false; reason: string };

interface PdfEntry {
    name: string;
    filename: string;
    url: string;
}

// Store basic placement info (normalized to PDF points)
interface Placement {
    id: string;
    pageIndex: number; // 0-based
    x: number; // PDF points (from left)
    y: number; // PDF points (from bottom, ReportLab style)
    width: number; // PDF points
    height: number; // PDF points
    dataUrl: string; // The image source
}

const PLACEMENT_INSTRUCTIONS_ID = "signature-placement-instructions";
const MIN_PLACEMENT_SIZE_PT = 32;

function DocumentSigningPage() {
    const [searchParams] = useSearchParams();
    const navigate = useNavigate();
    const [order, setOrder] = useState<OrderDetail | null>(null);
    const [selectedPdf, setSelectedPdf] = useState<PdfEntry | null>(null);
    const [loadingOrder, setLoadingOrder] = useState(true);
    const [orderError, setOrderError] = useState<string | null>(null);
    const [pageViewport, setPageViewport] = useState<{ width: number; height: number } | null>(null);
    const [containerWidth, setContainerWidth] = useState<number | null>(null);

    // Signing State
    const [modalOpen, setModalOpen] = useState(false);
    const [placements, setPlacements] = useState<Placement[]>([]);
    const [selectedPlacementId, setSelectedPlacementId] = useState<string | null>(null);
    const viewerRef = useRef<HTMLDivElement | null>(null);
    const [isSaving, setIsSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [cachedSignature, setCachedSignature] = useState<LastSignature | null>(null);

    const refreshCachedSignature = useCallback(() => {
        setCachedSignature(signatureCache.load());
    }, []);

    useEffect(() => {
        refreshCachedSignature();
    }, [refreshCachedSignature]);

    const orderRecipientNormalized = useMemo(
        () => normalizeText(order?.recipient_name),
        [order?.recipient_name]
    );

    const orderLocationNormalized = useMemo(
        () => normalizeText(order?.delivery_location),
        [order?.delivery_location]
    );

    const reusableSignature = useMemo<ReusableSignatureState>(() => {
        if (!cachedSignature) {
            return { canUse: false, reason: "No saved signature available." };
        }

        const ageMs = Date.now() - cachedSignature.createdAt;
        if (ageMs > SIGNATURE_CACHE_TTL_MS) {
            return { canUse: false, reason: "Saved signature expired. Capture a new one." };
        }

        if (!cachedSignature.recipientNameNormalized || !cachedSignature.deliveryLocationNormalized) {
            return { canUse: false, reason: "Saved signature is missing recipient/location context." };
        }

        if (
            cachedSignature.recipientNameNormalized !== orderRecipientNormalized ||
            cachedSignature.deliveryLocationNormalized !== orderLocationNormalized
        ) {
            return {
                canUse: false,
                reason: "Saved signature belongs to a different recipient or location.",
            };
        }

        return { canUse: true, entry: cachedSignature };
    }, [cachedSignature, orderLocationNormalized, orderRecipientNormalized]);

    // Load order when component mounts
    useEffect(() => {
        const orderId = searchParams.get('orderId');
        if (!orderId) {
            setOrderError('No order ID provided');
            setLoadingOrder(false);
            return;
        }

        const loadOrder = async () => {
            try {
                setLoadingOrder(true);
                setOrderError(null);
                const orderData = await ordersApi.getOrder(orderId);
                setOrder(orderData);

                if (orderData.picklist_path) {
                    setSelectedPdf({
                        name: `Picklist for Order ${orderData.inflow_order_id || orderData.id.slice(0, 8)}`,
                        filename: `order-${orderData.id}-picklist.pdf`,
                        url: `/api/orders/${orderData.id}/picklist`
                    });
                } else {
                    setOrderError('No picklist available for this order');
                }
            } catch (err) {
                console.error('Failed to load order:', err);
                setOrderError('Failed to load order details');
            } finally {
                setLoadingOrder(false);
            }
        };

        loadOrder();
    }, [searchParams]);

    const selectedPdfUrl = useMemo(() => (selectedPdf ? selectedPdf.url : null), [selectedPdf]);
    const returnTo = useMemo(() => {
        const fromQuery = searchParams.get('returnTo');
        if (fromQuery) {
            return fromQuery;
        }
        return order ? `/orders/${order.inflow_order_id || order.id}` : '/orders';
    }, [order, searchParams]);

    // Responsive sizing logic
    useEffect(() => {
        if (!viewerRef.current) return;
        const observer = new ResizeObserver((entries) => {
            const rect = entries[0]?.contentRect;
            if (rect && rect.width > 0) {
                setContainerWidth(rect.width);
            }
        });
        observer.observe(viewerRef.current);
        return () => observer.disconnect();
    }, []);

    const scale = useMemo(() => {
        if (!pageViewport) return 1;
        // react-pdf v10 renders the canvas at its native CSS dimensions (e.g. 612x792 for Letter)
        // regardless of the width prop. The width prop only affects the canvas pixel buffer.
        // So placement coordinates (in PDF points) map 1:1 to displayed CSS pixels.
        return 1;
    }, [pageViewport]);

    const handlePageLoad = useCallback((page: PDFPageProxy) => {
        const viewport = page.getViewport({ scale: 1 });
        setPageViewport({ width: viewport.width, height: viewport.height });
    }, []);

    // --- Placement Logic ---

    const addPlacement = (dataUrl: string, imgW: number, imgH: number) => {
        if (!pageViewport) {
            setError("PDF is still loading. Try again in a moment.");
            setModalOpen(true);
            return false;
        }

        if (!Number.isFinite(imgW) || !Number.isFinite(imgH) || imgW <= 0 || imgH <= 0) {
            signatureCache.clear();
            setError("Saved signature data was invalid. Please add a new signature.");
            setModalOpen(true);
            return false;
        }

        // Default size logic: e.g. 150pt width, preserve aspect ratio
        const targetWidthPt = 150;
        const aspectRatio = imgW / imgH;
        const targetHeightPt = targetWidthPt / aspectRatio;

        // Center on page
        // PDF coords (bottom-left origin) vs Viewport (top-left origin for width calc is same)
        // Center X = (PageW - SigW) / 2

        const x = (pageViewport.width - targetWidthPt) / 2;
        // Place signature near the bottom of the page (close to the signature line)
        // PDF bottom-left coordinate system: y=0 is bottom, y=792 is top
        // The "Customer Signature:" label is typically at y≈70, underline at y≈60
        const y = 50;

        const newPlacement: Placement = {
            id: Math.random().toString(36).substr(2, 9),
            pageIndex: 0,
            x,
            y,
            width: targetWidthPt,
            height: targetHeightPt,
            dataUrl
        };

        console.log('[SIG_PLACEMENT]', {
            id: newPlacement.id,
            x, y, w: targetWidthPt, h: targetHeightPt,
            imgW, imgH, aspectRatio,
            pageW: pageViewport.width, pageH: pageViewport.height,
            scale,
            placementsLen: placements.length + 1,
        });

        setError(null);
        setPlacements(prev => [...prev.filter(p => p.id !== 'temp'), newPlacement]);
        setSelectedPlacementId(newPlacement.id);
        return true;
    };

    const handleModalSave = (dataUrl: string, w: number, h: number) => {
        if (order) {
            signatureCache.save(dataUrl, w, h, {
                recipientNameNormalized: normalizeText(order.recipient_name),
                deliveryLocationNormalized: normalizeText(order.delivery_location),
                sourceOrderId: order.id,
            });
            refreshCachedSignature();
        }

        const placed = addPlacement(dataUrl, w, h);
        if (placed) {
            setModalOpen(false);
        }
        return placed;
    };

    const openSignatureModal = () => {
        setError(null);
        setModalOpen(true);
    };

    const useLastSignature = () => {
        if (!reusableSignature.canUse) {
            setError(reusableSignature.reason);
            return;
        }

        const placed = addPlacement(
            reusableSignature.entry.dataUrl,
            reusableSignature.entry.width,
            reusableSignature.entry.height
        );

        if (!placed) {
            return;
        }

        setError(null);
    };

    const removePlacement = useCallback((id: string) => {
        setPlacements((prev) => prev.filter((p) => p.id !== id));
        setSelectedPlacementId((prev) => (prev === id ? null : prev));
    }, []);

    const updatePlacement = useCallback((id: string, updater: (placement: Placement) => Placement) => {
        setPlacements((prev) => {
            const next = prev.map((placement) => (placement.id === id ? updater(placement) : placement));
            console.log('updatePlacement:', id, 'prev ids:', prev.map(p => p.id), 'next ids:', next.map(p => p.id));
            console.log('updatePlacement coords:', id, ' prev:', prev.map(p => ({x: p.x, y: p.y})), 'next:', next.map(p => ({x: p.x, y: p.y})));
            return next;
        });
    }, []);

    const pxToPoints = useCallback((px: number) => {
        const effectiveScale = scale && scale > 0 ? scale : 1;
        return px / effectiveScale;
    }, [scale]);

    const movePlacement = useCallback((id: string, deltaX: number, deltaY: number) => {
        updatePlacement(id, (placement) => ({
            ...placement,
            x: placement.x + deltaX,
            y: placement.y + deltaY,
        }));
    }, [updatePlacement]);

    const resizePlacement = useCallback((id: string, delta: number) => {
        updatePlacement(id, (placement) => {
            const aspectRatio = placement.width / Math.max(placement.height, 0.1);
            let nextWidth = placement.width + delta;
            nextWidth = Math.max(nextWidth, MIN_PLACEMENT_SIZE_PT);
            const nextHeight = Math.max(nextWidth / aspectRatio, MIN_PLACEMENT_SIZE_PT);

            return {
                ...placement,
                width: nextWidth,
                height: nextHeight,
            };
        });
    }, [updatePlacement]);

    const handlePlacementKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>, placement: Placement) => {
        const key = event.key;
        const moveStepPx = 8;
        const movementStep = pxToPoints(moveStepPx);
        const resizeStep = pxToPoints(moveStepPx);

        if (key === "Delete" || key === "Backspace") {
            event.preventDefault();
            removePlacement(placement.id);
            return;
        }

        const arrowMoves: Record<string, { dx: number; dy: number }> = {
            ArrowLeft: { dx: -movementStep, dy: 0 },
            ArrowRight: { dx: movementStep, dy: 0 },
            ArrowUp: { dx: 0, dy: movementStep },
            ArrowDown: { dx: 0, dy: -movementStep },
        };

        if (key in arrowMoves) {
            event.preventDefault();
            const { dx, dy } = arrowMoves[key];
            if (event.shiftKey) {
                const direction = key === "ArrowLeft" || key === "ArrowDown" ? -resizeStep : resizeStep;
                resizePlacement(placement.id, direction);
            } else {
                movePlacement(placement.id, dx, dy);
            }
        }
    }, [movePlacement, pxToPoints, removePlacement, resizePlacement]);

    const saveSignedPdf = async () => {
        if (!order || placements.length === 0) {
            setError("Place a signature before saving.");
            return;
        }

        setIsSaving(true);
        setError(null);

        try {
            // Pick the first signature image for the 'main' image (legacy field requirement?)
            // The backend update said signature_image is required.
            // We'll send the dataUrl of the first placement, or the last one used.
            // And send the FULL list of placements.

            const mainSig = placements[0].dataUrl;

            // Map frontend placements to backend format
            // Backend expects { page_number, x, y, width, height }
            // Backend x/y are drawing coordinates (Bottom-Left).
            // Our state is ALREADY in PDF Points (Bottom-Left).

            // Just double check boundaries? No, backend just stamps.

            const payload = {
                signature_image: mainSig, // Required by schema base
                page_width: pageViewport?.width || 612,
                page_height: pageViewport?.height || 792,
                placements: placements.map(p => ({
                    page_number: p.pageIndex + 1, // 1-based for Backend
                    x: p.x,
                    y: p.y,
                    width: p.width,
                    height: p.height
                })),
                expected_updated_at: order.updated_at,
            };

            console.log('SIGN_PAYLOAD:', JSON.stringify(payload, null, 2));
            await ordersApi.signOrder(order.id, payload);

            navigate(returnTo, {
                state: { message: 'Document signed successfully!' }
            });

        } catch (saveError: unknown) {
            console.error(saveError);
            if (isAxiosError(saveError) && saveError.response?.status === 409) {
                setError("This order changed while you were signing. Reloaded the latest order data; review and sign again.");
                const refreshedOrder = await ordersApi.getOrder(order.id);
                setOrder(refreshedOrder);
                return;
            }
            setError("Unable to complete signing. Please try again.");
        } finally {
            setIsSaving(false);
        }
    };

    // --- Render ---

    if (loadingOrder) {
        return <div className="p-8 text-center text-muted-foreground">Loading document...</div>;
    }

    if (orderError || !order) {
        return <div className="p-8 text-center text-destructive">{orderError || "Order not found"}</div>;
    }

    return (
        <div className="min-h-[100dvh] bg-gradient-to-b from-white/70 via-muted/20 to-background px-4 pb-8">
            <Suspense fallback={null}>
                <SignatureModal
                    open={modalOpen}
                    onOpenChange={setModalOpen}
                    onSave={handleModalSave}
                />
            </Suspense>

            <div className="max-w-6xl mx-auto pt-6">
                <header className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                        <h1 className="text-2xl font-bold tracking-tight text-foreground">Sign Delivery Document</h1>
                        <p className="text-muted-foreground">
                            Order {order.inflow_order_id} • {order.recipient_name}
                        </p>
                        {reusableSignature.canUse && (
                            <p className="text-xs text-muted-foreground">
                                Last signature from order {reusableSignature.entry.sourceOrderId?.slice(0, 8) || "unknown"} is available for this recipient/location.
                            </p>
                        )}
                    </div>
                    <div className="flex flex-wrap items-center gap-3">
                        <Button asChild variant="ghost" className="gap-2">
                            <Link to={returnTo}>
                                <ArrowLeft className="h-4 w-4" />
                                Back
                            </Link>
                        </Button>
                        <Button
                            variant="outline"
                            onClick={openSignatureModal}
                            className="hidden sm:flex"
                        >
                            <PenTool className="w-4 h-4 mr-2" />
                            Add Signature
                        </Button>
                        <Button
                            variant="outline"
                            onClick={useLastSignature}
                            className="hidden sm:flex"
                            disabled={!reusableSignature.canUse}
                            title={reusableSignature.canUse ? "Apply last saved signature" : reusableSignature.reason}
                        >
                            Use Last Signature
                        </Button>
                        <Button
                            onClick={saveSignedPdf}
                            disabled={placements.length === 0 || isSaving}
                        >
                            {isSaving ? "Saving..." : "Finish & Save"}
                        </Button>
                    </div>
                </header>

                <section className="overflow-hidden rounded-2xl border border-border/70 bg-card/80 shadow-none">
                    <div className="flex flex-col gap-2 border-b border-border bg-gradient-to-r from-muted/40 to-card px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                        <div className="text-sm text-muted-foreground">
                            <span className="font-medium text-foreground">Picklist</span>
                            <span className="mx-2 text-muted-foreground/40">•</span>
                            <span>1 page</span>
                        </div>
                        <div className="text-xs text-muted-foreground">
                            Drag to place • Resize from bottom-left • Tap Save when finished
                        </div>
                    </div>
                    <p id={PLACEMENT_INSTRUCTIONS_ID} className="sr-only">
                        Focus a signature placement, use arrow keys to move, hold shift plus arrow to resize, delete/backspace removes.
                    </p>
                    <div
                        className="relative flex min-h-[500px] justify-center overflow-hidden bg-muted/30 p-4 select-none"
                        ref={viewerRef}
                    >
                         {selectedPdfUrl ? (
                             <div className="relative shadow-premium ring-1 ring-border/50">
                                 <Suspense fallback={<div className="p-10 text-muted-foreground">Loading PDF...</div>}>
                                     <PdfPane
                                         fileUrl={selectedPdfUrl}
                                         containerWidth={containerWidth}
                                         onPageLoad={handlePageLoad}
                                     />
                                 </Suspense>

                                  {/* Overlay Layer */}
                                  <SignaturePlacementLayer
                                        placements={placements}
                                        pageViewport={pageViewport}
                                        scale={scale}
                                        selectedPlacementId={selectedPlacementId}
                                        onSelect={setSelectedPlacementId}
                                        onRemove={removePlacement}
                                        onUpdatePlacement={updatePlacement}
                                        onKeyDown={handlePlacementKeyDown}
                                    />

                                 {/* Empty State Hint */}
                                 {placements.length === 0 && (
                                     <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 pointer-events-none">
                                         <div className="rounded-full bg-foreground/90 px-4 py-2 text-sm text-background shadow-sm">
                                             Tap "Add Signature" to draw or "Use Last Signature", then drag to place
                                         </div>
                                     </div>
                                 )}
                             </div>
                         ) : (
                             <div className="self-center text-muted-foreground">No PDF Loaded</div>
                         )}
                    </div>

                    {/* Mobile Floating Action Button */}
                    <div className="sm:hidden fixed bottom-4 right-4 flex flex-col items-end gap-3">
                        <Button
                            variant="outline"
                            onClick={useLastSignature}
                            disabled={!reusableSignature.canUse}
                            title={reusableSignature.canUse ? "Apply last saved signature" : reusableSignature.reason}
                            className="min-h-[44px] min-w-[160px] text-xs font-semibold"
                        >
                            Use Last Signature
                        </Button>
                        <Button
                            className="h-14 w-14 rounded-full p-0 shadow-premium"
                            onClick={openSignatureModal}
                            aria-label="Add new signature"
                        >
                            <PenTool className="w-6 h-6 text-white" />
                        </Button>
                    </div>

                    {error && (
                        <div className="border-t border-destructive/20 bg-destructive/10 p-4 text-center text-sm text-destructive">
                            {error}
                        </div>
                    )}
                </section>
            </div>
        </div>
    );
}

export default DocumentSigningPage;
