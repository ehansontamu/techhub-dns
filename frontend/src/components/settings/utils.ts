import { toast } from "sonner";

export function formatStatusLabel(value: string) {
    return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function getStatusBadgeVariant(status: string): "success" | "warning" | "secondary" | "destructive" {
    if (status === "active") return "success";
    if (status === "warning") return "warning";
    if (status === "error") return "destructive";
    return "secondary";
}

export function formatTimestamp(value: string | null | undefined) {
    if (!value) return "Never";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString();
}

export async function copyToClipboard(value: string, successMessage: string) {
    try {
        await navigator.clipboard.writeText(value);
        toast.success(successMessage);
        return true;
    } catch (_error) {
        toast.error("Copy failed");
        return false;
    }
}
