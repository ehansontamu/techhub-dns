import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ordersApi } from "../../api/orders";
import type { DeliveryEvidence } from "../../types/order";
import { OrderStatus } from "../../types/order";
import { DeliveryPhotoStep } from "./DeliveryPhotoStep";

vi.mock("../../api/orders", () => ({ ordersApi: { completeDeliveryEvidence: vi.fn(), getOrder: vi.fn() } }));

const evidence: DeliveryEvidence = {
    id: "proof-1", signed_at: "2026-10-23T14:00:00Z", signed_by: "runner@example.test",
    signed_picklist_path: "https://sharepoint.test/signed.pdf", bundle_path: "https://sharepoint.test/bundle.pdf",
    photos: [], skip_reason: null, completed_at: null, completed_by: null,
};

function setup(record = evidence) {
    const onComplete = vi.fn();
    render(<DeliveryPhotoStep orderId="order-1" evidence={record} onComplete={onComplete} />);
    return onComplete;
}

beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:photo") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    vi.mocked(ordersApi.completeDeliveryEvidence).mockResolvedValue({ success: true, message: "Delivered", delivery_evidence: evidence });
});

describe("DeliveryPhotoStep", () => {
    it("requires a photo or nonblank skip reason, and submits the trimmed reason", async () => {
        const onComplete = setup();
        expect(screen.getByRole("button", { name: "Complete delivery" })).toBeDisabled();
        fireEvent.click(screen.getByRole("checkbox"));
        const reason = screen.getByLabelText("Reason for skipping photos (required)");
        fireEvent.change(reason, { target: { value: "   " } });
        expect(screen.getByRole("button", { name: "Complete delivery" })).toBeDisabled();
        fireEvent.change(reason, { target: { value: " Camera unavailable " } });
        fireEvent.click(screen.getByRole("button", { name: "Complete delivery" }));
        await waitFor(() => expect(onComplete).toHaveBeenCalledOnce());
        expect(ordersApi.completeDeliveryEvidence).toHaveBeenCalledWith("order-1", "proof-1", [], "Camera unavailable");
    });

    it("previews and removes selected photos, accepts ten, and rejects eleven", () => {
        setup();
        const files = Array.from({ length: 10 }, (_value, i) => new File(["photo"], `${i}.jpg`, { type: "image/jpeg" }));
        fireEvent.change(screen.getByLabelText("Upload delivery photos"), { target: { files } });
        expect(screen.getAllByRole("img")).toHaveLength(10);
        expect(screen.getByRole("button", { name: "Complete delivery" })).toBeEnabled();
        expect(screen.getByRole("button", { name: "Upload photos" })).toBeDisabled();
        fireEvent.change(screen.getByLabelText("Upload delivery photos"), { target: { files: [files[0]] } });
        expect(screen.getByRole("alert")).toHaveTextContent("at most 10");
        fireEvent.click(screen.getByRole("button", { name: "Remove selected photo 1" }));
        expect(screen.getAllByRole("img")).toHaveLength(9);
        expect(URL.revokeObjectURL).toHaveBeenCalled();
    });

    it("retains selections after a failed upload and resumes with confirmed photos", async () => {
        const onComplete = setup();
        const files = [new File(["a"], "a.jpg"), new File(["b"], "b.jpg")];
        const saved = { url: "https://sharepoint.test/photo.jpg", filename: "photo.jpg", sha256: "abc", uploaded_at: evidence.signed_at, uploaded_by: evidence.signed_by };
        vi.mocked(ordersApi.completeDeliveryEvidence).mockRejectedValueOnce(new Error("Network failed"));
        vi.mocked(ordersApi.getOrder).mockResolvedValue({
            status: OrderStatus.IN_DELIVERY, signature_captured_at: evidence.signed_at,
            signed_picklist_path: evidence.signed_picklist_path, bundle_path: evidence.bundle_path,
            delivery_evidence: [{ ...evidence, photos: [saved] }],
        } as never);
        fireEvent.change(screen.getByLabelText("Upload delivery photos"), { target: { files } });
        fireEvent.click(screen.getByRole("button", { name: "Complete delivery" }));
        await screen.findByRole("link", { name: "Saved photo 1" });
        expect(screen.getAllByRole("img")).toHaveLength(2);
        expect(onComplete).not.toHaveBeenCalled();
        expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Complete delivery" }));
        await waitFor(() => expect(onComplete).toHaveBeenCalledOnce());
        expect(ordersApi.completeDeliveryEvidence).toHaveBeenLastCalledWith("order-1", "proof-1", files, undefined);
    });

    it("allows a resumed delivery to complete with its already uploaded photo", async () => {
        const saved = { url: "https://sharepoint.test/photo.jpg", filename: "photo.jpg", sha256: "abc", uploaded_at: evidence.signed_at, uploaded_by: evidence.signed_by };
        const onComplete = setup({ ...evidence, photos: [saved] });
        expect(screen.getByRole("button", { name: "Complete delivery" })).toBeEnabled();
        fireEvent.click(screen.getByRole("button", { name: "Complete delivery" }));
        await waitFor(() => expect(onComplete).toHaveBeenCalledOnce());
        expect(ordersApi.completeDeliveryEvidence).toHaveBeenCalledWith("order-1", "proof-1", [], undefined);
    });

    it("blocks retry when the order was reopened instead of reporting old proof as completion", async () => {
        const onComplete = setup();
        vi.mocked(ordersApi.completeDeliveryEvidence).mockRejectedValueOnce(new Error("Order changed"));
        vi.mocked(ordersApi.getOrder).mockResolvedValue({
            status: OrderStatus.PRE_DELIVERY, signature_captured_at: null,
            delivery_evidence: [{ ...evidence, completed_at: evidence.signed_at }],
        } as never);
        fireEvent.click(screen.getByRole("checkbox"));
        fireEvent.change(screen.getByLabelText("Reason for skipping photos (required)"), { target: { value: "No camera" } });
        fireEvent.click(screen.getByRole("button", { name: "Complete delivery" }));
        await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Return to the order details"));
        expect(onComplete).not.toHaveBeenCalled();
        expect(screen.getByRole("button", { name: "Complete delivery" })).toBeDisabled();
        expect(screen.getByRole("checkbox")).toBeDisabled();
        expect(screen.getByLabelText("Reason for skipping photos (required)")).toBeDisabled();
    });

    it("explains HEIC conversion before uploading unsupported library photos", () => {
        setup();
        fireEvent.change(screen.getByLabelText("Upload delivery photos"), {
            target: { files: [new File(["photo"], "package.heic", { type: "image/heic" })] },
        });
        expect(screen.getByRole("alert")).toHaveTextContent("Export HEIC photos as JPEG");
        expect(screen.getByRole("button", { name: "Complete delivery" })).toBeDisabled();
        expect(ordersApi.completeDeliveryEvidence).not.toHaveBeenCalled();
    });
});
