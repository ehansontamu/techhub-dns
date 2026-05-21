import { useRef, useState, useEffect, useCallback } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "./ui/dialog";
import { Button } from "./ui/button";
import { Eraser, PenTool, Check, AlertCircle } from "lucide-react";
import { signatureCache } from "../lib/signatureCache";

interface SignatureModalProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onSave: (dataUrl: string, width: number, height: number) => boolean;
}

export function SignatureModal({ open, onOpenChange, onSave }: SignatureModalProps) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const drawingRef = useRef(false);
    const [hasSignature, setHasSignature] = useState(false);
    const [requirePenInput, setRequirePenInput] = useState(true);
    const [debugInfo, setDebugInfo] = useState<string>("");

    // Reset canvas when opening
    useEffect(() => {
        if (open) {
            setRequirePenInput(true);
            setTimeout(() => {
                resizeCanvas();
                clearCanvas();
            }, 100); // Small delay to ensure render
        }
    }, [open]);

    useEffect(() => {
        if (!open) return;
        const handleResize = () => resizeCanvas();
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, [open]);

    const clearCanvas = () => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        ctx.clearRect(0, 0, canvas.width, canvas.height);
        setHasSignature(false);
        setDebugInfo("");
    };

    const resizeCanvas = () => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const rect = canvas.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        const nextWidth = Math.max(1, Math.floor(rect.width * dpr));
        const nextHeight = Math.max(1, Math.floor(rect.height * dpr));

        if (canvas.width !== nextWidth || canvas.height !== nextHeight) {
            canvas.width = nextWidth;
            canvas.height = nextHeight;
        }

        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const getPointerPos = (canvas: HTMLCanvasElement, clientX: number, clientY: number) => {
        const rect = canvas.getBoundingClientRect();
        return {
            x: clientX - rect.left,
            y: clientY - rect.top
        };
    };

    const handlePointerMove = useCallback((e: PointerEvent) => {
        if (!drawingRef.current) return;
        const canvas = canvasRef.current;
        if (!canvas) return;

        e.preventDefault();
        const { x, y } = getPointerPos(canvas, e.clientX, e.clientY);
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        ctx.lineWidth = e.pressure ? Math.max(1, e.pressure * 4) : 3;
        ctx.lineTo(x, y);
        ctx.stroke();
        setHasSignature(prev => prev ? prev : true);
    }, []);

    const handlePointerUp = useCallback(() => {
        if (!drawingRef.current) return;
        drawingRef.current = false;
        setDebugInfo("");
    }, []);

    // Attach/detach document-level listeners for move/up to survive pointer leaving canvas
    useEffect(() => {
        if (!open) return;
        document.addEventListener('pointermove', handlePointerMove);
        document.addEventListener('pointerup', handlePointerUp);
        document.addEventListener('pointercancel', handlePointerUp);
        return () => {
            document.removeEventListener('pointermove', handlePointerMove);
            document.removeEventListener('pointerup', handlePointerUp);
            document.removeEventListener('pointercancel', handlePointerUp);
        };
    }, [open, handlePointerMove, handlePointerUp]);

    const startDrawing = (e: React.PointerEvent) => {
        if (requirePenInput && e.pointerType !== 'pen') {
            setDebugInfo(`Ignored ${e.pointerType} input (Pen only mode)`);
            return;
        }

        e.preventDefault();
        const canvas = canvasRef.current;
        if (!canvas) return;

        const { x, y } = getPointerPos(canvas, e.clientX, e.clientY);
        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineWidth = e.pressure ? e.pressure * 4 : 3;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.strokeStyle = 'black';

        drawingRef.current = true;
        setDebugInfo("Drawing...");
    };

    // Cropping Logic
    const trimCanvas = (c: HTMLCanvasElement) => {
        const ctx = c.getContext('2d');
        if (!ctx) return null;

        const w = c.width;
        const h = c.height;
        const imageData = ctx.getImageData(0, 0, w, h);
        const data = imageData.data;

        let minX = w, minY = h, maxX = 0, maxY = 0;
        let found = false;

        for (let y = 0; y < h; y++) {
            for (let x = 0; x < w; x++) {
                const alpha = data[(y * w + x) * 4 + 3];
                if (alpha > 0) {
                    if (x < minX) minX = x;
                    if (x > maxX) maxX = x;
                    if (y < minY) minY = y;
                    if (y > maxY) maxY = y;
                    found = true;
                }
            }
        }

        if (!found) return null; // Empty canvas

        // Add padding
        const padding = 10;
        minX = Math.max(0, minX - padding);
        minY = Math.max(0, minY - padding);
        maxX = Math.min(w, maxX + padding);
        maxY = Math.min(h, maxY + padding);

        const trimW = maxX - minX;
        const trimH = maxY - minY;

        const copy = document.createElement('canvas');
        copy.width = trimW;
        copy.height = trimH;
        const copyCtx = copy.getContext('2d');
        if (!copyCtx) return null;

        copyCtx.drawImage(c, minX, minY, trimW, trimH, 0, 0, trimW, trimH);
        return {
            dataUrl: copy.toDataURL('image/png'),
            width: trimW,
            height: trimH
        };
    };

    const handleSave = () => {
        if (!canvasRef.current) return;
        const result = trimCanvas(canvasRef.current);
        if (result) {
            // Save to cache
            signatureCache.save(result.dataUrl, result.width, result.height);
            // Pass back to parent
            const didSave = onSave(result.dataUrl, result.width, result.height);
            if (didSave) {
                onOpenChange(false);
            }
        }
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-xl">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <PenTool className="h-5 w-5" />
                        Add Signature
                    </DialogTitle>
                    <DialogDescription>
                    Sign with mouse, touch, or pen. Use the toggle below to require pen input if needed.
                    {requirePenInput && <span className="ml-2 text-xs text-amber-600 font-medium inline-flex items-center gap-1"><AlertCircle className="h-3 w-3" /> Pen Input Only</span>}
                    </DialogDescription>
                </DialogHeader>
 
                <div className="relative flex min-h-[300px] touch-none items-center justify-center rounded-lg border-2 border-dashed border-border bg-muted/30">
                    <canvas
                        ref={canvasRef}
                        width={600}
                        height={300}
                        className="h-[300px] w-full touch-none select-none cursor-crosshair rounded-lg bg-background"
                        style={{ touchAction: 'none', userSelect: 'none', WebkitUserSelect: 'none' }}
                        onPointerDown={startDrawing}
                    />
                    {!hasSignature && (
                        <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-muted-foreground/60">
                            Sign here
                        </div>
                    )}
                </div>
 
                {import.meta.env.DEV ? (
                    <div className="h-4 text-xs text-muted-foreground" aria-live="polite">
                        {debugInfo}
                    </div>
                ) : null}

                <DialogFooter className="gap-2 sm:gap-0">
                    <div className="flex-1 flex justify-start">
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={clearCanvas}
                            onPointerDown={(e) => {
                                if (e.pointerType === 'pen') {
                                    e.preventDefault();
                                    clearCanvas();
                                }
                            }}
                            disabled={!hasSignature}
                            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                        >
                            <Eraser className="h-4 w-4 mr-2" />
                            Clear
                        </Button>

                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setRequirePenInput(!requirePenInput)}
                            onPointerDown={(e) => {
                                if (e.pointerType === 'pen') {
                                    e.preventDefault();
                                    setRequirePenInput(!requirePenInput);
                                }
                            }}
                            className="text-muted-foreground hover:text-foreground"
                        >
                            {requirePenInput ? "Allow Touch" : "Require Pen"}
                        </Button>
                    </div>

                    <Button 
                        variant="outline" 
                        onClick={() => onOpenChange(false)}
                        onPointerDown={(e) => {
                            if (e.pointerType === 'pen') {
                                e.preventDefault();
                                onOpenChange(false);
                            }
                        }}
                    >
                        Cancel
                    </Button>
                    <Button 
                        onClick={handleSave} 
                        onPointerDown={(e) => {
                            if (e.pointerType === 'pen') {
                                e.preventDefault();
                                if (hasSignature) {
                                    handleSave();
                                }
                            }
                        }}
                        disabled={!hasSignature}
                    >
                        <Check className="h-4 w-4 mr-2" />
                        Done
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
