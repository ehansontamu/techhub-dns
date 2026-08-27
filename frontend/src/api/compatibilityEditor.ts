import { apiClient } from "./client";

export const COMPATIBILITY_EDITOR_DETAIL_FIELDS = [
  "display",
  "charging",
  "usbDetection",
  "ethernet",
  "audio",
  "sdCard",
] as const;

export const COMPATIBILITY_EDITOR_DETAIL_STATUS_VALUES = [
  "Functional",
  "Partially Functional",
  "Non-functional",
  "N/A",
] as const;

export const COMPATIBILITY_EDITOR_STATUS_VALUES = [
  "Compatible",
  "Incompatible",
  "Partially Compatible",
] as const;

export type CompatibilityEditorDetailField = (typeof COMPATIBILITY_EDITOR_DETAIL_FIELDS)[number];
export type CompatibilityEditorDetailStatus = (typeof COMPATIBILITY_EDITOR_DETAIL_STATUS_VALUES)[number];
export type CompatibilityEditorStatus = (typeof COMPATIBILITY_EDITOR_STATUS_VALUES)[number];

export const COMPATIBILITY_EDITOR_DETAIL_LABELS: Record<CompatibilityEditorDetailField, string> = {
  display: "Display(s)",
  charging: "Charging",
  usbDetection: "Thumb drive detection",
  ethernet: "Ethernet connection",
  audio: "3.5mm audio",
  sdCard: "SD card slot",
};

export interface CompatibilityEditorDock {
  name: string;
  url?: string;
  hidden?: boolean;
  studentEdited?: boolean;
  [key: string]: unknown;
}

export interface CompatibilityEditorCell {
  compatibilityStatus?: CompatibilityEditorStatus;
  display?: CompatibilityEditorDetailStatus;
  charging?: CompatibilityEditorDetailStatus;
  usbDetection?: CompatibilityEditorDetailStatus;
  ethernet?: CompatibilityEditorDetailStatus;
  audio?: CompatibilityEditorDetailStatus;
  sdCard?: CompatibilityEditorDetailStatus;
  rebootNeeded?: boolean | string;
  notes?: string;
  studentEdited?: boolean;
  [key: string]: unknown;
}

export interface CompatibilityEditorComputer {
  name: string;
  url?: string;
  hidden?: boolean;
  studentEdited?: boolean;
  incompatibleWith?: string[];
  partiallyCompatibleWith?: string[];
  compatibilityNotes?: Record<string, string>;
  compatibilityData?: Record<string, CompatibilityEditorCell>;
  [key: string]: unknown;
}

export interface CompatibilityEditorPayload {
  computers: Record<string, CompatibilityEditorComputer>;
  docks: Record<string, CompatibilityEditorDock>;
  [key: string]: unknown;
}

export interface CompatibilityEditorVersions {
  computers: Record<string, number>;
  docks: Record<string, number>;
  cells: Record<string, Record<string, number>>;
}

export interface CompatibilityEditorPublication {
  configured: boolean;
  publishedRevision: number;
  pending: boolean;
  pendingSince: string | null;
  lastPublishedAt: string | null;
  lastAttemptAt: string | null;
  lastError: string | null;
  sha256: string | null;
  filename: string;
}

export interface CompatibilityEditorChange {
  id: string;
  target: string;
  mutationType: CompatibilityEditorMutation["type"];
  baseVersion: number;
  version: number;
  proposedData: Record<string, unknown>;
  currentData: Record<string, unknown> | null;
  status: "pending" | "approved" | "rejected";
  readyForReview: boolean;
  bundle: CompatibilityEditorBundle | null;
  submittedBy: string;
  updatedBy: string;
  submittedAt: string | null;
  updatedAt: string | null;
  reviewedBy: string | null;
  reviewedAt: string | null;
  reviewNote: string | null;
}

export interface CompatibilityEditorBundle {
  axis: "computer" | "dock";
  itemKey: string;
  completedCells: number;
  requiredCells: number;
  missingTargets: string[];
  ready: boolean;
}

export interface CompatibilityEditorApproval {
  pendingCount: number;
  pendingChanges: CompatibilityEditorChange[];
  draftCount: number;
  draftBundles: CompatibilityEditorChange[];
}

export interface CompatibilityEditorDocument {
  data: CompatibilityEditorPayload;
  revision: number;
  workspaceRevision: number;
  versions: CompatibilityEditorVersions;
  approvedVersions: CompatibilityEditorVersions;
  approval: CompatibilityEditorApproval;
  publication: CompatibilityEditorPublication;
  duplicate?: boolean;
}

export type CompatibilityEditorMutation =
  | {
      type: "cell.update";
      computerKey: string;
      dockKey: string;
      expectedVersion: number;
      cell: CompatibilityEditorCell;
    }
  | {
      type: "computer.add";
      computerKey: string;
      computer: CompatibilityEditorComputer;
    }
  | {
      type: "computer.update";
      computerKey: string;
      expectedVersion: number;
      computer: CompatibilityEditorComputer;
    }
  | {
      type: "computer.delete";
      computerKey: string;
      expectedVersion: number;
      expectedRevision: number;
    }
  | { type: "dock.add"; dockKey: string; dock: CompatibilityEditorDock }
  | {
      type: "dock.update";
      dockKey: string;
      expectedVersion: number;
      dock: CompatibilityEditorDock;
    }
  | {
      type: "dock.delete";
      dockKey: string;
      expectedVersion: number;
      expectedRevision: number;
    };

export const compatibilityEditorApi = {
  async getData(): Promise<CompatibilityEditorDocument> {
    const response = await apiClient.get("/system/compatibility-editor");
    return response.data;
  },

  async mutate(
    mutation: CompatibilityEditorMutation,
    operationId: string
  ): Promise<CompatibilityEditorDocument> {
    const response = await apiClient.patch("/system/compatibility-editor", {
      operationId,
      mutation,
    });
    return response.data;
  },

  async publish(): Promise<{
    attempted: boolean;
    success: boolean;
    revision: number;
    pending: boolean;
    error: string | null;
    filename: string;
    snapshotId: string | null;
  }> {
    const response = await apiClient.post("/system/compatibility-editor/publish");
    return response.data;
  },

  async review(changeId: string, action: "approve" | "reject", note?: string): Promise<CompatibilityEditorDocument> {
    const response = await apiClient.post(`/system/compatibility-editor/changes/${changeId}/review`, {
      action,
      ...(note ? { note } : {}),
    });
    return response.data;
  },

  async submitBundle(changeId: string): Promise<CompatibilityEditorDocument> {
    const response = await apiClient.post(`/system/compatibility-editor/changes/${changeId}/submit`);
    return response.data;
  },
};
