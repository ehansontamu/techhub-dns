import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { Cable, Loader2, Monitor, Plus, RefreshCw, Save, Trash2, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import {
  compatibilityEditorApi,
  COMPATIBILITY_EDITOR_DETAIL_FIELDS,
  COMPATIBILITY_EDITOR_DETAIL_LABELS,
  COMPATIBILITY_EDITOR_DETAIL_STATUS_VALUES,
  COMPATIBILITY_EDITOR_STATUS_VALUES,
  type CompatibilityEditorCell,
  type CompatibilityEditorComputer,
  type CompatibilityEditorDetailField,
  type CompatibilityEditorDetailStatus,
  type CompatibilityEditorDock,
  type CompatibilityEditorPayload,
  type CompatibilityEditorStatus,
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
import { getUserDisplayName } from "../utils/userDisplay";

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

const clonePayload = (payload: CompatibilityEditorPayload): CompatibilityEditorPayload =>
  JSON.parse(JSON.stringify(payload)) as CompatibilityEditorPayload;

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

const applyStatusToComputer = (
  computer: CompatibilityEditorComputer,
  dockKey: string,
  status: CompatibilityEditorStatus
) => {
  computer.incompatibleWith = (computer.incompatibleWith ?? []).filter((key) => key !== dockKey);
  computer.partiallyCompatibleWith = (computer.partiallyCompatibleWith ?? []).filter((key) => key !== dockKey);

  if (status === "Incompatible") {
    computer.incompatibleWith.push(dockKey);
    return;
  }

  if (status === "Partially Compatible") {
    computer.partiallyCompatibleWith.push(dockKey);
  }
};

export default function CompatibilityEditor() {
  const { isAdmin, isLoading: authLoading, user } = useAuth();
  const currentUserLabel = getUserDisplayName(user, "you");
  const [payload, setPayload] = useState<CompatibilityEditorPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activeCell, setActiveCell] = useState<ActiveCell | null>(null);
  const [cellForm, setCellForm] = useState<CellForm | null>(null);
  const [addItemKind, setAddItemKind] = useState<AddItemKind>(null);
  const [addItemForm, setAddItemForm] = useState<AddItemForm>(defaultAddItemForm);
  const [draftId] = useState(createClientId);

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

  const loadData = useCallback(async () => {
    if (!isAdmin) {
      setLoading(false);
      return;
    }

    setLoading(true);
    try {
      const nextPayload = await compatibilityEditorApi.getStagingData();
      setPayload(nextPayload);
    } catch (error: unknown) {
      const message = extractApiErrorMessage(error, "Failed to load compatibility staging data.");
      toast.error("Failed to load Compatibility Editor", { description: message });
      setPayload(null);
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    if (!payload || !activeCell) {
      setCellForm(null);
      return;
    }

    const computer = payload.computers[activeCell.computerKey];
    const cell = computer?.compatibilityData?.[activeCell.dockKey];
    if (!computer) {
      setCellForm(null);
      return;
    }

    setCellForm(createCellForm(computer, activeCell.dockKey, cell));
  }, [activeCell, payload]);

  const saveData = async () => {
    if (!payload) {
      return;
    }

    setSaving(true);
    try {
      await compatibilityEditorApi.saveStagingData(payload);
      toast.success("Compatibility staging data saved");
    } catch (error: unknown) {
      const message = extractApiErrorMessage(error, "Save failed.");
      toast.error("Failed to save Compatibility Editor", { description: message });
    } finally {
      setSaving(false);
    }
  };

  const updatePayload = (updater: (draft: CompatibilityEditorPayload) => void) => {
    setPayload((current) => {
      if (!current) {
        return current;
      }

      const draft = clonePayload(current);
      updater(draft);
      return draft;
    });
  };

  const openAddDialog = (kind: MatrixAxis) => {
    setAddItemKind(kind);
    setAddItemForm(defaultAddItemForm);
  };

  const addItem = () => {
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

    updatePayload((draft) => {
      if (addItemKind === "computer") {
        draft.computers[sku] = {
          name,
          url,
          hidden: false,
          studentEdited: true,
          incompatibleWith: [],
          partiallyCompatibleWith: [],
          compatibilityNotes: {},
          compatibilityData: Object.fromEntries(
            Object.keys(draft.docks).map((dockKey) => [dockKey, { studentEdited: true }])
          ),
        };
        return;
      }

      draft.docks[sku] = {
        name,
        url,
        hidden: false,
        studentEdited: true,
      };

      for (const computer of Object.values(draft.computers)) {
        computer.compatibilityData = {
          ...(computer.compatibilityData ?? {}),
          [sku]: { studentEdited: true },
        };
      }
    });

    setAddItemKind(null);
  };

  const updateComputer = (
    computerKey: string,
    updater: (computer: CompatibilityEditorComputer) => void
  ) => {
    updatePayload((draft) => {
      const computer = draft.computers[computerKey];
      if (computer) {
        updater(computer);
      }
    });
  };

  const updateDock = (dockKey: string, updater: (dock: CompatibilityEditorDock) => void) => {
    updatePayload((draft) => {
      const dock = draft.docks[dockKey];
      if (dock) {
        updater(dock);
      }
    });
  };

  const removeComputer = (computerKey: string) => {
    if (!window.confirm(`Remove ${payload?.computers[computerKey]?.name ?? "this computer"} from staging?`)) {
      return;
    }

    updatePayload((draft) => {
      delete draft.computers[computerKey];
    });
  };

  const removeDock = (dockKey: string) => {
    if (!window.confirm(`Remove ${payload?.docks[dockKey]?.name ?? "this dock"} from staging?`)) {
      return;
    }

    updatePayload((draft) => {
      delete draft.docks[dockKey];
      for (const computer of Object.values(draft.computers)) {
        computer.incompatibleWith = (computer.incompatibleWith ?? []).filter((key) => key !== dockKey);
        computer.partiallyCompatibleWith = (computer.partiallyCompatibleWith ?? []).filter((key) => key !== dockKey);
        if (computer.compatibilityNotes) {
          delete computer.compatibilityNotes[dockKey];
        }
        if (computer.compatibilityData) {
          delete computer.compatibilityData[dockKey];
        }
      }
    });
  };

  const saveCell = () => {
    if (!activeCell || !cellForm) {
      return;
    }

    updatePayload((draft) => {
      const computer = draft.computers[activeCell.computerKey];
      if (!computer) {
        return;
      }

      computer.compatibilityData = computer.compatibilityData ?? {};
      const cell: CompatibilityEditorCell = {
        ...(computer.compatibilityData[activeCell.dockKey] ?? {}),
        compatibilityStatus: cellForm.status,
        rebootNeeded: cellForm.rebootNeeded,
      };

      for (const field of COMPATIBILITY_EDITOR_DETAIL_FIELDS) {
        cell[field] = cellForm.details[field];
      }

      const notes = cellForm.notes.trim();
      computer.compatibilityNotes = computer.compatibilityNotes ?? {};
      if (notes) {
        cell.notes = notes;
        computer.compatibilityNotes[activeCell.dockKey] = notes;
      } else {
        delete cell.notes;
        delete computer.compatibilityNotes[activeCell.dockKey];
      }

      computer.compatibilityData[activeCell.dockKey] = cell;
      applyStatusToComputer(computer, activeCell.dockKey, cellForm.status);
    });

    setActiveCell(null);
    toast.success("Compatibility cell updated");
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

  if (!isAdmin) {
    return (
      <div className="container mx-auto space-y-4 py-6">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Compatibility Editor</h1>
          <p className="text-sm text-muted-foreground">Admin-only staging matrix editor.</p>
        </div>
        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
          <h2 className="text-base font-semibold tracking-tight">Access denied</h2>
          <p className="mt-1 text-sm text-muted-foreground">Admin access is required to view this page.</p>
          <p className="mt-4 text-sm text-muted-foreground">
            {currentUserLabel ? `Signed in as ${currentUserLabel}.` : "You are not signed in."}
          </p>
        </section>
      </div>
    );
  }

  return (
    <div className="container mx-auto space-y-4 py-6">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Compatibility Editor</h1>
          <p className="text-sm text-muted-foreground">Staging matrix for computers and docks.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="outline" onClick={() => void loadData()} disabled={saving || loading}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
          <Button type="button" onClick={() => void saveData()} disabled={!payload || saving} className="btn-lift">
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
            Save Staging
          </Button>
        </div>
      </div>

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
                                  onClick={() => setActiveCell({ computerKey, dockKey })}
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
                    onNameChange={(name) => updateComputer(computerKey, (draft) => { draft.name = name; })}
                    onUrlChange={(url) => updateComputer(computerKey, (draft) => { draft.url = url; })}
                    onHiddenChange={(hidden) => updateComputer(computerKey, (draft) => { draft.hidden = hidden; })}
                    onRemove={() => removeComputer(computerKey)}
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
                    onNameChange={(name) => updateDock(dockKey, (draft) => { draft.name = name; })}
                    onUrlChange={(url) => updateDock(dockKey, (draft) => { draft.url = url; })}
                    onHiddenChange={(hidden) => updateDock(dockKey, (draft) => { draft.hidden = hidden; })}
                    onRemove={() => removeDock(dockKey)}
                  />
                );
              })}
            </MatrixItemSection>
          </TabsContent>
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
            <Button type="button" onClick={saveCell}>
              <Save className="mr-2 h-4 w-4" />
              Save Cell
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
            <Button type="button" onClick={addItem}>
              <Plus className="mr-2 h-4 w-4" />
              Add
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
  onNameChange: (name: string) => void;
  onUrlChange: (url: string) => void;
  onHiddenChange: (hidden: boolean) => void;
  onRemove: () => void;
};

function MatrixItemRow({
  sku,
  name,
  url,
  hidden,
  onNameChange,
  onUrlChange,
  onHiddenChange,
  onRemove,
}: MatrixItemRowProps) {
  return (
    <div className="grid gap-3 rounded-lg border bg-card p-3 md:grid-cols-[minmax(8rem,0.8fr)_minmax(12rem,1.4fr)_minmax(12rem,1.8fr)_auto_auto] md:items-center">
      <div>
        <div className="text-xs font-medium uppercase text-muted-foreground">SKU</div>
        <div className="break-words text-sm font-medium text-foreground">{sku}</div>
      </div>
      <Input value={name} onChange={(event) => onNameChange(event.target.value)} aria-label={`Name for ${sku}`} />
      <Input
        value={url}
        type="url"
        inputMode="url"
        autoCapitalize="off"
        onChange={(event) => onUrlChange(event.target.value)}
        aria-label={`URL for ${sku}`}
      />
      <Checkbox checked={hidden} onChange={(event) => onHiddenChange(event.target.checked)} label="Hidden" />
      <Button type="button" variant="destructive" size="icon" onClick={onRemove} aria-label={`Remove ${name || sku}`}>
        <Trash2 className="h-4 w-4" />
      </Button>
    </div>
  );
}
