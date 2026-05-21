import { useEffect, useMemo, useState } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  vettingEditorApi,
  VETTING_EDITOR_CATEGORIES,
  VETTING_EDITOR_LEGACY_SECTION_ORDER,
  VETTING_EDITOR_SECTIONS,
  VETTING_EDITOR_VETTING_URL_SECTIONS,
  normalizeVettingEditorSection,
  type VettingEditorCategory,
  type VettingEditorItem,
  type VettingEditorPayload,
  type VettingEditorSection,
} from "../api/vettingEditor";
import { useAuth } from "../contexts/AuthContext";
import { getUserDisplayName } from "../utils/userDisplay";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { cn } from "../lib/utils";
import { extractApiErrorMessage } from "../utils/apiErrors";

type VettingEditorRow = {
  id: string;
  name: string;
  section: VettingEditorSection;
  category: VettingEditorCategory;
  url: string;
  vettingUrl: string;
};

type RequiredField = "name" | "url";
type RowFieldErrors = Partial<Record<RequiredField, string>>;

const defaultSection: VettingEditorSection = VETTING_EDITOR_SECTIONS[0];
const defaultCategory: VettingEditorCategory = "ACCESSORIES";
const vettingUrlSections = new Set<VettingEditorSection>(VETTING_EDITOR_VETTING_URL_SECTIONS);

const sectionUsesVettingUrl = (section: VettingEditorSection): boolean => vettingUrlSections.has(section);

const formatSectionLabel = (section: VettingEditorSection): string => section.replace(/([a-z])([A-Z])/g, "$1 $2");

const formatCategoryLabel = (category: VettingEditorCategory): string =>
  category
    .toLowerCase()
    .replace(/\b\w/g, (char) => char.toUpperCase());

const createRow = (item?: Partial<VettingEditorRow>): VettingEditorRow => ({
  id: typeof crypto !== "undefined" && typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
  name: item?.name ?? "",
  section: item?.section ?? defaultSection,
  category: item?.category ?? defaultCategory,
  url: item?.url ?? "",
  vettingUrl: item?.vettingUrl ?? "",
});

const flattenPayload = (payload: VettingEditorPayload): VettingEditorRow[] => {
  const rows: VettingEditorRow[] = [];
  for (const section of VETTING_EDITOR_SECTIONS) {
    for (const item of payload[section] ?? []) {
      rows.push(
        createRow({
          name: item.name,
          section,
          category: item.category,
          url: item.url,
          vettingUrl: item.vettingUrl ?? "",
        })
      );
    }
  }
  return rows;
};

const buildPayload = (rows: VettingEditorRow[]): VettingEditorPayload => {
  const sectionItemsByCanonicalSection: Partial<Record<VettingEditorSection, VettingEditorItem[]>> = {};

  for (const row of rows) {
    const canonicalSection = normalizeVettingEditorSection(row.section);
    if (!canonicalSection) {
      throw new Error(`Row has unsupported section '${row.section}'.`);
    }

    const name = row.name.trim();
    const url = row.url.trim();
    if (!name || !url) {
      throw new Error("Each row requires Name and Product URL.");
    }

    const item: VettingEditorItem = {
      name,
      category: row.category,
      url,
    };

    const vettingUrl = row.vettingUrl.trim();
    if (sectionUsesVettingUrl(canonicalSection) && vettingUrl) {
      item.vettingUrl = vettingUrl;
    }

    const sectionItems = sectionItemsByCanonicalSection[canonicalSection] ?? [];
    sectionItems.push(item);
    sectionItemsByCanonicalSection[canonicalSection] = sectionItems;
  }

  const payload: VettingEditorPayload = {};
  for (const section of VETTING_EDITOR_LEGACY_SECTION_ORDER) {
    const sectionItems = sectionItemsByCanonicalSection[section];
    if (!sectionItems) {
      continue;
    }
    payload[section] = sectionItems;
  }

  return payload;
};

export default function VettingEditor() {
    const { isAdmin, isLoading: authLoading, user } = useAuth();
    const currentUserLabel = getUserDisplayName(user, "you");
  const [rows, setRows] = useState<VettingEditorRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [rowFieldErrors, setRowFieldErrors] = useState<Record<string, RowFieldErrors>>({});

  const rowCount = rows.length;

  const sectionCounts = useMemo(() => {
    const counts = Object.fromEntries(VETTING_EDITOR_SECTIONS.map((section) => [section, 0])) as Record<VettingEditorSection, number>;
    for (const row of rows) counts[row.section] += 1;
    return counts;
  }, [rows]);

  const sectionSummary = useMemo(
    () => VETTING_EDITOR_SECTIONS.map((section) => `${formatSectionLabel(section)} ${sectionCounts[section]}`).join(" · "),
    [sectionCounts]
  );

  useEffect(() => {
    if (!isAdmin) {
      setLoading(false);
      return;
    }

    const run = async () => {
      setLoading(true);
      try {
        const payload = await vettingEditorApi.getData();
        setRows(flattenPayload(payload));
        setRowFieldErrors({});
      } catch (error: unknown) {
        const message = extractApiErrorMessage(error, "Failed to load vetting data.");
        toast.error("Failed to load Vetting Editor", { description: message });
        setRows([]);
      } finally {
        setLoading(false);
      }
    };

    void run();
  }, [isAdmin]);

  const updateRow = (id: string, updater: (row: VettingEditorRow) => VettingEditorRow) => {
    setRows((current) => current.map((row) => (row.id === id ? updater(row) : row)));
  };

  const getRequiredFieldError = (field: RequiredField, value: string): string | undefined => {
    if (value.trim()) {
      return undefined;
    }

    if (field === "name") {
      return "Name is required.";
    }

    return "Product URL is required.";
  };

  const clearRowErrors = (id: string) => {
    setRowFieldErrors((current) => {
      if (!current[id]) {
        return current;
      }

      const next = { ...current };
      delete next[id];
      return next;
    });
  };

  const validateFieldOnBlur = (row: VettingEditorRow, field: RequiredField) => {
    const nextError = getRequiredFieldError(field, row[field]);
    setRowFieldErrors((current) => {
      const rowErrors = current[row.id] ?? {};
      const existingError = rowErrors[field];

      if (existingError === nextError) {
        return current;
      }

      if (!nextError) {
        if (!existingError) {
          return current;
        }

        const nextRowErrors = { ...rowErrors };
        delete nextRowErrors[field];
        if (Object.keys(nextRowErrors).length === 0) {
          const next = { ...current };
          delete next[row.id];
          return next;
        }

        return {
          ...current,
          [row.id]: nextRowErrors,
        };
      }

      return {
        ...current,
        [row.id]: {
          ...rowErrors,
          [field]: nextError,
        },
      };
    });
  };

  const validateRows = (currentRows: VettingEditorRow[]): boolean => {
    const nextErrors: Record<string, RowFieldErrors> = {};

    for (const row of currentRows) {
      const nameError = getRequiredFieldError("name", row.name);
      const urlError = getRequiredFieldError("url", row.url);

      if (!nameError && !urlError) {
        continue;
      }

      nextErrors[row.id] = {
        ...(nameError ? { name: nameError } : {}),
        ...(urlError ? { url: urlError } : {}),
      };
    }

    setRowFieldErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const updateRequiredField = (id: string, field: RequiredField, value: string) => {
    updateRow(id, (current) => ({
      ...current,
      [field]: value,
    }));

    setRowFieldErrors((current) => {
      const rowErrors = current[id];
      if (!rowErrors?.[field]) {
        return current;
      }

      const nextError = getRequiredFieldError(field, value);
      if (nextError) {
        return current;
      }

      const nextRowErrors = { ...rowErrors };
      delete nextRowErrors[field];
      if (Object.keys(nextRowErrors).length === 0) {
        const next = { ...current };
        delete next[id];
        return next;
      }

      return {
        ...current,
        [id]: nextRowErrors,
      };
    });
  };

  const addRow = () => setRows((current) => [createRow(), ...current]);

  const deleteRow = (id: string) => {
    setRows((current) => current.filter((row) => row.id !== id));
    clearRowErrors(id);
  };

  const handleSave = async () => {
    if (!validateRows(rows)) {
      toast.error("Please complete required fields", {
        description: "Each product must include Name and Product URL before saving.",
      });
      return;
    }

    setSaving(true);
    try {
      const payload = buildPayload(rows);
      await vettingEditorApi.saveData(payload);
      toast.success("Vetting data saved");
    } catch (error: unknown) {
      const message = extractApiErrorMessage(error, "Save failed.");
      toast.error("Failed to save Vetting Editor", { description: message });
    } finally {
      setSaving(false);
    }
  };

  if (authLoading || loading) {
    return (
      <div className="container mx-auto py-6">
        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading vetting editor...
          </div>
        </section>
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="container mx-auto py-6 space-y-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Vetting Editor</h1>
          <p className="text-sm text-muted-foreground">Admin-only vetting list editor.</p>
        </div>
        <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
          <h2 className="text-base font-semibold tracking-tight">Access denied</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Admin access is required to view this page.
          </p>
          <p className="mt-4 text-sm text-muted-foreground">
            {currentUserLabel ? `Signed in as ${currentUserLabel}.` : "You are not signed in."}
          </p>
        </section>
      </div>
    );
  }

  return (
    <div className="container mx-auto space-y-4 py-6 pb-28 md:pb-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Vetting Editor</h1>
          <p className="text-sm text-muted-foreground">
            Manage products across the vetting lifecycle.
          </p>
        </div>
        <div className="hidden items-center gap-2 md:flex">
          <Button type="button" variant="outline" onClick={addRow} disabled={saving}>
            <Plus className="mr-2 h-4 w-4" />
            Add Product
          </Button>
          <Button type="button" onClick={() => void handleSave()} disabled={saving} className="btn-lift">
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Save
          </Button>
        </div>
      </div>

      <section className="rounded-2xl border border-border/70 bg-card/80 p-5 shadow-none">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-base font-semibold tracking-tight">Products</h2>
            <p className="text-sm text-muted-foreground">{rowCount} total · {sectionSummary}</p>
          </div>
        </div>
        <div className="mt-4">
          {rows.length === 0 ? (
            <div className="rounded-lg border border-dashed bg-muted/30 p-6 text-center text-sm text-muted-foreground">
              No products found. Add a product to begin.
            </div>
          ) : (
            <>
              <div className="space-y-4 md:hidden">
                {rows.map((row, index) => {
                  const nameError = rowFieldErrors[row.id]?.name;
                  const productUrlError = rowFieldErrors[row.id]?.url;

                  return (
                    <div key={row.id} className="rounded-lg border bg-card p-4 shadow-sm">
                      <div className="mb-4 flex items-center justify-between gap-3">
                        <div className="text-sm font-medium text-foreground">Product {index + 1}</div>
                        <Button
                          type="button"
                          size="icon"
                          variant="destructive"
                          onClick={() => deleteRow(row.id)}
                          aria-label={`Delete ${row.name.trim() || `product ${index + 1}`}`}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>

                      <div className="space-y-3">
                        <div className="space-y-1.5">
                          <label htmlFor={`vetting-mobile-name-${row.id}`} className="text-sm font-medium text-foreground">
                            Name
                          </label>
                          <Input
                            id={`vetting-mobile-name-${row.id}`}
                            name={`vettingMobileName-${row.id}`}
                            value={row.name}
                            onChange={(event) => updateRequiredField(row.id, "name", event.target.value)}
                            onBlur={() => validateFieldOnBlur(row, "name")}
                            aria-invalid={Boolean(nameError)}
                            className={cn(nameError && "border-destructive focus-visible:ring-destructive")}
                            placeholder="e.g., Magic Keyboard"
                          />
                          {nameError ? <p className="text-xs text-destructive">{nameError}</p> : null}
                        </div>

                        <div className="space-y-1.5">
                          <label htmlFor={`vetting-mobile-section-${row.id}`} className="text-sm font-medium text-foreground">
                            Section
                          </label>
                          <select
                            id={`vetting-mobile-section-${row.id}`}
                            name={`vettingMobileSection-${row.id}`}
                            value={row.section}
                            onChange={(event) => {
                              const section = normalizeVettingEditorSection(event.target.value);
                              if (!section) {
                                return;
                              }
                              updateRow(row.id, (current) => ({
                                ...current,
                                section,
                                vettingUrl: sectionUsesVettingUrl(section) ? current.vettingUrl : "",
                              }));
                            }}
                            className="flex h-11 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                          >
                            {VETTING_EDITOR_SECTIONS.map((section) => (
                              <option key={section} value={section}>
                                {formatSectionLabel(section)}
                              </option>
                            ))}
                          </select>
                        </div>

                        <div className="space-y-1.5">
                          <label htmlFor={`vetting-mobile-category-${row.id}`} className="text-sm font-medium text-foreground">
                            Category
                          </label>
                          <select
                            id={`vetting-mobile-category-${row.id}`}
                            name={`vettingMobileCategory-${row.id}`}
                            value={row.category}
                            onChange={(event) => {
                              const category = event.target.value as VettingEditorCategory;
                              updateRow(row.id, (current) => ({ ...current, category }));
                            }}
                            className="flex h-11 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                          >
                            {VETTING_EDITOR_CATEGORIES.map((category) => (
                              <option key={category} value={category}>
                                {formatCategoryLabel(category)}
                              </option>
                            ))}
                          </select>
                        </div>

                        <div className="space-y-1.5">
                          <label htmlFor={`vetting-mobile-url-${row.id}`} className="text-sm font-medium text-foreground">
                            Product URL
                          </label>
                          <Input
                            id={`vetting-mobile-url-${row.id}`}
                            name={`vettingMobileUrl-${row.id}`}
                            type="url"
                            inputMode="url"
                            autoCapitalize="off"
                            value={row.url}
                            onChange={(event) => updateRequiredField(row.id, "url", event.target.value)}
                            onBlur={() => validateFieldOnBlur(row, "url")}
                            aria-invalid={Boolean(productUrlError)}
                            className={cn(productUrlError && "border-destructive focus-visible:ring-destructive")}
                            placeholder="e.g., https://store.example.com"
                          />
                          {productUrlError ? <p className="text-xs text-destructive">{productUrlError}</p> : null}
                        </div>

                        {sectionUsesVettingUrl(row.section) ? (
                          <div className="space-y-1.5">
                            <label htmlFor={`vetting-mobile-review-url-${row.id}`} className="text-sm font-medium text-foreground">
                              Vetting URL
                            </label>
                            <Input
                              id={`vetting-mobile-review-url-${row.id}`}
                              name={`vettingMobileReviewUrl-${row.id}`}
                              type="url"
                              inputMode="url"
                              autoCapitalize="off"
                              value={row.vettingUrl}
                              onChange={(event) =>
                                updateRow(row.id, (current) => ({
                                  ...current,
                                  vettingUrl: event.target.value,
                                }))
                              }
                              placeholder="e.g., https://store.example.com"
                            />
                          </div>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className="hidden rounded-lg border bg-card md:block">
                <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-20">Actions</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>Section</TableHead>
                    <TableHead>Category</TableHead>
                    <TableHead>Product URL</TableHead>
                    <TableHead>Vetting URL</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => {
                    const nameError = rowFieldErrors[row.id]?.name;
                    const productUrlError = rowFieldErrors[row.id]?.url;

                    return (
                      <TableRow key={row.id}>
                      <TableCell>
                        <Button
                          type="button"
                          size="sm"
                          variant="destructive"
                          onClick={() => deleteRow(row.id)}
                          aria-label={`Delete ${row.name.trim() || "product"}`}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </TableCell>
                      <TableCell>
                        <div className="space-y-1.5">
                          <Input
                            id={`vetting-name-${row.id}`}
                            name={`vettingName-${row.id}`}
                            value={row.name}
                            onChange={(event) => updateRequiredField(row.id, "name", event.target.value)}
                            onBlur={() => validateFieldOnBlur(row, "name")}
                            aria-invalid={Boolean(nameError)}
                            className={cn(nameError && "border-destructive focus-visible:ring-destructive")}
                            placeholder="e.g., Magic Keyboard"
                          />
                          {nameError ? <p className="text-xs text-destructive">{nameError}</p> : null}
                        </div>
                      </TableCell>
                      <TableCell>
                        <select
                          id={`vetting-section-${row.id}`}
                          name={`vettingSection-${row.id}`}
                          value={row.section}
                          onChange={(event) => {
                            const section = normalizeVettingEditorSection(event.target.value);
                            if (!section) {
                              return;
                            }
                            updateRow(row.id, (current) => ({
                              ...current,
                              section,
                              vettingUrl: sectionUsesVettingUrl(section) ? current.vettingUrl : "",
                            }));
                          }}
                          className="flex h-10 w-full min-w-[10rem] rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 md:min-w-[12rem] lg:min-w-[13rem]"
                        >
                          {VETTING_EDITOR_SECTIONS.map((section) => (
                            <option key={section} value={section}>
                              {formatSectionLabel(section)}
                            </option>
                          ))}
                        </select>
                      </TableCell>
                      <TableCell>
                        <select
                          id={`vetting-category-${row.id}`}
                          name={`vettingCategory-${row.id}`}
                          value={row.category}
                          onChange={(event) => {
                            const category = event.target.value as VettingEditorCategory;
                            updateRow(row.id, (current) => ({ ...current, category }));
                          }}
                          className="flex h-10 w-full min-w-[10rem] rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 md:min-w-[12rem] lg:min-w-[15rem]"
                        >
                          {VETTING_EDITOR_CATEGORIES.map((category) => (
                            <option key={category} value={category}>
                              {formatCategoryLabel(category)}
                            </option>
                          ))}
                        </select>
                      </TableCell>
                      <TableCell>
                        <div className="space-y-1.5">
                          <Input
                            id={`vetting-url-${row.id}`}
                            name={`vettingUrl-${row.id}`}
                            type="url"
                            inputMode="url"
                            autoCapitalize="off"
                            value={row.url}
                            onChange={(event) => updateRequiredField(row.id, "url", event.target.value)}
                            onBlur={() => validateFieldOnBlur(row, "url")}
                            aria-invalid={Boolean(productUrlError)}
                            className={cn(productUrlError && "border-destructive focus-visible:ring-destructive")}
                            placeholder="e.g., https://store.example.com"
                          />
                          {productUrlError ? <p className="text-xs text-destructive">{productUrlError}</p> : null}
                        </div>
                      </TableCell>
                      <TableCell>
                        {sectionUsesVettingUrl(row.section) ? (
                          <Input
                            id={`vetting-review-url-${row.id}`}
                            name={`vettingReviewUrl-${row.id}`}
                            type="url"
                            inputMode="url"
                            autoCapitalize="off"
                            value={row.vettingUrl}
                            onChange={(event) =>
                              updateRow(row.id, (current) => ({
                                ...current,
                                vettingUrl: event.target.value,
                              }))
                            }
                            placeholder="e.g., https://store.example.com"
                          />
                        ) : null}
                      </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
                </Table>
              </div>
            </>
          )}
        </div>
      </section>

      <div className="fixed inset-x-0 bottom-0 z-30 border-t bg-background/95 p-3 backdrop-blur supports-[backdrop-filter]:bg-background/80 md:hidden">
        <div className="container mx-auto flex items-center gap-2 px-0">
          <Button type="button" variant="outline" onClick={addRow} disabled={saving} className="flex-1">
            <Plus className="mr-2 h-4 w-4" />
            Add Product
          </Button>
          <Button type="button" onClick={() => void handleSave()} disabled={saving} className="btn-lift flex-1">
            {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Save
          </Button>
        </div>
      </div>
    </div>
  );
}
