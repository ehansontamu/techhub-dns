import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Cable, Check, Cloud, CloudOff, Loader2, Monitor, Plus, RefreshCw, Save, Trash2, X, type LucideIcon } from "lucide-react";
import { io } from "socket.io-client";
import { toast } from "sonner";
import {
  compatibilityEditorApi,
  COMPATIBILITY_EDITOR_DETAIL_FIELDS,
  COMPATIBILITY_EDITOR_DETAIL_LABELS,
  COMPATIBILITY_EDITOR_DETAIL_STATUS_VALUES,
  COMPATIBILITY_EDITOR_STATUS_VALUES,
  type CompatibilityEditorCell,
  type CompatibilityEditorChange,
  type CompatibilityEditorComputer,
  type CompatibilityEditorDetailField,
  type CompatibilityEditorDetailStatus,
  type CompatibilityEditorDock,
  type CompatibilityEditorDocument,
  type CompatibilityEditorMutation,
  type CompatibilityEditorPayload,
  type CompatibilityEditorPublication,
  type CompatibilityEditorStatus,
  type CompatibilityEditorVersions,
} from "../api/compatibilityEditor";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Checkbox } from "../components/ui/checkbox";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "../components/ui/dialog";
import { Input } from "../components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { useAuth } from "../contexts/AuthContext";
import { cn } from "../lib/utils";
import { extractApiErrorMessage } from "../utils/apiErrors";

type MatrixAxis = "computer" | "dock";
type AddItemKind = MatrixAxis | null;

type AddItemForm = {
  sku: string;
  name: string;
  url: string;
};

type ActiveCell = {
  computerKey: string;
  dockKey: string;
  expectedVersion: number;
};

type CellForm = {
  status: CompatibilityEditorStatus;
  rebootNeeded: boolean;
  notes: string;
  details: Record<CompatibilityEditorDetailField, CompatibilityEditorDetailStatus>;
};

type CellVisual = {
  symbol: string;
  label: string;
  className: string;
};

const defaultAddItemForm: AddItemForm = {
  sku: "",
  name: "",
  url: "",
};

const defaultDetails = (): Record<CompatibilityEditorDetailField, CompatibilityEditorDetailStatus> =>
  Object.fromEntries(COMPATIBILITY_EDITOR_DETAIL_FIELDS.map((field) => [field, "N/A"])) as Record<
    CompatibilityEditorDetailField,
    CompatibilityEditorDetailStatus
  >;

const isCompatibilityStatus = (value: unknown): value is CompatibilityEditorStatus =>
  typeof value === "string" && COMPATIBILITY_EDITOR_STATUS_VALUES.includes(value as CompatibilityEditorStatus);

const isDetailStatus = (value: unknown): value is CompatibilityEditorDetailStatus =>
  typeof value === "string" && COMPATIBILITY_EDITOR_DETAIL_STATUS_VALUES.includes(value as CompatibilityEditorDetailStatus);

const createClientId = () =>
  typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;

const sortKeysByName = <T extends { name: string }>(items: Record<string, T>) =>
  Object.keys(items).sort((a, b) => items[a].name.localeCompare(items[b].name));

const normalizeRebootNeeded = (value: CompatibilityEditorCell["rebootNeeded"]): boolean => {
  if (typeof value === "boolean") {
    return value;
  }

  if (typeof value === "string") {
    return ["true", "1", "yes", "y", "on"].includes(value.trim().toLowerCase());
  }

  return false;
};

const getStatusForCell = (
  computer: CompatibilityEditorComputer,
  dockKey: string,
  cell?: CompatibilityEditorCell
): CompatibilityEditorStatus => {
  if (isCompatibilityStatus(cell?.compatibilityStatus)) {
    return cell.compatibilityStatus;
  }

  if (computer.incompatibleWith?.includes(dockKey)) {
    return "Incompatible";
  }

  if (computer.partiallyCompatibleWith?.includes(dockKey)) {
    return "Partially Compatible";
  }

  return "Compatible";
};

const getCellNotes = (computer: CompatibilityEditorComputer, dockKey: string, cell?: CompatibilityEditorCell) => {
  const cellNotes = typeof cell?.notes === "string" ? cell.notes.trim() : "";
  if (cellNotes) {
    return cellNotes;
  }

  return computer.compatibilityNotes?.[dockKey]?.trim() ?? "";
};

const getCellVisual = (
  computer: CompatibilityEditorComputer,
  dock: CompatibilityEditorDock,
  dockKey: string
): CellVisual => {
  const cell = computer.compatibilityData?.[dockKey];
  if (computer.hidden || dock.hidden) {
    return {
      symbol: "Hidden",
      label: "Hidden",
      className: "border-muted bg-muted text-muted-foreground",
    };
  }

  const hasNotes = Boolean(getCellNotes(computer, dockKey, cell));
  if (cell?.studentEdited) {
    return {
      symbol: hasNotes ? "? *" : "?",
      label: "Needs review",
      className: "border-sky-300 bg-sky-100 text-sky-900 hover:bg-sky-200",
    };
  }

  const status = getStatusForCell(computer, dockKey, cell);
  if (status === "Incompatible") {
    return {
      symbol: hasNotes ? "No *" : "No",
      label: "Incompatible",
      className: "border-red-200 bg-red-50 text-red-800 hover:bg-red-100",
    };
  }

  if (status === "Partially Compatible") {
    return {
      symbol: hasNotes ? "Partial *" : "Partial",
      label: "Partially compatible",
      className: "border-amber-200 bg-amber-50 text-amber-900 hover:bg-amber-100",
    };
  }

  return {
    symbol: hasNotes ? "Yes *" : "Yes",
    label: "Compatible",
    className: "border-emerald-200 bg-emerald-50 text-emerald-900 hover:bg-emerald-100",
  };
};

const createCellForm = (
  computer: CompatibilityEditorComputer,
  dockKey: string,
  cell?: CompatibilityEditorCell
): CellForm => {
  const details = defaultDetails();
  for (const field of COMPATIBILITY_EDITOR_DETAIL_FIELDS) {
    if (isDetailStatus(cell?.[field])) {
      details[field] = cell[field];
    }
  }

  return {
    status: getStatusForCell(computer, dockKey, cell),
    rebootNeeded: normalizeRebootNeeded(cell?.rebootNeeded),
    notes: getCellNotes(computer, dockKey, cell),
    details,
  };
};

export default function CompatibilityEditor() {
  const { isAdmin, isLoading: authLoading } = useAuth();
  const [payload, setPayload] = useState<CompatibilityEditorPayload | null>(null);
  const [versions, setVersions] = useState<CompatibilityEditorVersions | null>(null);
  const [approvedVersions, setApprovedVersions] = useState<CompatibilityEditorVersions | null>(null);
  const [pendingChanges, setPendingChanges] = useState<CompatibilityEditorChange[]>([]);
  const [revision, setRevision] = useState(0);
  const [publication, setPublication] = useState<CompatibilityEditorPublication | null>(null);
  const [conflictMessage, setConflictMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [reviewingId, setReviewingId] = useState<string | null>(null);
  const [activeCell, setActiveCell] = useState<ActiveCell | null>(null);
  const [cellForm, setCellForm] = useState<CellForm | null>(null);
  const [addItemKind, setAddItemKind] = useState<AddItemKind>(null);
  const [addItemForm, setAddItemForm] = useState<AddItemForm>(defaultAddItemForm);
  const [draftId] = useState(createClientId);
  const workspaceRevisionRef = useRef(0);

  const computerKeys = useMemo(() => (payload ? sortKeysByName(payload.computers) : []), [payload]);
  const dockKeys = useMemo(() => (payload ? sortKeysByName(payload.docks) : []), [payload]);
  const visibleComputerCount = useMemo(
    () => computerKeys.filter((key) => !payload?.computers[key].hidden).length,
    [computerKeys, payload]
  );
  const visibleDockCount = useMemo(
    () => dockKeys.filter((key) => !payload?.docks[key].hidden).length,
    [dockKeys, payload]
  );

  const applyDocument = useCallback((document: CompatibilityEditorDocument) => {
    if (document.workspaceRevision < workspaceRevisionRef.current) {
      return;
    }
    setPayload(document.data);
    setVersions(document.versions);
    setApprovedVersions(document.approvedVersions);
    setPendingChanges(document.approval.pendingChanges);
    setRevision(document.revision);
    workspaceRevisionRef.current = document.workspaceRevision;
    setPublication(document.publication);
  }, []);

  const loadData = useCallback(async (silent = false) => {
    if (!silent) {
      setLoading(true);
    }
    try {
      const document = await compatibilityEditorApi.getData();
      applyDocument(document);
    } catch (error: unknown) {
      const message = extractApiErrorMessage(error, "Failed to load compatibility data.");
      if (!silent) {
        toast.error("Failed to load Compatibility Editor", { description: message });
        setPayload(null);
      }
    } finally {
      if (!silent) {
        setLoading(false);
      }
    }
  }, [applyDocument]);

  useEffect(() => {
    void loadData(false);
  }, [loadData]);

  useEffect(() => {
    const socket = io(`${window.location.protocol}//${window.location.host}`, {
      path: "/socket.io",
      transports: ["websocket", "polling"],
      reconnection: true,
    });
    socket.on("connect", () => socket.emit("join", { room: "compatibility-editor" }));
    socket.on("compatibility_editor_updated", (event: { workspaceRevision?: number }) => {
      if (
        typeof event.workspaceRevision !== "number"
        || event.workspaceRevision > workspaceRevisionRef.current
      ) {
        void loadData(true);
      }
    });
    const interval = window.setInterval(() => void loadData(true), 15_000);
    return () => {
      window.clearInterval(interval);
      socket.disconnect();
    };
  }, [loadData]);

  const performMutation = async (
    mutation: CompatibilityEditorMutation,
    successMessage: string
  ): Promise<void> => {
    setSaving(true);
    try {
      const document = await compatibilityEditorApi.mutate(mutation, createClientId());
      applyDocument(document);
      toast.success(successMessage);
    } catch (error: unknown) {
      const message = extractApiErrorMessage(error, "Save failed.");
      const conflictDocument = (
        error as { response?: { status?: number; data?: { document?: CompatibilityEditorDocument } } }
      ).response?.data?.document;
      if (conflictDocument) {
        applyDocument(conflictDocument);
        if (mutation.type === "cell.update") {
          const latestVersion = conflictDocument.versions.cells[mutation.computerKey]?.[mutation.dockKey];
          if (typeof latestVersion === "number") {
            setActiveCell((current) => current ? { ...current, expectedVersion: latestVersion } : current);
          }
          setConflictMessage(
            "This cell changed after you opened it. Your draft is preserved; review it, then save again to replace the newer value."
          );
        }
      }
      toast.error(
        (error as { response?: { status?: number } }).response?.status === 409
          ? "Another editor changed this item"
          : "Failed to save Compatibility Editor",
        { description: message }
      );
      throw error;
    } finally {
      setSaving(false);
    }
  };

  const openCell = (computerKey: string, dockKey: string) => {
    if (!payload || !versions) {
      return;
    }
    const computer = payload.computers[computerKey];
    if (!computer) {
      return;
    }
    setCellForm(createCellForm(computer, dockKey, computer.compatibilityData?.[dockKey]));
    setConflictMessage(null);
    setActiveCell({
      computerKey,
      dockKey,
      expectedVersion: isAdmin
        ? approvedVersions?.cells[computerKey]?.[dockKey] ?? versions.cells[computerKey]?.[dockKey] ?? 0
        : versions.cells[computerKey]?.[dockKey] ?? 0,
    });
  };

  const openAddDialog = (kind: MatrixAxis) => {
    setAddItemKind(kind);
    setAddItemForm(defaultAddItemForm);
  };

  const addItem = async () => {
    if (!payload || !addItemKind) {
      return;
    }

    const sku = addItemForm.sku.trim();
    const name = addItemForm.name.trim();
    const url = addItemForm.url.trim();
    if (!sku || !name) {
      toast.error("SKU and name are required");
      return;
    }

    if (addItemKind === "computer" && payload.computers[sku]) {
      toast.error("A computer with that SKU already exists");
      return;
    }

    if (addItemKind === "dock" && payload.docks[sku]) {
      toast.error("A dock with that SKU already exists");
      return;
    }

    try {
      if (addItemKind === "computer") {
        await performMutation({
          type: "computer.add",
          computerKey: sku,
          computer: {
          name,
          url,
          hidden: false,
          studentEdited: true,
          },
        }, isAdmin ? "Computer added to approved data" : "Computer submitted for review");
      } else {
        await performMutation({
          type: "dock.add",
          dockKey: sku,
          dock: { name, url, hidden: false, studentEdited: true },
        }, isAdmin ? "Dock added to approved data" : "Dock submitted for review");
      }
      setAddItemKind(null);
    } catch {
      // The shared mutation handler displays the error and refreshes conflicts.
    }
  };

  const saveComputer = async (
    computerKey: string,
    computer: CompatibilityEditorComputer,
    expectedVersion: number
  ) => {
    await performMutation(
      { type: "computer.update", computerKey, computer, expectedVersion },
      "Computer updated"
    );
  };

  const saveDock = async (
    dockKey: string,
    dock: CompatibilityEditorDock,
    expectedVersion: number
  ) => {
    await performMutation(
      { type: "dock.update", dockKey, dock, expectedVersion },
      "Dock updated"
    );
  };

  const removeComputer = async (computerKey: string) => {
    if (!window.confirm(`Remove ${payload?.computers[computerKey]?.name ?? "this computer"} from the compatibility matrix?`)) {
      return;
    }
    try {
      await performMutation(
        {
          type: "computer.delete",
          computerKey,
          expectedVersion: versions?.computers[computerKey] ?? 0,
          expectedRevision: revision,
        },
        "Computer removed"
      );
    } catch {
      // Error already displayed.
    }
  };

  const removeDock = async (dockKey: string) => {
    if (!window.confirm(`Remove ${payload?.docks[dockKey]?.name ?? "this dock"} from the compatibility matrix?`)) {
      return;
    }
    try {
      await performMutation(
        {
          type: "dock.delete",
          dockKey,
          expectedVersion: versions?.docks[dockKey] ?? 0,
          expectedRevision: revision,
        },
        "Dock removed"
      );
    } catch {
      // Error already displayed.
    }
  };

  const saveCell = async () => {
    if (!activeCell || !cellForm) {
      return;
    }
    const currentCell = payload?.computers[activeCell.computerKey]?.compatibilityData?.[activeCell.dockKey];
    const cell: CompatibilityEditorCell = {
        ...(currentCell ?? {}),
        compatibilityStatus: cellForm.status,
        rebootNeeded: cellForm.rebootNeeded,
      };

      for (const field of COMPATIBILITY_EDITOR_DETAIL_FIELDS) {
        cell[field] = cellForm.details[field];
      }

      const notes = cellForm.notes.trim();
      if (notes) {
        cell.notes = notes;
      } else {
        delete cell.notes;
      }
    try {
      await performMutation(
        {
          type: "cell.update",
          computerKey: activeCell.computerKey,
          dockKey: activeCell.dockKey,
          expectedVersion: activeCell.expectedVersion,
          cell,
        },
        isAdmin ? "Compatibility cell approved" : "Compatibility change submitted for review"
      );
      setActiveCell(null);
      setCellForm(null);
      setConflictMessage(null);
    } catch {
      // Keep the modal open so the user can compare/retry after a conflict.
    }
  };

  const reviewPendingChange = async (changeId: string, action: "approve" | "reject") => {
    setReviewingId(changeId);
    try {
      const document = await compatibilityEditorApi.review(changeId, action);
      applyDocument(document);
      toast.success(action === "approve" ? "Change approved" : "Change rejected");
    } catch (error: unknown) {
      toast.error("Review failed", {
        description: extractApiErrorMessage(error, "The pending change could not be reviewed."),
      });
      await loadData(true);
    } finally {
      setReviewingId(null);
    }
  };

  const publishApprovedData = async () => {
    const pendingWarning = pendingChanges.length
      ? ` ${pendingChanges.length} pending change${pendingChanges.length === 1 ? "" : "s"} will remain excluded.`
      : "";
    if (!window.confirm(`Save approved revision ${revision} to compatibility_superapp.json?${pendingWarning}`)) {
      return;
    }
    setPublishing(true);
    try {
      const result = await compatibilityEditorApi.publish();
      if (!result.success) {
        throw new Error(result.error || "WebDAV did not accept the file.");
      }
      toast.success("Approved compatibility JSON saved to WebDAV");
      await loadData(true);
    } catch (error: unknown) {
      toast.error("WebDAV save failed", {
        description: extractApiErrorMessage(error, "The approved snapshot is queued for retry."),
      });
      await loadData(true);
    } finally {
      setPublishing(false);
    }
  };

  if (authLoading || loading) {
    return (
      <div className="container mx-auto py-6">
        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading compatibility editor...
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="container mx-auto space-y-4 py-6">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Compatibility Editor</h1>
          <p className="text-sm text-muted-foreground">Collaborative matrix for computers and docks.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={pendingChanges.length ? "warning" : "success"}>
            {pendingChanges.length} pending review
          </Badge>
          {isAdmin ? (
            <Badge variant={!publication?.configured || publication?.lastError ? "destructive" : publication?.pending ? "warning" : "success"}>
              {!publication?.configured || publication?.lastError ? (
                <CloudOff className="mr-1.5 h-3.5 w-3.5" />
              ) : (
                <Cloud className="mr-1.5 h-3.5 w-3.5" />
              )}
              {!publication?.configured
                ? "WebDAV not configured"
                : publication?.lastError
                ? "WebDAV retry pending"
                : publication?.pending
                  ? "Approved changes not published"
                  : "WebDAV current"}
            </Badge>
          ) : null}
          <span className="text-xs text-muted-foreground">Revision {revision}</span>
          {isAdmin ? (
            <Button
              type="button"
              onClick={() => void publishApprovedData()}
              disabled={publishing || saving || Boolean(reviewingId) || !publication?.configured}
            >
              {publishing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
              Save to WebDAV
            </Button>
          ) : null}
          <Button type="button" variant="outline" onClick={() => void loadData(false)} disabled={saving || loading}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>
      </div>

      {!isAdmin ? (
        <section className="rounded-lg border border-sky-300 bg-sky-50 px-4 py-3 text-sm text-sky-900">
          Your changes are saved to the app for admin review and cannot update the website JSON directly.
        </section>
      ) : !publication?.configured ? (
        <section className="rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          Database editing is available, but the WebDAV folder is not configured.
        </section>
      ) : publication?.lastError ? (
        <section className="rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          Database changes are safe, but WebDAV publishing is retrying: {publication.lastError}
        </section>
      ) : null}

      {!payload ? (
        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
          <p className="text-sm text-muted-foreground">No compatibility data is loaded.</p>
        </section>
      ) : (
        <Tabs defaultValue="matrix" className="space-y-4">
          <TabsList className="max-w-3xl">
            <TabsTrigger value="matrix">Matrix</TabsTrigger>
            <TabsTrigger value="computers">Computers</TabsTrigger>
            <TabsTrigger value="docks">Docks</TabsTrigger>
            {isAdmin ? <TabsTrigger value="review">Review ({pendingChanges.length})</TabsTrigger> : null}
          </TabsList>

          <TabsContent value="matrix" className="space-y-4">
            <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <h2 className="text-base font-semibold tracking-tight">Matrix</h2>
                  <p className="text-sm text-muted-foreground">
                    {visibleComputerCount} visible computers · {visibleDockCount} visible docks
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="success">Compatible</Badge>
                  <Badge variant="warning">Partial</Badge>
                  <Badge variant="destructive">Incompatible</Badge>
                  <Badge variant="secondary">Needs review</Badge>
                </div>
              </div>

              <div className="custom-scrollbar mt-4 overflow-auto rounded-lg border bg-card">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="sticky left-0 z-20 min-w-[12rem] bg-card">Computers / Docks</TableHead>
                      {dockKeys.map((dockKey) => {
                        const dock = payload.docks[dockKey];
                        return (
                          <TableHead key={dockKey} className="min-w-[9rem] max-w-[11rem] text-center">
                            <span className={cn("block whitespace-normal break-words text-xs", dock.hidden && "text-muted-foreground line-through")}>
                              {dock.name}
                            </span>
                          </TableHead>
                        );
                      })}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {computerKeys.map((computerKey) => {
                      const computer = payload.computers[computerKey];
                      return (
                        <TableRow key={computerKey}>
                          <TableCell className="sticky left-0 z-10 min-w-[12rem] bg-card font-medium">
                            <span className={cn("block whitespace-normal break-words text-sm", computer.hidden && "text-muted-foreground line-through")}>
                              {computer.name}
                            </span>
                          </TableCell>
                          {dockKeys.map((dockKey) => {
                            const dock = payload.docks[dockKey];
                            const visual = getCellVisual(computer, dock, dockKey);
                            return (
                              <TableCell key={dockKey} className="p-1.5 text-center">
                                <button
                                  type="button"
                                  onClick={() => openCell(computerKey, dockKey)}
                                  title={`${computer.name} / ${dock.name}: ${visual.label}`}
                                  className={cn(
                                    "h-12 w-full min-w-[6.5rem] rounded-md border px-2 text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                                    visual.className
                                  )}
                                >
                                  {visual.symbol}
                                </button>
                              </TableCell>
                            );
                          })}
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            </section>
          </TabsContent>

          <TabsContent value="computers" className="space-y-4">
            <MatrixItemSection
              icon={Monitor}
              title="Computers"
              count={computerKeys.length}
              onAdd={() => openAddDialog("computer")}
            >
              {computerKeys.map((computerKey) => {
                const computer = payload.computers[computerKey];
                return (
                  <MatrixItemRow
                    key={computerKey}
                    sku={computerKey}
                    name={computer.name}
                    url={computer.url ?? ""}
                    hidden={Boolean(computer.hidden)}
                    version={(isAdmin ? approvedVersions : versions)?.computers[computerKey] ?? 0}
                    pending={Boolean(computer.studentEdited)}
                    editable={isAdmin && !computer.studentEdited && !publishing}
                    onSave={(value, expectedVersion) => saveComputer(computerKey, { ...computer, ...value }, expectedVersion)}
                    onRemove={() => void removeComputer(computerKey)}
                  />
                );
              })}
            </MatrixItemSection>
          </TabsContent>

          <TabsContent value="docks" className="space-y-4">
            <MatrixItemSection
              icon={Cable}
              title="Docks"
              count={dockKeys.length}
              onAdd={() => openAddDialog("dock")}
            >
              {dockKeys.map((dockKey) => {
                const dock = payload.docks[dockKey];
                return (
                  <MatrixItemRow
                    key={dockKey}
                    sku={dockKey}
                    name={dock.name}
                    url={dock.url ?? ""}
                    hidden={Boolean(dock.hidden)}
                    version={(isAdmin ? approvedVersions : versions)?.docks[dockKey] ?? 0}
                    pending={Boolean(dock.studentEdited)}
                    editable={isAdmin && !dock.studentEdited && !publishing}
                    onSave={(value, expectedVersion) => saveDock(dockKey, { ...dock, ...value }, expectedVersion)}
                    onRemove={() => void removeDock(dockKey)}
                  />
                );
              })}
            </MatrixItemSection>
          </TabsContent>

          {isAdmin ? (
            <TabsContent value="review" className="space-y-4">
              <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
                <div>
                  <h2 className="text-base font-semibold tracking-tight">Pending review</h2>
                  <p className="text-sm text-muted-foreground">
                    Approvals update the database only. Use Save to WebDAV when the complete approved set is ready.
                  </p>
                </div>
                {pendingChanges.length === 0 ? (
                  <p className="mt-4 text-sm text-muted-foreground">There are no pending changes.</p>
                ) : (
                  <div className="mt-4 space-y-4">
                    {pendingChanges.map((change) => (
                      <div key={change.id} className="rounded-lg border bg-background p-4">
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                          <div>
                            <div className="flex flex-wrap items-center gap-2">
                              <Badge variant="secondary">{change.mutationType}</Badge>
                              <span className="font-mono text-sm font-medium">{change.target}</span>
                            </div>
                            <p className="mt-1 text-xs text-muted-foreground">
                              Submitted by {change.submittedBy}{change.updatedAt ? ` · ${new Date(change.updatedAt).toLocaleString()}` : ""}
                            </p>
                          </div>
                          <div className="flex gap-2">
                            <Button
                              type="button"
                              variant="outline"
                              onClick={() => void reviewPendingChange(change.id, "reject")}
                              disabled={Boolean(reviewingId) || publishing}
                            >
                              {reviewingId === change.id ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <X className="mr-2 h-4 w-4" />}
                              Reject
                            </Button>
                            <Button
                              type="button"
                              onClick={() => void reviewPendingChange(change.id, "approve")}
                              disabled={Boolean(reviewingId) || publishing}
                            >
                              {reviewingId === change.id ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Check className="mr-2 h-4 w-4" />}
                              Approve
                            </Button>
                          </div>
                        </div>
                        <div className="mt-3 grid gap-3 lg:grid-cols-2">
                          <div>
                            <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">Currently approved</div>
                            <pre className="max-h-52 overflow-auto rounded-md bg-muted p-3 text-xs">{JSON.stringify(change.currentData, null, 2)}</pre>
                          </div>
                          <div>
                            <div className="mb-1 text-xs font-medium uppercase text-muted-foreground">Proposed</div>
                            <pre className="max-h-52 overflow-auto rounded-md bg-sky-50 p-3 text-xs text-sky-950">{JSON.stringify(change.proposedData, null, 2)}</pre>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </TabsContent>
          ) : null}
        </Tabs>
      )}

      <Dialog open={Boolean(activeCell)} onOpenChange={(open) => !open && setActiveCell(null)}>
        <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>
              {activeCell && payload
                ? `${payload.docks[activeCell.dockKey]?.name ?? "Dock"} / ${payload.computers[activeCell.computerKey]?.name ?? "Computer"}`
                : "Compatibility"}
            </DialogTitle>
          </DialogHeader>

          {cellForm ? (
            <div className="space-y-5">
              {conflictMessage ? (
                <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  {conflictMessage}
                </div>
              ) : null}
              <div className="grid gap-3 sm:grid-cols-3">
                {COMPATIBILITY_EDITOR_STATUS_VALUES.map((status) => (
                  <label
                    key={status}
                    className={cn(
                      "flex min-h-11 cursor-pointer items-center justify-center rounded-md border px-3 py-2 text-center text-sm font-medium transition-colors",
                      cellForm.status === status ? "border-primary bg-primary text-primary-foreground" : "border-border bg-background hover:bg-muted/50"
                    )}
                  >
                    <input
                      type="radio"
                      name={`compatibility-status-${draftId}`}
                      value={status}
                      checked={cellForm.status === status}
                      onChange={() => setCellForm((current) => current ? { ...current, status } : current)}
                      className="sr-only"
                    />
                    {status}
                  </label>
                ))}
              </div>

              <div className="rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Test</TableHead>
                      <TableHead>Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {COMPATIBILITY_EDITOR_DETAIL_FIELDS.map((field) => (
                      <TableRow key={field}>
                        <TableCell className="font-medium">{COMPATIBILITY_EDITOR_DETAIL_LABELS[field]}</TableCell>
                        <TableCell>
                          <select
                            value={cellForm.details[field]}
                            onChange={(event) => {
                              const status = event.target.value as CompatibilityEditorDetailStatus;
                              setCellForm((current) =>
                                current
                                  ? {
                                      ...current,
                                      details: {
                                        ...current.details,
                                        [field]: status,
                                      },
                                    }
                                  : current
                              );
                            }}
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                          >
                            {COMPATIBILITY_EDITOR_DETAIL_STATUS_VALUES.map((status) => (
                              <option key={status} value={status}>
                                {status}
                              </option>
                            ))}
                          </select>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              <Checkbox
                checked={cellForm.rebootNeeded}
                onChange={(event) => setCellForm((current) => current ? { ...current, rebootNeeded: event.target.checked } : current)}
                label="Reboot needed"
              />

              <div className="space-y-1.5">
                <label htmlFor="compatibility-notes" className="text-sm font-medium text-foreground">
                  Notes
                </label>
                <textarea
                  id="compatibility-notes"
                  value={cellForm.notes}
                  onChange={(event) => setCellForm((current) => current ? { ...current, notes: event.target.value } : current)}
                  className="min-h-24 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                />
              </div>
            </div>
          ) : null}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setActiveCell(null)}>
              Cancel
            </Button>
            <Button type="button" onClick={() => void saveCell()} disabled={saving || publishing}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
              {isAdmin ? "Save and Approve" : "Submit for Review"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(addItemKind)} onOpenChange={(open) => !open && setAddItemKind(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add {addItemKind === "computer" ? "Computer" : "Dock"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <label htmlFor="compatibility-add-sku" className="text-sm font-medium text-foreground">
                SKU
              </label>
              <Input
                id="compatibility-add-sku"
                value={addItemForm.sku}
                onChange={(event) => setAddItemForm((current) => ({ ...current, sku: event.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="compatibility-add-name" className="text-sm font-medium text-foreground">
                Name
              </label>
              <Input
                id="compatibility-add-name"
                value={addItemForm.name}
                onChange={(event) => setAddItemForm((current) => ({ ...current, name: event.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="compatibility-add-url" className="text-sm font-medium text-foreground">
                URL
              </label>
              <Input
                id="compatibility-add-url"
                type="url"
                inputMode="url"
                autoCapitalize="off"
                value={addItemForm.url}
                onChange={(event) => setAddItemForm((current) => ({ ...current, url: event.target.value }))}
              />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setAddItemKind(null)}>
              Cancel
            </Button>
            <Button type="button" onClick={() => void addItem()} disabled={saving || publishing}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
              {isAdmin ? "Add" : "Submit for Review"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

type MatrixItemSectionProps = {
  icon: LucideIcon;
  title: string;
  count: number;
  children: ReactNode;
  onAdd: () => void;
};

function MatrixItemSection({ icon: Icon, title, count, children, onAdd }: MatrixItemSectionProps) {
  return (
    <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-md bg-secondary text-secondary-foreground">
            <Icon className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-base font-semibold tracking-tight">{title}</h2>
            <p className="text-sm text-muted-foreground">{count} total</p>
          </div>
        </div>
        <Button type="button" variant="outline" onClick={onAdd}>
          <Plus className="mr-2 h-4 w-4" />
          Add
        </Button>
      </div>
      <div className="mt-4 space-y-3">{children}</div>
    </section>
  );
}

type MatrixItemRowProps = {
  sku: string;
  name: string;
  url: string;
  hidden: boolean;
  version: number;
  pending: boolean;
  editable: boolean;
  onSave: (
    value: { name: string; url: string; hidden: boolean },
    expectedVersion: number
  ) => Promise<void>;
  onRemove: () => void;
};

function MatrixItemRow({
  sku,
  name,
  url,
  hidden,
  version,
  pending,
  editable,
  onSave,
  onRemove,
}: MatrixItemRowProps) {
  const [draft, setDraft] = useState({ name, url, hidden });
  const [rowSaving, setRowSaving] = useState(false);
  const editingRef = useRef(false);
  const baseVersionRef = useRef(version);

  useEffect(() => {
    if (!editingRef.current && !rowSaving) {
      setDraft({ name, url, hidden });
      baseVersionRef.current = version;
    }
  }, [hidden, name, rowSaving, url, version]);

  const beginEditing = () => {
    if (!editingRef.current) {
      editingRef.current = true;
      baseVersionRef.current = version;
    }
  };

  const saveDraft = async (nextDraft = draft) => {
    editingRef.current = false;
    if (nextDraft.name === name && nextDraft.url === url && nextDraft.hidden === hidden) {
      return;
    }
    setRowSaving(true);
    try {
      await onSave(nextDraft, baseVersionRef.current);
    } catch {
      setDraft({ name, url, hidden });
    } finally {
      setRowSaving(false);
    }
  };

  return (
    <div className="grid gap-3 rounded-lg border bg-card p-3 md:grid-cols-[minmax(8rem,0.8fr)_minmax(12rem,1.4fr)_minmax(12rem,1.8fr)_auto_auto] md:items-center">
      <div>
        <div className="text-xs font-medium uppercase text-muted-foreground">SKU</div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="break-words text-sm font-medium text-foreground">{sku}</div>
          {pending ? <Badge variant="secondary">Pending approval</Badge> : null}
        </div>
      </div>
      <Input
        value={draft.name}
        onFocus={beginEditing}
        onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
        onBlur={() => void saveDraft()}
        disabled={rowSaving || !editable}
        aria-label={`Name for ${sku}`}
      />
      <Input
        value={draft.url}
        type="url"
        inputMode="url"
        autoCapitalize="off"
        onFocus={beginEditing}
        onChange={(event) => setDraft((current) => ({ ...current, url: event.target.value }))}
        onBlur={() => void saveDraft()}
        disabled={rowSaving || !editable}
        aria-label={`URL for ${sku}`}
      />
      <Checkbox
        checked={draft.hidden}
        disabled={rowSaving || !editable}
        onChange={(event) => {
          const nextDraft = { ...draft, hidden: event.target.checked };
          beginEditing();
          setDraft(nextDraft);
          void saveDraft(nextDraft);
        }}
        label="Hidden"
      />
      <Button type="button" variant="destructive" size="icon" onClick={onRemove} disabled={rowSaving || !editable} aria-label={`Remove ${name || sku}`}>
        {rowSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
      </Button>
    </div>
  );
}
