import { useEffect, useRef, useState } from "react";
import { Camera, Upload, X } from "lucide-react";

import { ordersApi } from "../../api/orders";
import type { DeliveryEvidence } from "../../types/order";
import { OrderStatus } from "../../types/order";
import { extractApiErrorMessage } from "../../utils/apiErrors";
import { Button } from "../ui/button";

interface Props {
    orderId: string;
    evidence: DeliveryEvidence;
    onComplete: () => void;
}

export function DeliveryPhotoStep({ orderId, evidence, onComplete }: Props) {
    const [record, setRecord] = useState(evidence);
    const [photos, setPhotos] = useState<File[]>([]);
    const [previews, setPreviews] = useState<string[]>([]);
    const [skip, setSkip] = useState(false);
    const [reason, setReason] = useState("");
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [orderChanged, setOrderChanged] = useState(false);
    const cameraRef = useRef<HTMLInputElement>(null);
    const uploadRef = useRef<HTMLInputElement>(null);
    const selectionLimit = 10 - evidence.photos.length;

    useEffect(() => {
        const urls = photos.map(photo => URL.createObjectURL(photo));
        setPreviews(urls);
        return () => urls.forEach(url => URL.revokeObjectURL(url));
    }, [photos]);

    function addPhotos(input: HTMLInputElement) {
        const selected = Array.from(input.files || []);
        input.value = "";
        if (photos.length + selected.length > selectionLimit) {
            setError("A delivery can have at most 10 photos.");
            return;
        }
        if (selected.some(photo => photo.size === 0 || photo.size > 10 * 1024 * 1024)) {
            setError("Each photo must be nonempty and no larger than 10 MB.");
            return;
        }
        if (selected.some(photo => photo.type && !["image/jpeg", "image/png", "image/webp"].includes(photo.type))) {
            setError("Choose a JPEG, PNG, or WebP photo. Export HEIC photos as JPEG before uploading.");
            return;
        }
        setError(null);
        setPhotos(previous => [...previous, ...selected]);
    }

    async function complete() {
        setSaving(true);
        setError(null);
        try {
            await ordersApi.completeDeliveryEvidence(orderId, record.id, skip ? [] : photos, skip ? reason.trim() : undefined);
            onComplete();
        } catch (uploadError: unknown) {
            setError(extractApiErrorMessage(uploadError, "Unable to complete delivery. Your signature is saved; retry the photo upload."));
            // Retain selected files for retry; confirmed uploads are deduplicated by the server.
            try {
                const order = await ordersApi.getOrder(orderId);
                const updated = order.delivery_evidence?.find(item => item.id === record.id);
                if (updated?.completed_at && order.status === OrderStatus.DELIVERED) onComplete();
                else if (order.status !== OrderStatus.IN_DELIVERY || !updated || updated.completed_at
                    || updated.signed_picklist_path !== order.signed_picklist_path
                    || updated.bundle_path !== order.bundle_path
                    || new Date(updated.signed_at).getTime() !== new Date(order.signature_captured_at || "").getTime()) {
                    setOrderChanged(true);
                    setError("This order changed while saving delivery proof. Return to the order details and review its current status.");
                } else {
                    setRecord(updated);
                    if (updated.photos.length) setSkip(false);
                }
            } catch {
                setError("Unable to confirm the upload. Your signature is saved; reconnect and retry.");
            }
        } finally {
            setSaving(false);
        }
    }

    const canComplete = skip ? reason.trim().length > 0 : photos.length + record.photos.length > 0;

    return (
        <section className="mx-auto max-w-3xl space-y-5 rounded-2xl border border-border/70 bg-card p-6">
            <div>
                <h1 className="text-xl font-semibold">Delivery photos</h1>
                <p className="mt-2 text-sm text-muted-foreground">
                    The customer signature is saved. Take or upload a photo of the delivered packages to complete delivery.
                    You can add up to 10 photos, or skip with a recorded reason.
                </p>
                <p className="mt-1 text-sm text-muted-foreground">JPEG, PNG, or WebP; up to 10 MB and 24 megapixels per photo.</p>
            </div>
            {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
            {!skip && (
                <>
                    <input ref={cameraRef} type="file" accept="image/jpeg,image/png,image/webp" capture="environment"
                        aria-label="Take a delivery photo" className="sr-only" disabled={saving || orderChanged}
                        onChange={event => addPhotos(event.currentTarget)} />
                    <input ref={uploadRef} type="file" accept="image/jpeg,image/png,image/webp" multiple
                        aria-label="Upload delivery photos" className="sr-only" disabled={saving || orderChanged}
                        onChange={event => addPhotos(event.currentTarget)} />
                    <div className="flex flex-wrap gap-2">
                        <Button variant="outline" disabled={saving || orderChanged || photos.length >= selectionLimit} onClick={() => cameraRef.current?.click()}>
                            <Camera className="mr-2 h-4 w-4" />Take photo
                        </Button>
                        <Button variant="outline" disabled={saving || orderChanged || photos.length >= selectionLimit} onClick={() => uploadRef.current?.click()}>
                            <Upload className="mr-2 h-4 w-4" />Upload photos
                        </Button>
                    </div>
                    <p className="text-sm">{photos.length} selected · {record.photos.length} saved in the delivery bundle</p>
                    {record.photos.length > 0 && (
                        <div className="flex flex-wrap gap-3">
                            {record.photos.every(photo => photo.url === record.bundle_path) ? (
                                <a href={record.bundle_path} target="_blank" rel="noreferrer" className="text-sm text-primary underline">
                                    View delivery bundle ({record.photos.length} photos)
                                </a>
                            ) : record.photos.map((photo, index) => (
                                <a key={photo.sha256} href={photo.url} target="_blank" rel="noreferrer" className="text-sm text-primary underline">
                                    Saved photo {index + 1}
                                </a>
                            ))}
                        </div>
                    )}
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                        {photos.map((photo, index) => (
                            <div key={`${photo.name}-${index}`} className="relative rounded-lg border p-2">
                                <img src={previews[index]} alt={`Delivery photo ${index + 1}`} className="h-36 w-full rounded object-contain" />
                                <p className="mt-1 truncate text-xs">{photo.name}</p>
                                <Button variant="outline" size="icon" className="absolute right-2 top-2" disabled={saving || orderChanged}
                                    aria-label={`Remove selected photo ${index + 1}`} onClick={() => setPhotos(items => items.filter((_item, i) => i !== index))}>
                                    <X className="h-4 w-4" />
                                </Button>
                            </div>
                        ))}
                    </div>
                </>
            )}
            {record.photos.length === 0 && (
                <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={skip} disabled={saving || orderChanged} onChange={event => setSkip(event.target.checked)} />
                    Skip photos and record a reason
                </label>
            )}
            {skip && (
                <div className="space-y-2">
                    <label htmlFor="delivery-photo-skip-reason" className="text-sm font-medium">Reason for skipping photos (required)</label>
                    <textarea id="delivery-photo-skip-reason" value={reason} maxLength={1000} disabled={saving || orderChanged} rows={3}
                        className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
                        onChange={event => setReason(event.target.value)} placeholder="Explain why no delivery photo was taken" />
                    <p className="text-xs text-muted-foreground">Your identity, reason, and time will be recorded in the order history.</p>
                </div>
            )}
            <Button onClick={() => void complete()} disabled={saving || orderChanged || !canComplete}>
                {saving ? "Saving delivery proof…" : "Complete delivery"}
            </Button>
        </section>
    );
}
