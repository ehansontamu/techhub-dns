import { useEffect, useMemo, useState } from "react";
import { isAxiosError } from "axios";
import { AlertTriangle, Loader2, Lock, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
    allowedUsersApi,
    type GetAllowedUsersResponse,
} from "../../api/allowedUsers";
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

export default function AllowedUsersTab() {
    const [data, setData] = useState<GetAllowedUsersResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const [draft, setDraft] = useState<string[]>([]);
    const [newEmail, setNewEmail] = useState("");

    const envAllowedUsers = useMemo(() => data?.env_allowed_users || [], [data?.env_allowed_users]);
    const dbAllowedUsers = useMemo(() => data?.db_allowed_users || [], [data?.db_allowed_users]);
    const hasEnv = envAllowedUsers.length > 0;
    const source = data?.source;
    const restrictionEnabled = Boolean(data?.restriction_enabled);

    const load = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await allowedUsersApi.getAllowedUsers();
            setData(res);
            setDraft(res.db_allowed_users || []);
        } catch (e: unknown) {
            const msg = extractApiErrorMessage(e, "Failed to load app access allowlist");
            setError(msg);
            toast.error("Failed to load allowed users", { description: msg });
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void load();
    }, []);

    const sortedDraft = useMemo(() => {
        const uniq = Array.from(new Set(draft.map(normalizeEmail).filter(Boolean)));
        uniq.sort();
        return uniq;
    }, [draft]);

    const isDirty = useMemo(() => {
        const a = dbAllowedUsers.slice().sort().join("|");
        const b = sortedDraft.slice().sort().join("|");
        return a !== b;
    }, [dbAllowedUsers, sortedDraft]);

    const add = () => {
        const email = normalizeEmail(newEmail);
        if (!email) return;
        if (!looksLikeEmail(email)) {
            toast.error("Invalid email", { description: "Enter a valid TAMU email address." });
            return;
        }
        if (sortedDraft.includes(email) || envAllowedUsers.includes(email)) {
            setNewEmail("");
            return;
        }
        setDraft((prev) => [...prev, email]);
        setNewEmail("");
    };

    const remove = (email: string) => {
        setDraft((prev) => prev.filter((e) => normalizeEmail(e) !== normalizeEmail(email)));
    };

    const save = async () => {
        setSaving(true);
        try {
            const res = await allowedUsersApi.updateAllowedUsers(sortedDraft);
            setData(res);
            setDraft(res.db_allowed_users || []);
            toast.success("Allowed-user list updated", {
                description: `${(res.allowed_users || []).length} allowed user${(res.allowed_users || []).length === 1 ? "" : "s"}`,
            });
        } catch (e: unknown) {
            const status = isAxiosError(e) ? e.response?.status : undefined;
            const msg = extractApiErrorMessage(e, "Failed to update app access allowlist");
            toast.error(status === 409 ? "Allowed-user list is read-only" : "Failed to update allowed users", { description: msg });
        } finally {
            setSaving(false);
        }
    };

    const banner = hasEnv ? (
        <div className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 p-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 text-warning" />
            <div className="min-w-0">
                <div className="text-sm font-medium text-foreground">Env access entries are pinned</div>
                <div className="mt-1 text-xs text-muted-foreground">
                    Entries from <code>ALLOWED_USER_EMAILS</code> cannot be removed here. The table below manages additional users stored in the database.
                </div>
            </div>
        </div>
    ) : source === "default" ? (
        <div className="flex items-start gap-2 rounded-md border bg-muted/20 p-3">
            <ShieldCheck className="mt-0.5 h-4 w-4 text-muted-foreground" />
            <div className="min-w-0">
                <div className="text-sm font-medium text-foreground">Open access until configured</div>
                <div className="mt-1 text-xs text-muted-foreground">
                    No app-access allowlist is configured yet, so any authenticated TAMU user can still enter the app. Saving at least one allowed user turns the restriction on. Admins remain allowed automatically.
                </div>
            </div>
        </div>
    ) : null;

    const sourceBadge =
        source === "env" ? "warning" : source === "mixed" ? "warning" : source === "db" ? "success" : "secondary";

    return (
        <div className="space-y-4">
            <Card>
                <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                        <CardTitle className="text-base">App access</CardTitle>
                        <CardDescription>Manage which TAMU users are allowed to sign in to TechHub.</CardDescription>
                    </div>
                    <div className="flex items-center gap-2">
                        <Badge variant={restrictionEnabled ? "success" : "secondary"}>
                            {restrictionEnabled ? "Restricted" : "Open"}
                        </Badge>
                        {source ? <Badge variant={sourceBadge as any}>{source}</Badge> : null}
                    </div>
                </CardHeader>
                <CardContent className="space-y-4">
                    {banner}

                    {error ? (
                        <div className="flex items-start gap-2 rounded-md border border-destructive/20 bg-destructive/5 p-3">
                            <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive" />
                            <div className="text-sm text-destructive">{error}</div>
                        </div>
                    ) : null}

                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                        <Input
                            type="email"
                            placeholder="student.worker@tamu.edu"
                            value={newEmail}
                            onChange={(e) => setNewEmail(e.target.value)}
                            disabled={loading || saving}
                            onKeyDown={(e) => {
                                if (e.key === "Enter") add();
                            }}
                        />
                        <Button type="button" onClick={add} disabled={loading || saving || !newEmail.trim()}>
                            <Plus className="mr-2 h-4 w-4" />
                            Add
                        </Button>
                        <Button
                            type="button"
                            variant="default"
                            onClick={() => void save()}
                            disabled={loading || saving || !isDirty}
                        >
                            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                            Save
                        </Button>
                    </div>

                    <div className="rounded-lg border bg-card overflow-hidden">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Email</TableHead>
                                    <TableHead>Source</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {envAllowedUsers.length === 0 && sortedDraft.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={3} className="text-sm text-muted-foreground">
                                            No allowed users configured.
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    <>
                                        {envAllowedUsers.map((email) => (
                                            <TableRow key={`env-${email}`}>
                                                <TableCell className="font-mono text-sm">{email}</TableCell>
                                                <TableCell>
                                                    <Badge variant="warning">env</Badge>
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    <Button type="button" size="sm" variant="ghost" disabled className="cursor-not-allowed">
                                                        <Lock className="mr-2 h-4 w-4" />
                                                        Pinned
                                                    </Button>
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                        {sortedDraft.map((email) => (
                                            <TableRow key={email}>
                                                <TableCell className="font-mono text-sm">{email}</TableCell>
                                                <TableCell>
                                                    <Badge variant="secondary">db</Badge>
                                                </TableCell>
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

                    <div className="text-xs text-muted-foreground">
                        Admins remain allowed to access the app even if their email is not listed here.
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
