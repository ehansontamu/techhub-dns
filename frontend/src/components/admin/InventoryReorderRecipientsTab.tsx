import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2, Lock, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
    inventoryReorderRecipientsApi,
    type GetInventoryReorderRecipientsResponse,
} from "../../api/inventoryReorderRecipients";
import { extractApiErrorMessage } from "../../utils/apiErrors";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Input } from "../ui/input";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "../ui/table";

const normalizeEmail = (value: string) => value.trim().toLowerCase();
const looksLikeEmail = (value: string) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/i.test(value.trim());

export default function InventoryReorderRecipientsTab() {
    const [data, setData] = useState<GetInventoryReorderRecipientsResponse | null>(null);
    const [draft, setDraft] = useState<string[]>([]);
    const [newEmail, setNewEmail] = useState("");
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const envRecipients = useMemo(() => data?.env_recipients || [], [data?.env_recipients]);
    const dbRecipients = useMemo(() => data?.db_recipients || [], [data?.db_recipients]);
    const sortedDraft = useMemo(() => {
        const unique = Array.from(new Set(draft.map(normalizeEmail).filter(Boolean)));
        unique.sort();
        return unique;
    }, [draft]);
    const isDirty = useMemo(() => {
        const saved = dbRecipients.slice().sort().join("|");
        return saved !== sortedDraft.join("|");
    }, [dbRecipients, sortedDraft]);

    useEffect(() => {
        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                const response = await inventoryReorderRecipientsApi.getRecipients();
                setData(response);
                setDraft(response.db_recipients || []);
            } catch (caught: unknown) {
                const message = extractApiErrorMessage(
                    caught,
                    "Failed to load inventory reorder recipients"
                );
                setError(message);
                toast.error("Failed to load recipients", { description: message });
            } finally {
                setLoading(false);
            }
        };

        void load();
    }, []);

    const add = () => {
        const email = normalizeEmail(newEmail);
        if (!email) return;
        if (!looksLikeEmail(email)) {
            toast.error("Invalid email", { description: "Enter a valid email address." });
            return;
        }
        if (sortedDraft.includes(email) || envRecipients.includes(email)) {
            setNewEmail("");
            return;
        }
        setDraft((current) => [...current, email]);
        setNewEmail("");
    };

    const remove = (email: string) => {
        setDraft((current) => current.filter((item) => normalizeEmail(item) !== email));
    };

    const save = async () => {
        setSaving(true);
        try {
            const response = await inventoryReorderRecipientsApi.updateRecipients(sortedDraft);
            setData(response);
            setDraft(response.db_recipients || []);
            toast.success("Inventory reorder recipients updated", {
                description: `${response.recipients.length} recipient${response.recipients.length === 1 ? "" : "s"}`,
            });
        } catch (caught: unknown) {
            toast.error("Failed to update recipients", {
                description: extractApiErrorMessage(caught, "Please try again."),
            });
        } finally {
            setSaving(false);
        }
    };

    const source = data?.source;
    const sourceVariant = source === "db" ? "success" : source === "default" ? "secondary" : "warning";

    return (
        <div className="space-y-4">
            <Card>
                <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                        <CardTitle className="text-base">Teams alert recipients</CardTitle>
                        <CardDescription>
                            Manage who receives inventory reorder alerts for new high-quantity BigCommerce orders.
                        </CardDescription>
                    </div>
                    {source ? <Badge variant={sourceVariant}>{source}</Badge> : null}
                </CardHeader>
                <CardContent className="space-y-4">
                    {envRecipients.length > 0 ? (
                        <div className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 p-3">
                            <AlertTriangle className="mt-0.5 h-4 w-4 text-warning" />
                            <div className="min-w-0">
                                <div className="text-sm font-medium text-foreground">Environment recipients are pinned</div>
                                <div className="mt-1 text-xs text-muted-foreground">
                                    Entries from <code>INVENTORY_REORDER_TEAMS_RECIPIENT_EMAIL</code> cannot be removed here. Additional recipients are stored in the database.
                                </div>
                            </div>
                        </div>
                    ) : null}

                    {error ? (
                        <div className="flex items-start gap-2 rounded-md border border-destructive/20 bg-destructive/5 p-3">
                            <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive" />
                            <div className="text-sm text-destructive">{error}</div>
                        </div>
                    ) : null}

                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                        <Input
                            type="email"
                            placeholder="inventory-team@example.com"
                            value={newEmail}
                            onChange={(event) => setNewEmail(event.target.value)}
                            onKeyDown={(event) => {
                                if (event.key === "Enter") add();
                            }}
                            disabled={loading || saving}
                        />
                        <Button type="button" onClick={add} disabled={loading || saving || !newEmail.trim()}>
                            <Plus className="mr-2 h-4 w-4" />
                            Add
                        </Button>
                        <Button
                            type="button"
                            onClick={() => void save()}
                            disabled={loading || saving || !isDirty}
                        >
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            Save
                        </Button>
                    </div>

                    <div className="overflow-hidden rounded-lg border bg-card">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Email</TableHead>
                                    <TableHead>Source</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {envRecipients.length === 0 && sortedDraft.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={3} className="text-sm text-muted-foreground">
                                            No inventory reorder recipients configured.
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    <>
                                        {envRecipients.map((email) => (
                                            <TableRow key={`env-${email}`}>
                                                <TableCell className="font-mono text-sm">{email}</TableCell>
                                                <TableCell><Badge variant="warning">env</Badge></TableCell>
                                                <TableCell className="text-right">
                                                    <Button type="button" size="sm" variant="ghost" disabled className="cursor-not-allowed">
                                                        <Lock className="mr-2 h-4 w-4" />
                                                        Pinned
                                                    </Button>
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                        {sortedDraft.map((email) => (
                                            <TableRow key={`db-${email}`}>
                                                <TableCell className="font-mono text-sm">{email}</TableCell>
                                                <TableCell><Badge variant="secondary">db</Badge></TableCell>
                                                <TableCell className="text-right">
                                                    <Button
                                                        type="button"
                                                        size="sm"
                                                        variant="ghost"
                                                        onClick={() => remove(email)}
                                                        disabled={loading || saving}
                                                    >
                                                        <Trash2 className="mr-2 h-4 w-4" />
                                                        Remove
                                                    </Button>
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </>
                                )}
                            </TableBody>
                        </Table>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
