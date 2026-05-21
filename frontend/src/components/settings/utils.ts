import { toast } from "sonner";

export function formatStatusLabel(value: string) {
    return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function dedupeByName<T extends { name: string }>(items: T[] | undefined | null): T[] {
    if (!items?.length) return [];
    const seen = new Set<string>();
    return items.filter((item) => {
        const key = item.name.trim().toLowerCase();
        if (!key || seen.has(key)) return false;
        seen.add(key);
        return true;
    });
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
